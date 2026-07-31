# Luồng Scalping M5 độc lập — Design Spec

Ngày: 2026-07-31

## Bối cảnh

Bot hiện tại (`strategy.py`, `smc/`) là chiến lược SMC đa khung H4→M15, rủi ro cố định
1.15-2%/lệnh, tần suất thấp (vài lệnh/tuần). Yêu cầu: thêm một **luồng giao dịch hoàn
toàn độc lập** — scalping đơn khung M5, tần suất cao hơn, áp dụng cho cả 3 symbol đang
có (XAUUSDm, EURUSDm, GBPUSDm). Luồng này chạy **song song, tiến trình riêng**, không
đụng vào `main.py`/`strategy.py`/`config.py` hiện có, risk % tách biệt hoàn toàn (không
cộng dồn với risk của bot SMC dù cùng tài khoản).

## Mục tiêu / Phi mục tiêu

**Mục tiêu:**
- Chiến lược 1 khung M5: EMA trend-pullback + RSI momentum + ATR volatility/SL/TP.
- Backtest riêng để kiểm chứng trước khi chạy live/demo.
- Live/demo loop riêng, đặt lệnh MARKET, SL/TP cố định tại broker (không BE/partial).
- Áp dụng độc lập cho 3 symbol hiện có, mỗi symbol 1 bộ tham số riêng (giống pattern
  `config.py` hiện tại: 1 schema, nhiều instance).

**Phi mục tiêu (rõ ràng để không lấn phạm vi):**
- Không sửa `TradingConfig`, `strategy.py`, `smc/*`, `main.py`, `backtest.py` hiện có.
- Không tune sâu tham số (EMA period, ATR multiplier, ngưỡng RSI...) trong spec này —
  giá trị dưới đây là **điểm khởi đầu hợp lý**, tinh chỉnh bằng cách chạy lặp lại
  `scalp_backtest.py` sau khi triển khai xong (tương tự cách `backtest-tuning` skill
  làm với bot SMC).
- Không dùng breakeven/partial-close — scalp vào/ra dứt khoát bằng SL/TP cố định.
- Không thêm symbol mới ngoài 3 symbol đã có (nếu cần mở rộng, làm sau theo pattern
  `add-new-symbol`).

## Kiến trúc file

| File | Vai trò |
|---|---|
| `scalp_config.py` | `ScalpConfig` dataclass (schema riêng) + 3 instance (XAUUSD_SCALP, EURUSD_SCALP, GBPUSD_SCALP) + registry `SCALP_CONFIGS` + `get_scalp_config(name)` |
| `scalp_strategy.py` | `analyze_scalp(m5_df, cfg, balance, symbol_info) -> TradePlan \| None` |
| `scalp_backtest.py` | CLI backtest bar-by-bar, 1 khung M5, 1 symbol/lần chạy |
| `scalp_main.py` | CLI live/demo loop, hỗ trợ `--symbol` và `--symbols` (nhiều symbol 1 process, giống `main.py`) |

**Tái dùng nguyên vẹn, không viết lại:**
- `risk.py`: `calc_lot_size`, `validate_rr`, `RiskGuard`, `trade_cost`, `TradePlan`
  (dùng chung dataclass `TradePlan` đã có trong `risk.py`).
- `journal.py`: `TradeJournal(symbol, path=...)` — truyền `path` riêng để không đụng
  file journal của SMC.
- `mt5_client.py`: `MT5Client` dùng nguyên (đã hỗ trợ M5, `market_order`,
  `position_close_info`...).
- Từ `main.py`: copy lại 2 hàm generic không phụ thuộc SMC — `in_session`,
  `reconcile_journal` (logic thuần túy dựa trên journal + MT5 history, không đọc field
  nào riêng của `TradingConfig`/SMC).

**KHÔNG tái dùng** (theo quyết định thiết kế): `manage_step`/`manage_tick`/
`breakeven_level`/`manage_open_positions` — vì luồng scalp không có BE/partial. SL/TP
đặt cứng ngay lúc `market_order`, broker tự đóng lệnh khi chạm — không cần vòng quản lý
mỗi tick.

## `ScalpConfig` schema

