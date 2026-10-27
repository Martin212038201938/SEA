"""Konfiguration aus Umgebungsvariablen (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in {"1", "true", "yes", "ja", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _parse_users(raw: str) -> dict[str, str]:
    users: dict[str, str] = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        name, pw_hash = part.split(":", 1)
        users[name.strip().lower()] = pw_hash.strip()
    return users


def _parse_map(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (raw or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


@dataclass
class Settings:
    secret_key: str = os.getenv("COCKPIT_SECRET_KEY", "dev-secret-bitte-aendern")
    base_url: str = os.getenv("COCKPIT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    users: dict[str, str] = field(default_factory=lambda: _parse_users(os.getenv("COCKPIT_USERS", "")))
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'cockpit.db'}")
    timezone: str = os.getenv("TIMEZONE", "Europe/Berlin")
    demo_mode: bool = _bool("DEMO_MODE", False)

    target_cpl: float = _float("TARGET_CPL_EUR", 150.0)
    max_cpl: float = _float("MAX_CPL_EUR", 300.0)
    avg_deal_value: float = _float("AVG_DEAL_VALUE_EUR", 5500.0)
    close_rate: float = _float("CLOSE_RATE", 0.4)

    google_enabled: bool = _bool("GOOGLE_ADS_ENABLED", False)
    google_customer_id: str = os.getenv("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "")
    google_login_customer_id: str = os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").replace("-", "")
    google_client_id: str = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
    google_client_secret: str = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    google_refresh_token: str = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN", "")
    google_developer_token: str = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "unused") or "unused"
    google_service_account_file: str = os.getenv("GOOGLE_ADS_SERVICE_ACCOUNT_FILE", "")

    ms_enabled: bool = _bool("MS_ADS_ENABLED", False)
    ms_developer_token: str = os.getenv("MS_ADS_DEVELOPER_TOKEN", "")
    ms_client_id: str = os.getenv("MS_ADS_CLIENT_ID", "")
    ms_client_secret: str = os.getenv("MS_ADS_CLIENT_SECRET", "")
    ms_refresh_token: str = os.getenv("MS_ADS_REFRESH_TOKEN", "")
    ms_customer_id: str = os.getenv("MS_ADS_CUSTOMER_ID", "")
    ms_account_id: str = os.getenv("MS_ADS_ACCOUNT_ID", "")

    trello_enabled: bool = _bool("TRELLO_ENABLED", False)
    trello_key: str = os.getenv("TRELLO_KEY", "")
    trello_token: str = os.getenv("TRELLO_TOKEN", "")
    trello_board_id: str = os.getenv("TRELLO_BOARD_ID", "")
    trello_list_status_map: dict[str, str] = field(
        default_factory=lambda: _parse_map(os.getenv("TRELLO_LIST_STATUS_MAP", ""))
    )
    trello_list_channel_map: dict[str, str] = field(
        default_factory=lambda: _parse_map(os.getenv("TRELLO_LIST_CHANNEL_MAP", ""))
    )

    bookings_webhook_secret: str = os.getenv("BOOKINGS_WEBHOOK_SECRET", "")

    llm_provider: str = os.getenv("LLM_PROVIDER", "openai")  # anthropic | openai
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_workspace_id: str = os.getenv("ANTHROPIC_WORKSPACE_ID", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    anthropic_model_cheap: str = os.getenv("ANTHROPIC_MODEL_CHEAP", "claude-haiku-5-5")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-6.1-terra")
    openai_model_cheap: str = os.getenv("OPENAI_MODEL_CHEAP", "gpt-6-luna")

    teams_webhook_url: str = os.getenv("TEAMS_WEBHOOK_URL", "")
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587") or 587)
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    mail_from: str = os.getenv("MAIL_FROM", "sea-cockpit@copilotenschule.de")
    alert_email_to: list[str] = field(
        default_factory=lambda: [a.strip() for a in os.getenv("ALERT_EMAIL_TO", "").split(",") if a.strip()]
    )

    mcp_token: str = os.getenv("MCP_TOKEN", "")

    @property
    def openai_ready(self) -> bool:
        """Sprachmodell einsatzbereit (Name historisch; gilt für den konfigurierten Anbieter)."""
        if (self.llm_provider or "openai").lower() == "anthropic":
            return bool(self.anthropic_api_key)
        return bool(self.openai_api_key)

    @property
    def google_ready(self) -> bool:
        if not (self.google_enabled and self.google_customer_id):
            return False
        if self.google_service_account_file:
            return Path(self.google_service_account_file).is_file()
        return all([self.google_client_id, self.google_client_secret, self.google_refresh_token])

    @property
    def ms_ready(self) -> bool:
        return self.ms_enabled and all(
            [self.ms_developer_token, self.ms_client_id, self.ms_refresh_token, self.ms_account_id, self.ms_customer_id]
        )

    @property
    def trello_ready(self) -> bool:
        return self.trello_enabled and all([self.trello_key, self.trello_token, self.trello_board_id])


settings = Settings()
