"""
Vòng lặp giao dịch live/demo — hỗ trợ chạy 1 HOẶC NHIỀU symbol trong CÙNG 1 tiến trình.
Chạy 1 symbol (tương thích ngược): python main.py --symbol XAUUSDm   (mặc định)
Chạy nhiều symbol cùng lúc:        python main.py --symbols EURUSDm,GBPUSDm
Mỗi symbol dùng config RIÊNG trong config.py (magic_number riêng), risk/journal
độc lập theo symbol — chỉ dùng CHUNG 1 kết nối MT5 (global) và 1 vòng lặp tuần tự.
Mặc định dry_run trong config.py — kiểm tra trước khi chạy tiền thật.
"""
import time
import logging
import argparse
from dataclasses import dataclass
from datetime import datetime, date, timedelta
import pandas as pd
from config import get_config, TradingConfig
from mt5_client import MT5Client
from strategy import analyze
from risk import RiskGuard, PositionState, manage_tick, manage_step
from journal import TradeJournal
from notifier import TelegramNotifier
from news import in_news_blackout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("main")

# Phút mỗi nến theo timeframe — dùng tính hạn (expiry) cho lệnh LIMIT (#4).
TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


@dataclass
class SymbolRunner:
    """Trạng thái độc lập cho 1 symbol khi nhiều symbol chạy chung 1 vòng lặp."""
    cfg: TradingConfig
    client: MT5Client
    journal: TradeJournal
    guard: RiskGuard
    notifier: TelegramNotifier
    symbol_info: dict
    # Trạng thái chống re-entry / cooldown (khớp backtest.py để live == backtest)
    last_ltf_bar: pd.Timestamp | None = None
    last_sweep_level: float | None = None
    bars_since_exit: int = 10 ** 9
    trades_today: int = 0
    trade_day: date | None = None
    had_open: bool = False
    # Throttle log cảnh báo daily-loss (KHÔNG dùng để sleep/chặn — xem process_symbol)
    daily_loss_warn_until: datetime | None = None


def in_session(now: datetime, cfg) -> bool:
    if not cfg.use_session_filter:
        return True
    t = now.strftime("%H:%M")
    return any(start <= t <= end for start, end in cfg.sessions)


def pre_weekend_guard(now: datetime, cfg) -> bool:
    """#11: True nếu đang trong vùng cấm mở lệnh MỚI trước khi thị trường đóng cửa cuối
    tuần (Thứ Sáu) — KHÔNG ảnh hưởng quản lý lệnh đang mở (chạy mọi lúc, xem main loop).
    0 = tắt (mặc định, dùng cho vàng — vàng vẫn chạy T2-T6 bình thường)."""
    if cfg.weekend_guard_hours <= 0 or now.weekday() != 4 or not cfg.sessions:
        return False
    end_h, end_m = (int(x) for x in cfg.sessions[-1][1].split(":"))
    session_end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    hours_to_close = (session_end - now).total_seconds() / 3600
    return hours_to_close <= cfg.weekend_guard_hours


def manage_open_positions(client: MT5Client, cfg, journal: TradeJournal, symbol_info: dict):
    """Breakeven + partial close cho các vị thế của bot — dùng CHUNG engine manage_tick
    với backtest/paper để không lệch nhau.

    #1 FIX: KHÔNG dựa vào comment "partial-done" (MT5 không hề gán comment này cho vị thế
    còn lại sau partial → guard cũ luôn True → partial bắn lại mỗi vòng, băm nát runner).
    Thay vào đó suy ra `partial_done` từ VOLUME hiện tại < lot GỐC ghi trong nhật ký
    (bền vững qua restart). `be_done` suy từ SL đã ở/vượt entry hay chưa."""
    cs = symbol_info["contract_size"]
    tick = client.get_tick()
    for pos in client.open_positions():
        is_buy = pos.type == 0
        entry, sl = pos.price_open, pos.sl
        price = tick.bid if is_buy else tick.ask

        rec = journal.get(pos.ticket)
        original_sl = float(rec["sl"]) if rec and rec.get("sl") not in (None, "") else sl
        original_lot = float(rec["lot"]) if rec and rec.get("lot") not in (None, "") else pos.volume
        if not original_sl or abs(entry - original_sl) <= 0:
            continue

        state = PositionState(
            direction="buy" if is_buy else "sell", entry=entry, sl=sl,
            original_sl=original_sl, tp=pos.tp, lot=pos.volume, cs=cs,
            partial_done=pos.volume < original_lot - 1e-9,
            be_done=(is_buy and sl >= entry) or (not is_buy and sl <= entry),
        )
        actions = manage_tick(state, price, cfg)

        if actions["partial_lot"]:
            lot_close = actions["partial_lot"]
            if lot_close >= 0.01 and pos.volume - lot_close >= 0.01:
                client.close_partial(pos, lot_close)
        if actions["move_sl_to"] is not None:
            client.modify_sl(pos, actions["move_sl_to"])


