"""
Liquidity: các pool thanh khoản (equal highs/lows, swing cũ) và phát hiện sweep.
Sweep = giá quét qua đỉnh/đáy (râu nến) rồi đóng cửa quay trở lại → stop hunt.
Đây là "chữ ký" của smart money trước khi đảo chiều thật sự.
"""
import pandas as pd
from dataclasses import dataclass
from .structure import Swing


@dataclass
class LiquidityPool:
    price: float
    kind: str            # "buyside" (trên đỉnh) | "sellside" (dưới đáy)
    index: int
    swept: bool = False


@dataclass
class SweepEvent:
    index: int
    kind: str            # "buyside_sweep" | "sellside_sweep"
    swept_level: float   # mức POOL bị quét (dùng cho TP-context & chống re-entry)
    time: pd.Timestamp
    extreme: float = None  # ĐỈNH/ĐÁY RÂU NẾN thật của cú quét (buyside=high, sellside=low)
    #   → dùng đặt SL NGOÀI râu quét, không phải tại mức pool (pool nằm TRONG vùng đã bị chạm).


def build_liquidity_pools(swings: list[Swing], eq_tolerance: float = 1.0) -> list[LiquidityPool]:
    """
    Pool từ swing highs/lows. Equal highs/lows (chênh <= tolerance USD)
    được gộp thành 1 pool mạnh hơn tại mức extreme.
    """
    pools: list[LiquidityPool] = []
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    for group, kind, pick in ((highs, "buyside", max), (lows, "sellside", min)):
        used = set()
        for i, s in enumerate(group):
            if i in used:
                continue
            cluster = [s]
            for j in range(i + 1, len(group)):
                if abs(group[j].price - s.price) <= eq_tolerance:
                    cluster.append(group[j])
                    used.add(j)
            level = pick(x.price for x in cluster)
            idx = max(x.index for x in cluster)
            pools.append(LiquidityPool(price=level, kind=kind, index=idx))
    return pools


def detect_sweeps(df: pd.DataFrame, pools: list[LiquidityPool],
                  lookback: int = 40, min_rank: int = 0) -> list[SweepEvent]:
    """
    Sweep hợp lệ: high vượt pool buyside nhưng CLOSE dưới pool (hoặc ngược lại).
    Chỉ xét trong `lookback` nến gần nhất để tín hiệu còn "tươi".

    `min_rank` (#10, 0 = tắt): nếu > 0, một pool CHỈ được coi là hợp lệ để quét khi nó
    nằm trong nhóm `min_rank` pool CỰC TRỊ NHẤT cùng chiều (buyside: giá cao nhất;
    sellside: giá thấp nhất) — lọc các đỉnh/đáy phụ nông giữa một dải giá rộng hơn,
    tránh vào lệnh theo cú quét một mức thanh khoản yếu.
    """
    sweeps: list[SweepEvent] = []
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    n = len(df)
    start = max(0, n - lookback)

    eligible = pools
    if min_rank > 0:
        eligible_ids = set()
        for kind, reverse in (("buyside", True), ("sellside", False)):
            ranked = sorted((p for p in pools if p.kind == kind),
                            key=lambda p: p.price, reverse=reverse)
            eligible_ids.update(id(p) for p in ranked[:min_rank])
        eligible = [p for p in pools if id(p) in eligible_ids]

    for pool in eligible:
        for i in range(max(start, pool.index + 1), n):
            if pool.swept:
                break
            if pool.kind == "buyside" and h[i] > pool.price and c[i] < pool.price:
                sweeps.append(SweepEvent(i, "buyside_sweep", pool.price, df.index[i], extreme=h[i]))
                pool.swept = True
            elif pool.kind == "sellside" and l[i] < pool.price and c[i] > pool.price:
                sweeps.append(SweepEvent(i, "sellside_sweep", pool.price, df.index[i], extreme=l[i]))
                pool.swept = True
    return sweeps


def nearest_target_pool(pools: list[LiquidityPool], price: float, direction: str) -> float | None:
    """TP theo thanh khoản: lệnh BUY nhắm pool buyside gần nhất phía trên, SELL ngược lại."""
    if direction == "bullish":
        candidates = [p.price for p in pools if p.kind == "buyside" and p.price > price and not p.swept]
        return min(candidates) if candidates else None
    candidates = [p.price for p in pools if p.kind == "sellside" and p.price < price and not p.swept]
    return max(candidates) if candidates else None
