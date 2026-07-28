"""
Nhật ký lệnh: lưu MỌI lệnh đã đặt cùng kết quả.
- Mỗi record: ngày tạo, entry, SL, TP, ... và result.
- Chạm TP  → result = "success".
- Chạm SL  → result = "failed"  (SL dời về entry = "breakeven").
- Đóng tay/khác → result = "closed".

Lưu ra CSV (mở bằng Excel được). File tách theo symbol: trades_<symbol>.csv.
Không phụ thuộc MetaTrader5 nên import/backtest trên mọi OS đều chạy.
"""
import csv
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("journal")

FIELDS = [
    "ticket", "symbol", "mode", "created", "direction",
    "entry", "sl", "tp", "lot", "rr", "risk_amount", "reason",
    "result", "closed", "close_price", "profit",
]


class TradeJournal:
    """Nhật ký bền vững ghi ra CSV. Cập nhật kết quả bằng cách ghi đè cả file
    (file nhỏ nên chi phí không đáng kể)."""

    def __init__(self, symbol: str, path: str | None = None):
        self.symbol = symbol
        self.path = Path(path or f"trades_{symbol}.csv")
        self._records: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def _flush(self):
        with self.path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for r in self._records:
                w.writerow(r)

    def open_tickets(self) -> set[str]:
        """Ticket của các lệnh còn đang mở (chưa có kết quả)."""
        return {r["ticket"] for r in self._records if r["result"] == "open"}

    def has(self, ticket) -> bool:
        return any(r["ticket"] == str(ticket) for r in self._records)

    def get(self, ticket) -> dict | None:
        """Bản ghi (mới nhất) của một ticket — dùng để suy ra lot/SL gốc khi quản lý live."""
        for r in reversed(self._records):
            if r["ticket"] == str(ticket):
                return r
        return None

    def record_open(self, ticket, direction, entry, sl, tp, lot, rr,
                    risk_amount, reason, mode="live", created=None):
        """Ghi một lệnh mới (result="open"). Bỏ qua nếu ticket đã có."""
        if self.has(ticket):
            return
        rec = {
            "ticket": str(ticket),
            "symbol": self.symbol,
            "mode": mode,
            "created": (created or datetime.now()).isoformat(timespec="seconds"),
            "direction": direction,
            "entry": entry, "sl": sl, "tp": tp, "lot": lot,
            "rr": rr, "risk_amount": risk_amount, "reason": reason,
            "result": "open", "closed": "", "close_price": "", "profit": "",
        }
        self._records.append(rec)
        self._flush()
        log.info(f"📓 Ghi lệnh #{ticket} ({mode}) vào {self.path.name}")

    def record_close(self, ticket, result, close_price=None, profit=None, closed=None):
        """Cập nhật kết quả cho một lệnh đang mở."""
        for r in self._records:
            if r["ticket"] == str(ticket) and r["result"] == "open":
                r["result"] = result
                r["closed"] = (closed or datetime.now()).isoformat(timespec="seconds")
                r["close_price"] = "" if close_price is None else close_price
                r["profit"] = "" if profit is None else profit
                self._flush()
                log.info(f"📓 Lệnh #{ticket} → {result}"
                         + (f" (P/L {profit})" if profit is not None else ""))
                return

    def open_records(self) -> list[dict]:
        """Bản ghi các lệnh đang mở (để engine paper theo dõi)."""
        return [r for r in self._records if r["result"] == "open"]
