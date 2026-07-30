# Multi-symbol main.py loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho phép `main.py` chạy nhiều symbol (vd EURUSDm + GBPUSDm) đồng thời trong 1 tiến trình/1 lệnh, thay vì 1 process/symbol như hiện tại.

**Architecture:** Gộp thành 1 vòng lặp `while True` tuần tự, đơn luồng. Mỗi symbol có state riêng gói trong dataclass `SymbolRunner` (client MT5, journal, risk guard, cooldown state — tất cả độc lập theo symbol). Mỗi tick, vòng lặp ngoài lần lượt gọi `process_symbol(runner)` cho từng symbol rồi sleep 1 lần. Xem đầy đủ lý do kiến trúc, các phương án đã loại (multi-process, multi-thread), và quyết định sản phẩm (risk riêng theo symbol, CLI tổng quát, bỏ-qua-symbol-lỗi-lúc-khởi-động) tại spec: `docs/superpowers/specs/2026-07-30-multi-symbol-loop-design.md`.

**Tech Stack:** Python, `MetaTrader5` (Windows-only, kết nối global qua `mt5.initialize()`), `dataclasses`, `pandas`. Không có framework test trong repo này (`pytest` không được dùng — xem CLAUDE.md: "There is no test suite, linter, or build step"). Vì vậy các bước "test" trong plan này dùng đúng quy ước đã có của repo: (a) script Python một-lần (`python -c`/heredoc, KHÔNG commit) để kiểm chứng logic thuần không cần MT5 thật, và (b) chạy `main.py` thật (có MT5 đã kết nối, `dry_run=True` mặc định) trong thời gian giới hạn rồi đọc `bot.log`.

---

## Bối cảnh file hiện tại

Toàn bộ thay đổi nằm trong **`main.py`** (16KB, ~319 dòng). Các hàm không đổi
chữ ký: `in_session`, `pre_weekend_guard`, `manage_open_positions`,
`log_open_positions`, `reconcile_journal`, `resolve_paper_trades`. Phần thay
đổi: imports, thêm dataclass `SymbolRunner`, thêm `build_runner()`, tách thân
vòng lặp cũ (`main.py:206-306`) thành `process_symbol()`, viết lại `main()`,
sửa khối `__main__`.

**Ghi chú quan trọng khi đọc plan này**: quá trình viết plan phát hiện thêm 1
điểm spec chưa nêu — vài dòng log tín hiệu (`🎯 TÍN HIỆU`, `⏭️ Bỏ qua`, `⛔ Vượt
portfolio heat cap`) trong code GỐC **không có tên symbol** trong message (chỉ
dựa vào việc mỗi symbol chạy 1 process/1 file log riêng để phân biệt). Khi gộp
nhiều symbol vào chung 1 `bot.log`, các dòng này cần thêm `[symbol]` để không
mơ hồ — chi tiết ở Task 3.

---

### Task 1: Thêm import `date` + dataclass `SymbolRunner`

**Files:**
- Modify: `main.py:1-19` (imports), chèn dataclass mới ngay sau `TF_MINUTES`
  (khoảng dòng 29-30 hiện tại)

- [ ] **Step 1: Sửa khối import**

Thay:
```python
import time
import logging
import argparse
from datetime import datetime, timedelta
import pandas as pd
from config import get_config
from mt5_client import MT5Client
from strategy import analyze
from risk import RiskGuard, PositionState, manage_tick, manage_step
from journal import TradeJournal
from notifier import TelegramNotifier
from news import in_news_blackout
```
bằng:
```python
import time
import logging
import argparse
from dataclasses import dataclass
from datetime import datetime, date, timedelta
import pandas as pd
from config import get_config, TradingConfig
from mt5_client import MT5Client
from strategy import analyze
from risk import RiskGuard, PositionState, manage_tick, manage_step
from journal import TradeJournal
from notifier import TelegramNotifier
from news import in_news_blackout
```

