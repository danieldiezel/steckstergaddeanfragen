"""Konfiguration: liest .env und profil.json."""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_env(BASE_DIR / ".env")


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def env_int(key: str, default: int) -> int:
    try:
        return int(env(key, str(default)))
    except ValueError:
        return default


def env_bool(key: str, default: bool = False) -> bool:
    return env(key, "1" if default else "0").lower() in ("1", "true", "yes", "ja", "on")


# --- LLM ---
# Reihenfolge der Anbieter, der zweite springt ein, wenn der erste ausfällt
LLM_PROVIDER = env("LLM_PROVIDER", "groq,mistral")
# LLM2_* wird auch akzeptiert (gleiche Namen wie im MGT_Anfragen-Bot)
GROQ_API_KEY = env("GROQ_API_KEY") or env("LLM2_API_KEY")
GROQ_MODEL = env("GROQ_MODEL") or env("LLM2_MODEL") or "llama-3.3-70b-versatile"
GROQ_BASE_URL = (env("GROQ_BASE_URL") or env("LLM2_BASE_URL")
                 or "https://api.groq.com/openai/v1").rstrip("/")
MISTRAL_API_KEY = env("MISTRAL_API_KEY")
MISTRAL_MODEL = env("MISTRAL_MODEL", "mistral-small-latest")

# --- Mail ---
SMTP_HOST = env("SMTP_HOST", "smtp.ionos.de")
SMTP_PORT = env_int("SMTP_PORT", 587)
SMTP_USER = env("SMTP_USER")
SMTP_PASS = env("SMTP_PASS")
MAIL_FROM = env("MAIL_FROM", SMTP_USER)
MAIL_FROM_NAME = env("MAIL_FROM_NAME", "Steckster Gadde")
MAIL_BCC = env("MAIL_BCC")  # optional: Kopie an euch selbst
IMAP_HOST = env("IMAP_HOST", "imap.ionos.de")
IMAP_SENT_FOLDER = env("IMAP_SENT_FOLDER", "Gesendete Objekte")
IMAP_ENABLED = env_bool("IMAP_ENABLED", True)

# --- Telegram ---
TELEGRAM_TOKEN = env("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")

# --- Ablauf ---
DAILY_LIMIT = env_int("DAILY_LIMIT", 10)
SEND_START_HOUR = env_int("SEND_START_HOUR", 9)   # Versandfenster (lokale Zeit)
SEND_END_HOUR = env_int("SEND_END_HOUR", 17)
SEND_WEEKDAYS_ONLY = env_bool("SEND_WEEKDAYS_ONLY", True)
MIN_FIT_SCORE = env_int("MIN_FIT_SCORE", 6)       # 1..10, darunter wird nicht angeschrieben
DRY_RUN = env_bool("DRY_RUN", True)               # erst testen, dann auf 0 stellen
DB_PATH = env("DB_PATH", str(BASE_DIR / "sponsoren.db"))
TIMEZONE = env("TIMEZONE", "Europe/Berlin")


def load_profile() -> dict:
    with open(BASE_DIR / "profil.json", encoding="utf-8") as f:
        return json.load(f)
