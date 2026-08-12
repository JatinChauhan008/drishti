import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent


def _load_dotenv() -> None:
    env_path = PROJECT_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _is_placeholder(value: str | None) -> bool:
    return not value or value.strip().lower().startswith("your_")


def _valid_base_url(value: str | None) -> bool:
    return bool(value and value.strip().startswith(("http://", "https://")))

# CivicDataSpace
BASE_URL = os.environ.get(
    "CIVICDATA_BASE_URL",
    "https://dev.api.civicdataspace.in"
)

# Sarvam
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY")

SARVAM_BASE_URL = "https://api.sarvam.ai/v1"

MODEL = "sarvam-105b"      # or sarvam-105b if available to you

# Generic LLM settings used by chatbot.py.
_raw_llm_base_url = os.environ.get("LLM_BASE_URL")
_misplaced_llm_key = _raw_llm_base_url if _raw_llm_base_url and _raw_llm_base_url.startswith("sk_") else None

LLM_API_KEY = next(
    (
        value
        for value in (
            os.environ.get("LLM_API_KEY"),
            os.environ.get("OPENAI_API_KEY"),
            SARVAM_API_KEY,
            _misplaced_llm_key,
        )
        if not _is_placeholder(value)
    ),
    None,
)
LLM_BASE_URL = _raw_llm_base_url if _valid_base_url(_raw_llm_base_url) else SARVAM_BASE_URL
LLM_MODEL = os.environ.get("LLM_MODEL", MODEL)
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "4096"))
if LLM_MODEL.startswith("sarvam-105b"):
    LLM_MAX_TOKENS = min(LLM_MAX_TOKENS, 4096)
CHAT_HISTORY_MESSAGES = int(os.environ.get("CHAT_HISTORY_MESSAGES", "12"))

# Live trace console (log_server.py), served on its own port.
LOG_SERVER_PORT = int(os.environ.get("LOG_SERVER_PORT", "8001"))

# OpenRouter judge -- verifies each answer against the sources the tools
# retrieved for it. Set OPENROUTER_API_KEY to enable; leave unset to skip
# verification (the app still works, it just won't judge answers).
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
if _is_placeholder(OPENROUTER_API_KEY):
    OPENROUTER_API_KEY = None
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_SITE_URL = os.environ.get("OPENROUTER_SITE_URL", "http://localhost")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "openai/gpt-4o-mini")
ENABLE_JUDGE = os.environ.get("ENABLE_JUDGE", "true").strip().lower() not in {"false", "0", "no"}
