"""Environment-driven settings. Secrets come from env / .env only; nothing is hardcoded."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    you_api_key: str = field(default_factory=lambda: os.getenv("YOU_API_KEY", ""))
    you_search_url: str = field(default_factory=lambda: os.getenv("YOU_SEARCH_URL", "https://ydc-index.io/v1/search"))
    you_contents_url: str = field(default_factory=lambda: os.getenv("YOU_CONTENTS_URL", "https://ydc-index.io/v1/contents"))
    you_research_url: str = field(default_factory=lambda: os.getenv("YOU_RESEARCH_URL", "https://api.you.com/v1/research"))
    opencrawl_url: str = field(default_factory=lambda: os.getenv("OPENCRAWL_MCP_URL", "https://app.getopencrawl.com/mcp"))
    opencrawl_api_key: str = field(default_factory=lambda: os.getenv("OPENCRAWL_API_KEY", ""))
    opencrawl_tool: str = field(default_factory=lambda: os.getenv("OPENCRAWL_TOOL", ""))
    http_timeout: float = field(default_factory=lambda: _f("HTTP_TIMEOUT_SECONDS", 20))
    opencrawl_timeout: float = field(default_factory=lambda: _f("OPENCRAWL_TIMEOUT_SECONDS", 60))
    database_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "./jobverify.db"))
    poll_interval: float = field(default_factory=lambda: _f("POLL_INTERVAL_SECONDS", 300))
    watcher_enabled: bool = field(default_factory=lambda: os.getenv("WATCHER_ENABLED", "true").lower() != "false")
    discord_webhook_url: str = field(default_factory=lambda: os.getenv("DISCORD_WEBHOOK_URL", ""))
    slack_webhook_url: str = field(default_factory=lambda: os.getenv("SLACK_WEBHOOK_URL", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    cors_origins: tuple = field(default_factory=lambda: tuple(o.strip() for o in os.getenv("BACKEND_CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()))
    news_window_hours: int = 72
    news_accept_undated: bool = False  # undated news never triggers the layoff/freeze penalty


def get_settings() -> Settings:
    return Settings()
