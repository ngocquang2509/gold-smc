# Thiết kế: RSI Momentum Confluence Filter

## Bối cảnh

Hệ thống đang live từ 2026-07-27 trên 3 symbol (XAUUSDm, EURUSDm, GBPUSDm),
nhưng tần suất lệnh còn thấp và các lệnh gần đây phần lớn thua/hủy (xem
`trades_*.csv`: 1 lệnh lỗ EURUSDm -189.88, 2 lệnh limit bị hủy hết hạn, 1 đang
mở). Toàn bộ entry sequence hiện tại (`strategy.analyze`, xem CLAUDE.md mục
"Entry sequence") chỉ dựa vào price-action/cấu trúc SMC thuần: HTF trend →
liquidity sweep → CHoCH/BOS xác nhận → retest OB/FVG. Không có bất kỳ chỉ báo
momentum/volatility/volume nào xác nhận thêm cho tín hiệu.

**Động lực:** thêm một lớp lọc dựa trên momentum để phân biệt tốt hơn giữa các
setup sweep+CHoCH có thực sự phản ánh kiệt sức xu hướng ngược (đáng tin) và các
setup chỉ là nhiễu cấu trúc. Mục tiêu là cải thiện chất lượng lệnh (winrate/PF)
mà không siết tần suất mạnh như filter premium/discount (#6) đã từng đo được
(giảm mạnh số lệnh, không rõ lợi ích — xem memory `nine-fixes-2026-07`).

## Quyết định đã chốt với người dùng

- **Ưu tiên:** cân bằng — tìm chỉ báo phân biệt setup tốt/xấu tốt hơn, không
  đơn thuần siết cứng nhắc.
- **Phạm vi symbol:** áp dụng cho cả 3 symbol (XAUUSDm, EURUSDm, GBPUSDm) —
  cùng một code path `strategy.analyze()`, threshold có thể tune riêng per-symbol
  sau nếu cần (giữ convention "mỗi symbol 1 config instance").
- **Chỉ báo chọn:** RSI (Relative Strength Index) trên LTF (M15), dùng làm
  confluence filter tùy chọn (mặc định tắt cho tới khi backtest chứng minh có
  lợi — đúng pattern đã áp dụng cho `require_discount_premium`,
  `sweep_pool_min_rank`).
- **Bước tiếp theo sau khi có kết quả:** người dùng sẽ cân nhắc thêm ATR
  volatility filter (approach B) dựa trên kết quả backtest của RSI filter này.
  Approach B KHÔNG nằm trong scope của spec này.

## Kiến trúc

### File mới: `indicators.py` (root, ngang hàng `risk.py`, `mt5_client.py`)

Không đặt trong `smc/` vì các module ở đó là primitive thuần SMC (structure,
liquidity, order_blocks, fvg) theo mô tả CLAUDE.md; RSI là chỉ báo momentum độc
lập, không phải khái niệm SMC. Hai hàm thuần, không trạng thái, không I/O:

```python
def rsi(series: pd.Series, period: int) -> pd.Series:
    """RSI chuẩn (Wilder smoothing) trên chuỗi giá đóng cửa."""

def rsi_confirms(ltf_df: pd.DataFrame, cfg: TradingConfig, trend: str,
                  sweep_index: int, confirm_index: int) -> bool:
    """True nếu momentum đã kiệt sức tại sweep rồi đảo chiều tới lúc confirm."""
```

### Logic `rsi_confirms`

Không kiểm tra "RSI hiện tại > 50" chung chung — bám sát đúng câu chuyện cấu
trúc của sweep+CHoCH: momentum có thực sự kiệt sức tại điểm sweep rồi quay đầu
tới lúc xác nhận không?

- Tính `s = rsi(ltf_df["close"], cfg.rsi_period)`.
- Nếu không đủ nến lịch sử trước `sweep_index` để tính RSI (cần tối thiểu
  `cfg.rsi_period + 1` nến), trả về `False` (fail-safe — không đoán mò trên
  NaN, bỏ qua lệnh thay vì cho qua).
- **Bullish** (cần sellside sweep trước đó):
  `min(s[sweep_index : confirm_index + 1]) <= cfg.rsi_oversold`
  **và** `s[confirm_index] > s[sweep_index]`.
- **Bearish** (đối xứng, buyside sweep):
  `max(s[sweep_index : confirm_index + 1]) >= cfg.rsi_overbought`
  **và** `s[confirm_index] < s[sweep_index]`.
- Nếu không có `last_sweep` (trường hợp `require_sweep=False`), dùng
  `confirm_index` làm cả hai đầu mút của khoảng kiểm tra.

### Tích hợp vào `strategy.py`

Thêm bước **4c** ngay sau bước 4 (xác nhận CHoCH/BOS) và trước bước 4b (trần
tuổi setup) / bước 5 (tìm vùng entry OB/FVG) — cùng nhóm các filter tùy chọn
như bước 5c (`require_discount_premium`, #6):

```python
# ── 4c. RSI momentum confluence (tùy chọn) ──────────
if cfg.rsi_filter_enabled:
    sweep_idx = last_sweep.index if last_sweep else confirm.index
    if not rsi_confirms(ltf_df, cfg, trend, sweep_idx, confirm.index):
        log.debug("RSI không xác nhận momentum kiệt sức tại sweep — bỏ.")
        return None
```

Không có rủi ro lookahead mới: `ltf_df` tại thời điểm gọi đã là slice-đến-hiện-tại
trong backtest (`backtest.py` cắt tới bar `i`) hoặc `iloc[:-1]` trong live
(`main.py`) — giống mọi module `smc/` khác đang dùng `ltf_df` nguyên trạng.
`indicators.py` không cần biết gì về việc này, nó chỉ nhận DataFrame đã được
cắt sẵn.

### Config mới trong `TradingConfig` (`config.py`)

Đặt cạnh block #6 (`require_discount_premium`), cùng comment giải thích default
tắt:

```python
# #12 — Lọc confluence RSI: chỉ vào lệnh nếu momentum đã kiệt sức tại điểm
#   sweep (RSI chạm vùng oversold/overbought) rồi đảo chiều tới lúc CHoCH xác
#   nhận. Lọc các setup cấu trúc "giả" (sweep không có momentum kiệt sức thật).
#   MẶC ĐỊNH TẮT — cần backtest-tuning đo trước khi bật, theo đúng cách #6 đã làm.
rsi_filter_enabled: bool = False
rsi_period: int = 14
rsi_oversold: float = 30.0
rsi_overbought: float = 70.0
```

Không override riêng cho EURUSD/GBPUSD trong spec này — dùng chung default,
việc tune riêng (nếu cần) là công việc của `backtest-tuning` sau khi có baseline.

## Testing / Validation

- Không có test suite trong repo (theo CLAUDE.md) — validation là qua
  `backtest.py`, dùng skill `backtest-tuning`.
- Quy trình: baseline (rsi_filter_enabled=False, hiện trạng) → bật
  `rsi_filter_enabled=True` với default (14/30/70) → so sánh PF/winrate/CAGR/số
  lệnh full 2 năm **và** nửa đầu/nửa sau (H1/H2) trên cả 3 symbol
  (XAUUSDm, EURUSDm, GBPUSDm) — theo đúng checklist của skill `backtest-tuning`.
- Quyết định giữ/bỏ dựa trên kết quả đo được, không dựa trên lý thuyết.
- Sau khi có kết quả, người dùng sẽ quyết định có tiếp tục sang approach B (ATR
  volatility filter) hay không — nằm ngoài scope thực thi của spec này.

## Ngoài phạm vi (out of scope)

- ATR volatility filter (approach B) — chờ kết quả RSI filter.
- Composite confluence score nhiều chỉ báo (approach C).
- Tune threshold riêng theo từng symbol — chỉ làm nếu baseline cho thấy cần.
- Thay đổi bất kỳ logic SL/TP/position-sizing nào.
