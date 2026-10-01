"""Telegram: Meldungen senden und Befehle empfangen (Long Polling, nur eure Chat-ID)."""
import logging

import requests

from . import config, db

log = logging.getLogger("telegram")
API = "https://api.telegram.org/bot{token}/{method}"


def _call(method: str, **params):
    if not config.TELEGRAM_TOKEN:
        return None
    try:
        r = requests.post(API.format(token=config.TELEGRAM_TOKEN, method=method),
                          json=params, timeout=params.get("timeout", 10) + 10)
        return r.json()
    except requests.RequestException as e:
        log.warning("Telegram-Fehler: %s", e)
        return None


def send(text: str) -> None:
    if not config.TELEGRAM_CHAT_ID:
        log.info("[Telegram] %s", text)
        return
    for i in range(0, len(text), 3900):  # Telegram-Limit 4096
        _call("sendMessage", chat_id=config.TELEGRAM_CHAT_ID, text=text[i:i + 3900],
              disable_web_page_preview=True)


def poll(timeout: int = 20) -> list[tuple[str, str]]:
    """Gibt [(befehl, argument)] aus eurem Chat zurück."""
    offset = int(db.get_state("tg_offset", 0))
    res = _call("getUpdates", offset=offset, timeout=timeout)
    cmds = []
    if not res or not res.get("ok"):
        return cmds
    for upd in res["result"]:
        db.set_state("tg_offset", upd["update_id"] + 1)
        msg = upd.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(config.TELEGRAM_CHAT_ID):
            continue  # Fremde ignorieren
        text = (msg.get("text") or "").strip()
        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            cmds.append((parts[0].split("@")[0].lower(), parts[1] if len(parts) > 1 else ""))
    return cmds