- [ ] **Step 2: Cập nhật docstring đầu file** (mô tả cách chạy multi-symbol)

Thay:
```python
"""
Vòng lặp giao dịch live/demo.
Chạy vàng:   python main.py --symbol XAUUSDm   (mặc định)
Chạy EURUSD: python main.py --symbol EURUSDm
Mỗi symbol dùng config RIÊNG trong config.py (magic_number riêng → chạy song song được).
Mặc định dry_run trong config.py — kiểm tra trước khi chạy tiền thật.
"""
```
bằng:
```python
"""
Vòng lặp giao dịch live/demo — hỗ trợ chạy 1 HOẶC NHIỀU symbol trong CÙNG 1 tiến trình.
Chạy 1 symbol (tương thích ngược): python main.py --symbol XAUUSDm   (mặc định)
Chạy nhiều symbol cùng lúc:        python main.py --symbols EURUSDm,GBPUSDm
Mỗi symbol dùng config RIÊNG trong config.py (magic_number riêng), risk/journal
độc lập theo symbol — chỉ dùng CHUNG 1 kết nối MT5 (global) và 1 vòng lặp tuần tự.
Mặc định dry_run trong config.py — kiểm tra trước khi chạy tiền thật.
"""
```

- [ ] **Step 3: Thêm dataclass `SymbolRunner`**

Chèn ngay sau khối `TF_MINUTES = {...}` (trước `def in_session(...)`):

```python
@dataclass
class SymbolRunner:
    """Trạng thái độc lập cho 1 symbol khi nhiều symbol chạy chung 1 vòng lặp."""
    cfg: TradingConfig
    client: MT5Client
    journal: TradeJournal
    guard: RiskGuard
    notifier: TelegramNotifier
    symbol_info: dict
    # Trạng thái chống re-entry / cooldown (khớp backtest.py để live == backtest)
    last_ltf_bar: pd.Timestamp | None = None
    last_sweep_level: float | None = None
    bars_since_exit: int = 10 ** 9
    trades_today: int = 0
    trade_day: date | None = None
    had_open: bool = False
    # Throttle log cảnh báo daily-loss (KHÔNG dùng để sleep/chặn — xem process_symbol)
    daily_loss_warn_until: datetime | None = None
```

- [ ] **Step 4: Kiểm tra cú pháp**

Run: `python -c "import main"`
Expected: không lỗi (import thành công, không traceback). Lệnh này sẽ tạo/ghi
`bot.log` do `logging.basicConfig` chạy ở module level — bình thường.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "Add SymbolRunner dataclass for multi-symbol main loop state"
```

---

### Task 2: Thêm `build_runner()` — kết nối + khởi tạo state cho 1 symbol

**Files:**
- Modify: `main.py`, chèn hàm mới ngay TRƯỚC `def main(cfg):` (giữ `def main(cfg):`
  cũ nguyên vẹn — task này chỉ ADD, không xoá gì, để file vẫn chạy được y hệt
  hiện tại giữa các task)

- [ ] **Step 1: Thêm hàm `build_runner`**

```python
def build_runner(name: str) -> SymbolRunner | None:
    """Kết nối MT5 + khởi tạo toàn bộ state cho 1 symbol. Trả về None (KHÔNG raise)
    nếu riêng symbol này không chọn được trên broker — để các symbol khác trong
    cùng lệnh vẫn chạy được (quyết định đã chốt trong spec)."""
    cfg = get_config(name)
    client = MT5Client(cfg.symbol, cfg.magic_number, cfg.deviation)
    if not client.connect():
        log.error(f"⛔ Bỏ qua {cfg.symbol} — không kết nối/chọn được trên broker.")
        return None
    symbol_info = client.get_symbol_info()
    guard = RiskGuard(cfg.max_daily_loss_pct, cfg.portfolio_heat_pct)
    journal = TradeJournal(cfg.symbol)
    notifier = TelegramNotifier.from_config(cfg)
    log.info(f"🚀 {cfg.symbol} sẵn sàng | HTF {cfg.htf} → LTF {cfg.ltf} | "
             f"risk {cfg.risk_per_trade_pct}%/lệnh | dry_run={cfg.dry_run}")
    log.info(f"📁 [{cfg.symbol}] Nhật ký: {journal.path.resolve()} | Telegram: "
             f"{'bật' if notifier.enabled else 'tắt'}")
    log_open_positions(client, journal)
    return SymbolRunner(cfg=cfg, client=client, journal=journal, guard=guard,
                         notifier=notifier, symbol_info=symbol_info)
