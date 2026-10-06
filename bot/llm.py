"""LLM (Groq oder Mistral): Firmen bewerten und individuelle Sponsoring-Mails schreiben."""
import json
import logging
import re
import time

import requests

from . import config

log = logging.getLogger("llm")

PROVIDERS = {
    "groq": (None,
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
    url = url or f"{config.GROQ_BASE_URL}/chat/completions"
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


def _profile_text(p: dict, pakete: bool = True) -> str:
    erfolge = "\n".join(f"- {e}" for e in p["erfolge"])
    text = (f"Verein: {p['verein']}\nTeam: {p['team_beschreibung']}\n"
            f"Erfolge:\n{erfolge}\nReichweite/Kanäle: {p['kanaele']}\n"
            f"Region: {p['region']}")
    if pakete:
        stufen = "\n".join(f"- {s['name']}: {s['preis']} ({s['leistung']})" for s in p["pakete"])
        text += f"\nSponsoring-Pakete (Saison ca. 6 Monate):\n{stufen}"
    return text


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
    """Allgemeine Partnerschaftsanfrage. -> {'betreff': str, 'text': str}

    Bewusst allgemein: keine Annahmen darüber, was die Firma macht oder will,
    keine Pakete/Preise. Details gibt es erst, wenn die Firma Interesse zeigt.
    """
    region = ""
    if company["source"] == "lokal":
        region = "Das Unternehmen sitzt in unserer Region, erwähne kurz, dass wir ebenfalls aus der Region kommen."

    sys = ("Du schreibst im Namen eines Vereins kurze, allgemeine Anfragen für eine Partnerschaft bzw. "
           "ein Sponsoring auf Deutsch. Klingt wie von einem Menschen geschrieben, freundlich und seriös, Sie-Form. "
           "Niemals Gedankenstriche verwenden. Keine Floskeln wie 'Ich hoffe, diese Mail erreicht Sie gut'. "
           "Erfinde nichts über das Unternehmen: keine Vermutungen, was es anbietet, plant oder sich wünscht, "
           "keine Ideen für gemeinsame Projekte, keine Branchenbezüge. Keine Preise, keine Pakete, keine Zahlen "
           "außer den gegebenen Erfolgen. Antworte nur als JSON.")
    user = f"""{_profile_text(profile, pakete=False)}

Empfänger: {qual.get('firmenname') or company['domain']}
{region}

Schreibe eine allgemeine Anfrage (90 bis 140 Wörter), ob das Unternehmen sich grundsätzlich eine
Partnerschaft bzw. ein Sponsoring unseres CS2-Teams vorstellen kann, zum Beispiel als Trikotsponsor.
Aufbau: wer wir sind (kurz, mit einem Erfolg), dass wir Partner für die kommende Saison suchen,
dass es verschiedene Möglichkeiten gibt und wir gerne Details schicken, wenn Interesse besteht,
Bitte um kurze Rückmeldung per Mail. Kein Telefonat und kein Treffen vorschlagen. {profile.get('mail_zusatz', '')}
Anrede: "Sehr geehrte Damen und Herren".
Grußformel und Signatur NICHT schreiben, die wird automatisch angehängt.
JSON-Felder: betreff (max 60 Zeichen, schlicht, z.B. "Partnerschaftsanfrage Steckster Gadde CS2"), text."""
    data = _chat([{"role": "system", "content": sys}, {"role": "user", "content": user}], 0.5)
    body = _clean(data.get("text", ""))
    body += "\n\n" + profile["signatur"].strip()
    if profile.get("abmelde_hinweis", "").strip():
        body += "\n\n" + profile["abmelde_hinweis"].strip()
    return {"betreff": _clean(data.get("betreff") or "Partnerschaftsanfrage Steckster Gadde"), "text": body}


KINDS = ("interesse", "frage", "absage", "abmeldung", "sonstiges")


def write_reply(company: dict, incoming: dict, our_mail: dict | None, profile: dict,
                hinweis: str = "") -> dict:
    """Antwortvorschlag auf die Mail einer Firma.

    -> {'art': str, 'zusammenfassung': str, 'betreff': str, 'text': str}
    Pakete und Preise dürfen hier vorkommen, wenn die Firma danach fragt oder Interesse zeigt.
    """
    sys = ("Du hilfst dem Teammanagement eines Esports-Vereins, auf Antworten von Unternehmen "
           "auf eine Sponsoring-Anfrage zu reagieren. Du schreibst Antwortentwürfe auf Deutsch, Sie-Form, "
           "freundlich, klar und menschlich. Niemals Gedankenstriche verwenden. Kein Telefonat, keinen Call "
           "und kein Treffen vorschlagen, alles Weitere läuft per Mail. Keine Floskeln. "
           "Erfinde keine Fakten, Leistungen, Zahlen oder Termine. Was nicht in den Vereinsinfos steht, "
           "versprichst du nicht, sondern sagst, dass ihr das gern klärt. Antworte nur als JSON.")
    ours = ""
    if our_mail:
        ours = f"Unsere ursprüngliche Anfrage:\nBetreff: {our_mail['subject']}\n{our_mail['body'][:1500]}\n"
    extra = f"\nZusätzliche Vorgabe vom Teammanagement: {hinweis}\n" if hinweis else ""
    user = f"""{_profile_text(profile, pakete=True)}
Ansprechpartner (unterschreibt): {profile.get('ansprechpartner', '')}

{ours}
Antwort des Unternehmens {company.get('name') or company.get('domain')} ({incoming['sender']}):
Betreff: {incoming['subject']}
{incoming['body'][:3000]}
{extra}
Aufgaben:
1. Ordne die Antwort ein: interesse (will mehr wissen oder ist offen), frage (konkrete Frage),
   absage (kein Interesse, kein Budget), abmeldung (will keine Mails mehr), sonstiges.
2. Fasse in einem Satz zusammen, was das Unternehmen will.
3. Schreibe einen passenden Antwortentwurf (60 bis 160 Wörter):
   - interesse/frage: bedanken, Fragen konkret beantworten, wenn passend die Sponsoring-Pakete
     mit Name, Preis und Leistung nennen (eine Zeile je Paket), Hinweis dass individuelle Absprachen
     möglich sind, nächsten Schritt per Mail vorschlagen.
   - absage: kurz und herzlich bedanken, Tür offen lassen, nicht nachhaken.
   - abmeldung: kurz bestätigen, dass keine weiteren Mails kommen.
   - sonstiges: passend reagieren.
   Anrede mit Namen, wenn der Absender einen Namen nennt, sonst "Sehr geehrte Damen und Herren".
   Grußformel und Signatur NICHT schreiben, die werden automatisch angehängt.
JSON-Felder: art, zusammenfassung, betreff (Antwortbetreff, beginnt mit "AW: " und dem Originalbetreff), text."""
    data = _chat([{"role": "system", "content": sys}, {"role": "user", "content": user}], 0.4)
    art = str(data.get("art", "sonstiges")).lower().strip()
    subject = incoming["subject"] or "Sponsoring Steckster Gadde"
    betreff = _clean(data.get("betreff") or "")
    if not betreff.lower().startswith(("aw:", "re:")):
        betreff = subject if subject.lower().startswith(("aw:", "re:")) else f"AW: {subject}"
    body = _clean(data.get("text", "")) + "\n\n" + profile["signatur"].strip()
    return {"art": art if art in KINDS else "sonstiges",
            "zusammenfassung": _clean(data.get("zusammenfassung", "")),
            "betreff": betreff, "text": body}