```python
@dataclass
class ScalpConfig:
    symbol: str = "XAUUSDm"
    timeframe: str = "M5"
    bars: int = 300              # số nến M5 tải mỗi lần phân tích (đủ cho EMA50 + ATR14 warmup)

    # Trend + entry
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_period: int = 14
    rsi_buy_min: float = 45.0     # dải RSI hợp lệ cho lệnh BUY
    rsi_buy_max: float = 70.0
    rsi_sell_min: float = 30.0    # dải RSI hợp lệ cho lệnh SELL
    rsi_sell_max: float = 55.0

    # Volatility filter + SL/TP
    atr_period: int = 14
    min_atr_points: float = 0.0   # sàn ATR tối thiểu (đơn vị giá symbol) — per-symbol, đo sau khi có backtest đầu tiên
    sl_atr_mult: float = 1.2
    tp_atr_mult: float = 1.8
    min_rr: float = 1.3

    # Chống vào lệnh trùng lặp / tần suất
    cooldown_bars: int = 3        # nến M5 nghỉ sau khi 1 lệnh đóng (~15 phút)
    max_trades_per_day: int = 15
    max_open_positions: int = 1

    # Risk (TÁCH BIỆT hoàn toàn với bot SMC dù cùng tài khoản)
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 3.0
    portfolio_heat_pct: float = 3.0

    # Session (giờ server MT5 — điểm khởi đầu: tái dùng session đã tune của bot SMC
    # cho từng symbol, điều chỉnh riêng sau nếu backtest scalp cho kết quả khác)
    use_session_filter: bool = True
    sessions: list = field(default_factory=lambda: [("06:00", "19:00")])

    # Chi phí giao dịch — CÙNG schema field name với TradingConfig để trade_cost()
    # trong risk.py dùng được nguyên vẹn (duck-typing qua cfg.spread_points, ...).
    spread_points: float = 0.28
    commission_per_lot: float = 0.0
    swap_long_per_lot: float = -49.04
    swap_short_per_lot: float = 0.0

    # Execution
    magic_number: int = 20260731   # magic RIÊNG cho luồng scalp, khác 3 magic của SMC
    deviation: int = 20
    poll_seconds: int = 10         # M5 → poll nhanh hơn SMC (bar mới đóng cần bắt kịp)
    dry_run: bool = True
```

3 instance cụ thể (`XAUUSD_SCALP`, `EURUSD_SCALP`, `GBPUSD_SCALP`) override: `symbol`,
`price scale`-liên quan (`min_atr_points`, `sl_atr_mult`/`tp_atr_mult` nếu cần scale
theo giá), chi phí thật (copy từ config XAUUSD/EURUSD/GBPUSD hiện có trong
`config.py` — đã đo thật từ MT5 Exness), `magic_number` riêng biệt cho mỗi symbol
(20260731/32/33), `sessions` (điểm khởi đầu = session đã tune của SMC cho symbol đó).

`get_scalp_config(name)` giống hệt `get_config()`: alias-lookup + `dataclasses.replace()`
để trả bản copy.

## Entry sequence (`analyze_scalp`)

Chạy trên `m5_df.iloc[:-1]` (bỏ nến đang chạy — bất biến no-repaint giữ nguyên):

1. Tính EMA(`ema_fast`), EMA(`ema_slow`), RSI(`rsi_period`), ATR(`atr_period`) trên
   toàn bộ cửa sổ đã tải.
2. **Trend**: `up` nếu EMA_fast[-1] > EMA_slow[-1], `down` nếu ngược lại. Không có
   "neutral" — luôn có 1 hướng (khác SMC).
3. **Volatility gate**: nếu `ATR[-1] < cfg.min_atr_points` → không có tín hiệu (thị
   trường quá lặng, không đủ biên độ cho SL/TP theo ATR).
4. **Pullback trigger** trên nến đã đóng cuối cùng (index -1):
   - Buy (trend=up): `low[-1] <= EMA_fast[-1]` (giá chạm/xuyên EMA20) **và**
     `close[-1] > open[-1]` (nến đóng cửa xanh, bật lại) **và** `close[-1] > EMA_fast[-1]`.
   - Sell (trend=down): đối xứng (`high[-1] >= EMA_fast[-1]`, nến đỏ, `close[-1] < EMA_fast[-1]`).
5. **Momentum confirm**: `RSI[-1]` nằm trong `[rsi_buy_min, rsi_buy_max]` cho buy, hoặc
   `[rsi_sell_min, rsi_sell_max]` cho sell. Ngoài dải → bỏ qua (quá mua/quá bán hoặc
   yếu momentum).
