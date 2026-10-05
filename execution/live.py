"""
Vòng lặp LIVE (ADR 0001 bước 7 — Forward Test trên demo).

Gọi ĐÚNG `CANDIDATE.signals()` của backtest trên các bar ĐÃ ĐÓNG, lấy hàng cuối, và
thực thi theo đúng ngữ nghĩa của backtest/engine.py (live ≡ backtest):
  - Tín hiệu ở bar i → hành động ngay sau khi i đóng (≈ open bar i+1).
  - Mỗi slot (Strategy × symbol) tối đa 1 vị thế + 1 lệnh chờ (OCO = 2 chân); có vị thế
    hoặc lệnh chờ thì tín hiệu mới bị bỏ qua. Chân OCO khớp trước → hủy chân kia.
  - trail_long/trail_short của hàng i dời SL (chỉ theo hướng có lợi) sau khi i đóng;
    mức trail đã vượt giá hiện tại → đóng ngay (engine: gap qua SL → khớp open).
  - flat[i] → đóng vị thế sau khi i đóng.
  - max_bars (của hàng tín hiệu vào lệnh) → đóng khi đã có max_bars bar ĐÓNG tính từ bar
    khớp (gồm bar khớp) = open bar khớp+max_bars. Đếm BAR, không đếm giờ (cuối tuần).
  - Lệnh chờ hết hạn tại open(j) + (expiry+1)·TF (broker tự hủy + bot tự hủy dự phòng).

Broker là nguồn sự thật: vị thế/lệnh chờ đọc lại từ MT5 mỗi nhịp (lọc theo magic RIÊNG
của slot). Trạng thái cục bộ chỉ gồm bar đã xử lý, giờ hết hạn lệnh chờ và bar khớp + max_bars
của vị thế đang mở (state/, gitignore).

An toàn:
  - Chỉ chạy Strategy đã QUA Final Holdout (tham số đóng băng lấy từ holdout/<name>.json).
  - Mặc định KHÔNG đặt lệnh (chỉ log "[DRY]"). `--execute` chỉ được phép trên tài khoản
    DEMO; tài khoản thật bị từ chối (Forward Test chưa xong).
  - Kill-switch chặn lệnh mới; vị thế đang mở vẫn được quản lý (trail/flat).

    python -m execution.live --strategy c1_donchian                # chỉ log tín hiệu
    python -m execution.live --strategy c1_donchian --execute      # đặt lệnh trên DEMO
"""
import argparse
import importlib
import json
import logging
import math
import os
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from datafeed.bars import TIMEFRAMES
from risk.killswitch import KillSwitch
from risk.risk import RiskGuard, TradePlan, calc_lot_size

log = logging.getLogger("live")

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
LIVE_BARS = 5000                          # đủ dài để trạng thái tuần tự (vd swing C4) hội tụ
MAX_ENTRY_LAG = pd.Timedelta(minutes=15)  # vào lệnh trễ hơn mức này so với open bar kế → bỏ


@dataclass
class Pos:
    ticket: int
    direction: str          # "buy" | "sell"
    entry: float
    sl: float
    tp: float               # NaN = không TP
    lot: float
    opened: pd.Timestamp | None = None   # giờ khớp (giờ server); None → lấy giờ lúc thấy lần đầu


def slot_magic(base: int, strategy: str) -> int:
    """Magic RIÊNG cho từng Strategy trên cùng symbol (ổn định, không phụ thuộc thứ tự)."""
    return base * 100 + zlib.crc32(strategy.encode()) % 100


def tf_len(tf: str) -> pd.Timedelta:
    return pd.Timedelta(TIMEFRAMES[tf])


def pending_expiry(signal_bar: pd.Timestamp, tf: str, expiry_bars: int) -> pd.Timestamp:
    """Engine: lệnh chờ sống ở bar j+1 … j+expiry → hết hạn tại open(j) + (expiry+1)·TF."""
    return signal_bar + (max(int(expiry_bars), 1) + 1) * tf_len(tf)


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