def log_open_positions(client: MT5Client, journal: TradeJournal):
    """In toàn bộ lệnh đang mở/chờ của bot mỗi vòng lặp, để dễ tracking khi chạy.
    Gồm cả lệnh PAPER (dry_run) từ nhật ký — vì dry_run không tạo position thật trên MT5."""
    positions = client.open_positions()
    pending = client.pending_orders()
    paper = [r for r in journal.open_records() if r["mode"] == "paper"]
    if not positions and not pending and not paper:
        log.info("📊 Không có lệnh nào đang mở/chờ.")
        return
    for p in positions:
        direction = "BUY" if p.type == 0 else "SELL"
        log.info(f"📊 [MỞ] #{p.ticket} {direction} {p.volume} lot @ {p.price_open} | "
                 f"SL {p.sl} | TP {p.tp} | lãi/lỗ {p.profit:.2f}")
    for o in pending:
        direction = "BUY" if o.type == 2 else "SELL"   # 2 = ORDER_TYPE_BUY_LIMIT (loại duy nhất bot đặt)
        log.info(f"📊 [CHỜ] #{o.ticket} {direction} {o.volume_current} lot @ {o.price_open} | "
                 f"SL {o.sl} | TP {o.tp}")
    for r in paper:
        log.info(f"📊 [PAPER] #{r['ticket']} {r['direction'].upper()} {r['lot']} lot @ {r['entry']} | "
                 f"SL {r['sl']} | TP {r['tp']} | R:R {r['rr']}")


def reconcile_journal(client: MT5Client, journal: TradeJournal, notifier: TelegramNotifier):
    """Đối chiếu các lệnh THẬT đang mở trong nhật ký với MT5.
    - Còn là lệnh chờ (chưa khớp) → để yên.
    - Đã khớp rồi đóng → tra lịch sử ghi kết quả (TP=success / SL=failed).
    - #4: LIMIT hết hạn mà KHÔNG khớp (không thành position, không còn chờ) → 'cancelled'."""
    open_now = {str(p.ticket) for p in client.open_positions()}
    pending_now = {str(o.ticket) for o in client.pending_orders()}
    for r in journal.open_records():
        if r["mode"] != "live" or r["ticket"] in open_now or r["ticket"] in pending_now:
            continue
        info = client.position_close_info(int(r["ticket"]))
        if info:
            result, close_price, profit = info
            journal.record_close(r["ticket"], result, close_price, profit)
            notifier.notify_closed(r["symbol"], r, result, profit)
        else:
            # Không thành position và không còn là lệnh chờ → limit hết hạn, chưa khớp.
            journal.record_close(r["ticket"], "cancelled", None)