```

Lưu ý: `client.connect()` (`mt5_client.py:35-50`) raise `RuntimeError` nếu thư
viện `MetaTrader5` không cài/không chạy được (`MT5_AVAILABLE=False`) — đây là lỗi
môi trường ảnh hưởng MỌI symbol như nhau, nên **không** bắt lỗi này ở đây, để nó
lan lên và dừng chương trình ngay (đúng hành vi cũ). Chỉ trường hợp
`mt5.initialize()`/`symbol_select()` trả `False` (lỗi riêng symbol/broker) mới
khiến `connect()` trả `False` và bị xử lý ở đây.

- [ ] **Step 2: Kiểm tra cú pháp**

Run: `python -c "import main"`
Expected: không lỗi.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "Add build_runner() to connect+init state for one symbol"
```

---

### Task 3: Tách thân vòng lặp cũ thành `process_symbol(runner)`

**Files:**
- Modify: `main.py`, chèn hàm mới ngay SAU `build_runner` (TRƯỚC `def main(cfg):`
  cũ, vẫn giữ `main(cfg)` cũ nguyên vẹn ở bước này — additive, `process_symbol`
  chưa được gọi ở đâu cả cho tới Task 4)

- [ ] **Step 1: Thêm hàm `process_symbol`**

```python
def process_symbol(runner: SymbolRunner) -> None:
    """Một lượt xử lý cho 1 symbol: quản lý lệnh mở → các cổng chặn (daily-loss/
    session/weekend/tin tức) → tìm tín hiệu mới nếu có nến LTF mới đóng. KHÔNG tự
    sleep — vòng lặp ngoài (main()) sleep 1 lần sau khi xử lý xong MỌI symbol
    trong tick, để 1 symbol không làm nghẽn nhịp của symbol khác."""
    cfg, client, journal = runner.cfg, runner.client, runner.journal
    guard, notifier, symbol_info = runner.guard, runner.notifier, runner.symbol_info

    now = client.server_time()   # giờ SERVER, không dùng datetime.now()
    balance = client.get_balance()
    guard.update_day(now.date(), balance)

    # Quản lý lệnh mở + đối chiếu nhật ký chạy MỖI TICK, kể cả ngoài session hay
    # đang bị daily-loss lock (xem ghi chú THAY ĐỔI HÀNH VI ngay dưới).
    manage_open_positions(client, cfg, journal, symbol_info)
    reconcile_journal(client, journal, notifier)

    if guard.daily_loss_exceeded(balance):
        # THAY ĐỔI HÀNH VI có chủ đích so với bản 1-symbol-1-process cũ (vốn
        # `time.sleep(300); continue` chặn CẢ manage_open_positions/reconcile_journal
        # trong 5 phút — main.py:216-219 bản gốc). Trong vòng lặp multi-symbol dùng
        # chung, sleep ở đây sẽ chặn LUÔN các symbol khác nên KHÔNG được sleep.
        # Quản lý lệnh vẫn chạy đều mỗi tick (đã chạy ở trên); chỉ throttle LOG
        # cảnh báo còn ~300s/lần để không spam.
        if runner.daily_loss_warn_until is None or now >= runner.daily_loss_warn_until:
            log.warning(f"⛔ [{cfg.symbol}] Chạm giới hạn lỗ ngày — tạm dừng tìm tín hiệu đến ngày mai.")
            runner.daily_loss_warn_until = now + timedelta(seconds=300)
        return

    if not in_session(now, cfg):
        return
    if pre_weekend_guard(now, cfg):
        return
    # #7 — Cấm vào lệnh quanh tin mạnh (news_filter_enabled=False mặc định).
    if in_news_blackout(now, cfg):
        return

    ltf_df = client.get_rates(cfg.ltf, cfg.ltf_bars)
    current_bar = ltf_df.index[-1]

    # Chỉ phân tích khi có nến LTF mới đóng (dùng nến đã đóng, bỏ nến đang chạy)
    if current_bar == runner.last_ltf_bar:
        return
    runner.last_ltf_bar = current_bar

    # Xử lý kết quả lệnh paper (dry_run) trên các nến vừa đóng
    resolve_paper_trades(journal, ltf_df, cfg, symbol_info)

    # ── Cập nhật trạng thái theo nến mới (cooldown, đếm lệnh/ngày) ──
    n_open = len(client.open_positions())
    if runner.had_open and n_open == 0:      # lệnh vừa đóng → bắt đầu cooldown
        runner.bars_since_exit = 0
    runner.had_open = n_open > 0
    runner.bars_since_exit += 1
    if now.date() != runner.trade_day:
        runner.trade_day = now.date()
        runner.trades_today = 0

    # #4: đang có lệnh mở HOẶC lệnh LIMIT chờ khớp → không đặt thêm.
    n_pending = len(client.pending_orders())
    if n_open >= cfg.max_open_positions or n_pending > 0:
        return
    # Cooldown sau lệnh + trần lệnh/ngày (khớp backtest.py)
    if runner.bars_since_exit < cfg.cooldown_bars or runner.trades_today >= cfg.max_trades_per_day:
        return

    htf_df = client.get_rates(cfg.htf, cfg.htf_bars)
    # Bỏ nến đang chạy để tránh repaint
    plan = analyze(htf_df.iloc[:-1], ltf_df.iloc[:-1], cfg, balance, symbol_info)

    if not plan:
        return

    # Chống re-entry: mỗi cú sweep chỉ giao dịch 1 lần
    if cfg.one_trade_per_sweep and plan.sweep_level is not None \
            and plan.sweep_level == runner.last_sweep_level:
        log.info(f"⏭️  [{cfg.symbol}] Bỏ qua — đã giao dịch sweep {plan.sweep_level} rồi.")
        return

    open_risk_pct = n_open * cfg.risk_per_trade_pct
    if guard.heat_exceeded(open_risk_pct, cfg.risk_per_trade_pct):
        log.warning(f"⛔ [{cfg.symbol}] Vượt portfolio heat cap — bỏ qua tín hiệu.")
        return

    log.info(f"🎯 [{cfg.symbol}] TÍN HIỆU: {plan.direction.upper()} @ {plan.entry} | "
             f"SL {plan.sl} | TP {plan.tp} | lot {plan.lot} | "
             f"R:R {plan.rr} | risk ${plan.risk_amount} | {plan.reason}")
    if not cfg.dry_run:
        notifier.notify_signal(cfg.symbol, plan)
    if cfg.dry_run:
        log.info(f"[{cfg.symbol}] (dry_run — không đặt lệnh thật)")
        journal.record_open(f"paper-{int(now.timestamp())}", plan.direction,
                            plan.entry, plan.sl, plan.tp, plan.lot, plan.rr,
                            plan.risk_amount, plan.reason, mode="paper", created=now)
    else:
        # #4: đặt LIMIT nghỉ tại biên vùng, broker tự hết hạn sau
        # entry_expiry_bars nến nếu giá không hồi về khớp.
        expiry = now + timedelta(minutes=TF_MINUTES.get(cfg.ltf, 15)
                                 * cfg.entry_expiry_bars)
        ticket = client.pending_order(plan.direction, plan.lot, plan.entry,
                                      plan.sl, plan.tp, expiry,
                                      comment=f"SMC RR{plan.rr}")
        if ticket:
            # Ticket lệnh chờ == ticket position khi khớp → journal khớp luôn.
            journal.record_open(ticket, plan.direction, plan.entry, plan.sl,
                                plan.tp, plan.lot, plan.rr, plan.risk_amount,
                                plan.reason, mode="live", created=now)
            notifier.notify_opened(cfg.symbol, plan)
    runner.last_sweep_level = plan.sweep_level
    runner.trades_today += 1
```

