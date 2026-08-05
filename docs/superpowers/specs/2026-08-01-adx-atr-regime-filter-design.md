# Thiết kế: ADX Trend-Strength Gate + ATR-Regime Gate

## Bối cảnh

Toàn bộ entry sequence hiện tại (`strategy.analyze`, xem CLAUDE.md mục "Entry
sequence") chỉ dựa vào price-action/cấu trúc SMC thuần: HTF trend → liquidity
sweep → CHoCH/BOS xác nhận → retest OB/FVG. Bộ lọc "ranging" duy nhất hiện có
(`htf_trend_max_age_bars`, xem `config.py`) suy luận sideway gián tiếp từ chính
cơ chế swing/structure đang dùng để tìm entry — không phải một nguồn thông tin
độc lập, nên dễ trễ hoặc bị đánh lừa khi giá đi ngang nhưng thỉnh thoảng vẫn
phá một swing nhỏ ("structure giả trend").

**Động lực:** người dùng nhận định chỉ dựa thuần SMC là không đủ linh hoạt để
thích ứng với mọi chế độ thị trường (uptrend/downtrend/sideway), và muốn thêm
một lớp lọc độc lập, KHÔNG dựa trên swing giá, để xác nhận độ tin cậy của
sweep+CHoCH tại đúng thời điểm nó xảy ra.

## Bối cảnh liên quan: nhánh `rsi-momentum-filter`

Có một nhánh git `rsi-momentum-filter` (2026-07-31) đã triển khai một RSI
confluence filter cùng mục đích (xác nhận momentum tại điểm CHoCH), nhưng
**chưa từng backtest và chưa merge**. Theo quyết định của người dùng, nhánh này
sẽ bị **xoá** ở bước triển khai (không phải scope kỹ thuật của spec này) — ADX
đo cùng mục đích (xác nhận trend đủ mạnh để tin tưởng tín hiệu) theo cách trực
tiếp hơn, thay thế hoàn toàn vai trò dự kiến của RSI filter.

## Quyết định đã chốt với người dùng

- **Cơ chế tác động:** gate đơn giản (chặn/cho qua khi không đạt threshold),
  KHÔNG làm regime-adaptive (không tự động nới/siết các tham số khác như
  `cooldown_bars`, `max_setup_age_bars` theo chế độ thị trường).
- **Số cờ:** hai cờ độc lập — `adx_filter_enabled` và `atr_regime_filter_enabled`
  — mỗi cờ bật/tắt riêng, để `backtest-tuning` đo tác động từng cái tách bạch
  (đúng cách đã đo `require_discount_premium`, `sweep_pool_min_rank` trước đây).
- **Khung thời gian:** cả ADX và ATR-regime tính trên LTF (M15) — cùng khung
  nơi sweep/CHoCH thực sự xảy ra, nhất quán với các filter tuỳ chọn khác.
- **Hướng chặn ATR-regime:** chỉ chặn khi ATR percentile THẤP (biến động co
  hẹp → nghi ngờ sideway/thanh khoản mỏng). KHÔNG chặn khi percentile cao —
  chiến lược thuần theo trend nên biến động cao đi kèm trend mạnh/breakout vẫn
  được chấp nhận vào lệnh.
- **Phạm vi symbol:** áp dụng cho cả 3 symbol (XAUUSDm, EURUSDm, GBPUSDm), dùng
  chung threshold mặc định — tune riêng per-symbol (nếu cần) là việc của các
  lần `backtest-tuning` sau, không nằm trong spec này.

## Kiến trúc

### File mới: `indicators.py` (root, ngang hàng `risk.py`, `mt5_client.py`)

Không đặt trong `smc/` vì đây là chỉ báo kỹ thuật độc lập, không phải khái
niệm SMC (giống lý do RSI spec cũ đặt ngoài `smc/`). Ba hàm thuần, không trạng
thái, không I/O, đều nhận `df` đã được cắt-tới-hiện-tại (không có rủi ro
lookahead mới — mọi caller trong `strategy.analyze` đã dùng `ltf_df` đã cắt
sẵn, giống mọi module `smc/` khác):

```python
def atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR chuẩn (Wilder smoothing) từ True Range. Trả về Series cùng chiều
    dài df, NaN cho `period` phần tử đầu (chưa đủ dữ liệu làm ấm)."""

def adx(df: pd.DataFrame, period: int) -> pd.Series:
    """ADX chuẩn: +DM/-DM → Wilder-smoothed +DI/-DI (chia cho ATR cùng period)
    → DX = 100 * |+DI - -DI| / (+DI + -DI) → ADX = Wilder-smoothed DX.
    NaN cho ~2*period phần tử đầu (DX cần ATR ấm, ADX cần DX ấm)."""

def atr_percentile(df: pd.DataFrame, atr_period: int, lookback: int) -> pd.Series:
    """Percentile (0-100) của ATR tại mỗi nến so với `lookback` giá trị ATR
    liền trước nó (rolling percentile, KHÔNG nhìn tương lai — mỗi điểm i chỉ
    so với ATR trong cửa sổ [i-lookback+1, i]). NaN khi chưa đủ
    atr_period + lookback nến lịch sử."""
```

**Quy ước chỉ mục:** giống RSI spec cũ — mọi truy cập vào các Series trả về
phải dùng `.iloc[...]` (vị trí, 0-based), KHÔNG dùng `.loc[...]` — `ltf_df` có
`DatetimeIndex`, và `confirm.index`/mọi index trong `smc/` là vị trí
(positional), không phải timestamp label.

### Tích hợp vào `strategy.py`

Thêm hai bước gate mới ngay sau bước 4 (CHoCH/BOS xác nhận) và trước bước 4b
hiện có (trần tuổi setup `max_setup_age_bars`) — cùng nhóm các filter tuỳ chọn
đánh giá tại `confirm.index`:

```python
# ── 4a. ADX trend-strength gate (tùy chọn) ──────────
if cfg.adx_filter_enabled:
    adx_series = adx(ltf_df, cfg.adx_period)
    adx_at_confirm = adx_series.iloc[confirm.index]
    if pd.isna(adx_at_confirm) or adx_at_confirm < cfg.adx_min_threshold:
        log.debug("ADX tại điểm confirm quá yếu — trend không đủ lực, bỏ.")
        return None

# ── 4a2. ATR-regime gate (tùy chọn) — chặn khi biến động co hẹp (chop) ──
if cfg.atr_regime_filter_enabled:
    pct_series = atr_percentile(ltf_df, cfg.atr_period, cfg.atr_regime_lookback)
    pct_at_confirm = pct_series.iloc[confirm.index]
    if pd.isna(pct_at_confirm) or pct_at_confirm < cfg.atr_regime_min_percentile:
        log.debug("ATR percentile tại điểm confirm quá thấp — biến động co hẹp/chop, bỏ.")
        return None
```

Cả hai đánh giá tại đúng nến `confirm.index` (thời điểm CHoCH xác nhận), không
phải nến hiện tại cuối `ltf_df` — trả lời đúng câu hỏi "lúc xác nhận đó, trend
có đủ lực / biến động có đủ rộng để tin tưởng không", nhất quán với cách RSI
spec cũ định nghĩa "trạng thái tại lúc xác nhận".

Thứ tự các bước sau khi thêm: 1 → 2 → 3 → 4 → **4a (ADX, mới)** →
**4a2 (ATR-regime, mới)** → 4b (trần tuổi setup, hiện có) → 5 → 5b → 5c → 6.

### Config mới trong `TradingConfig` (`config.py`)

Đặt cạnh block #10-#11 hiện có trên `main` (số #12 từng được nhánh
`rsi-momentum-filter` dùng nhưng nhánh đó chưa merge và sẽ bị xoá — không tồn
tại trên `main`, nên đánh số tiếp từ #11 là #12/#13, không để hổng số):

```python
# #12 — ADX trend-strength gate: chỉ tin CHoCH/BOS xác nhận khi ADX (đo trên
#   LTF, tại đúng nến xác nhận) đủ mạnh — tránh sweep+CHoCH là nhiễu cấu trúc
#   trong thị trường yếu/sideway. MẶC ĐỊNH TẮT — cần backtest-tuning đo trước
#   khi bật, theo đúng cách #6 (require_discount_premium) đã làm.
adx_filter_enabled: bool = False
adx_period: int = 14
adx_min_threshold: float = 20.0

# #13 — ATR-regime gate: chặn khi biến động (ATR, đo trên LTF) co hẹp so với
#   lịch sử gần (percentile thấp) tại đúng nến xác nhận — dấu hiệu
#   sideway/thanh khoản mỏng. Không chặn percentile cao (trend mạnh vẫn được
#   chấp nhận). MẶC ĐỊNH TẮT — cần backtest-tuning đo trước khi bật.
atr_regime_filter_enabled: bool = False
atr_period: int = 14
atr_regime_lookback: int = 100
atr_regime_min_percentile: float = 25.0
```

Không override riêng cho EURUSD/GBPUSD trong spec này — dùng chung default,
tune riêng (nếu cần) là công việc của `backtest-tuning` sau khi có baseline.

## Testing / Validation

Không có test suite trong repo (theo CLAUDE.md) — validation là qua
`backtest.py`, dùng skill `backtest-tuning`.

Quy trình:
1. Baseline: cả hai cờ tắt (hiện trạng, không đổi kết quả).
2. Bật riêng `adx_filter_enabled=True` (ATR-regime vẫn tắt) → so PF/winrate/
   CAGR/số lệnh full-2y **và** nửa đầu/nửa sau (H1/H2), trên cả 3 symbol
   (XAUUSDm, EURUSDm, GBPUSDm).
3. Bật riêng `atr_regime_filter_enabled=True` (ADX tắt) → đo tương tự.
4. Nếu cả hai đều cho thấy lợi ích riêng lẻ (PF/CAGR cải thiện mà không giảm
   số lệnh quá mạnh) → thử bật đồng thời cả hai, đo lại — vì hai filter có thể
   loại bỏ chồng lấn setup, giảm số lệnh mạnh hơn tổng hai cái cộng riêng.
5. Quyết định giữ/bỏ từng cờ dựa trên số đo, không dựa trên lý thuyết —
   filter nào không cải thiện rõ rệt mà chỉ giảm số lệnh thì tắt (giữ mặc định
   `False`), đúng như đã làm với `require_discount_premium`.
6. Threshold mặc định (`adx_min_threshold=20`, `atr_regime_min_percentile=25`)
   chỉ là điểm khởi đầu chuẩn theo lý thuyết chỉ báo — dò ngưỡng tối ưu là công
   việc của các lần `backtest-tuning` lặp lại sau, ngoài scope implement ban đầu.

**Lưu ý về warmup trong backtest:** `backtest.py` truyền cho `analyze()` một
slice CUỐN CHIẾU (`ltf.iloc[max(0, i+1-cfg.ltf_bars): i+1]`), nên ở đầu giai
đoạn backtest, slice có thể ngắn hơn `2*adx_period` hoặc
`atr_period+atr_regime_lookback` nến cần thiết để `adx()`/`atr_percentile()`
"ấm" — khi đó `.iloc[confirm.index]` là NaN và gate fail-safe trả `None` (đúng
hành vi mong muốn, không crash, không lookahead). Hệ quả: khi bật một trong
hai cờ, số lệnh ở PHẦN ĐẦU giai đoạn backtest có thể bị đánh giá thấp hơn thực
tế do warmup, cần lưu ý khi so sánh H1 lúc chạy `backtest-tuning`.

## Ngoài phạm vi (out of scope)

- Regime-adaptive (tự động nới/siết `cooldown_bars`, `max_setup_age_bars`,
  `entry_expiry_bars`... theo ADX/ATR) — đã chọn hướng gate đơn giản.
- Chặn khi ATR percentile CAO (rủi ro slippage/tin tức) — đã chọn chỉ chặn
  percentile thấp.
- Tính ADX/ATR trên HTF (H4) — đã chọn LTF (M15) cho cả hai.
- Tune threshold riêng theo từng symbol — chỉ làm nếu baseline cho thấy cần.
- Xoá nhánh git `rsi-momentum-filter` cũ — xử lý ở bước triển khai, tách khỏi
  scope kỹ thuật của spec này.
- Bất kỳ thay đổi nào tới logic SL/TP/position-sizing.
