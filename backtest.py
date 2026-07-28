"""
Backtest engine đơn giản (bar-by-bar, không look-ahead):
- Chạy trên dữ liệu CSV hoặc dữ liệu tải từ MT5.
- Mỗi nến LTF mới: cắt dữ liệu đến nến đó, gọi strategy.analyze() y hệt live.
- Mô phỏng SL/TP, breakeven, partial close.

Dùng:
    python backtest.py --csv-ltf data/xauusd_m15.csv --csv-htf data/xauusd_h4.csv
hoặc để bot tự tải từ MT5 (chạy trên Windows có MT5):
    python backtest.py --from-mt5 --bars 5000
CSV cần cột: time,open,high,low,close (time ISO hoặc epoch giây).
"""
import argparse
import logging
import sys
import pandas as pd

# Console Windows (cp1252) không in được tiếng Việt → ép UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from config import get_config
from strategy import analyze
from risk import RiskGuard, PositionState, manage_step
from news import in_news_blackout

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backtest")

SYMBOL_INFO_DEFAULT = {  # XAUUSD chuẩn
    "contract_size": 100.0, "volume_min": 0.01,
    "volume_step": 0.01, "volume_max": 100.0, "point": 0.01, "digits": 2,
}

# Thông số hợp đồng tĩnh cho đường CSV (offline). Đường --from-mt5 lấy trực tiếp
# từ broker nên không dùng bảng này. contract_size sai sẽ làm SAI CAGR/DD (PnL tuyệt đối),
# dù PF/winrate là tỉ lệ nên không đổi.
SYMBOL_INFO = {
    "XAUUSDm": SYMBOL_INFO_DEFAULT,
    "EURUSDm": {"contract_size": 100_000.0, "volume_min": 0.01, "volume_step": 0.01,
                "volume_max": 200.0, "point": 1e-05, "digits": 5},
}


def in_session(ts, cfg) -> bool:
    """Lọc phiên — GIỐNG main.py để backtest khớp live (giờ server MT5)."""
    if not cfg.use_session_filter:
        return True
    t = ts.strftime("%H:%M")
    return any(start <= t <= end for start, end in cfg.sessions)