- [ ] **Step 2: Kiểm tra cú pháp**

Run: `python -c "import main"`
Expected: không lỗi.

- [ ] **Step 3: Kiểm chứng riêng nhánh throttle daily-loss (KHÔNG cần MT5 thật)**

Đây là logic đã bị review vòng 1 của spec bắt lỗi (hành vi sleep 300s bị bỏ sót
âm thầm) — viết 1 script độc lập (KHÔNG commit, xoá sau khi chạy) để xác nhận
`process_symbol` chỉ log cảnh báo ~1 lần/300s dù bị gọi liên tục, bằng cách giả
lập toàn bộ dependency (không cần MT5/broker thật):

Tạo file tạm `scratch_verify_throttle.py` ở gốc repo:
```python
from datetime import datetime, timedelta
from unittest.mock import patch
import main as m

class FakeGuard:
    def update_day(self, *a, **k): pass
    def daily_loss_exceeded(self, balance): return True   # luôn "chạm giới hạn"

class FakeClient:
    def __init__(self, t): self.t = t
    def server_time(self): return self.t
    def get_balance(self): return 9000.0

cfg = type("Cfg", (), {"symbol": "TESTUSDm"})()
runner = m.SymbolRunner(cfg=cfg, client=FakeClient(datetime(2026, 1, 1, 10, 0, 0)),
                        journal=None, guard=FakeGuard(), notifier=None,
                        symbol_info={"contract_size": 100.0})

with patch.object(m, "manage_open_positions"), patch.object(m, "reconcile_journal"), \
     patch.object(m.log, "warning") as warn, patch.object(m.time, "sleep") as sleep_mock:
    m.process_symbol(runner)                                    # t=10:00:00 -> warn #1
    runner.client.t = datetime(2026, 1, 1, 10, 0, 10)            # +10s, còn trong 300s
    m.process_symbol(runner)                                     # KHÔNG warn thêm
    runner.client.t = datetime(2026, 1, 1, 10, 5, 1)             # +5p01s, đã qua 300s
    m.process_symbol(runner)                                     # warn #2

assert warn.call_count == 2, f"Kỳ vọng 2 lần warning, thực tế {warn.call_count}"
# Guard cứng chống regression tái xuất hiện time.sleep(300) chặn cả loop (bug bị bắt
# ở review vòng 1 của spec) — patch time.sleep nghĩa là NẾU code có gọi lại, test này
# fail NGAY LẬP TỨC thay vì đứng im 5 phút rồi mới lộ ra.
assert sleep_mock.call_count == 0, \
    f"process_symbol() KHÔNG được tự sleep — phát hiện {sleep_mock.call_count} lần gọi time.sleep()"
print("OK: throttle daily-loss-warn hoạt động đúng (2 lần warn, không phải 3) và không tự sleep")
```

