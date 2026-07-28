# Thiết kế: Bộ lọc tin tức (News Filter, #7)

## Bối cảnh

`news.py` hiện là stub (`in_news_blackout` luôn trả `False`), đã được wire sẵn vào
call-site trong `main.py:228` (live) và `backtest.py:175` (backtest), cùng 3 field
config đã tồn tại: `news_filter_enabled` (mặc định `False`), `news_csv`,
`news_buffer_min` (mặc định 15). Việc bị defer trước đây chỉ vì thiếu nguồn dữ liệu
sự kiện lịch sử — không phải vì thiếu điểm cắm trong code.

**Động lực:** liquidity sweep — tín hiệu cốt lõi mà chiến lược SMC dùng để vào lệnh —
rất thường trùng với spike do tin tức mạnh (NFP, CPI, FOMC...), không phải stop-hunt
tổ chức thật. Không lọc tin nghĩa là một phần signal hiện tại có thể đang vô tình
trade theo noise tin tức. Đây là gap có giá trị sửa cao, rủi ro thấp so với các đề
xuất khác (chỉ báo lagging, correlation filter...).

**Nguyên tắc bắt buộc, theo đúng bài học từ filter #6 (premium/discount):** implement
xong, đo hiệu quả thật bằng backtest-tuning (full-period + H1/H2), chỉ bật mặc định
(`news_filter_enabled=True`) nếu chứng minh được cải thiện rõ ràng. Một filter "nghe
hợp lý về lý thuyết" từng làm giảm hiệu năng (#6 giảm PF EURUSD 1.24→0.88) — không có
gì đảm bảo filter tin sẽ khác, phải đo trước khi tin.

## Kiến trúc & luồng dữ liệu

```
[fetch_forexfactory_calendar.py]  (script mới, chạy độc lập, không phải phần lõi chiến lược)
        │
        │  --backfill: lặp qua các tuần trong quá khứ (khớp giai đoạn backtest 2 năm)
        │  --refresh:  lấy tuần hiện tại/tới, APPEND + dedupe vào CSV đang có
        ▼
   data/news_calendar.csv   (cột: time [ISO UTC], currency, impact)
        │
        ▼
   news.py::in_news_blackout(ts, cfg)   ← đã wire sẵn, KHÔNG đổi call-site
        │  load CSV (cache theo path), lọc impact=high + currency ∈ cfg.news_currencies,
        │  kiểm tra ts nằm trong ±cfg.news_buffer_min phút quanh sự kiện nào không
        ▼
   True/False → main.py & backtest.py cùng dùng để CHẶN ENTRY MỚI
                (lệnh đang mở KHÔNG bị động tới — ngoài phạm vi thiết kế này)
```

**Điểm mấu chốt kỹ thuật (timezone):** `ts`/`now` truyền vào `in_news_blackout` là
**giờ SERVER (GMT+0)** — xác nhận tại `main.py:207` (`client.server_time()`) và dùng
nhất quán trong `backtest.py`. ForexFactory trả timestamp theo múi giờ riêng (thường
EST/EDT có DST) — script backfill/refresh **phải chuẩn hoá về UTC** trước khi ghi
CSV. Sai lệch múi giờ ở đây sẽ làm hỏng cả backtest lẫn live một cách âm thầm (blackout
lệch giờ so với nến thật), nên đây là điểm cần test riêng, kỹ.

## Component 1: `fetch_forexfactory_calendar.py`

File mới ở gốc repo, ngang hàng `backtest.py` — là tool vận hành độc lập, tách biệt
khỏi phần lõi chiến lược, giống cách `news.py` đã tách riêng khỏi `strategy.py`.

Hai chế độ chạy, cùng 1 script, khác cờ CLI:

```bash
# Backfill lịch sử 1 lần (khớp giai đoạn backtest 2024-06 → nay, ~110 tuần)
python fetch_forexfactory_calendar.py --backfill --weeks 110 --out data/news_calendar.csv

# Refresh định kỳ cho live (chạy tay, ví dụ mỗi Chủ nhật)
python fetch_forexfactory_calendar.py --refresh --out data/news_calendar.csv
```

Logic:
- Gọi endpoint JSON tuần không chính thức của ForexFactory
  (`nfs.faireconomy.media/ff_calendar_thisweek.json` dùng được cho tuần hiện tại).
  **Rủi ro đã biết:** endpoint này có thể KHÔNG hỗ trợ truy vấn tuần quá khứ xa (chỉ
  thiết kế cho tuần hiện tại/kế tiếp). Việc xác nhận khả năng thực tế sẽ làm ngay khi
  bắt đầu code (spike nhỏ, không phải giả định trong spec này). Nếu endpoint không
  đáp ứng được backfill 2 năm, phương án dự phòng là dùng một dataset lịch sử cộng
  đồng (GitHub) **chỉ cho phần backfill một lần**, trong khi refresh định kỳ cho live
  vẫn dùng endpoint tuần hiện tại của FF. Quyết định cụ thể sẽ báo lại nếu phải đổi
  hướng so với spec.
