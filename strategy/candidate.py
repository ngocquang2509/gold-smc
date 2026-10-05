"""
Hợp đồng chung của mọi Candidate trong Bake-off (GLOSSARY.md, ADR 0001).

Một Candidate tính tín hiệu VECTOR HÓA cho cả lịch sử trong 1 lần (`signals`). Điều
kiện bắt buộc: hàng i chỉ được dùng dữ liệu bar ≤ i (nhân quả). Harness kiểm tra
điều này tự động (backtest.walkforward.assert_causal) — Candidate vi phạm bị loại.
Live sẽ gọi đúng hàm này trên các bar đã đóng gần nhất và lấy hàng cuối → backtest ≡ live.

Tín hiệu ở hàng i (tính lúc bar i ĐÓNG) được engine thực thi từ bar i+1.

Cột của DataFrame trả về (index trùng bars):
  signal       +1 mua / -1 bán / 0 không làm gì
  entry_type   "market" (khớp open bar kế) | "stop" | "limit" (lệnh chờ tại entry_price)
  entry_price  giá lệnh chờ (NaN với market)
  sl, tp       giá SL / TP (tp = NaN → không có TP, thoát bằng trail/flat)
  expiry       số bar lệnh chờ còn hiệu lực
Cột tùy chọn (để quản lý vị thế đang mở):
  trail_long / trail_short   mức SL trượt; engine chỉ dời SL theo hướng có lợi
  flat         True → đóng vị thế tại open bar kế (vd hết phiên)
Cột tùy chọn (lệnh chờ 2 chân OCO, chân kia NGƯỢC hướng signal, cùng entry_type/expiry):
  oco_price, oco_sl, oco_tp   NaN → không có chân thứ hai. Chân khớp trước hủy chân kia.
Cột tùy chọn (time stop):
  max_bars     đóng tại open bar khớp+max_bars nếu vị thế còn mở (NaN/0 = tắt)

`retired`: Candidate đã ĐÓNG (trượt Bake-off) — Bake-off mặc định bỏ qua, Final Holdout từ
chối. Không tune lại, không thêm filter (ADR 0001). Code + selftest giữ lại để tham khảo.
"""
from dataclasses import dataclass, field

import pandas as pd

MAX_PARAMS = 4   # Complexity Budget

SIGNAL_COLUMNS = ["signal", "entry_type", "entry_price", "sl", "tp", "expiry"]


@dataclass
class Candidate:
    name: str
    timeframe: str                                 # khung bar chạy: "M15" | "H1" | "H4" | "D1"
    param_grid: dict = field(default_factory=dict)  # tên tham số → danh sách giá trị thử
    uses_be_partial: bool = False                  # dùng breakeven/partial của risk.manage_step?
    retired: str | None = None                     # lý do đóng (vd "Bake-off #1 2026-10-04: trượt")

    def __post_init__(self):
        if len(self.param_grid) > MAX_PARAMS:
            raise ValueError(
                f"{self.name}: {len(self.param_grid)} tham số > Complexity Budget {MAX_PARAMS}"
            )

    def signals(self, bars: pd.DataFrame, **params) -> pd.DataFrame:
        raise NotImplementedError


def empty_signals(index: pd.Index) -> pd.DataFrame:
    """Khung tín hiệu rỗng để Candidate điền vào."""
    return pd.DataFrame({
        "signal": 0, "entry_type": "market", "entry_price": float("nan"),
        "sl": float("nan"), "tp": float("nan"), "expiry": 0,
    }, index=index)
