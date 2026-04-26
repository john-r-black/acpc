"""Email alerts with routing based on alert type."""

import logging
import smtplib
from email.message import EmailMessage

from . import database

log = logging.getLogger(__name__)

ROUTING = {
    "hvac_command_failure": ["admin"],
    "door_command_failure": ["admin"],
    "lockout_conflict": ["admin"],
    "zone_efficiency_degradation": ["admin", "trustee_chair"],
    "humidity_anomaly": ["admin", "trustee_chair"],
    "prestart_failure": ["admin", "trustee_chair"],
    "paired_unit_divergence": ["admin", "trustee_chair"],
    "weekly_summary": ["admin", "trustee_chair"],
}

SEVERITY = {
    "hvac_command_failure": "warning",
    "door_command_failure": "warning",
    "lockout_conflict": "critical",
    "zone_efficiency_degradation": "info",
    "humidity_anomaly": "warning",
    "prestart_failure": "warning",
    "paired_unit_divergence": "warning",
    "weekly_summary": "info",
}


def _resolve_recipients(alert_type: str, config: dict) -> list[str]:
    email_cfg = config.get("email", {})
    roles = ROUTING.get(alert_type, ["admin"])
    addresses: list[str] = []
    for role in roles:
        addr = email_cfg.get(role)
        if addr:
            addresses.append(addr)
    return addresses


def send_alert(
    alert_type: str,
    subject: str,
    body: str,
    config: dict,
    secrets: dict,
) -> bool:
    """Send an alert email and log it. Returns True if at least one recipient was notified."""
    recipients = _resolve_recipients(alert_type, config)
    if not recipients:
        log.warning("No recipients for alert %s", alert_type)
        return False

    severity = SEVERITY.get(alert_type, "info")
    email_cfg = config.get("email", {})
    password = secrets.get("email", {}).get("password")

    msg = EmailMessage()
    msg["Subject"] = f"[DPUMC {severity.upper()}] {subject}"
    msg["From"] = email_cfg["from_address"]
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    sent = False
    error: str | None = None
    try:
        with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"]) as smtp:
            smtp.starttls()
            smtp.login(email_cfg["from_address"], password)
            smtp.send_message(msg)
        sent = True
    except Exception as exc:
        error = str(exc)
        log.exception("Failed to send alert %s", alert_type)

    for recipient in recipients:
        database.record_alert(
            alert_type=alert_type,
            severity=severity,
            message=f"{subject}\n\n{body}" + (f"\n\nSEND ERROR: {error}" if error else ""),
            recipient=recipient,
            sent=sent,
        )
    return sent
