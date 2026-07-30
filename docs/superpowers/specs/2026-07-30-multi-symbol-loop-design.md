# Thiết kế: Gộp nhiều symbol vào 1 tiến trình `main.py`

## Bối cảnh

Hiện tại mỗi symbol chạy `main.py` trong 1 tiến trình Python riêng
(`python main.py --symbol XAUUSDm`, `python main.py --symbol EURUSDm`), mỗi tiến
trình giữ 1 `MT5Client`, 1 `TradeJournal`, 1 `RiskGuard`, và state chống re-entry
(cooldown, sweep cuối, đếm lệnh/ngày) là biến local trong vòng lặp `while True` —
tất cả đều theo symbol (`main.py:182-311`).

Kết nối MT5 (`mt5.initialize()`) là **global cho cả process**, không phải per-client
(`mt5_client.py:28-50`: `MT5Client` chỉ lưu `symbol`/`magic`/`deviation`, mọi thao
tác đều gọi hàm module-level `mt5.*`). Vì vậy nhiều `MT5Client` — mỗi cái ứng với
1 symbol — hoàn toàn dùng chung được 1 kết nối trong cùng 1 tiến trình.

**Động lực:** sau khi thêm GBPUSDm (bên cạnh XAUUSDm, EURUSDm đã có), người dùng
muốn chạy 1 lệnh duy nhất để bot theo dõi và vào lệnh đồng thời cho EURUSDm +
GBPUSDm (và tổng quát hơn — bất kỳ tập symbol nào đã đăng ký trong `CONFIGS`),
thay vì phải mở nhiều cửa sổ terminal chạy nhiều tiến trình riêng.

## Quyết định đã chốt với người dùng

- **Risk management**: giữ **RIÊNG theo từng symbol** — mỗi symbol vẫn có
  `RiskGuard` độc lập, tính daily-loss/portfolio-heat trên % balance của config
  symbol đó. Không gộp thành 1 ngân sách rủi ro chung toàn account. `risk.py`
  KHÔNG đổi.
- **Phạm vi CLI**: tổng quát — cho phép chọn bất kỳ tập symbol nào đã đăng ký
  trong `CONFIGS` qua `--symbols a,b,c`, không cố định cứng EURUSDm+GBPUSDm.
- **Xử lý lỗi khởi động**: nếu 1 symbol không kết nối/chọn được trên broker
  (`symbol_select` thất bại), log cảnh báo và **bỏ symbol đó**, tiếp tục chạy với
  các symbol còn lại kết nối được. Chỉ dừng hẳn nếu KHÔNG symbol nào kết nối được.

## Kiến trúc: vòng lặp tuần tự, 1 process, 1 thread

**Phương án chọn**: gộp thành 1 vòng lặp `while True` duy nhất trong `main.py`,
mỗi tick xử lý tuần tự từng symbol, rồi sleep 1 lần.

**Đã cân nhắc và loại**:
- *Multi-process (launcher spawn `main.py --symbol X` cho từng symbol)*: giữ
  nguyên `main.py`, nhưng phải tự quản lý start/stop/log của N process con, và
  tốn N kết nối MT5 riêng biệt thay vì dùng chung 1 connection sẵn có.
- *Multi-thread (mỗi symbol 1 thread, dùng lại nguyên vòng lặp cũ)*: thư viện
  `MetaTrader5` không được tài liệu hoá là thread-safe — gọi đồng thời từ nhiều
  thread có rủi ro race condition khi giao tiếp với terminal (IPC). Không dùng.

Vòng lặp tuần tự phù hợp vì: `poll_seconds` mặc định 30s trong khi xử lý 1 symbol
(vài lệnh gọi MT5 + phân tích SMC) tốn cỡ vài trăm ms — với 2-3 symbol, tổng thời
gian xử lý mỗi tick vẫn nhỏ hơn nhiều so với chu kỳ poll.

## Luồng dữ liệu / thay đổi cụ thể

### 1. CLI (`main.py:__main__`)

```python
p.add_argument("--symbol", default=None,
               help="[Tương thích ngược] 1 symbol duy nhất: XAUUSDm | EURUSDm | GBPUSDm.")
p.add_argument("--symbols", default=None,
               help="Danh sách symbol, phân tách bằng dấu phẩy: vd EURUSDm,GBPUSDm. "
                    "Chấp nhận alias (gold/eurusd/gbpusd).")
args = p.parse_args()
if args.symbols:
    names = [s.strip() for s in args.symbols.split(",") if s.strip()]
elif args.symbol:
    names = [args.symbol]
else:
    names = ["XAUUSDm"]   # mặc định giữ nguyên hành vi cũ
main(names)
```