def resolve_paper_trades(journal: TradeJournal, ltf_df: pd.DataFrame, cfg, symbol_info: dict):
    """Xử lý kết quả cho lệnh PAPER (dry_run) bằng CHÍNH engine manage_step của backtest,
    nên paper áp dụng BE + partial y hệt backtest (không còn mô hình quản lý thứ 3 lệch).
    Kết quả ghi là PnL RÒNG gộp (partial + lần đóng cuối): net>0 → success, ~0 → breakeven."""
    closed = ltf_df.iloc[:-1]   # bỏ nến đang chạy
    cs = symbol_info["contract_size"]
    for r in journal.open_records():
        if r["mode"] != "paper":
            continue
        bars = closed[closed.index > pd.to_datetime(r["created"])]
        if bars.empty:
            continue
        is_buy = r["direction"] == "buy"
        level = float(r["entry"])

        # #4: mô phỏng KHỚP LIMIT y hệt backtest — chỉ vào lệnh khi giá hồi về biên vùng
        # trong cửa sổ entry_expiry_bars; quá hạn không chạm → huỷ ('cancelled').
        window = bars.iloc[:cfg.entry_expiry_bars]
        fill_ts = None
        for ts, b in window.iterrows():
            if (is_buy and b["low"] <= level) or (not is_buy and b["high"] >= level):
                fill_ts = ts
                break
        if fill_ts is None:
            if len(bars) >= cfg.entry_expiry_bars:   # đủ thời gian mà không khớp
                journal.record_close(r["ticket"], "cancelled", None,
                                     closed=window.index[-1].to_pydatetime())
            continue

        state = PositionState(
            direction=r["direction"], entry=level, sl=float(r["sl"]),
            original_sl=float(r["sl"]), tp=float(r["tp"]), lot=float(r["lot"]), cs=cs,
            entry_time=fill_ts, rr=float(r["rr"]),
        )
        net = 0.0
        last_exit = None
        done = False
        exit_ts = None
        for ts, b in bars[bars.index >= fill_ts].iterrows():   # gồm cả nến khớp
            delta, rows, done = manage_step(state, b["high"], b["low"], b["close"], ts, cfg)
            net += delta
            if rows:
                last_exit = rows[-1]["exit"]
            if done:
                exit_ts = ts
                break
        if done:
            result = "success" if net > 0.01 else ("breakeven" if abs(net) <= 0.01 else "failed")
            journal.record_close(r["ticket"], result, last_exit, profit=round(net, 2),
                                 closed=exit_ts.to_pydatetime())


def build_runner(name: str) -> SymbolRunner | None:
    """Kết nối MT5 + khởi tạo toàn bộ state cho 1 symbol. Trả về None (KHÔNG raise)
    nếu riêng symbol này không chọn được trên broker — để các symbol khác trong
    cùng lệnh vẫn chạy được (quyết định đã chốt trong spec)."""
    cfg = get_config(name)
    client = MT5Client(cfg.symbol, cfg.magic_number, cfg.deviation)
    if not client.connect():
        log.error(f"⛔ Bỏ qua {cfg.symbol} — không kết nối/chọn được trên broker.")
        return None
    symbol_info = client.get_symbol_info()
    guard = RiskGuard(cfg.max_daily_loss_pct, cfg.portfolio_heat_pct)
    journal = TradeJournal(cfg.symbol)
    notifier = TelegramNotifier.from_config(cfg)
    log.info(f"🚀 {cfg.symbol} sẵn sàng | HTF {cfg.htf} → LTF {cfg.ltf} | "
             f"risk {cfg.risk_per_trade_pct}%/lệnh | dry_run={cfg.dry_run}")
    log.info(f"📁 [{cfg.symbol}] Nhật ký: {journal.path.resolve()} | Telegram: "
             f"{'bật' if notifier.enabled else 'tắt'}")
    log_open_positions(client, journal)
    return SymbolRunner(cfg=cfg, client=client, journal=journal, guard=guard,
                         notifier=notifier, symbol_info=symbol_info)


