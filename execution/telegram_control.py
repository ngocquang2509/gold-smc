"""
Lệnh Telegram CHIỀU VÀO (ADR 0001): /status, /pause [tên], /resume [tên] — điều khiển
Kill-switch. KHÔNG có lệnh nào đặt/đóng lệnh giao dịch.

An toàn:
  - Chỉ nhận lệnh từ chat RIÊNG của chủ (chat.id = TELEGRAM_CHAT_ID, type "private").
    Người lạ / group bị bỏ qua và KHÔNG được trả lời (không lộ bot).
  - Lệnh cũ hơn MAX_AGE_S bị bỏ qua → /resume cũ không "phát lại" sau khi restart.
  - Offset getUpdates ghi đĩa; mọi lỗi mạng bị nuốt (log) — không làm crash vòng lặp live.
  - Không blocking (timeout=0): vòng lặp live gọi poll_once() mỗi nhịp, không cần thread.
"""
import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Callable

from execution.notifier import _ssl_context
from risk.killswitch import KillSwitch

log = logging.getLogger("telegram_control")

MAX_AGE_S = 300
API_URL = "https://api.telegram.org/bot{token}/{method}"
HELP = ("Lệnh:\n/status — trạng thái từng Strategy\n/pause [tên] — ngừng vào lệnh mới (bỏ tên = tất cả)\n"
        "/resume [tên] — mở lại; Strategy bị Kill-switch dừng sẽ bắt đầu đếm lại từ đầu\n"
        "(Không có lệnh đặt/đóng giao dịch qua Telegram.)")


def http_api(token: str) -> Callable[[str, dict], dict]:
    def call(method: str, payload: dict) -> dict:
        req = urllib.request.Request(API_URL.format(token=token, method=method),
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    return call


class TelegramControl:
    def __init__(self, api: Callable[[str, dict], dict], owner_chat_id, ks: KillSwitch,
                 offset_path: Path, clock: Callable[[], float] = time.time, max_age_s: int = MAX_AGE_S):
        self.api = api
        self.owner = int(owner_chat_id)
        self.ks = ks
        self.offset_path = Path(offset_path)
        self.clock = clock
        self.max_age_s = max_age_s
        self.offset = (json.loads(self.offset_path.read_text(encoding="utf-8")).get("offset")
                       if self.offset_path.exists() else None)

    @classmethod
    def from_env(cls, ks: KillSwitch, offset_path: Path) -> "TelegramControl | None":
        token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
        if not (token and chat):
            log.warning("Thiếu TELEGRAM_TOKEN / TELEGRAM_CHAT_ID — tắt điều khiển Telegram")
            return None
        return cls(http_api(token), chat, ks, offset_path)

    def _save_offset(self) -> None:
        self.offset_path.parent.mkdir(parents=True, exist_ok=True)
        self.offset_path.write_text(json.dumps({"offset": self.offset}), encoding="utf-8")

    def _reply(self, text: str) -> None:
        try:
            self.api("sendMessage", {"chat_id": self.owner, "text": text})
        except Exception as e:
            log.warning(f"Trả lời Telegram thất bại: {e}")

    def poll_once(self) -> list[str]:
        """Xử lý các lệnh mới. Trả về danh sách lệnh đã thực hiện."""
        payload = {"timeout": 0}
        if self.offset is not None:
            payload["offset"] = self.offset
        try:
            updates = self.api("getUpdates", payload).get("result", [])
        except Exception as e:
            log.warning(f"getUpdates thất bại: {e}")
            return []
        done = []
        for u in updates:
            self.offset = u["update_id"] + 1
            msg = u.get("message") or {}
            chat = msg.get("chat", {})
            text = (msg.get("text") or "").strip()
            if chat.get("id") != self.owner or chat.get("type") != "private":
                log.warning(f"Bỏ qua tin từ chat lạ {chat.get('id')} ({chat.get('type')})")
                continue
            if self.clock() - msg.get("date", 0) > self.max_age_s:
                log.info(f"Bỏ qua lệnh cũ: {text!r}")
                continue
            if text.startswith("/"):
                done.append(self._handle(text))
        self._save_offset()
        return done

    def _handle(self, text: str) -> str:
        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1] if len(parts) > 1 else None
        try:
            if cmd == "/status":
                self._reply(self.status_text())
            elif cmd == "/pause":
                names = self.ks.pause(arg)
                self._reply(f"⏸ Ngừng vào lệnh mới: {', '.join(names)}. Vị thế đang mở vẫn được quản lý.")
            elif cmd == "/resume":
                names = self.ks.resume(arg)
                self._reply(f"▶️ Mở lại: {', '.join(names)}\n\n{self.status_text()}")
            else:
                self._reply(HELP)
                return f"? {text}"
        except KeyError:
            self._reply(f"Không có Strategy '{arg}'. Có: {', '.join(self.ks.oos_max_dd)}")
            return f"? {text}"
        log.info(f"Lệnh Telegram: {text}")
        return text

    def status_text(self) -> str:
        lines = []
        for name in self.ks.oos_max_dd:
            s = self.ks.status(name)
            if s["halted"]:
                head = f"⛔ DỪNG (Kill-switch): {s['reason']}"
            elif s["paused"]:
                head = "⏸ tạm dừng (tay)"
            else:
                head = "✅ đang chạy"
            pf = "—" if s["pf"] is None else s["pf"]
            lines.append(f"{name}: {head}\n  DD {s['dd_pct']}% / giới hạn {s['dd_limit_pct']}% | "
                         f"PF50 {pf} | {s['n']} lệnh từ {s['armed_at'][:16]}")
        return "\n".join(lines)
