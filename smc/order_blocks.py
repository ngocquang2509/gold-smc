"""
Order Block (OB): nến ngược hướng cuối cùng trước một cú phá vỡ cấu trúc mạnh.
- Bullish OB: nến giảm cuối cùng trước động thái tăng phá swing high.
- Bearish OB: nến tăng cuối cùng trước động thái giảm phá swing low.
OB chất lượng cao thường đi kèm imbalance (FVG) ngay sau nó.
"""
import pandas as pd
from dataclasses import dataclass, field
from .structure import StructureEvent


@dataclass
class OrderBlock:
    index: int
    direction: str        # "bullish" | "bearish"
    top: float
    bottom: float
    time: pd.Timestamp
    origin_event: str     # "BOS" | "CHOCH"
    mitigated: bool = False


def find_order_blocks(df: pd.DataFrame, events: list[StructureEvent],
                      require_imbalance: bool = True,
                      max_age_bars: int = 100) -> list[OrderBlock]:
    obs: list[OrderBlock] = []
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    n = len(df)

    for ev in events:
        # Tìm ngược từ nến phá vỡ về trước: nến ngược hướng cuối cùng
        ob_idx = None
        for j in range(ev.index - 1, max(ev.index - 15, -1), -1):
            if ev.direction == "bullish" and c[j] < o[j]:   # nến giảm
                ob_idx = j
                break
            if ev.direction == "bearish" and c[j] > o[j]:   # nến tăng
                ob_idx = j
                break
        if ob_idx is None:
            continue

        # Kiểm tra imbalance: sau OB phải có gap (FVG) thể hiện lực đẩy
        if require_imbalance and not _has_imbalance_after(df, ob_idx, ev.direction):
            continue

        obs.append(OrderBlock(
            index=ob_idx,
            direction=ev.direction,
            top=h[ob_idx],
            bottom=l[ob_idx],
            time=df.index[ob_idx],
            origin_event=ev.kind,
        ))

    # Đánh dấu OB đã bị mitigate (giá quay lại chạm) hoặc quá cũ
    for ob in obs:
        for k in range(ob.index + 2, n):
            if ob.direction == "bullish" and l[k] <= ob.top:
                # chạm lần đầu là "test"; xuyên thủng đáy OB là invalid
                if c[k] < ob.bottom:
                    ob.mitigated = True
                    break
            if ob.direction == "bearish" and h[k] >= ob.bottom:
                if c[k] > ob.top:
                    ob.mitigated = True
                    break
        if n - ob.index > max_age_bars:
            ob.mitigated = True

    return [ob for ob in obs if not ob.mitigated]


def _has_imbalance_after(df: pd.DataFrame, ob_idx: int, direction: str) -> bool:
    """FVG 3 nến bắt đầu từ OB: gap giữa high[i] và low[i+2] (bullish) hoặc ngược lại."""
    h, l = df["high"].values, df["low"].values
    for i in range(ob_idx, min(ob_idx + 3, len(df) - 2)):
        if direction == "bullish" and l[i + 2] > h[i]:
            return True
        if direction == "bearish" and h[i + 2] < l[i]:
            return True
    return False
