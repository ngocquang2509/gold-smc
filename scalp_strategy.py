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