Run: `python scratch_verify_throttle.py`
Expected: in ra `OK: throttle daily-loss-warn hoạt động đúng (2 lần warn, không phải 3) và không tự sleep`,
không traceback/AssertionError. Nếu ai đó lỡ tái đưa `time.sleep(300)` vào nhánh
`daily_loss_exceeded`, script này FAIL NGAY (AssertionError trên `sleep_mock.call_count`)
thay vì chỉ đứng treo 5 phút rồi mới bị nghi ngờ.

Sau khi PASS, xoá file: `rm scratch_verify_throttle.py` (không commit file này).

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "Add process_symbol() extracted from old main loop body"
```

---

### Task 4: Viết lại `main()` để orchestrate nhiều `SymbolRunner`, xoá code cũ

**Files:**
- Modify: `main.py` — XOÁ toàn bộ `def main(cfg): ...` cũ (đoạn từ
  `def main(cfg):` tới hết `client.shutdown()` trước dòng `if __name__`), thay
  bằng `main(symbol_names)` mới dùng `build_runner`/`process_symbol` đã có từ
  Task 2/3.

- [ ] **Step 1: Xoá `def main(cfg):` cũ, thêm `def main(symbol_names):` mới**

Toàn bộ khối cũ (nguyên văn hiện tại, ~dòng 182-310, bắt đầu `def main(cfg):`
kết thúc `client.shutdown()`) bị XOÁ và thay bằng:

```python
def main(symbol_names: list[str]) -> None:
    runners = [r for r in (build_runner(n) for n in symbol_names) if r is not None]
    if not runners:
        raise SystemExit("Không symbol nào kết nối được — dừng bot.")
    poll_seconds = min(r.cfg.poll_seconds for r in runners)
    log.info(f"▶️  Bot khởi động | {len(runners)} symbol: "
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

Ghi chú: `client.shutdown()` gọi `mt5.shutdown()` — kết nối là global nên gọi 1
lần là đủ về kỹ thuật, nhưng gọi lặp lại cho từng runner an toàn (idempotent,
`mt5_client.py:52-54`) và giữ code đơn giản.

**Cảnh báo trạng thái trung gian**: sau commit của task này, `main()` đã đổi
chữ ký thành `main(symbol_names: list[str])`, nhưng khối `if __name__ ==
"__main__":` (Task 5, chưa chạy) vẫn còn gọi `main(get_config(args.symbol))` —
tức truyền 1 `TradingConfig` object thay vì `list[str]`. `python -c "import
main"` ở Step 2 dưới đây PASS bình thường (import không chạy khối
`__main__`), nhưng `python main.py --symbol XAUUSDm` sẽ CRASH ở commit này
(`build_runner` gọi `name.lower()` trên 1 object không phải string). Đây là
trạng thái trung gian CHẤP NHẬN ĐƯỢC trong 1 phiên làm việc liên tục — Task 5
chạy ngay sau đó khớp nối lại CLI — nhưng nếu dừng giữa chừng ở đúng commit
này (vd. `git bisect`, review theo từng commit riêng lẻ), `main.py` chưa chạy
được qua CLI. Không rollback/deploy dừng ở commit của Task 4 một mình.

- [ ] **Step 2: Kiểm tra cú pháp**

Run: `python -c "import main"`
Expected: không lỗi.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "Rewrite main() to orchestrate multiple SymbolRunners in one loop"
```

---

### Task 5: Cập nhật CLI `__main__` cho `--symbol`/`--symbols`

**Files:**
- Modify: `main.py:313-318` (khối `if __name__ == "__main__":`)

- [ ] **Step 1: Sửa khối CLI**

Thay:
```python
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="XAUUSDm",
                   help="Symbol/config để chạy live: XAUUSDm | EURUSDm (alias: gold/eurusd).")
    args = p.parse_args()
    main(get_config(args.symbol))
