"""
Live/demo loop cho luồng SCALPING M5 độc lập — tiến trình RIÊNG với main.py (bot SMC).
Không dùng chung magic_number/journal/risk với bot SMC. Khớp MARKET ngay khi có tín
hiệu, SL/TP đặt cứng ở broker — KHÔNG cần manage_open_positions (không BE/partial).

Chạy 1 symbol:  python scalp_main.py --symbol XAUUSDm
Chạy nhiều:     python scalp_main.py --symbols EURUSDm,GBPUSDm
Mặc định dry_run=True trong scalp_config.py — kiểm tra kỹ trước khi tắt.

LƯU Ý VẬN HÀNH (xem spec, mục "Rủi ro cần xác minh"): chạy song song với main.py nhắm
CÙNG 1 terminal MT5 thường an toàn (mỗi process có kênh IPC riêng) nhưng NÊN xác minh
bằng 1 lượt demo ngắn trước khi tin tưởng ở live — và market_order() ở mt5_client.py
chưa từng được gọi thật trong codebase này trước luồng này.
"""
import time
import logging
import argparse
from dataclasses import dataclass
from datetime import date
import pandas as pd
from scalp_config import get_scalp_config, ScalpConfig
from scalp_strategy import analyze_scalp
from mt5_client import MT5Client
from journal import TradeJournal
from notifier import TelegramNotifier
from risk import PositionState, RiskGuard, position_pnl, trade_cost

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.FileHandler("scalp_bot.log", encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("scalp_main")


def in_session(now, cfg) -> bool:
    if not cfg.use_session_filter:
        return True
    t = now.strftime("%H:%M")
    return any(start <= t <= end for start, end in cfg.sessions)


def reconcile_journal(client: MT5Client, journal: TradeJournal, notifier: TelegramNotifier):
    """Copy từ main.py — logic thuần journal + MT5 history, không phụ thuộc TradingConfig."""
    open_now = {str(p.ticket) for p in client.open_positions()}
    for r in journal.open_records():
        if r["mode"] != "live" or r["ticket"] in open_now:
            continue
        info = client.position_close_info(int(r["ticket"]))
        if info:
            result, close_price, profit = info
            journal.record_close(r["ticket"], result, close_price, profit)
            notifier.notify_closed(r["symbol"], r, result, profit)


def resolve_scalp_paper_trades(journal: TradeJournal, m5_df: pd.DataFrame, cfg, symbol_info: dict):
    """Xử lý kết quả PAPER (dry_run): entry đã biết ngay (market fill), chỉ cần theo
    dõi các nến SAU 'created' để tìm SL/TP-hit — không có logic khớp limit như SMC."""
    closed = m5_df.iloc[:-1]
    cs = symbol_info["contract_size"]
    for r in journal.open_records():
        if r["mode"] != "paper":
            continue
        bars = closed[closed.index > pd.to_datetime(r["created"])]
        if bars.empty:
            continue
        state = PositionState(
            direction=r["direction"], entry=float(r["entry"]), sl=float(r["sl"]),
            original_sl=float(r["sl"]), tp=float(r["tp"]), lot=float(r["lot"]), cs=cs,
            entry_time=pd.to_datetime(r["created"]), rr=float(r["rr"]),
        )
        for ts, b in bars.iterrows():
            is_buy = state.direction == "buy"
            hit_sl = b["low"] <= state.sl if is_buy else b["high"] >= state.sl
            hit_tp = b["high"] >= state.tp if is_buy else b["low"] <= state.tp
            if hit_sl or hit_tp:
                exit_price = state.sl if hit_sl else state.tp
                cost = trade_cost(state, ts, state.lot, cfg)
                pnl = position_pnl(state, exit_price) - cost
                result = "success" if hit_tp else "failed"
                journal.record_close(r["ticket"], result, exit_price, profit=round(pnl, 2),
                                     closed=ts.to_pydatetime())
                break


@dataclass
class ScalpRunner:
    """Trạng thái độc lập cho 1 symbol khi nhiều symbol chạy chung 1 vòng lặp."""
    cfg: ScalpConfig
    client: MT5Client
    journal: TradeJournal
    guard: RiskGuard
    notifier: TelegramNotifier
    symbol_info: dict
    last_m5_bar: pd.Timestamp | None = None
    bars_since_exit: int = 10 ** 9
    trades_today: int = 0
    trade_day: date | None = None
    had_open: bool = False


def build_scalp_runner(name: str) -> ScalpRunner | None:
    cfg = get_scalp_config(name)
    client = MT5Client(cfg.symbol, cfg.magic_number, cfg.deviation)
    if not client.connect():
        log.error(f"⛔ Bỏ qua {cfg.symbol} — không kết nối/chọn được trên broker.")
        return None
    symbol_info = client.get_symbol_info()
    guard = RiskGuard(cfg.max_daily_loss_pct, cfg.portfolio_heat_pct)
    journal = TradeJournal(cfg.symbol, path=f"scalp_trades_{cfg.symbol}.csv")
    notifier = TelegramNotifier.from_config(cfg)
    log.info(f"🚀 [SCALP] {cfg.symbol} sẵn sàng | M5 EMA{cfg.ema_fast}/{cfg.ema_slow} | "
             f"risk {cfg.risk_per_trade_pct}%/lệnh | dry_run={cfg.dry_run}")
    log.info(f"📁 [SCALP {cfg.symbol}] Nhật ký: {journal.path.resolve()}")
    return ScalpRunner(cfg=cfg, client=client, journal=journal, guard=guard,
                       notifier=notifier, symbol_info=symbol_info)


def process_scalp_symbol(runner: ScalpRunner) -> None:
    cfg, client, journal, notifier = runner.cfg, runner.client, runner.journal, runner.notifier
    guard = runner.guard
    now = client.server_time()
    balance = client.get_balance()
    guard.update_day(now.date(), balance)

    reconcile_journal(client, journal, notifier)

    if guard.daily_loss_exceeded(balance):
        return
    if not in_session(now, cfg):
        return

    m5_df = client.get_rates("M5", cfg.bars)
    current_bar = m5_df.index[-1]
    if current_bar == runner.last_m5_bar:
        return
    runner.last_m5_bar = current_bar

    resolve_scalp_paper_trades(journal, m5_df, cfg, runner.symbol_info)

    n_open = len(client.open_positions())
    if runner.had_open and n_open == 0:
        runner.bars_since_exit = 0
    runner.had_open = n_open > 0
    runner.bars_since_exit += 1
    if now.date() != runner.trade_day:
        runner.trade_day = now.date()
        runner.trades_today = 0

    if n_open >= cfg.max_open_positions:
        return
    if runner.bars_since_exit < cfg.cooldown_bars or runner.trades_today >= cfg.max_trades_per_day:
        return

    plan = analyze_scalp(m5_df.iloc[:-1], cfg, balance, runner.symbol_info)
    if not plan:
        return

    log.info(f"🎯 [SCALP {cfg.symbol}] {plan.direction.upper()} @ {plan.entry} | "
             f"SL {plan.sl} | TP {plan.tp} | lot {plan.lot} | R:R {plan.rr} | {plan.reason}")

    if cfg.dry_run:
        log.info(f"[SCALP {cfg.symbol}] (dry_run — không đặt lệnh thật)")
        journal.record_open(f"scalp-paper-{int(now.timestamp())}", plan.direction,
                            plan.entry, plan.sl, plan.tp, plan.lot, plan.rr,
                            plan.risk_amount, plan.reason, mode="paper", created=now)
    else:
        ticket = client.market_order(plan.direction, plan.lot, plan.sl, plan.tp,
                                     comment=f"Scalp RR{plan.rr}")
        if ticket:
            journal.record_open(ticket, plan.direction, plan.entry, plan.sl, plan.tp,
                                plan.lot, plan.rr, plan.risk_amount, plan.reason,
                                mode="live", created=now)
            notifier.notify_opened(cfg.symbol, plan)
    runner.trades_today += 1


def main(symbol_names: list[str]) -> None:
    runners = [r for r in (build_scalp_runner(n) for n in symbol_names) if r is not None]
    if not runners:
        raise SystemExit("Không symbol nào kết nối được — dừng scalp bot.")
    poll_seconds = min(r.cfg.poll_seconds for r in runners)
    log.info(f"▶️  Scalp bot khởi động | {len(runners)} symbol: "
             f"{', '.join(r.cfg.symbol for r in runners)} | poll {poll_seconds}s")
    try:
        while True:
            for runner in runners:
                try:
                    process_scalp_symbol(runner)
                except Exception:
                    log.exception(f"⚠️ Lỗi xử lý {runner.cfg.symbol} — bỏ qua tick này.")
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        log.info("Dừng scalp bot theo yêu cầu.")
    finally:
        for runner in runners:
            runner.client.shutdown()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=None,
                   help="1 symbol: XAUUSDm | EURUSDm | GBPUSDm (alias: gold/eurusd/gbpusd).")
    p.add_argument("--symbols", default=None,
                   help="Danh sách symbol phân tách dấu phẩy — vd EURUSDm,GBPUSDm.")
    args = p.parse_args()
    if args.symbols:
        names = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.symbol:
        names = [args.symbol]
    else:
        names = ["XAUUSDm"]
    main(names)