def process_symbol(runner: SymbolRunner) -> None:
    """Một lượt xử lý cho 1 symbol: quản lý lệnh mở → các cổng chặn (daily-loss/
    session/weekend/tin tức) → tìm tín hiệu mới nếu có nến LTF mới đóng. KHÔNG tự
    sleep — vòng lặp ngoài (main()) sleep 1 lần sau khi xử lý xong MỌI symbol
    trong tick, để 1 symbol không làm nghẽn nhịp của symbol khác."""
    cfg, client, journal = runner.cfg, runner.client, runner.journal
    guard, notifier, symbol_info = runner.guard, runner.notifier, runner.symbol_info

    now = client.server_time()   # giờ SERVER, không dùng datetime.now()
    balance = client.get_balance()
    guard.update_day(now.date(), balance)

    # Quản lý lệnh mở + đối chiếu nhật ký chạy MỖI TICK, kể cả ngoài session hay
    # đang bị daily-loss lock (xem ghi chú THAY ĐỔI HÀNH VI ngay dưới).
    manage_open_positions(client, cfg, journal, symbol_info)
    reconcile_journal(client, journal, notifier)

    if guard.daily_loss_exceeded(balance):
        # THAY ĐỔI HÀNH VI có chủ đích so với bản 1-symbol-1-process cũ (vốn
        # `time.sleep(300); continue` chặn CẢ manage_open_positions/reconcile_journal
        # trong 5 phút — main.py:216-219 bản gốc). Trong vòng lặp multi-symbol dùng
        # chung, sleep ở đây sẽ chặn LUÔN các symbol khác nên KHÔNG được sleep.
        # Quản lý lệnh vẫn chạy đều mỗi tick (đã chạy ở trên); chỉ throttle LOG
        # cảnh báo còn ~300s/lần để không spam.
        if runner.daily_loss_warn_until is None or now >= runner.daily_loss_warn_until:
            log.warning(f"⛔ [{cfg.symbol}] Chạm giới hạn lỗ ngày — tạm dừng tìm tín hiệu đến ngày mai.")
            runner.daily_loss_warn_until = now + timedelta(seconds=300)
        return

    if not in_session(now, cfg):
        return
    if pre_weekend_guard(now, cfg):
        return
    # #7 — Cấm vào lệnh quanh tin mạnh (news_filter_enabled=False mặc định).
    if in_news_blackout(now, cfg):
        return

    ltf_df = client.get_rates(cfg.ltf, cfg.ltf_bars)
    current_bar = ltf_df.index[-1]

    # Chỉ phân tích khi có nến LTF mới đóng (dùng nến đã đóng, bỏ nến đang chạy)
    if current_bar == runner.last_ltf_bar:
        return
    runner.last_ltf_bar = current_bar

    # Xử lý kết quả lệnh paper (dry_run) trên các nến vừa đóng
    resolve_paper_trades(journal, ltf_df, cfg, symbol_info)

    # ── Cập nhật trạng thái theo nến mới (cooldown, đếm lệnh/ngày) ──
    n_open = len(client.open_positions())
    if runner.had_open and n_open == 0:      # lệnh vừa đóng → bắt đầu cooldown
        runner.bars_since_exit = 0
    runner.had_open = n_open > 0
    runner.bars_since_exit += 1
    if now.date() != runner.trade_day:
        runner.trade_day = now.date()
        runner.trades_today = 0

    # #4: đang có lệnh mở HOẶC lệnh LIMIT chờ khớp → không đặt thêm.
    n_pending = len(client.pending_orders())
    if n_open >= cfg.max_open_positions or n_pending > 0:
        return
    # Cooldown sau lệnh + trần lệnh/ngày (khớp backtest.py)
    if runner.bars_since_exit < cfg.cooldown_bars or runner.trades_today >= cfg.max_trades_per_day:
        return

    htf_df = client.get_rates(cfg.htf, cfg.htf_bars)
    # Bỏ nến đang chạy để tránh repaint
    plan = analyze(htf_df.iloc[:-1], ltf_df.iloc[:-1], cfg, balance, symbol_info)

    if not plan:
        return

    # Chống re-entry: mỗi cú sweep chỉ giao dịch 1 lần
    if cfg.one_trade_per_sweep and plan.sweep_level is not None \
            and plan.sweep_level == runner.last_sweep_level:
        log.info(f"⏭️  [{cfg.symbol}] Bỏ qua — đã giao dịch sweep {plan.sweep_level} rồi.")
        return

    open_risk_pct = n_open * cfg.risk_per_trade_pct
    if guard.heat_exceeded(open_risk_pct, cfg.risk_per_trade_pct):
        log.warning(f"⛔ [{cfg.symbol}] Vượt portfolio heat cap — bỏ qua tín hiệu.")
        return

    log.info(f"🎯 [{cfg.symbol}] TÍN HIỆU: {plan.direction.upper()} @ {plan.entry} | "
             f"SL {plan.sl} | TP {plan.tp} | lot {plan.lot} | "
             f"R:R {plan.rr} | risk ${plan.risk_amount} | {plan.reason}")
    if not cfg.dry_run:
        notifier.notify_signal(cfg.symbol, plan)
    if cfg.dry_run:
        log.info(f"[{cfg.symbol}] (dry_run — không đặt lệnh thật)")
        journal.record_open(f"paper-{int(now.timestamp())}", plan.direction,
                            plan.entry, plan.sl, plan.tp, plan.lot, plan.rr,
                            plan.risk_amount, plan.reason, mode="paper", created=now)
    else:
        # #4: đặt LIMIT nghỉ tại biên vùng, broker tự hết hạn sau
        # entry_expiry_bars nến nếu giá không hồi về khớp.
        expiry = now + timedelta(minutes=TF_MINUTES.get(cfg.ltf, 15)
                                 * cfg.entry_expiry_bars)
        ticket = client.pending_order(plan.direction, plan.lot, plan.entry,
                                      plan.sl, plan.tp, expiry,
                                      comment=f"SMC RR{plan.rr}")
        if ticket:
            # Ticket lệnh chờ == ticket position khi khớp → journal khớp luôn.
            journal.record_open(ticket, plan.direction, plan.entry, plan.sl,
                                plan.tp, plan.lot, plan.rr, plan.risk_amount,
                                plan.reason, mode="live", created=now)
            notifier.notify_opened(cfg.symbol, plan)
    runner.last_sweep_level = plan.sweep_level
    runner.trades_today += 1