class Slot:
    """Một Strategy chạy trên một symbol."""

    def __init__(self, cand, params: dict, cfg, broker, journal, state_dir: Path = STATE_DIR):
        self.cand, self.params, self.cfg = cand, params, cfg
        self.broker, self.journal = broker, journal
        self.name, self.symbol = cand.name, cfg.symbol
        self.state_path = Path(state_dir) / f"live_{self.name}_{self.symbol}.json"
        self.state = (json.loads(self.state_path.read_text(encoding="utf-8"))
                      if self.state_path.exists() else {"last_bar": None, "pending": {}})
        self.state.setdefault("fills", {})      # ticket → {"bar": bar khớp, "max_bars": n}

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def open_risk(self) -> float:
        """Tiền đang chịu rủi ro tới SL hiện tại (SL đã qua entry có lợi → 0)."""
        cs = self.cfg.contract_size
        out = 0.0
        for p in self.broker.positions():
            loss = (p.entry - p.sl) if p.direction == "buy" else (p.sl - p.entry)
            out += max(loss, 0.0) * p.lot * cs
        return out


class LiveRunner:
    def __init__(self, slots: list[Slot], ks: KillSwitch, notifier, control=None,
                 guard: RiskGuard | None = None, execute: bool = False):
        self.slots, self.ks, self.notifier, self.control = slots, ks, notifier, control
        self.guard = guard
        self.execute = execute

    # ── Một nhịp ──────────────────────────────────────────
    def step(self) -> None:
        if self.control is not None:
            self.control.poll_once()
        for slot in self.slots:
            try:
                self._step_slot(slot)
            except Exception:
                log.exception(f"{slot.name} {slot.symbol}: lỗi trong nhịp — bỏ qua nhịp này")

    def _step_slot(self, slot: Slot) -> None:
        b = slot.broker
        self._sync_closed(slot)
        positions = b.positions()
        self._sync_opened(slot, positions)
        orders = b.pending()
        now = b.now()

        # 1 vị thế / slot: vị thế có rồi → hủy mọi lệnh chờ (= chân OCO còn lại).
        if positions and orders:
            for t in orders:
                self._cancel(slot, t, "chân OCO còn lại / đã có vị thế")
            orders = []
        if len(positions) > 1:   # 2 chân OCO cùng khớp giữa 2 nhịp → giữ lệnh cũ nhất
            for p in sorted(positions, key=lambda p: p.ticket)[1:]:
                log.error(f"{slot.name} {slot.symbol}: >1 vị thế, đóng #{p.ticket}")
                b.close(p.ticket)
        # Hết hạn (dự phòng nếu broker không tự hủy).
        for t in orders:
            exp = slot.state["pending"].get(str(t))
            if exp and now >= pd.Timestamp(exp):
                self._cancel(slot, t, "hết hạn")
        live_tickets = {str(t) for t in b.pending()}
        slot.state["pending"] = {k: v for k, v in slot.state["pending"].items() if k in live_tickets}

        # Bar mới đóng?
        bars, forming_open = b.closed_bars(slot.cand.timeframe, LIVE_BARS)
        last = bars.index[-1]
        if slot.state["last_bar"] is not None and last <= pd.Timestamp(slot.state["last_bar"]):
            slot.save()
            return
        row = slot.cand.signals(bars, **slot.params).iloc[-1]

        positions = b.positions()
        for p in positions:
            self._manage(slot, p, row, bars)
        if int(row["signal"]) != 0 and not positions and not b.pending():
            if now - forming_open > MAX_ENTRY_LAG:
                log.warning(f"{slot.name} {slot.symbol}: tín hiệu bar {last} đã trễ "
                            f"{now - forming_open} (> {MAX_ENTRY_LAG}) — bỏ qua")
            else:
                self._enter(slot, row, last)
        slot.state["last_bar"] = str(last)
        slot.save()

    # ── Quản lý vị thế (trail / flat) ─────────────────────
    def _manage(self, slot: Slot, p: Pos, row, bars: pd.DataFrame) -> None:
        b = slot.broker
        if bool(row.get("flat", False)):
            log.info(f"{slot.name} {slot.symbol}: hết phiên → đóng #{p.ticket}")
            b.close(p.ticket)
            return
        fill = slot.state["fills"].get(str(p.ticket))
        if fill and fill["max_bars"] and (bars.index >= pd.Timestamp(fill["bar"])).sum() >= fill["max_bars"]:
            log.info(f"{slot.name} {slot.symbol}: đủ {fill['max_bars']} bar từ lúc khớp → đóng #{p.ticket}")
            b.close(p.ticket)
            return
        is_buy = p.direction == "buy"
        trail = row.get("trail_long" if is_buy else "trail_short")
        if _nan(trail):
            return
        better = trail > p.sl if is_buy else trail < p.sl
        if not better:
            return
        price = b.price("sell" if is_buy else "buy")   # giá thoát hiện tại
        if (is_buy and trail >= price) or (not is_buy and trail <= price):
            log.info(f"{slot.name} {slot.symbol}: trail {trail} đã vượt giá {price} → đóng #{p.ticket}")
            b.close(p.ticket)
        else:
            b.modify_sl(p.ticket, trail)

    # ── Vào lệnh ──────────────────────────────────────────
    def _enter(self, slot: Slot, row, signal_bar: pd.Timestamp) -> None:
        name, sym, b, cfg = slot.name, slot.symbol, slot.broker, slot.cfg
        if not self.ks.can_enter(name):
            log.info(f"{name} {sym}: có tín hiệu nhưng Strategy đang dừng/tạm dừng — bỏ qua")
            return
        direction = "buy" if int(row["signal"]) > 0 else "sell"
        kind = row["entry_type"]
        legs = [(direction, b.price(direction) if kind == "market" else float(row["entry_price"]),
                 float(row["sl"]), float(row["tp"]))]
        if kind != "market" and not _nan(row.get("oco_price")):
            legs.append(("sell" if direction == "buy" else "buy", float(row["oco_price"]),
                         float(row["oco_sl"]), float(row["oco_tp"])))

        balance = b.balance()
        if self.guard is not None:
            self.guard.update_day(b.now().date(), balance)
            if self.guard.daily_loss_exceeded(balance):
                log.warning(f"{name} {sym}: chạm giới hạn lỗ trong ngày — bỏ qua tín hiệu")
                return
        info = b.symbol_info()
        plans = []
        for d, entry, sl, tp in legs:
            if (d == "buy") != (sl < entry):   # engine.open_pos cũng loại trường hợp này
                log.warning(f"{name} {sym}: SL {sl} sai phía entry {entry} ({d}) — bỏ qua chân này")
                continue
            lot = calc_lot_size(balance, cfg.risk_per_trade_pct, entry, sl, info["contract_size"],
                                info["volume_min"], info["volume_step"], info["volume_max"])
            if lot <= 0:
                log.warning(f"{name} {sym}: lot = 0 (SL quá xa so với vốn) — bỏ qua chân này")
                continue
            plans.append((d, entry, sl, tp, lot))
        if not plans:
            return
        if self.guard is not None:
            open_pct = sum(s.open_risk() for s in self.slots) / balance * 100
            if self.guard.heat_exceeded(open_pct, cfg.risk_per_trade_pct):
                log.warning(f"{name} {sym}: vượt portfolio heat ({open_pct:.2f}% đang mở) — bỏ qua")
                return

        expires = pending_expiry(signal_bar, slot.cand.timeframe, row["expiry"])
        mb = row.get("max_bars")
        slot.state["next_max_bars"] = 0 if _nan(mb) or mb <= 0 else int(mb)
        for d, entry, sl, tp, lot in plans:
            what = (f"{kind.upper()} {d.upper()} {lot} @ {entry} SL {sl} TP {tp}"
                    + ("" if kind == "market" else f" (hết hạn {expires})"))
            if not self.execute:
                log.info(f"[DRY] {name} {sym}: {what}")
                continue
            if kind == "market":
                t = b.place_market(d, lot, sl, tp, comment=name)
                if t:   # khớp ngay trong bar đang hình thành
                    slot.state["fills"][str(t)] = {"bar": str(b.now().floor(TIMEFRAMES[slot.cand.timeframe])),
                                                   "max_bars": slot.state["next_max_bars"]}
            else:
                t = b.place_pending(d, kind, lot, entry, sl, tp, expires, comment=name)
                if t:
                    slot.state["pending"][str(t)] = str(expires)
            log.info(f"{name} {sym}: {what} → {'ticket ' + str(t) if t else 'THẤT BẠI'}")

    def _cancel(self, slot: Slot, ticket, why: str) -> None:
        if slot.broker.cancel(ticket):
            log.info(f"{slot.name} {slot.symbol}: hủy lệnh chờ {ticket} ({why})")
            slot.state["pending"].pop(str(ticket), None)

    # ── Đồng bộ nhật ký ───────────────────────────────────
    def _sync_opened(self, slot: Slot, positions: list[Pos]) -> None:
        """Vị thế mới thấy lần đầu → ghi nhật ký. SL lúc này là SL GỐC (trail chỉ dời sau khi
        bar đóng, và nhịp nào cũng đồng bộ trước khi quản lý) → risk_amount = 1R thật."""
        tf = TIMEFRAMES[slot.cand.timeframe]
        for p in positions:
            if str(p.ticket) not in slot.state["fills"]:
                opened = p.opened if p.opened is not None else slot.broker.now()
                slot.state["fills"][str(p.ticket)] = {"bar": str(pd.Timestamp(opened).floor(tf)),
                                                      "max_bars": slot.state.get("next_max_bars", 0)}
            if slot.journal.has(p.ticket):
                continue
            risk = round(abs(p.entry - p.sl) * p.lot * slot.cfg.contract_size, 2)
            rr = 0.0 if _nan(p.tp) else round(abs(p.tp - p.entry) / abs(p.entry - p.sl), 2)
            slot.journal.record_open(p.ticket, p.direction, p.entry, p.sl, "" if _nan(p.tp) else p.tp,
                                     p.lot, rr, risk, f"{slot.name} {slot.params}",
                                     mode="demo", strategy=slot.name)
            self.notifier.notify_opened(slot.symbol, TradePlan(p.direction, p.entry, p.sl, p.tp, p.lot,
                                                               rr, risk, slot.name))

    def _sync_closed(self, slot: Slot) -> None:
        live = {str(p.ticket) for p in slot.broker.positions()}
        slot.state["fills"] = {k: v for k, v in slot.state["fills"].items() if k in live}
        for rec in slot.journal.open_records():
            if rec.get("strategy") != slot.name or rec["ticket"] in live:
                continue
            info = slot.broker.close_info(int(rec["ticket"]))
            if info is None:
                continue   # lịch sử chưa có deal thoát — thử lại nhịp sau
            result, close_price, profit = info
            slot.journal.record_close(rec["ticket"], result, close_price, profit)
            self.notifier.notify_closed(slot.symbol, rec, result, profit)
            self._evaluate(slot.name)

    def _evaluate(self, name: str) -> None:
        """Kill-switch đo trên MỌI symbol của Strategy (mỗi symbol 1 file nhật ký)."""
        journals = {id(s.journal): s.journal for s in self.slots if s.name == name}
        closed = [x for j in journals.values() for x in j.closed_r(name)]
        reason = self.ks.evaluate(name, closed)
        if reason:
            log.error(f"KILL-SWITCH {name}: {reason}")
            self.notifier.notify_alert(name, reason)


