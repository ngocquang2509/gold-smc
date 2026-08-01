# Gold SMC Bot — XAUUSD trên MetaTrader 5

Bot giao dịch vàng theo Smart Money Concepts (SMC), đa khung thời gian H4 → M15, quản lý vốn theo R:R với rủi ro cố định 1%/lệnh.

## ⚠️ Cảnh báo quan trọng

- Thư viện `MetaTrader5` cho Python **chỉ chạy trên Windows** (máy có cài terminal MT5 và đang đăng nhập).
- **Luôn chạy demo trước.** Mặc định `dry_run=True` — bot chỉ log tín hiệu, không đặt lệnh. Backtest → dry run → demo account → (cân nhắc kỹ) live.
- Backtest engine ở đây là mô phỏng đơn giản (không tính spread/commission/slippage đầy đủ). Kết quả backtest tốt **không đảm bảo** lợi nhuận thực tế. Giao dịch vàng có đòn bẩy rủi ro rất cao.

## Cài đặt

```bash
pip install MetaTrader5 pandas numpy
```

Mở MT5, đăng nhập tài khoản **demo**, bật "Algo Trading". Kiểm tra tên symbol vàng của broker (XAUUSD / GOLD / XAUUSDm...) và sửa `symbol` trong `config/config.py` nếu cần.

## Cấu trúc dự án

Tổ chức theo vai trò kỹ thuật, mỗi thư mục là 1 package (chạy bằng `python -m` từ gốc dự án):

```
gold-smc-bot/
├── config/
│   └── config.py       # Mọi tham số tinh chỉnh
├── strategy/
│   ├── strategy.py      # Logic tổng hợp tín hiệu
│   └── smc/
│       ├── structure.py   # Swing, BOS, CHoCH, trend HTF
│       ├── order_blocks.py# Order Block + kiểm tra imbalance
│       ├── fvg.py         # Fair Value Gap
│       └── liquidity.py   # Liquidity pools, sweep, TP theo thanh khoản
├── risk/
│   └── risk.py          # Position sizing, R:R, daily loss, heat cap
├── backtest/
│   └── backtest.py       # Engine backtest bar-by-bar
└── execution/
    ├── main.py           # Vòng lặp live/demo
    └── mt5_client.py      # Wrapper MetaTrader5 (kết nối, dữ liệu, lệnh)
```

## Logic giao dịch

1. **H4** — xác định xu hướng bằng market structure (chuỗi BOS/CHoCH). Trend `neutral` → đứng ngoài.
2. **M15** — chỉ tìm lệnh thuận hướng H4:
   - Chờ **liquidity sweep** ngược hướng (quét đáy trước khi buy, quét đỉnh trước khi sell).
   - Sau sweep, chờ **CHoCH/BOS** trên M15 quay về hướng H4 (xác nhận).
   - Entry khi giá **retest OB hoặc FVG** hình thành từ cú xác nhận.
3. **SL** — ngoài biên OB/mức sweep + buffer 1.5 USD.
4. **TP** — pool thanh khoản đối diện gần nhất; nếu không đủ R:R ≥ 2 thì dùng TP cố định 2.5R.
5. **Quản lý vốn**:
   - 1.5% tài khoản/lệnh, lot tự tính theo khoảng SL.
   - Chỉ vào lệnh khi R:R ≥ 2.
   - Dời SL về hòa vốn tại 1R; chốt 50% tại 1.5R.
   - Dừng trong ngày nếu lỗ 3%; heat cap tổng 6%.

## Sử dụng

### Backtest

```bash
# Tải dữ liệu trực tiếp từ MT5 (chạy trên Windows):
python -m backtest.backtest --from-mt5 --bars 5000 --balance 10000

# Hoặc từ CSV (cột: time,open,high,low,close):
python -m backtest.backtest --csv-ltf data/xauusd_m15.csv --csv-htf data/xauusd_h4.csv
```

Kết quả: winrate, PnL, max drawdown + file `backtest_trades.csv`, `backtest_equity.csv`.

### Chạy demo

```bash
python -m execution.main
```

Ban đầu để `dry_run=True` vài ngày để quan sát tín hiệu trong `bot.log`. Khi hài lòng, đổi `dry_run=False` trong `config/config.py` — bot đặt lệnh trên tài khoản demo đang đăng nhập.

## Tinh chỉnh đáng thử trong `config/config.py`

- `require_sweep=False` — nới lỏng, nhiều tín hiệu hơn (chất lượng thấp hơn).
- `entry_mode` — thử `"ob_only"` vs `"fvg_only"` để xem vùng nào cho winrate tốt hơn với vàng.
- `sessions` — giờ theo **server MT5** (thường GMT+2/+3), khác giờ Việt Nam. Kiểm tra giờ server trong tab Market Watch và điều chỉnh cho khớp phiên London/NY.
- `fvg_min_size_points` — vàng biến động mạnh, tăng lên 1.0–2.0 để lọc nhiễu.

## Hướng mở rộng

- Gắn Telegram bot báo tín hiệu — chỉ cần thêm 1 hàm gửi message tại điểm log "🎯 TÍN HIỆU".
- Ghi journal lệnh ra CSV/SQLite để thống kê theo setup (OB vs FVG, có sweep vs không).
- Walk-forward test: chia dữ liệu thành nhiều giai đoạn để tránh overfit tham số.
