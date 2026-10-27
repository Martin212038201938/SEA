"""Benachrichtigungen: Microsoft Teams (Incoming Webhook / Workflows) und E-Mail (SMTP)."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import httpx

from .config import settings

log = logging.getLogger(__name__)


def _md_to_html(md: str) -> str:
    """Sehr kleine Markdown-Umsetzung für Mails (Überschriften, Listen, Fett, Tabellen bleiben als Text)."""
    import html

    lines = []
    in_list = False
    for raw in md.splitlines():
        line = html.escape(raw)
        if line.startswith("## "):
            if in_list:
                lines.append("</ul>")
                in_list = False
            lines.append(f"<h3>{line[3:]}</h3>")
        elif line.startswith("# "):
            lines.append(f"<h2>{line[2:]}</h2>")
        elif line.startswith("- "):
            if not in_list:
                lines.append("<ul>")
                in_list = True
            lines.append(f"<li>{line[2:]}</li>")
        elif line.strip() == "":
            if in_list:
                lines.append("</ul>")
                in_list = False
            lines.append("")
        else:
            if in_list:
                lines.append("</ul>")
                in_list = False
            lines.append(f"<p style='margin:4px 0'>{line}</p>")
    if in_list:
        lines.append("</ul>")
    body = "\n".join(lines).replace("**", "")
    return f"<div style='font-family:Aptos,Segoe UI,Arial,sans-serif;font-size:14px;color:#1b2340'>{body}</div>"


def send_teams(title: str, text_md: str, link: str | None = None) -> bool:
    if not settings.teams_webhook_url:
        return False
    card = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": title, "wrap": True},
                        {"type": "TextBlock", "text": text_md[:6000], "wrap": True},
                    ],
                    "actions": ([{"type": "Action.OpenUrl", "title": "Im Cockpit öffnen", "url": link}] if link else []),
                },
            }
        ],
    }
    try:
        r = httpx.post(settings.teams_webhook_url, json=card, timeout=20)
        r.raise_for_status()
        return True
    except Exception as exc:
        log.warning("Teams-Benachrichtigung fehlgeschlagen: %s", exc)
        return False


def send_mail(subject: str, text_md: str, to: list[str] | None = None) -> bool:
    to = to or settings.alert_email_to
    if not (settings.smtp_host and to):
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.mail_from
    msg["To"] = ", ".join(to)
    msg.set_content(text_md)
    msg.add_alternative(_md_to_html(text_md), subtype="html")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
            s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
        return True
    except Exception as exc:
        log.warning("Mail-Versand fehlgeschlagen: %s", exc)
        return False


def broadcast(title: str, text_md: str, link_path: str = "/") -> dict[str, bool]:
    link = f"{settings.base_url}{link_path}"
    return {"teams": send_teams(title, text_md, link), "mail": send_mail(title, text_md + f"\n\nIm Cockpit: {link}")}
