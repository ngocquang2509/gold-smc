"""
Chiến lược SCALPING M5 độc lập — 1 khung, không HTF, không sweep/CHoCH/OB/FVG.
EMA trend-pullback + RSI momentum + ATR volatility/SL-TP. Xem
docs/superpowers/specs/2026-07-31-scalp-m5-design.md cho spec đầy đủ.
"""
import pandas as pd
from risk import TradePlan, calc_lot_size, validate_rr


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, period: int) -> pd.Series:
    """RSI chuẩn (Wilder smoothing qua ewm alpha=1/period)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100)   # avg_loss kéo dài về 0 (toàn nến tăng) → RSI=100 (quá mua)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def analyze_scalp(df: pd.DataFrame, cfg, balance: float, symbol_info: dict) -> TradePlan | None:
    """Phân tích 1 khung M5 độc lập. `df` PHẢI đã bỏ nến đang chạy — caller truyền
    df.iloc[:-1] (bất biến no-repaint, giống strategy.analyze của bot SMC)."""
    min_bars = max(cfg.ema_slow, cfg.atr_period, cfg.rsi_period) + 1
    if len(df) < min_bars:
        return None

    ema_fast = _ema(df["close"], cfg.ema_fast)
    ema_slow = _ema(df["close"], cfg.ema_slow)
    rsi = _rsi(df["close"], cfg.rsi_period)
    atr = _atr(df, cfg.atr_period)

    ef, es = ema_fast.iloc[-1], ema_slow.iloc[-1]
    if ef == es:
        return None   # tie hiếm gặp — không coi là up cũng không down
    trend = "up" if ef > es else "down"

    a = atr.iloc[-1]
    if not (a > 0) or a < cfg.min_atr_points:
        return None   # thị trường quá lặng, không đủ biên độ cho SL/TP theo ATR

    last = df.iloc[-1]
    r = rsi.iloc[-1]

    if trend == "up":
        pullback = last["low"] <= ef and last["close"] > last["open"] and last["close"] > ef
        rsi_ok = cfg.rsi_buy_min <= r <= cfg.rsi_buy_max
        direction = "buy"
    else:
        pullback = last["high"] >= ef and last["close"] < last["open"] and last["close"] < ef
        rsi_ok = cfg.rsi_sell_min <= r <= cfg.rsi_sell_max
        direction = "sell"

    if not (pullback and rsi_ok):
        return None

    entry = last["close"]
    if direction == "buy":
        sl = entry - cfg.sl_atr_mult * a
        tp = entry + cfg.tp_atr_mult * a
    else:
        sl = entry + cfg.sl_atr_mult * a
        tp = entry - cfg.tp_atr_mult * a

    digits = symbol_info["digits"]
    entry, sl, tp = round(entry, digits), round(sl, digits), round(tp, digits)

    ok, rr = validate_rr(entry, sl, tp, cfg.min_rr)
    if not ok:
        return None

    lot = calc_lot_size(balance, cfg.risk_per_trade_pct, entry, sl,
                        symbol_info["contract_size"], symbol_info["volume_min"],
                        symbol_info["volume_step"], symbol_info["volume_max"])
    if lot <= 0:
        return None

    risk_amount = round(balance * cfg.risk_per_trade_pct / 100.0, 2)
    reason = f"EMA{cfg.ema_fast}/{cfg.ema_slow} {trend} pullback + RSI {r:.1f}"
    return TradePlan(direction=direction, entry=entry, sl=sl, tp=tp, lot=lot, rr=rr,
                     risk_amount=risk_amount, reason=reason, sweep_level=None,
                     order_kind="market")