`--symbol XAUUSDm` (1 symbol) tiếp tục chạy giống hệt hiện tại — không đổi hành
vi observable cho cách dùng cũ. `--symbols` mới là đường mở rộng.

### 2. State per-symbol — dataclass mới trong `main.py`

Gom các biến local hiện đang lặp lại ý nghĩa cho từng symbol
(`main.py:198-204`) vào 1 dataclass:

```python
@dataclass
class SymbolRunner:
    cfg: TradingConfig
    client: MT5Client
    journal: TradeJournal
    guard: RiskGuard
    notifier: TelegramNotifier
    symbol_info: dict
    # state chống re-entry / cooldown (mirror backtest.py để live == backtest)
    last_ltf_bar: pd.Timestamp | None = None
    last_sweep_level: float | None = None
    bars_since_exit: int = 10 ** 9
    trades_today: int = 0
    trade_day: date | None = None
    had_open: bool = False
```

### 3. Khởi tạo (thay `main(cfg)` bằng `main(symbol_names)`)

```python
def build_runner(name: str) -> SymbolRunner | None:
    cfg = get_config(name)
    client = MT5Client(cfg.symbol, cfg.magic_number, cfg.deviation)
    if not client.connect():
        log.error(f"⛔ Bỏ qua {cfg.symbol} — không kết nối/chọn được trên broker.")
        return None
    symbol_info = client.get_symbol_info()
    guard = RiskGuard(cfg.max_daily_loss_pct, cfg.portfolio_heat_pct)
    journal = TradeJournal(cfg.symbol)
    notifier = TelegramNotifier.from_config(cfg)
    log_open_positions(client, journal)
    return SymbolRunner(cfg, client, journal, guard, notifier, symbol_info)

def main(symbol_names: list[str]):
    runners = [r for r in (build_runner(n) for n in symbol_names) if r is not None]
    if not runners:
        raise SystemExit("Không symbol nào kết nối được — dừng bot.")
    poll_seconds = min(r.cfg.poll_seconds for r in runners)
    log.info(f"🚀 Bot khởi động | {len(runners)} symbol: "
             f"{', '.join(r.cfg.symbol for r in runners)} | poll {poll_seconds}s")
    try:
        while True:
            for runner in runners:
                try:
                    process_symbol(runner)
                except Exception:
                    log.exception(f"⚠️ Lỗi xử lý {runner.cfg.symbol} — bỏ qua tick này.")
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        log.info("Dừng bot theo yêu cầu.")
    finally:
        for runner in runners:
            runner.client.shutdown()
```

Lưu ý `client.shutdown()` gọi `mt5.shutdown()` — dùng chung 1 connection nên chỉ
cần gọi 1 lần là đủ về mặt kỹ thuật, nhưng gọi lặp lại cho từng runner là an toàn
(idempotent) và giữ code đơn giản, không cần theo dõi "ai gọi trước".

### 4. Thân vòng lặp cũ → `process_symbol(runner)`

Toàn bộ logic từ `main.py:206-306` (session gate → new-bar gate → analyze →
order) chuyển vào hàm `process_symbol(runner: SymbolRunner) -> None`, đọc/ghi
qua `runner.cfg`, `runner.client`, v.v. thay vì biến đóng (closure) trên `cfg`.

**Thay đổi bắt buộc về luồng điều khiển**: các câu `time.sleep(cfg.poll_seconds); continue`
hiện dùng để "bỏ qua vòng lặp, chờ tick sau" — trong `process_symbol` không còn
tự sleep (sleep chuyển ra ngoài vòng `for`), nên các điểm đó đổi thành `return`
đơn giản (kết thúc xử lý symbol này cho tick hiện tại, sang symbol kế tiếp ngay,
không chờ).

Các nhánh cần đổi (đối chiếu theo dòng hiện tại):
- `guard.daily_loss_exceeded` (`main.py:216-219`): log cảnh báo, `return` (bỏ
  qua toàn bộ phần tìm tín hiệu tick này cho symbol này — KHÔNG `time.sleep(300)`
  chặn cả bot, vì symbol khác vẫn cần chạy đúng nhịp).
- `not in_session` (`221-223`), `pre_weekend_guard` (`224-226`),
  `in_news_blackout` (`227-230`): `return`.
