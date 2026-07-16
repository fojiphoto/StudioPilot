"""WhatsApp alerts - delivers undelivered Alert rows via the WhatsApp Cloud API.

Setup (Meta developer app -> Add Product -> WhatsApp):
  WHATSAPP_TOKEN            access token (test token from API Setup page, or a
                            permanent System User token later)
  WHATSAPP_PHONE_NUMBER_ID  the "Phone number ID" of the sender (Meta test number)
  WHATSAPP_TO               recipient number(s), comma-separated, e.g. 92300xxxxxxx

Caveat: with free-form text messages, WhatsApp requires the recipient to have
messaged the sender number within the last 24h. Send "hi" to the test number
once a day, or approve a message template later for anytime delivery.
"""
from __future__ import annotations

import os

import httpx

from gameos.kernel.models import Alert
from gameos.kernel.module import Module, ModuleInfo, ModuleType
from gameos.kernel.runtime import Context

API_BASE = "https://graph.facebook.com/v19.0"
MAX_ALERTS_PER_MESSAGE = 10

_SEVERITY_ICON = {"info": "i", "warn": "!", "critical": "!!"}


class WhatsAppAlerts(Module):
    info = ModuleInfo(
        name="whatsapp_alerts",
        type=ModuleType.OUTPUT,
        description="Pushes new alerts to the owner's WhatsApp (Cloud API).",
        default_interval_minutes=30,
    )

    def _config(self) -> tuple[str, str, list[str]] | None:
        token = os.getenv("WHATSAPP_TOKEN", "").strip()
        phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        to = [t.strip() for t in os.getenv("WHATSAPP_TO", "").split(",") if t.strip()]
        if not (token and phone_id and to):
            return None
        return token, phone_id, to

    def _send(self, token: str, phone_id: str, to: str, body: str) -> None:
        response = httpx.post(
            f"{API_BASE}/{phone_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}},
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(f"WhatsApp send failed {response.status_code}: {response.text[:300]}")

    def run(self, ctx: Context) -> None:
        config = self._config()
        if config is None:
            self.log.debug("WHATSAPP_* not configured - skipping")
            return
        token, phone_id, recipients = config

        with ctx.session() as session:
            pending = (
                session.query(Alert)
                .filter(Alert.delivered_via.is_(None))
                .order_by(Alert.created_at)
                .limit(MAX_ALERTS_PER_MESSAGE)
                .all()
            )
            if not pending:
                return
            lines = [
                f"[{_SEVERITY_ICON.get(a.severity, '?')}] {a.module}: {a.message}" for a in pending
            ]
            body = "GameOS alerts:\n" + "\n".join(lines)
            try:
                for to in recipients:
                    self._send(token, phone_id, to, body)
            except Exception:
                # leave delivered_via NULL so we retry next cycle
                self.log.exception("delivery failed - will retry next cycle")
                return
            for alert in pending:
                alert.delivered_via = "whatsapp"
            session.commit()
        self.log.info("delivered %d alerts to %d recipient(s)", len(pending), len(recipients))

    def self_test(self, ctx: Context) -> tuple[bool, str]:
        config = self._config()
        if config is None:
            return False, "WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_TO missing from .env"
        token, phone_id, recipients = config
        try:
            for to in recipients:
                self._send(token, phone_id, to, "GameOS: WhatsApp alerts are working.")
        except Exception as exc:
            return False, f"send failed: {exc}"
        return True, f"test message sent to {', '.join(recipients)}"


def get_module() -> Module:
    return WhatsAppAlerts()