```
bằng:
```python
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=None,
                   help="[Tương thích ngược] 1 symbol duy nhất: XAUUSDm | EURUSDm | "
                        "GBPUSDm (alias: gold/eurusd/gbpusd).")
    p.add_argument("--symbols", default=None,
                   help="Danh sách symbol, phân tách bằng dấu phẩy — vd EURUSDm,GBPUSDm. "
                        "Chấp nhận alias. Ưu tiên hơn --symbol nếu truyền cả hai.")
    args = p.parse_args()
    if args.symbols:
        names = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.symbol:
        names = [args.symbol]
    else:
        names = ["XAUUSDm"]   # mặc định giữ nguyên hành vi cũ
    main(names)
```

Lưu ý: `get_config()` không còn gọi trực tiếp ở `__main__` nữa — mỗi tên trong
`names` được resolve thành config RIÊNG bên trong `build_runner()` (Task 2),
vì mỗi symbol cần 1 lần gọi `get_config` độc lập (trả về bản copy — xem
`config.py:264-272`).

- [ ] **Step 2: Kiểm tra cú pháp + `--help`**

Run: `python main.py --help`
Expected: in usage message, thấy cả `--symbol` và `--symbols` trong output,
không traceback.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "Update main.py CLI: add --symbols, keep --symbol for backward compat"
```