# ═════════════════════════════════════════════════════════
#  Adapter MT5 (mỏng — logic nằm ở LiveRunner để selftest chạy bằng broker giả)
# ═════════════════════════════════════════════════════════
class MT5Broker:
    def __init__(self, symbol: str, magic: int, deviation: int):
        from execution.mt5_client import MT5Client, mt5
        self.mt5 = mt5
        self.client = MT5Client(symbol, magic, deviation)

    def closed_bars(self, tf: str, n: int) -> tuple[pd.DataFrame, pd.Timestamp]:
        """Bỏ bar cuối (đang hình thành). Trả về (bar đã đóng, giờ mở bar đang hình thành)."""
        df = self.client.get_rates(tf, n + 1)
        return df.iloc[:-1][["open", "high", "low", "close"]], df.index[-1]

    def positions(self) -> list[Pos]:
        out = []
        for p in self.client.open_positions():
            out.append(Pos(p.ticket, "buy" if p.type == self.mt5.POSITION_TYPE_BUY else "sell",
                           p.price_open, p.sl, p.tp if p.tp else float("nan"), p.volume,
                           pd.Timestamp(p.time, unit="s")))   # epoch giờ server (GMT+0)
        return out

    def pending(self) -> list[int]:
        return [o.ticket for o in self.client.pending_orders()]

    def price(self, direction: str) -> float:
        t = self.client.get_tick()
        return t.ask if direction == "buy" else t.bid

    def now(self) -> pd.Timestamp:
        return pd.Timestamp(self.client.server_time())

    def balance(self) -> float:
        return self.client.get_balance()

    def symbol_info(self) -> dict:
        return self.client.get_symbol_info()

    def place_market(self, direction, lot, sl, tp, comment):
        return self.client.market_order(direction, lot, sl, 0.0 if _nan(tp) else tp, comment=comment)

    def place_pending(self, direction, kind, lot, price, sl, tp, expires, comment):
        return self.client.pending_order(direction, lot, price, sl, 0.0 if _nan(tp) else tp,
                                         expires.to_pydatetime(), comment=comment, kind=kind)

    def cancel(self, ticket) -> bool:
        return self.client.cancel_order(ticket)

    def _raw(self, ticket):
        got = self.mt5.positions_get(ticket=int(ticket))
        return got[0] if got else None

    def modify_sl(self, ticket, sl) -> bool:
        raw = self._raw(ticket)
        return bool(raw) and self.client.modify_sl(raw, sl)

    def close(self, ticket) -> bool:
        raw = self._raw(ticket)
        return bool(raw) and self.client.close_partial(raw, raw.volume)

    def close_info(self, ticket):
        return self.client.position_close_info(ticket)