def main(cfg):
    client = MT5Client(cfg.symbol, cfg.magic_number, cfg.deviation)
    if not client.connect():
        raise SystemExit("Không kết nối được MT5. Đảm bảo terminal đang chạy và đã đăng nhập.")

    symbol_info = client.get_symbol_info()
    guard = RiskGuard(cfg.max_daily_loss_pct, cfg.portfolio_heat_pct)
    journal = TradeJournal(cfg.symbol)
    notifier = TelegramNotifier.from_config(cfg)
    log.info(f"🚀 Bot khởi động | {cfg.symbol} | HTF {cfg.htf} → LTF {cfg.ltf} | "
             f"risk {cfg.risk_per_trade_pct}%/lệnh | dry_run={cfg.dry_run}")
    log.info(f"📁 Nhật ký: {journal.path.resolve()} | Telegram: "
             f"{'bật' if notifier.enabled else 'tắt'}")
    # Check lệnh đang mở/chờ MỘT LẦN lúc khởi động — không lặp lại mỗi vòng để đỡ tốn tài nguyên.
    log_open_positions(client, journal)

    last_ltf_bar = None
    # Trạng thái chống re-entry (khớp backtest.py để live == backtest)
    last_sweep_level = None
    bars_since_exit = 10 ** 9
    trades_today = 0
    trade_day = None
    had_open = False
    try:
        while True:
            now = client.server_time()   # giờ SERVER, không dùng datetime.now() (lệch múi giờ)
            balance = client.get_balance()
            guard.update_day(now.date(), balance)

            # Quản lý lệnh mở mỗi vòng lặp (kể cả ngoài session)
            manage_open_positions(client, cfg, journal, symbol_info)
            # Đối chiếu kết quả lệnh thật đã đóng vào nhật ký
            reconcile_journal(client, journal, notifier)

            if guard.daily_loss_exceeded(balance):
                log.warning("⛔ Chạm giới hạn lỗ ngày — tạm dừng tìm tín hiệu đến ngày mai.")
                time.sleep(300)
                continue

            if not in_session(now, cfg):
                time.sleep(cfg.poll_seconds)
                continue
            if pre_weekend_guard(now, cfg):
                time.sleep(cfg.poll_seconds)
                continue
            # #7 — Cấm vào lệnh quanh tin mạnh (news_filter_enabled=False mặc định).
            if in_news_blackout(now, cfg):
                time.sleep(cfg.poll_seconds)
                continue

            ltf_df = client.get_rates(cfg.ltf, cfg.ltf_bars)
            current_bar = ltf_df.index[-1]

            # Chỉ phân tích khi có nến LTF mới đóng (dùng nến đã đóng, bỏ nến đang chạy)
            if current_bar == last_ltf_bar:
                time.sleep(cfg.poll_seconds)
                continue
            last_ltf_bar = current_bar

            # Xử lý kết quả lệnh paper (dry_run) trên các nến vừa đóng
            resolve_paper_trades(journal, ltf_df, cfg, symbol_info)

            # ── Cập nhật trạng thái theo nến mới (cooldown, đếm lệnh/ngày) ──
            n_open = len(client.open_positions())
            if had_open and n_open == 0:      # lệnh vừa đóng → bắt đầu cooldown
                bars_since_exit = 0
            had_open = n_open > 0
            bars_since_exit += 1
            if now.date() != trade_day:
                trade_day = now.date()
                trades_today = 0

            # #4: đang có lệnh mở HOẶC lệnh LIMIT chờ khớp → không đặt thêm.
            n_pending = len(client.pending_orders())
            if n_open >= cfg.max_open_positions or n_pending > 0:
                time.sleep(cfg.poll_seconds)
                continue
            # Cooldown sau lệnh + trần lệnh/ngày (khớp backtest.py)
            if bars_since_exit < cfg.cooldown_bars or trades_today >= cfg.max_trades_per_day:
                time.sleep(cfg.poll_seconds)
                continue

            htf_df = client.get_rates(cfg.htf, cfg.htf_bars)
            # Bỏ nến đang chạy để tránh repaint
            plan = analyze(htf_df.iloc[:-1], ltf_df.iloc[:-1], cfg, balance, symbol_info)

            if plan:
                # Chống re-entry: mỗi cú sweep chỉ giao dịch 1 lần
                if cfg.one_trade_per_sweep and plan.sweep_level is not None \
                        and plan.sweep_level == last_sweep_level:
                    log.info(f"⏭️  Bỏ qua — đã giao dịch sweep {plan.sweep_level} rồi.")
                    time.sleep(cfg.poll_seconds)
                    continue
                open_risk_pct = n_open * cfg.risk_per_trade_pct
                if guard.heat_exceeded(open_risk_pct, cfg.risk_per_trade_pct):
                    log.warning("⛔ Vượt portfolio heat cap — bỏ qua tín hiệu.")
                else:
                    log.info(f"🎯 TÍN HIỆU: {plan.direction.upper()} @ {plan.entry} | "
                             f"SL {plan.sl} | TP {plan.tp} | lot {plan.lot} | "
                             f"R:R {plan.rr} | risk ${plan.risk_amount} | {plan.reason}")
                    if not cfg.dry_run:
                        notifier.notify_signal(cfg.symbol, plan)
                    if cfg.dry_run:
                        log.info("(dry_run — không đặt lệnh thật)")
                        journal.record_open(f"paper-{int(now.timestamp())}", plan.direction,
                                            plan.entry, plan.sl, plan.tp, plan.lot, plan.rr,
                                            plan.risk_amount, plan.reason, mode="paper", created=now)
                    else:
                        # #4: đặt LIMIT nghỉ tại biên vùng, broker tự hết hạn sau
                        # entry_expiry_bars nến nếu giá không hồi về khớp.
                        expiry = now + timedelta(minutes=TF_MINUTES.get(cfg.ltf, 15)
                                                 * cfg.entry_expiry_bars)
                        ticket = client.pending_order(plan.direction, plan.lot, plan.entry,
                                                      plan.sl, plan.tp, expiry,
                                                      comment=f"SMC RR{plan.rr}")
                        if ticket:
                            # Ticket lệnh chờ == ticket position khi khớp → journal khớp luôn.
                            journal.record_open(ticket, plan.direction, plan.entry, plan.sl,
                                                plan.tp, plan.lot, plan.rr, plan.risk_amount,
                                                plan.reason, mode="live", created=now)
                            notifier.notify_opened(cfg.symbol, plan)
                    last_sweep_level = plan.sweep_level
                    trades_today += 1

            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        log.info("Dừng bot theo yêu cầu.")
    finally:
        client.shutdown()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="XAUUSDm",
                   help="Symbol/config để chạy live: XAUUSDm | EURUSDm (alias: gold/eurusd).")
    args = p.parse_args()
    main(get_config(args.symbol))
