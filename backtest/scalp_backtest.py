"""
Backtest cho luồng SCALPING M5 độc lập (bar-by-bar, single-timeframe, không HTF).

Dùng:
    python -m backtest.scalp_backtest --from-mt5 --symbol XAUUSDm --years 2 --balance 10000
    python -m backtest.scalp_backtest --csv-m5 data/xauusd_m5.csv --symbol XAUUSDm

CSV cần cột: time,open,high,low,close (time ISO hoặc epoch giây).
"""
import argparse
import logging
import sys
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from config.scalp_config import get_scalp_config
from strategy.scalp_strategy import analyze_scalp
from risk.risk import PositionState, RiskGuard, position_pnl, trade_cost

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scalp_backtest")

SYMBOL_INFO = {
    "XAUUSDm": {"contract_size": 100.0, "volume_min": 0.01, "volume_step": 0.01,
                "volume_max": 100.0, "point": 0.01, "digits": 2},
    "EURUSDm": {"contract_size": 100_000.0, "volume_min": 0.01, "volume_step": 0.01,
                "volume_max": 200.0, "point": 1e-05, "digits": 5},
    "GBPUSDm": {"contract_size": 100_000.0, "volume_min": 0.01, "volume_step": 0.01,
                "volume_max": 200.0, "point": 1e-05, "digits": 5},
}


def in_session(ts, cfg) -> bool:
    if not cfg.use_session_filter:
        return True
    t = ts.strftime("%H:%M")
    return any(start <= t <= end for start, end in cfg.sessions)


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    if df["time"].dtype != "O":
        df["time"] = pd.to_datetime(df["time"], unit="s")
    else:
        df["time"] = pd.to_datetime(df["time"])
    df.set_index("time", inplace=True)
    return df[["open", "high", "low", "close"]]


def load_from_mt5(cfg, years: float = 2.0):
    from execution.mt5_client import MT5Client, TIMEFRAME_MAP, mt5
    from datetime import datetime, timedelta
    client = MT5Client(cfg.symbol, cfg.magic_number, cfg.deviation)
    if not client.connect():
        raise SystemExit("Không kết nối được MT5.")
    end = datetime.now()
    start = end - timedelta(days=int(365 * years) + 10)
    rates = mt5.copy_rates_range(cfg.symbol, TIMEFRAME_MAP[cfg.timeframe], start, end)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Không lấy được {cfg.timeframe}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    symbol_info = client.get_symbol_info()
    client.shutdown()
    log.info(f"Tải {len(df)} nến {cfg.timeframe} ({start.date()} → {end.date()}) | "
             f"symbol_info={symbol_info}")
    return df[["open", "high", "low", "close"]], symbol_info


def _manage_scalp_step(state: PositionState, high, low, now, cfg, apply_costs=True):
    """SL/TP-hit ONLY — KHÔNG BE/partial. Không dùng risk.manage_step (nó đọc
    cfg.partial_close_at_rr/move_sl_to_be_at_rr mà ScalpConfig không có)."""
    is_buy = state.direction == "buy"
    hit_sl = low <= state.sl if is_buy else high >= state.sl
    hit_tp = high >= state.tp if is_buy else low <= state.tp
    if not (hit_sl or hit_tp):
        return 0.0, None, False
    exit_price = state.sl if hit_sl else state.tp
    result = "SL" if hit_sl else "TP"
    cost = trade_cost(state, now, state.lot, cfg) if apply_costs else 0.0
    pnl = position_pnl(state, exit_price) - cost
    row = {"direction": state.direction, "entry": state.entry, "sl": state.sl, "tp": state.tp,
          "lot": state.lot, "rr": state.rr, "entry_time": state.entry_time,
          "reason": state.reason, "exit": exit_price, "exit_time": now, "pnl": pnl,
          "cost": cost, "result": result}
    return pnl, row, True