---

### Task 6: Kiểm chứng thủ công — hồi quy 1 symbol (so với hành vi cũ)

**Files:** không sửa file nào — chỉ chạy `main.py` thật và đọc `bot.log`.
**Yêu cầu môi trường**: Windows, MT5 terminal đã cài, đăng nhập, "Algo Trading"
bật (theo CLAUDE.md — nếu môi trường không đáp ứng, dừng và báo lại, KHÔNG
fabricate kết quả).

- [ ] **Step 1: Chạy 1 symbol bằng cú pháp cũ, giới hạn thời gian**

Run (giới hạn 75 giây — đủ ít nhất 1 tick với `poll_seconds` mặc định 30s):
```bash
timeout 75000  # (dùng tham số timeout của tool Bash, không phải lệnh shell timeout)
python main.py --symbol XAUUSDm
```
(Thực thi bằng cách gọi Bash tool với `command: "python main.py --symbol XAUUSDm"`,
`timeout: 75000`.)

Expected trong output/`bot.log`:
- Dòng `Kết nối OK — Account ...` (từ `mt5_client.py:49`)
- Dòng `🚀 XAUUSDm sẵn sàng | HTF H4 → LTF M15 | risk ...` (từ `build_runner`,
  Task 2 — thay cho dòng `🚀 Bot khởi động | XAUUSDm | ...` của bản CŨ, đây là
  khác biệt CHẤP NHẬN ĐƯỢC vì nội dung tương đương, chỉ đổi chỗ log)
- Dòng `▶️  Bot khởi động | 1 symbol: XAUUSDm | poll 30s` (từ `main()` mới)
- Dòng `📊 Không có lệnh nào đang mở/chờ.` (hoặc liệt kê lệnh nếu có)
- KHÔNG có traceback/Exception nào trong log.

- [ ] **Step 2: Xác nhận không có lỗi**

Đọc `bot.log` (Read tool, `tail`-style bằng `offset`/`limit` nếu file dài):
xác nhận không có dòng `ERROR`/`Traceback` liên quan tới lần chạy vừa rồi.

Không cần fix gì ở bước này nếu output khớp — chỉ ghi nhận PASS/FAIL. Nếu FAIL,
dừng lại, không sang Task 7 cho tới khi tìm ra nguyên nhân (dùng
superpowers:systematic-debugging nếu cần).

---

### Task 7: Kiểm chứng thủ công — 2 symbol chạy chung 1 lệnh

**Files:** không sửa file nào.

- [ ] **Step 1: Chạy 2 symbol cùng lúc, giới hạn thời gian**

Run (Bash tool, `command: "python main.py --symbols EURUSDm,GBPUSDm"`,
`timeout: 100000` — đủ ít nhất 2-3 tick ở poll 30s):

Expected trong output/`bot.log`:
- 2 dòng `Kết nối OK` (1 cho mỗi symbol — vì `connect()` gọi
  `mt5.symbol_select` riêng từng symbol dù cùng 1 kết nối MT5 global)
- 2 dòng `🚀 <symbol> sẵn sàng | ...` — 1 cho EURUSDm, 1 cho GBPUSDm
- Dòng `▶️  Bot khởi động | 2 symbol: EURUSDm, GBPUSDm | poll 30s`
- Log của cả 2 symbol xen kẽ trong CÙNG 1 file (không phải 2 file riêng)
- KHÔNG có traceback/Exception

- [ ] **Step 2: Xác nhận journal tách riêng theo symbol**

