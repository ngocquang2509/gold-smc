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
                  confirm_index: int, sweep_index: int | None) -> bool:
    """True nếu momentum đã kiệt sức trong cửa sổ trước confirm rồi đảo chiều.

    sweep_index: vị trí (positional) của sweep xác nhận trend đảo chiều, hoặc
    None nếu không có sweep (require_sweep=False) — khi đó dùng cửa sổ lùi lại
    cfg.rsi_period nến làm mốc bắt đầu thay vì so sweep_index == confirm_index.
    """
```

### Logic `rsi_confirms`

Không kiểm tra "RSI hiện tại > 50" chung chung — bám sát đúng câu chuyện cấu
trúc của sweep+CHoCH: momentum có thực sự kiệt sức trong khoảng
`[start_index, confirm_index]` rồi quay đầu tới lúc xác nhận không?

**Quan trọng — mọi chỉ mục (`sweep_index`, `confirm_index`, `start_index`) là
vị trí (positional, 0-based), KHÔNG phải label của `DatetimeIndex`** (
`ltf_df` có index là thời gian — xem `mt5_client.get_rates`). `rsi()` trả về
`pd.Series` cùng chiều dài với `ltf_df`, và mọi truy cập bên trong
`rsi_confirms` phải dùng `.iloc[...]`, không dùng `.loc[...]` hay slice trực
tiếp trên Series theo label — slice số nguyên trên Series pandas *tình cờ*
hoạt động theo vị trí bất kể dtype của index, nhưng đây là hành vi dễ gây nhầm
lẫn nên bắt buộc dùng `.iloc` tường minh để tránh implementer "dọn code" bằng
`.loc` và âm thầm sai.

- `start_index = last_sweep.index if last_sweep else max(0, confirm_index - cfg.rsi_period)`.
  Khi không có sweep (`require_sweep=False`), dùng một cửa sổ lùi lại
  `cfg.rsi_period` nến làm mốc bắt đầu — đảm bảo cửa sổ luôn có độ dài > 1
  (tránh trường hợp suy biến `start_index == confirm_index`, xem bug đã sửa
  bên dưới).
- Tính `s = rsi(ltf_df["close"], cfg.rsi_period)` (dùng `.iloc` khi truy cập).
- Nếu `start_index < cfg.rsi_period` (không đủ nến lịch sử để RSI tại
  `start_index` đã "ấm" — `rsi()` trả NaN cho `cfg.rsi_period` phần tử đầu),
  trả về `False` (fail-safe — không đoán mò trên NaN, bỏ qua lệnh thay vì cho
  qua).
- Tìm điểm cực trị trong cửa sổ, **hoàn toàn bằng vị trí (không qua
  `idxmin`/`idxmax` của pandas — hai hàm đó trả về LABEL của index, ở đây là
  `Timestamp`, không phải vị trí, nên không được dùng lại với `.iloc`)**:
  ```python
  window = s.iloc[start_index:confirm_index + 1].to_numpy()
  extreme_pos = start_index + int(window.argmin())   # bullish; .argmax() cho bearish
  ```
  `numpy.argmin`/`argmax` trả về vị trí trong mảng (int), cộng lại với
  `start_index` cho ra vị trí tuyệt đối trong `ltf_df` — dùng trực tiếp với
  `s.iloc[extreme_pos]`, không đi qua bất kỳ label nào của `s`.
  `extreme_pos` là vị trí RSI kiệt sức nhất trong cửa sổ, KHÔNG nhất thiết là
  `start_index` (sweep có thể không trùng đúng đáy RSI).
- **Bullish**: `s.iloc[extreme_pos] <= cfg.rsi_oversold` **và**
  `s.iloc[confirm_index] > s.iloc[extreme_pos]` (momentum đã quay đầu lên kể
  từ điểm kiệt sức).
- **Bearish** (đối xứng): `s.iloc[extreme_pos] >= cfg.rsi_overbought` **và**
  `s.iloc[confirm_index] < s.iloc[extreme_pos]`.
- **Kiểu trả về:** các phép so sánh trên `pd.Series.iloc[...]` cho ra
  `numpy.bool_`, không phải `bool` của Python — khác nhau dưới `is True`/
  `is False` (identity check), dù in ra màn hình trông giống hệt `True`/
  `False`. Hàm khai báo trả về `bool` nên PHẢI bọc kết quả cuối bằng
  `bool(...)` tường minh trước khi return, để tránh gây khó hiểu cho bất kỳ
  test nào dùng `is True`/`is False` (kể cả trong chính plan triển khai của
  spec này) hoặc bất kỳ code gọi nào ở nơi khác dựa vào identity thay vì
  equality.

**Bug đã phát hiện và sửa trong bản thiết kế này:** thiết kế ban đầu so sánh
`s[confirm_index]` với `s[sweep_index]` trực tiếp. Khi không có `last_sweep`,
bản nháp trước truyền `sweep_idx = confirm.index` từ `strategy.py`, khiến
`sweep_index == confirm_index` → so sánh một giá trị với chính nó → luôn
`False` → RSI filter reject **mọi** lệnh khi `require_sweep=False`, mâu thuẫn
ngầm với combo `rsi_filter_enabled=True` + `require_sweep=False`. Thiết kế mới
(dùng cửa sổ lùi lại `rsi_period` nến + tìm cực trị bằng `numpy.argmin`/`argmax`
trên mảng vị trí thuần túy, thay vì so sánh trực tiếp với `start_index` hay
dùng `idxmin`/`idxmax` của pandas — vốn trả về label chứ không phải vị trí,
và sẽ vỡ khi dùng lại với `.iloc`) loại bỏ hoàn toàn cả hai lỗi: trường hợp
suy biến khi không có sweep (cửa sổ luôn dài hơn 1 nến), và lỗi label-vs-position
khi tra cứu điểm cực trị.

### Tích hợp vào `strategy.py`

Thêm bước **4a** ngay sau bước 4 (xác nhận CHoCH/BOS) và trước bước 4b (trần
tuổi setup hiện có) / bước 5 (tìm vùng entry OB/FVG) — cùng nhóm các filter
tùy chọn như bước 5c (`require_discount_premium`, #6). Thứ tự các bước sau khi
thêm: 4 → **4a (mới)** → 4b → 5 → 5b → 5c → 6.

```python
# ── 4a. RSI momentum confluence (tùy chọn) ──────────
if cfg.rsi_filter_enabled:
    sweep_idx = last_sweep.index if last_sweep else None
    if not rsi_confirms(ltf_df, cfg, trend, confirm.index, sweep_idx):
        log.debug("RSI không xác nhận momentum kiệt sức trước điểm confirm — bỏ.")
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