def load_strategy(name: str, marker_dir: Path = ROOT / "holdout"):
    """(Candidate, tham số đóng băng, symbols) từ marker Final Holdout đã QUA."""
    m = json.loads((marker_dir / f"{name}.json").read_text(encoding="utf-8"))
    cand = importlib.import_module(f"strategy.candidates.{name}").CANDIDATE
    return cand, m["params"], m["symbols"]


def main():
    for stream in (sys.stdout, sys.stderr):   # console Windows mặc định cp1252
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Vòng lặp live (Forward Test trên demo)")
    p.add_argument("--strategy", action="append", required=True, help="tên Candidate đã qua Final Holdout")
    p.add_argument("--execute", action="store_true", help="đặt lệnh thật trên tài khoản DEMO")
    a = p.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(STATE_DIR / "live.log", encoding="utf-8")])

    from config.config import get_config
    from execution.journal import TradeJournal
    from execution.mt5_client import MT5_AVAILABLE, mt5
    from execution.notifier import TelegramNotifier
    from execution.telegram_control import TelegramControl
    from risk.killswitch import limits_from_holdout

    ks = KillSwitch(STATE_DIR / "killswitch.json", limits_from_holdout(a.strategy))
    if not MT5_AVAILABLE or not mt5.initialize():
        raise SystemExit("Cần terminal MT5 đang chạy & đăng nhập (Windows)")
    acc = mt5.account_info()
    if a.execute and acc.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        raise SystemExit(f"Tài khoản {acc.login} KHÔNG phải demo — --execute chỉ cho phép trên demo "
                         f"(Forward Test chưa xong, ADR 0001)")

    journals, slots, magics = {}, [], {}
    for name in a.strategy:
        cand, params, symbols = load_strategy(name)
        for sym in symbols:
            cfg = get_config(sym)
            magic = slot_magic(cfg.magic_number, name)
            if magic in magics:
                raise SystemExit(f"Trùng magic {magic}: {name} vs {magics[magic]} — đổi slot_magic")
            magics[magic] = name
            mt5.symbol_select(sym, True)
            journals.setdefault(sym, TradeJournal(sym, STATE_DIR / f"trades_{sym}.csv"))
            slots.append(Slot(cand, params, cfg, MT5Broker(sym, magic, cfg.deviation), journals[sym]))
            log.info(f"Slot {name} × {sym}: magic {magic}, tham số {params}")

    cfg0 = get_config(slots[0].symbol)
    runner = LiveRunner(slots, ks, TelegramNotifier.from_config(cfg0),
                        TelegramControl.from_env(ks, STATE_DIR / "telegram_offset.json"),
                        RiskGuard(cfg0.max_daily_loss_pct, cfg0.portfolio_heat_pct), execute=a.execute)
    log.info(f"Bắt đầu — {'ĐẶT LỆNH (demo ' + str(acc.login) + ')' if a.execute else 'DRY (chỉ log)'}")
    try:
        while True:
            runner.step()
            time.sleep(cfg0.poll_seconds)
    except KeyboardInterrupt:
        log.info("Dừng theo yêu cầu")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
