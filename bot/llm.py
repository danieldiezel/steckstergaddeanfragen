"""LLM (Groq oder Mistral): Firmen bewerten und individuelle Sponsoring-Mails schreiben."""
import json
import logging
import re
import time

import requests

from . import config

log = logging.getLogger("llm")

PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions",
             lambda: config.GROQ_API_KEY, lambda: config.GROQ_MODEL),
    "mistral": ("https://api.mistral.ai/v1/chat/completions",
                lambda: config.MISTRAL_API_KEY, lambda: config.MISTRAL_MODEL),
}


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)  # JSON aus Fließtext/Codeblock holen
        if m:
            return json.loads(m.group(0))
        raise


def _call(provider: str, messages: list[dict], temperature: float) -> dict:
    url, key_fn, model_fn = PROVIDERS[provider]
    key = key_fn()
    if not key:
        raise RuntimeError(f"API-Key für {provider} fehlt in .env")
    payload = {"model": model_fn(), "messages": messages, "temperature": temperature,
               "response_format": {"type": "json_object"}}
    for attempt in range(4):
        r = requests.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=60)
        if r.status_code == 429:  # Free-Tier-Limit: kurz warten
            wait = float(r.headers.get("retry-after", 20 * (attempt + 1)))
            time.sleep(min(wait, 120))
            continue
        if r.status_code == 400 and "response_format" in payload and "json" in r.text.lower():
            payload.pop("response_format")  # Modell kann keinen JSON-Modus: ohne versuchen
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"{provider}-Fehler {r.status_code}: {r.text[:300]}")
        return _parse_json(r.json()["choices"][0]["message"]["content"])
    raise RuntimeError(f"{provider}: Rate-Limit")


def _chat(messages: list[dict], temperature: float = 0.4) -> dict:
    """Probiert die Anbieter der Reihe nach (LLM_PROVIDER, dann Fallback)."""
    order = [p.strip() for p in config.LLM_PROVIDER.split(",") if p.strip() in PROVIDERS]
    errors = []
    for provider in order:
        try:
            return _call(provider, messages, temperature)
        except Exception as e:
            log.warning("%s fehlgeschlagen: %s", provider, e)
            errors.append(str(e))
    raise RuntimeError(" | ".join(errors) or "Kein LLM-Anbieter konfiguriert")


def _profile_text(p: dict) -> str:
    stufen = "\n".join(f"- {s['name']}: {s['preis']} ({s['leistung']})" for s in p["pakete"])
    erfolge = "\n".join(f"- {e}" for e in p["erfolge"])
    return (f"Verein: {p['verein']}\nTeam: {p['team_beschreibung']}\n"
            f"Erfolge:\n{erfolge}\nReichweite/Kanäle: {p['kanaele']}\n"
            f"Sponsoring-Pakete (Saison ca. 6 Monate):\n{stufen}\n"
            f"Region: {p['region']}")


def qualify(company: dict, profile: dict) -> dict:
    """-> {'score': 1-10, 'grund': str, 'firmenname': str, 'branche': str, 'aufhaenger': str}"""
    sys = ("Du bewertest Unternehmen als mögliche Trikot- bzw. Teamsponsoren für ein kleines "
           "Amateur-CS2-Esportsteam. Antworte nur als JSON.")
    user = f"""{_profile_text(profile)}

Unternehmen:
Domain: {company['domain']}
Gefunden über: {company['source']} ({company.get('source_detail') or '-'})
Webseitentext (Auszug):
{company['text'][:3500]}

Bewerte, wie realistisch dieses Unternehmen ein kleines Esportsteam mit Paketen von 40 bis 850 Euro pro Saison sponsert.
Hoch (7-10): passt zu Gaming/Tech/Energy/Food/lokalem Mittelstand in {profile['region']}, sponsert schon Vereine oder Esports, KMU.
Niedrig (1-4): Großkonzern ohne regionalen Bezug, Behörde, Glücksspiel, Alkohol/Tabak, Esports-Team oder Liga selbst, Agentur ohne Bezug, keine echte Firma, Seite kaputt.
JSON-Felder: score (int 1-10), grund (1 Satz), firmenname (offizieller Name), branche, aufhaenger (konkreter, wahrer Anknüpfungspunkt aus dem Text für die Mail, 1 Satz, oder leer)."""
    data = _chat([{"role": "system", "content": sys}, {"role": "user", "content": user}], 0.1)
    try:
        data["score"] = int(data.get("score", 0))
    except (TypeError, ValueError):
        data["score"] = 0
    return data


def _clean(text: str) -> str:
    """Keine Gedankenstriche (wirken KI-geschrieben), keine Markdown-Reste."""
    text = re.sub(r"\s[–—]\s", ", ", text)
    text = text.replace("–", "-").replace("—", "-")
    text = text.replace("**", "").replace("__", "")
    return text.strip()


def write_mail(company: dict, qual: dict, profile: dict) -> dict:
    """-> {'betreff': str, 'text': str}"""
    herkunft = ""
    if company["source"] == "sponsor_von_team":
        herkunft = (f"Das Unternehmen unterstützt bereits das Esports-Team "
                    f"'{company.get('source_detail')}'. Erwähne das kurz und wertschätzend, "
                    f"ohne das andere Team schlecht zu machen.")
    elif company["source"] == "lokal":
        herkunft = "Betone die regionale Verbundenheit (Rhein-Main / Aschaffenburg)."

    sys = ("Du schreibst im Namen eines Vereins kurze, persönliche Sponsoring-Anfragen auf Deutsch. "
           "Klingt wie von einem Menschen geschrieben, locker aber seriös, Sie-Form. "
           "Niemals Gedankenstriche verwenden. Keine Floskeln wie 'Ich hoffe, diese Mail erreicht Sie gut'. "
           "Keine erfundenen Fakten, keine Zahlen außer den gegebenen. Antworte nur als JSON.")
    user = f"""{_profile_text(profile)}

Empfänger: {qual.get('firmenname') or company['domain']} ({qual.get('branche', '')})
Aufhänger: {qual.get('aufhaenger') or '-'}
{herkunft}

Schreibe eine Mail (120 bis 180 Wörter) mit Bitte um Trikot- bzw. Teamsponsoring.
Aufbau: kurze Vorstellung des Teams mit einem Erfolg, warum genau dieses Unternehmen passt,
Pakete kurz nennen (Name und Preis, eine Zeile je Paket), Hinweis dass auch individuelle Absprachen möglich sind,
Bitte um kurze Rückmeldung per Mail. {profile.get('mail_zusatz', '')}
Anrede: "Sehr geehrte Damen und Herren" falls kein Name bekannt.
Grußformel und Signatur NICHT schreiben, die wird automatisch angehängt.
JSON-Felder: betreff (max 70 Zeichen, konkret, kein Clickbait), text."""
    data = _chat([{"role": "system", "content": sys}, {"role": "user", "content": user}], 0.6)
    body = _clean(data.get("text", ""))
    body += "\n\n" + profile["signatur"].strip()
    body += "\n\n" + profile["abmelde_hinweis"].strip()
    return {"betreff": _clean(data.get("betreff", "Sponsoring-Anfrage Steckster Gadde")), "text": body}