- Parse mỗi sự kiện: `time` (chuyển đổi tz → UTC), `currency`, `impact` (chuẩn hoá về
  `high`/`medium`/`low`).
- Ghi CSV theo đúng format đã định nghĩa sẵn trong docstring `news.py`:
  `time,currency,impact` (time ISO UTC).
- Chế độ `--refresh`: đọc CSV cũ, append sự kiện mới, dedupe theo `(time, currency)`
  trước khi ghi lại — an toàn khi chạy lại nhiều lần / chạy lỡ tay 2 lần.

## Component 2: `news.py::in_news_blackout`

```python
import pandas as pd

def _load_calendar(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    return df[df["impact"] == "high"]

def in_news_blackout(ts, cfg) -> bool:
    if not getattr(cfg, "news_filter_enabled", False):
        return False
    df = _load_calendar(cfg.news_csv)          # cache theo path, xem Error Handling
    relevant = df[df["currency"].isin(cfg.news_currencies)]
    window = pd.Timedelta(minutes=cfg.news_buffer_min)
    return bool(((relevant["time"] - ts).abs() <= window).any())
```

- Giữ nguyên chữ ký hàm `in_news_blackout(ts, cfg) -> bool` — không đổi call-site.
- Cache theo đường dẫn CSV, tránh load lại mỗi tick/mỗi bar. Cho live cần cơ chế
  refresh cache định kỳ (kiểm tra mtime file, reload nếu đổi) để không cần restart
  bot sau khi chạy `--refresh` cuối tuần. Backtest chỉ cần load 1 lần (file tĩnh
  suốt run, đảm bảo tái lập kết quả).
- Thay đổi hành vi so với stub hiện tại: khi `news_filter_enabled=True` mà
  `news_csv` không tồn tại/đọc lỗi → **raise lỗi ngay lúc khởi động** (fail-fast),
  KHÔNG lặng lẽ coi như không có tin như bản stub cũ — vì giờ có filter thật, im
  lặng bỏ qua tin nguy hiểm hơn crash sớm và dễ phát hiện.

## Component 3: `config.py`

Field đã có sẵn: `news_filter_enabled: bool = False`, `news_csv: str = ""`,
`news_buffer_min: int = 15`.

Thêm mới:
```python
news_currencies: tuple = ("USD", "XAU")   # default cho gold
```
EURUSD override: `news_currencies=("USD", "EUR")`.

Cả hai symbol **giữ `news_filter_enabled=False`** mặc định cho tới khi backtest-tuning
chứng minh cải thiện (xem Kiểm chứng bên dưới).

## Kiểm chứng (bắt buộc trước khi bật mặc định)

Dùng skill **backtest-tuning**, sweep:
- `news_buffer_min` ∈ {10, 15, 30, 60}
- `news_filter_enabled` ∈ {True, False}

trên cả hai symbol (XAUUSDm, EURUSDm), so sánh PF/WR/CAGR/MaxDD **full-period VÀ
H1/H2** (theo đúng thói quen đã dùng để phát hiện edge decay trong các lần tuning
trước) — vì số lệnh sẽ giảm khi bật filter, cần đảm bảo PF/CAGR bù lại đủ, không chỉ
"trông sạch hơn". Nếu filter không cải thiện rõ ràng hoặc làm giảm số lệnh quá nhiều
mà không bù lại PF, giữ `news_filter_enabled=False` mặc định — đúng tinh thần bài học
từ filter #6 (premium/discount).

## Error handling
- CSV thiếu/hỏng khi `news_filter_enabled=True` → raise lỗi rõ ràng lúc load, không
  fallback âm thầm.
- Backtest luôn dùng snapshot CSV tĩnh trong suốt 1 run (không tự refresh giữa
  chừng) → đảm bảo tái lập kết quả.
- Live: nếu `--refresh` không chạy đúng lịch (CSV cũ dần) → ghi log cảnh báo nếu file
  cũ hơn N ngày (ví dụ >10 ngày), để người vận hành biết cần refresh thủ công. Không
  tự động hoá việc refresh trong phạm vi thiết kế này.

## Ngoài phạm vi (out of scope)
- Không xử lý lệnh đang mở khi sắp có tin (không đóng sớm, không siết SL) — chỉ chặn
  entry mới, giữ đúng phạm vi ban đầu của #7.
- Không lọc impact `medium`/`low` — chỉ `high`.
- Không tự động hoá refresh CSV cho live (cron/scheduler) — chạy tay theo nhu cầu.
- Không tích hợp API trả phí (TradingEconomics/Finnhub...) — chỉ cân nhắc nếu
  ForexFactory chứng minh giá trị và cần độ ổn định cao hơn cho vận hành lâu dài.