- `current_bar == last_ltf_bar` (`236-238`): `return`.
- `n_open >= max_open_positions or n_pending > 0` (`254-258`): `return`.
- `bars_since_exit < cooldown_bars or trades_today >= max_trades_per_day`
  (`259-262`): `return`.
- Nhánh `plan` bị bỏ qua vì trùng sweep (`270-274`): `return` sau log (thay vì
  `continue`).
- Cuối hàm: không còn `time.sleep(cfg.poll_seconds)` — bỏ hẳn dòng đó, việc sleep
  đã chuyển ra vòng `for` ở `main()`.

`manage_open_positions` và `reconcile_journal` (`211-214`) vẫn chạy đầu mỗi lần
gọi `process_symbol`, giữ đúng hành vi cũ: quản lý lệnh mở/đối chiếu chạy mỗi
tick kể cả ngoài session, không bị early-return của session gate chặn.

### 5. Các hàm khác — không đổi chữ ký

`in_session`, `pre_weekend_guard`, `manage_open_positions`, `log_open_positions`,
`reconcile_journal`, `resolve_paper_trades` đã nhận `cfg`/`client`/`journal` làm
tham số tường minh — không đụng vào, chỉ gọi qua `runner.cfg`/`runner.client`/
`runner.journal` từ `process_symbol`.

## Nhật ký / thông báo — không cần đổi

- `TradeJournal(cfg.symbol)` đã đặt tên file theo symbol (`journal_<symbol>.csv`
  hay tương tự) → nhiều symbol ghi nhật ký riêng, không đụng nhau.
- `TelegramNotifier.notify_*` đã nhận `cfg.symbol` làm tham số đầu
  (`notifier.notify_signal(cfg.symbol, plan)` — `main.py:283`) → tin nhắn Telegram
  đã phân biệt symbol, không cần đổi.
- `bot.log` (file log chung, `main.py:24`) sẽ chứa log xen kẽ của nhiều symbol
  trong cùng 1 file — chấp nhận được vì mỗi dòng log hiện tại đã có
  `cfg.symbol`/tên logger, không mơ hồ khi đọc.

## Error handling

- Lỗi kết nối/`symbol_select` lúc khởi động cho 1 symbol → bỏ symbol đó, tiếp
  tục các symbol còn lại (đã chốt ở trên). Nếu tất cả đều lỗi → `SystemExit`.
- Exception bất kỳ trong `process_symbol` khi bot đang chạy (vd. MT5 tạm mất kết
  nối, lỗi mạng khi lấy rates) → log đầy đủ traceback (`log.exception`), bỏ qua
  tick này cho symbol đó, KHÔNG crash toàn bộ tiến trình, KHÔNG ảnh hưởng symbol
  khác trong cùng tick.

## Kiểm chứng

Đây là thay đổi kiến trúc thuần (luồng điều khiển/tổ chức code), **không đổi
logic tín hiệu hay quản lý lệnh** — `strategy.analyze`, `risk.py`, SMC modules
đều không đụng tới. Vì vậy không cần chạy lại `backtest-tuning` (không có tham
số chiến lược nào thay đổi); việc kiểm chứng tập trung vào:

- Chạy `--symbol XAUUSDm` (1 symbol, cú pháp cũ) sau khi refactor → xác nhận
  hành vi log/journal/order giống hệt bản trước refactor (regression check thủ
  công, so log output).
- Chạy `--symbols EURUSDm,GBPUSDm` ở `dry_run=True` (mặc định) một thời gian
  ngắn → xác nhận cả 2 symbol cùng nhận diện tín hiệu, cùng ghi journal riêng,
  cooldown/trần lệnh/ngày không lẫn giữa 2 symbol.

## Ngoài phạm vi (out of scope)

- Không gộp risk management thành ngân sách chung toàn account (đã chốt: giữ
  riêng theo symbol).
- Không đổi `backtest.py` — công cụ backtest vẫn chạy từng symbol độc lập
  (`--symbol` đơn), vì mục tiêu multi-symbol chỉ áp dụng cho vận hành live/demo,
  không phải quy trình tuning.
- Không thêm cơ chế ưu tiên/giới hạn tổng số lệnh mở đồng thời giữa các symbol
  (mỗi symbol vẫn dùng độc lập `max_open_positions` của chính nó).
- Không thay đổi `mt5_client.py` — không cần, `MT5Client` đã đủ generic (chỉ lưu
  `symbol`/`magic`/`deviation`, mọi state kết nối là global trong module `mt5`).
