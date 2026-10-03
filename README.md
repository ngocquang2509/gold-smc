# Gold SMC Bot — đang xây lại (rebuild)

Bot giao dịch tự động trên MetaTrader 5 (Exness) cho XAUUSDm / EURUSDm / GBPUSDm, báo cáo qua Telegram.

## Trạng thái

Tầng chiến lược cũ (SMC H4→M15 + scalp M5) **đã gỡ**: edge của nó đến từ việc tune trên đúng 1 cửa sổ 2 năm vàng tăng mạnh, và kết quả live không đạt. Code cũ vẫn lấy lại được qua tag git `legacy-v1`.

Chiến lược mới được chọn qua một **Bake-off** các Candidate đơn giản (≤ 4 tham số, dùng chung cho mọi symbol) so với một **Acceptance Gate** cố định:
out-of-sample PF ≥ 1.25, max DD ≤ 15%, ≥ 70% cửa sổ walk-forward có lãi, ≥ 200 lệnh OOS, tính dưới chi phí ×1.5, rồi qua **Final Holdout** 12 tháng (chạy đúng 1 lần).

- Quyết định & thứ tự xây dựng: [`docs/adr/0001-rebuild-strategy-layer-validation-first.md`](docs/adr/0001-rebuild-strategy-layer-validation-first.md)
- Thuật ngữ: [`GLOSSARY.md`](GLOSSARY.md)

## ⚠️ Cảnh báo

- Thư viện `MetaTrader5` cho Python **chỉ chạy trên Windows** (terminal MT5 đang đăng nhập, bật "Algo Trading").
- Mặc định `dry_run=True`. Không giao dịch tiền thật cho tới khi một Candidate qua Acceptance Gate **và** Forward Test trên demo.
- Telegram: đặt biến môi trường `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID`; không ghi secret vào code.

## Cài đặt

```bash
pip install MetaTrader5 pandas numpy certifi
```