6. **SL/TP**: buy → `sl = close[-1] - sl_atr_mult*ATR[-1]`, `tp = close[-1] + tp_atr_mult*ATR[-1]`
   (sell đối xứng). Làm tròn theo `symbol_info["digits"]` giống code hiện tại.
7. **Lot**: `calc_lot_size(balance, cfg.risk_per_trade_pct, entry, sl, symbol_info[...])`.
8. **R:R check**: `validate_rr(entry, sl, tp, cfg.min_rr)` — fail → không có tín hiệu.
9. Trả về `TradePlan(direction, entry=close[-1], sl, tp, lot, rr, risk_amount, reason,
   sweep_level=None, order_kind="market")`.

Không có khái niệm sweep/CHoCH/OB/FVG — toàn bộ logic SMC không áp dụng ở đây.

## Backtest (`scalp_backtest.py`)

CLI mirroring `backtest.py` nhưng đơn giản hơn (không có HTF):
```bash
python scalp_backtest.py --from-mt5 --symbol XAUUSDm --years 2 --balance 10000
python scalp_backtest.py --csv-m5 data/xauusd_m5.csv --symbol XAUUSDm
python scalp_backtest.py --from-mt5 --symbol EURUSDm --years 2 --no-costs
```
Vòng lặp bar-by-bar trên M5: tại mỗi nến mới đóng, gọi `analyze_scalp` trên slice
`df.iloc[:i]`, nếu có `TradePlan` thì mô phỏng khớp NGAY (market, không phải limit chờ
như SMC), rồi theo dõi các nến tiếp theo cho tới khi SL hoặc TP bị chạm (kiểm tra
`high`/`low` từng nến, thứ tự bảo thủ SL trước TP giống `manage_step`, nhưng không có
nhánh BE/partial). Cộng chi phí qua `trade_cost()` từ `risk.py` khi đóng lệnh. Xuất
`scalp_backtest_equity.csv` / `scalp_backtest_trades.csv` để không đè lên file báo cáo
của backtest SMC.

## Live/Demo (`scalp_main.py`)

Mirror cấu trúc multi-symbol của `main.py` (dataclass runner, vòng lặp tuần tự, mỗi
symbol state độc lập) nhưng loop mỗi tick đơn giản hơn nhiều vì không cần
`manage_open_positions`:

```
process_scalp_symbol(runner):
    reconcile_journal(client, journal, notifier)      # copy từ main.py, generic
    if daily_loss_exceeded or not in_session: return
    m5_df = client.get_rates("M5", cfg.bars)
    if m5_df nến cuối == last_bar đã xử lý: return     # chỉ xử lý mỗi nến mới đóng
    if n_open >= max_open_positions or cooldown chưa hết or trades_today >= max: return
    plan = analyze_scalp(m5_df.iloc[:-1], cfg, balance, symbol_info)
    if not plan: return
    if dry_run: ghi journal mode="paper" (resolve sau bằng theo dõi nến, tương tự resolve_paper_trades nhưng không cần logic limit-fill/entry_expiry — khớp ngay tại giá đóng nến trigger)
    else: client.market_order(...) rồi journal.record_open(mode="live")
```

`dry_run=True` mặc định (giữ nguyên bất biến toàn dự án). Tiến trình chạy độc lập với
`main.py` — cùng máy có thể chạy đồng thời `python main.py --symbols ...` và
`python scalp_main.py --symbols ...`, mỗi bên tự kết nối MT5 riêng (thư viện MT5 cho
phép nhiều `initialize()` trong các process khác nhau), magic number khác nhau nên
không bao giờ đụng lệnh của nhau.

## Testing / Validation

Sau khi code xong: chạy `scalp_backtest.py --from-mt5 --years 2` cho cả 3 symbol, xem
số lệnh/PF/winrate có hợp lý không (kỳ vọng tần suất cao hơn SMC nhiều — vài lệnh/ngày).
Đây là baseline đầu tiên — tune tham số (EMA period, ATR mult, RSI band, min_atr_points
per-symbol) là công việc **sau** plan này, lặp lại bằng cách chỉnh `scalp_config.py` rồi
chạy lại backtest, không cần thay đổi kiến trúc.
