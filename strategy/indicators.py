"""
Chỉ báo kỹ thuật độc lập (không phải khái niệm SMC) — dùng cho các gate tuỳ
chọn trong strategy.analyze() (ADX trend-strength, ATR-regime). Xem
docs/superpowers/specs/2026-08-01-adx-atr-regime-filter-design.md.

Mọi hàm đều thuần (không trạng thái, không I/O), nhận `df` đã được caller cắt
tới hiện tại (giống mọi module smc/*) — không có rủi ro lookahead ở đây.
"""
import pandas as pd


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR chuẩn (Wilder smoothing qua ewm alpha=1/period) — cùng công thức
    _atr() đã dùng trong scalp_strategy.py. `period` phần tử đầu bị ép NaN
    tường minh: ewm(adjust=False) của pandas KHÔNG tự sinh NaN đầu chuỗi, nên
    phải ép tay để caller (strategy.py) fail-safe đúng bằng pd.isna() thay vì
    dùng số liệu ATR chưa hội tụ đủ."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    result = tr.ewm(alpha=1 / period, adjust=False).mean()
    result.iloc[:period] = float("nan")
    return result


def adx(df: pd.DataFrame, period: int) -> pd.Series:
    """ADX chuẩn: +DM/-DM Wilder-smoothed thành +DI/-DI (chia ATR cùng
    period) → DX = 100*|+DI - -DI| / (+DI + -DI) → ADX = Wilder-smoothed DX.
    2*period phần tử đầu bị ép NaN tường minh (+DI/-DI cần period nến ấm, ADX
    cần thêm period nến làm mượt DX mới hội tụ đủ)."""
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low, (high - prev_close).abs(), (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / period, adjust=False).mean()

    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_.replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    result = dx.ewm(alpha=1 / period, adjust=False).mean()
    result.iloc[:2 * period] = float("nan")
    return result


def atr_percentile(df: pd.DataFrame, atr_period: int, lookback: int) -> pd.Series:
    """Percentile (0-100) của ATR tại mỗi nến so với `lookback` giá trị ATR
    liền trước nó — rolling window KẾT THÚC TẠI nến đó (không nhìn tương
    lai). NaN khi chưa đủ atr_period + lookback nến lịch sử (ATR chưa ấm HOẶC
    cửa sổ rolling chưa đủ `lookback` giá trị ATR hợp lệ — rolling(min_periods=
    lookback) coi các NaN đầu chuỗi ATR là thiếu quan sát, tự động lan NaN cho
    tới khi đủ cửa sổ toàn giá trị hợp lệ)."""
    atr_series = atr(df, atr_period)
    return atr_series.rolling(window=lookback, min_periods=lookback).rank(pct=True) * 100