Kiểm tra tồn tại 2 file journal riêng biệt (tên theo `TradeJournal(cfg.symbol)`,
xem `journal.py` để biết pattern tên file chính xác — ví dụ
`journal_EURUSDm.csv`/`trades_EURUSDm.csv` và tương ứng cho GBPUSDm). Xác nhận
KHÔNG có dữ liệu của symbol này lẫn vào file của symbol kia.

- [ ] **Step 3: Xác nhận cooldown/trần lệnh/ngày không lẫn giữa 2 symbol**

Nếu trong lúc chạy test có tín hiệu ở 1 trong 2 symbol (không bắt buộc phải có
— nếu không có tín hiệu trong cửa sổ test ngắn, bỏ qua bước này, ghi chú lại là
chưa test được nhánh này), xác nhận log dòng `🎯 [<symbol>] TÍN HIỆU: ...` chỉ
ảnh hưởng `trades_today`/`last_sweep_level`/`bars_since_exit` của ĐÚNG
`SymbolRunner` đó (đọc lại code Task 3 nếu cần đối chiếu — đây là review code,
không phải chạy thêm lệnh).

Nếu Task 6 hoặc Task 7 FAIL ở bất kỳ bước nào: dừng, quay lại các Task 1-5 để
sửa, KHÔNG tiếp tục sang Task 8 cho tới khi PASS.

---

### Task 8: Cập nhật tài liệu — `CLAUDE.md` Commands section

**Files:**
- Modify: `.claude/CLAUDE.md` (phần `## Commands`)

- [ ] **Step 1: Sửa đoạn mô tả + ví dụ lệnh**

Thay đoạn mở đầu:
```markdown
Each symbol has its own independent config in `config.py`, selected with `--symbol`
(`XAUUSDm`/`EURUSDm`, aliases `gold`/`eurusd`). Default is `XAUUSDm`. Tuning one symbol
never touches the other.
```
bằng:
```markdown
Each symbol has its own independent config in `config.py`, selected with `--symbol`
(`XAUUSDm`/`EURUSDm`/`GBPUSDm`, aliases `gold`/`eurusd`/`gbpusd`). Default is
`XAUUSDm`. Tuning one symbol never touches the other.

`main.py` (live/demo loop) can also run **multiple symbols in one process** via
`--symbols a,b,c` (comma-separated, aliases accepted) — a single sequential
single-thread loop processes each symbol's own state (risk guard, journal,
cooldown) independently every poll tick. `--symbol` (singular) still works for
one symbol, unchanged. `backtest.py` remains single-symbol only (`--symbol`) —
multi-symbol is a live/demo-loop-only capability.
```

Thay dòng ví dụ:
```markdown
# Live/demo loop (defaults to dry_run — logs signals, places no orders)
python main.py --symbol XAUUSDm       # gold
python main.py --symbol EURUSDm       # EURUSD
```
bằng:
```markdown
# Live/demo loop (defaults to dry_run — logs signals, places no orders)
python main.py --symbol XAUUSDm                  # 1 symbol (gold)
python main.py --symbols EURUSDm,GBPUSDm         # nhiều symbol, 1 process
```

- [ ] **Step 2: Đối chiếu lại toàn văn `## Commands` sau khi sửa**

Đọc lại section để đảm bảo không còn mâu thuẫn nội bộ (vd không còn chỗ nào
khác trong CLAUDE.md ngụ ý "chỉ chạy được 1 symbol/lệnh").

- [ ] **Step 3: Commit**

```bash
git add .claude/CLAUDE.md
git commit -m "Document --symbols multi-symbol main.py invocation in CLAUDE.md"
```

---

## Ngoài phạm vi plan này (đã nêu trong spec, nhắc lại để tránh lan phạm vi)

- Không gộp risk management thành ngân sách chung toàn account.
- Không đổi `backtest.py`.
- Không thêm cơ chế giới hạn tổng lệnh mở giữa các symbol.
- Không đổi `mt5_client.py`.
