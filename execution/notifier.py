"""
Thông báo Telegram cho vòng đời lệnh: tín hiệu → đặt lệnh → đóng lệnh.
Không phụ thuộc MetaTrader5 nên import trên mọi OS đều chạy.
Mọi lỗi gửi tin đều bị nuốt (log.warning) — KHÔNG BAO GIỜ được làm crash vòng lặp giao dịch.

Ghi chú SSL: Python trên Windows đôi khi không thấy root cert mà .NET/Edge đã tin cậy
(root store của Windows nạp lazy/không đồng bộ với kho mà OpenSSL của Python đọc được),
gây lỗi "self-signed certificate in certificate chain" dù chứng chỉ server hoàn toàn hợp lệ.
Dùng bundle CA của `certifi` (nếu có cài) thay vì kho chứng chỉ Windows để tránh lỗi này —
KHÔNG tắt xác thực SSL.
"""
import html
import os
import json
import logging
import ssl
import urllib.request
import urllib.error

log = logging.getLogger("notifier")

API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()

RESULT_LABELS = {
    "success": "✅ THÀNH CÔNG (chạm TP)",
    "failed": "❌ THẤT BẠI (chạm SL gốc)",
    "breakeven": "➖ HOÀ VỐN (SL đã dời về entry)",
    "closed": "◻️ ĐÓNG (tay/lý do khác)",
}


class TelegramNotifier:
    def __init__(self, token: str | None, chat_id: str | None, enabled: bool = True):
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled

    @classmethod
    def from_config(cls, cfg) -> "TelegramNotifier":
        """Ưu tiên biến môi trường TELEGRAM_TOKEN / TELEGRAM_CHAT_ID, fallback về config.py."""
        token = os.getenv("TELEGRAM_TOKEN") or getattr(cfg, "telegram_token", None)
        chat_id = os.getenv("TELEGRAM_CHAT_ID") or getattr(cfg, "telegram_chat_id", None)
        enabled = getattr(cfg, "telegram_enabled", True) and bool(token and chat_id)
        return cls(token, chat_id, enabled=enabled)

    def _send(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            url = API_URL.format(token=self.token)
            payload = json.dumps({
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
            }).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as resp:
                resp.read()
        except Exception as e:
            log.warning(f"Gửi Telegram thất bại: {e}")

    # ── Sự kiện vòng đời lệnh ────────────────────────────────
    def notify_signal(self, symbol: str, plan) -> None:
        direction = plan.direction.upper()
        text = (
            f"🎯 <b>TÍN HIỆU MỚI</b>\n"
            f"Symbol: <b>{symbol}</b>\n"
            f"Loại: <b>{'BUY' if direction == 'BUY' else 'SELL'}</b>\n"
            f"Entry: {plan.entry}\n"
            f"TP: {plan.tp}\n"
            f"SL: {plan.sl}\n"
            f"R:R: {plan.rr}"
        )
        self._send(text)

    def notify_opened(self, symbol: str, plan) -> None:
        direction = plan.direction.upper()
        text = (
            f"✅ <b>ĐÃ ĐẶT LỆNH</b>\n"
            f"Symbol: <b>{symbol}</b>\n"
            f"Loại: <b>{'BUY' if direction == 'BUY' else 'SELL'}</b>\n"
            f"Entry: {plan.entry}\n"
            f"TP: {plan.tp}\n"
            f"SL: {plan.sl}\n"
            f"Rủi ro: ${plan.risk_amount}"
        )
        self._send(text)

    def notify_closed(self, symbol: str, record: dict, result: str, profit) -> None:
        direction = str(record.get("direction", "")).upper()
        status = RESULT_LABELS.get(result, result)
        lines = [
            f"🔔 <b>ĐÓNG LỆNH</b>",
            f"Symbol: <b>{symbol}</b>",
            f"Loại: <b>{'BUY' if direction == 'BUY' else 'SELL'}</b>",
            f"Entry: {record.get('entry')}",
            f"TP: {record.get('tp')}",
            f"SL: {record.get('sl')}",
            f"Trạng thái: {status}",
        ]
        if profit is not None:
            if profit >= 0:
                lines.append(f"Nhận về: +${profit}")
            else:
                lines.append(f"Thua lỗ: -${abs(profit)}")
        self._send("\n".join(lines))

    def notify_alert(self, strategy: str, reason: str) -> None:
        """Kill-switch vừa dừng một Strategy. Escape HTML: lý do có ký tự '<' / '≥'."""
        self._send(
            f"🚨 <b>KILL-SWITCH: {html.escape(strategy)}</b>\n"
            f"{html.escape(reason)}\n"
            f"Ngừng vào lệnh mới; vị thế đang mở vẫn được quản lý.\n"
            f"Mở lại bằng /resume {html.escape(strategy)} (đếm lại từ đầu)."
        )