def pre_weekend_guard(now, cfg) -> bool:
    """#11 — GIỐNG main.py: cấm mở lệnh MỚI trong N giờ cuối trước khi đóng cửa cuối tuần
    (Thứ Sáu). 0 = tắt (mặc định/vàng)."""
    if cfg.weekend_guard_hours <= 0 or now.weekday() != 4 or not cfg.sessions:
        return False
    end_h, end_m = (int(x) for x in cfg.sessions[-1][1].split(":"))
    session_end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    hours_to_close = (session_end - now).total_seconds() / 3600
    return hours_to_close <= cfg.weekend_guard_hours


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
    """Tải dữ liệu theo KHOẢNG NGÀY (mặc định 2 năm gần nhất) thay vì số nến cố định.
    Dùng copy_rates_range để phủ đủ 2 năm cho cả M15 lẫn H4."""
    from mt5_client import MT5Client, TIMEFRAME_MAP, mt5
    from datetime import datetime, timedelta
    client = MT5Client(cfg.symbol, cfg.magic_number, cfg.deviation)
    if not client.connect():
        raise SystemExit("Không kết nối được MT5.")
    end = datetime.now()
    start = end - timedelta(days=int(365 * years) + 30)  # đệm 30 ngày cho warmup

    def fetch(tf_name):
        rates = mt5.copy_rates_range(cfg.symbol, TIMEFRAME_MAP[tf_name], start, end)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Không lấy được {tf_name}: {mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        return df[["open", "high", "low", "close"]]

    htf, ltf = fetch(cfg.htf), fetch(cfg.ltf)
    symbol_info = client.get_symbol_info()   # contract_size/point/digits thật của symbol
    client.shutdown()
    log.info(f"Tải {len(ltf)} nến {cfg.ltf} & {len(htf)} nến {cfg.htf} "
             f"({start.date()} → {end.date()}) | symbol_info={symbol_info}")
    return htf, ltf, symbol_info


def run_backtest(htf: pd.DataFrame, ltf: pd.DataFrame, cfg, initial_balance: float = 10_000.0,
                 symbol_info: dict = None, quiet: bool = False, apply_costs: bool = True):
    symbol_info = symbol_info or SYMBOL_INFO_DEFAULT
    balance = initial_balance
    equity_curve = []
    trades = []
    open_trade = None
    pending = None               # #4: lệnh LIMIT đang chờ khớp tại biên vùng
    guard = RiskGuard(cfg.max_daily_loss_pct, cfg.portfolio_heat_pct)

    # Trạng thái chống re-entry (nằm ở vòng lặp, giữ strategy.analyze thuần)
    last_sweep_level = None      # sweep của lệnh gần nhất — chống bắn trùng
    last_exit_i = -10**9         # index nến LTF lúc đóng lệnh gần nhất — cooldown
    trades_today = 0
    trade_day = None

    warmup = max(cfg.sweep_lookback, cfg.ob_max_age_bars) + 20

    for i in range(warmup, len(ltf)):
        now = ltf.index[i]
        bar = ltf.iloc[i]
        guard.update_day(now.date(), balance)

        # ── Quản lý lệnh đang mở (engine DÙNG CHUNG với live/paper) ──
        if open_trade:
            delta, rows, closed = manage_step(open_trade, bar["high"], bar["low"],
                                              bar["close"], now, cfg, apply_costs)
            balance += delta
            trades.extend(rows)
            if closed:
                open_trade = None
                last_exit_i = i

        # ── #4: khớp/huỷ lệnh LIMIT đang chờ tại biên vùng ──
        elif pending is not None:
            is_buy = pending["direction"] == "buy"
            filled = bar["low"] <= pending["level"] if is_buy else bar["high"] >= pending["level"]
            if filled:
                open_trade = PositionState(
                    direction=pending["direction"], entry=pending["level"], sl=pending["sl"],
                    original_sl=pending["sl"], tp=pending["tp"], lot=pending["lot"],
                    cs=symbol_info["contract_size"], entry_time=now,
                    reason=pending["reason"], rr=pending["rr"])
                pending = None
                # Cùng nến khớp: có thể chạm SL/TP ngay (engine bảo thủ tính SL trước).
                delta, rows, closed = manage_step(open_trade, bar["high"], bar["low"],
                                                  bar["close"], now, cfg, apply_costs)
                balance += delta
                trades.extend(rows)
                if closed:
                    open_trade = None
                    last_exit_i = i
            elif i >= pending["expiry_i"]:
                pending = None   # hết hạn → huỷ limit (giá không hồi về)

        equity_curve.append({"time": now, "balance": balance})

        # ── Tìm tín hiệu mới ────────────────────────────
        if open_trade or pending is not None or guard.daily_loss_exceeded(balance):
            continue

        # Reset đếm lệnh theo ngày
        if now.date() != trade_day:
            trade_day = now.date()
            trades_today = 0

        # Bộ lọc cấp vòng lặp (khớp live): phiên, tin tức, cooldown, trần lệnh/ngày
        if not in_session(now, cfg):
            continue
        if pre_weekend_guard(now, cfg):
            continue
        if in_news_blackout(now, cfg):   # #7 — news_filter_enabled=False mặc định (backtest bất biến)
            continue
        if i - last_exit_i < cfg.cooldown_bars:
            continue
        if trades_today >= cfg.max_trades_per_day:
            continue

        # Cửa sổ trượt khớp live (chỉ thấy cfg.ltf_bars/htf_bars nến gần nhất)
        ltf_slice = ltf.iloc[max(0, i + 1 - cfg.ltf_bars): i + 1]
        htf_slice = htf[htf.index <= now].iloc[-cfg.htf_bars:]
        if len(htf_slice) < 50:
            continue

        plan = analyze(htf_slice, ltf_slice, cfg, balance, symbol_info)
        if plan:
            # Chống re-entry: mỗi cú sweep chỉ giao dịch 1 lần
            if cfg.one_trade_per_sweep and plan.sweep_level is not None \
                    and plan.sweep_level == last_sweep_level:
                continue
            last_sweep_level = plan.sweep_level
            trades_today += 1
            # #4: đặt LIMIT chờ tại biên vùng thay vì mở market ngay.
            pending = {
                "direction": plan.direction, "level": plan.entry, "sl": plan.sl,
                "tp": plan.tp, "lot": plan.lot, "rr": plan.rr, "reason": plan.reason,
                "expiry_i": i + cfg.entry_expiry_bars,
            }
            if not quiet:
                log.info(f"{now} ⏳ LIMIT {plan.direction.upper()} @ {plan.entry} SL {plan.sl} "
                         f"TP {plan.tp} lot {plan.lot} RR {plan.rr} | {plan.reason}")

    metrics = _report(trades, equity_curve, initial_balance, balance, quiet=quiet)
    return trades, equity_curve, metrics


def _report(trades, equity, start_bal, end_bal, quiet: bool = False):
    tdf = pd.DataFrame(trades)
    # Winrate THỰC: gộp partial + lần đóng cuối thành 1 "lệnh logic", tính PnL ròng.
    # (Nếu chỉ đếm dòng SL/BE thì lệnh chốt lời một phần rồi về BE bị tính là thua.)
    if not tdf.empty:
        g = tdf.groupby(["entry_time", "entry", "direction"]).agg(net=("pnl", "sum")).reset_index()
        n = len(g)
        wins = g[g.net > 0.01]
        losses = g[g.net < -0.01]
        net_wr = len(wins) / n * 100 if n else 0
        gross_profit = wins.net.sum()
        gross_loss = -losses.net.sum()
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        avg_win = wins.net.mean() if len(wins) else 0
        avg_loss = losses.net.mean() if len(losses) else 0
    else:
        n = net_wr = pf = avg_win = avg_loss = 0

    total_pnl = sum(t["pnl"] for t in trades)
    total_cost = sum(t.get("cost", 0.0) for t in trades)
    peak, max_dd = start_bal, 0
    for pt in equity:
        peak = max(peak, pt["balance"])
        max_dd = max(max_dd, (peak - pt["balance"]) / peak * 100)

    # CAGR từ khoảng thời gian thực của equity
    ret_pct = (end_bal / start_bal - 1) * 100
    cagr = 0.0
    if equity:
        days = (pd.to_datetime(equity[-1]["time"]) - pd.to_datetime(equity[0]["time"])).days
        yrs = days / 365.25
        if yrs > 0:
            cagr = ((end_bal / start_bal) ** (1 / yrs) - 1) * 100

    metrics = {"n": n, "net_wr": net_wr, "pf": pf, "total_pnl": total_pnl,
               "ret_pct": ret_pct, "cagr": cagr, "max_dd": max_dd,
               "avg_win": avg_win, "avg_loss": avg_loss, "total_cost": total_cost}
    if quiet:
        return metrics

    print("\n" + "=" * 50)
    print("KẾT QUẢ BACKTEST")
    print("=" * 50)
    print(f"Số lệnh (logic)        : {n}")
    print(f"Winrate ròng           : {net_wr:.1f}%   (PnL ròng > 0 mỗi lệnh)")
    print(f"Profit factor          : {pf:.2f}")
    print(f"Tổng PnL (ròng)        : {total_pnl:+,.2f} USD")
    print(f"Tổng chi phí           : -{total_cost:,.2f} USD  (spread+swap+commission)")
    print(f"Balance                : {start_bal:,.0f} → {end_bal:,.2f}  ({ret_pct:+.1f}%)")
    print(f"CAGR (lợi nhuận/năm)   : {cagr:+.1f}%")
    print(f"Max drawdown           : {max_dd:.2f}%")
    print(f"Avg win / Avg loss     : {avg_win:,.2f} / {avg_loss:,.2f}")
    print("=" * 50)

    tdf.to_csv("backtest_trades.csv", index=False)
    pd.DataFrame(equity).to_csv("backtest_equity.csv", index=False)
    print("Đã lưu backtest_trades.csv & backtest_equity.csv")
    return metrics


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--csv-ltf")
    p.add_argument("--csv-htf")
    p.add_argument("--from-mt5", action="store_true")
    p.add_argument("--symbol", default="XAUUSDm",
                   help="Symbol/config để backtest: XAUUSDm | EURUSDm (alias: gold/eurusd). "
                        "Mặc định = vàng.")
    p.add_argument("--years", type=float, default=2.0,
                   help="Số năm dữ liệu gần nhất tải từ MT5 (mặc định 2 năm).")
    p.add_argument("--balance", type=float, default=10_000)
    p.add_argument("--no-costs", action="store_true",
                   help="Tắt mô phỏng spread/swap/commission (xem PnL gộp, gross).")
    args = p.parse_args()

    # Chọn config RIÊNG của symbol (vàng và EURUSD hoàn toàn độc lập).
    cfg = get_config(args.symbol)
    log.info(f"Config {cfg.symbol}: digits={cfg.price_digits} eq_tol={cfg.eq_tolerance} "
             f"fvg_min={cfg.fvg_min_size_points} sl_buf={cfg.sl_buffer_points} "
             f"min_sl={cfg.min_sl_distance_points} risk={cfg.risk_per_trade_pct}%")

    symbol_info = None
    if args.from_mt5:
        htf, ltf, symbol_info = load_from_mt5(cfg, args.years)
    elif args.csv_ltf and args.csv_htf:
        ltf, htf = load_csv(args.csv_ltf), load_csv(args.csv_htf)
        symbol_info = SYMBOL_INFO.get(cfg.symbol)   # đúng contract_size cho CSV offline
    else:
        raise SystemExit("Cần --from-mt5 hoặc cả --csv-ltf và --csv-htf.")

    run_backtest(htf, ltf, cfg, args.balance, symbol_info, apply_costs=not args.no_costs)