def run_backtest(m5: pd.DataFrame, cfg, initial_balance: float = 10_000.0,
                 symbol_info: dict = None, quiet: bool = False, apply_costs: bool = True):
    symbol_info = symbol_info or SYMBOL_INFO["XAUUSDm"]
    balance = initial_balance
    equity_curve = []
    trades = []
    open_trade = None
    last_exit_i = -10 ** 9
    trades_today = 0
    trade_day = None
    guard = RiskGuard(cfg.max_daily_loss_pct, cfg.portfolio_heat_pct)
    warmup = max(cfg.ema_slow, cfg.atr_period, cfg.rsi_period) + 1

    for i in range(warmup, len(m5)):
        now = m5.index[i]
        bar = m5.iloc[i]
        guard.update_day(now.date(), balance)

        if open_trade:
            delta, row, closed = _manage_scalp_step(open_trade, bar["high"], bar["low"],
                                                     now, cfg, apply_costs)
            balance += delta
            if row:
                trades.append(row)
            if closed:
                open_trade = None
                last_exit_i = i

        equity_curve.append({"time": now, "balance": balance})

        if open_trade or guard.daily_loss_exceeded(balance):
            continue
        if now.date() != trade_day:
            trade_day = now.date()
            trades_today = 0
        if not in_session(now, cfg):
            continue
        if i - last_exit_i < cfg.cooldown_bars:
            continue
        if trades_today >= cfg.max_trades_per_day:
            continue

        m5_slice = m5.iloc[max(0, i + 1 - cfg.bars): i + 1]
        plan = analyze_scalp(m5_slice, cfg, balance, symbol_info)
        if plan:
            trades_today += 1
            open_trade = PositionState(
                direction=plan.direction, entry=plan.entry, sl=plan.sl, original_sl=plan.sl,
                tp=plan.tp, lot=plan.lot, cs=symbol_info["contract_size"],
                entry_time=now, reason=plan.reason, rr=plan.rr,
            )
            if not quiet:
                log.info(f"{now} 🎯 {plan.direction.upper()} @ {plan.entry} SL {plan.sl} "
                         f"TP {plan.tp} lot {plan.lot} RR {plan.rr} | {plan.reason}")

    metrics = _report(trades, equity_curve, initial_balance, balance, quiet=quiet)
    return trades, equity_curve, metrics


def _report(trades, equity, start_bal, end_bal, quiet: bool = False):
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0.01]
    losses = [t for t in trades if t["pnl"] < -0.01]
    wr = len(wins) / n * 100 if n else 0
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    total_pnl = sum(t["pnl"] for t in trades)
    total_cost = sum(t.get("cost", 0.0) for t in trades)
    peak, max_dd = start_bal, 0
    for pt in equity:
        peak = max(peak, pt["balance"])
        max_dd = max(max_dd, (peak - pt["balance"]) / peak * 100)
    ret_pct = (end_bal / start_bal - 1) * 100
    cagr = 0.0
    if equity:
        days = (pd.to_datetime(equity[-1]["time"]) - pd.to_datetime(equity[0]["time"])).days
        yrs = days / 365.25
        if yrs > 0:
            cagr = ((end_bal / start_bal) ** (1 / yrs) - 1) * 100
    metrics = {"n": n, "wr": wr, "pf": pf, "total_pnl": total_pnl, "ret_pct": ret_pct,
              "cagr": cagr, "max_dd": max_dd, "total_cost": total_cost}
    if quiet:
        return metrics
    print("\n" + "=" * 50)
    print("KẾT QUẢ SCALP BACKTEST (M5)")
    print("=" * 50)
    print(f"Số lệnh                : {n}")
    print(f"Winrate                : {wr:.1f}%")
    print(f"Profit factor          : {pf:.2f}")
    print(f"Tổng PnL (ròng)        : {total_pnl:+,.2f} USD")
    print(f"Tổng chi phí           : -{total_cost:,.2f} USD")
    print(f"Balance                : {start_bal:,.0f} → {end_bal:,.2f}  ({ret_pct:+.1f}%)")
    print(f"CAGR                   : {cagr:+.1f}%")
    print(f"Max drawdown           : {max_dd:.2f}%")
    print("=" * 50)
    pd.DataFrame(trades).to_csv("scalp_backtest_trades.csv", index=False)
    pd.DataFrame(equity).to_csv("scalp_backtest_equity.csv", index=False)
    print("Đã lưu scalp_backtest_trades.csv & scalp_backtest_equity.csv")
    return metrics


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--csv-m5")
    p.add_argument("--from-mt5", action="store_true")
    p.add_argument("--symbol", default="XAUUSDm")
    p.add_argument("--years", type=float, default=2.0)
    p.add_argument("--balance", type=float, default=10_000)
    p.add_argument("--no-costs", action="store_true")
    args = p.parse_args()

    cfg = get_scalp_config(args.symbol)
    log.info(f"ScalpConfig {cfg.symbol}: EMA{cfg.ema_fast}/{cfg.ema_slow} RSI{cfg.rsi_period} "
             f"ATR{cfg.atr_period} sl_mult={cfg.sl_atr_mult} tp_mult={cfg.tp_atr_mult} "
             f"risk={cfg.risk_per_trade_pct}%")

    symbol_info = None
    if args.from_mt5:
        m5, symbol_info = load_from_mt5(cfg, args.years)
    elif args.csv_m5:
        m5 = load_csv(args.csv_m5)
        symbol_info = SYMBOL_INFO.get(cfg.symbol)
    else:
        raise SystemExit("Cần --from-mt5 hoặc --csv-m5.")

    run_backtest(m5, cfg, args.balance, symbol_info, apply_costs=not args.no_costs)
