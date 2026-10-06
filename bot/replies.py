"""Antworten von Firmen: Vorschlag erstellen, im Postfach ablegen, per Telegram freigeben."""
import logging

from . import config, db, llm, mailer, telegram

log = logging.getLogger("replies")

ICONS = {"interesse": "🔥 INTERESSE", "frage": "❓ FRAGE", "absage": "👋 Absage",
         "abmeldung": "🚫 Abmeldung", "sonstiges": "📬 Antwort"}


def _draft(reply_id: int, profile: dict, hinweis: str = "") -> dict:
    r = db.get_reply(reply_id)
    company = db.get_company(r["domain"])
    our = db.last_mail_to(r["domain"])
    d = llm.write_reply(dict(company) if company else {"domain": r["domain"]},
                        {"sender": r["sender"], "subject": r["subject"], "body": r["body"]},
                        dict(our) if our else None, profile, hinweis)
    db.update_reply(reply_id, kind=d["art"], summary=d["zusammenfassung"],
                    draft_subject=d["betreff"], draft_body=d["text"], status="offen")
    if d["art"] == "abmeldung":
        db.add_blacklist(r["domain"], "Abmeldung per Antwort (KI erkannt)")
    if config.REPLY_DRAFTS:
        mailer.save_draft(r["sender"], d["betreff"], d["text"], r["message_id"], r["refs"])
    return d


def _announce(reply_id: int, d: dict, neu: bool = False) -> None:
    r = db.get_reply(reply_id)
    head = "✏️ NEUER VORSCHLAG" if neu else ICONS.get(d["art"], "📬 Antwort")
    telegram.send(
        f"{head} #{reply_id} von {r['sender']}\n"
        f"Betreff: {r['subject']}\n"
        f"Worum es geht: {d['zusammenfassung'] or '-'}\n\n"
        f"Die Mail (Auszug):\n{r['body'][:600]}\n\n"
        f"──── Antwortvorschlag ────\n"
        f"Betreff: {d['betreff']}\n\n{d['text']}\n"
        f"──────────────────\n"
        f"/senden {reply_id}   so abschicken\n"
        f"/neu {reply_id} <Hinweis>   neu formulieren, z.B. /neu {reply_id} kürzer, nur Silber und Gold nennen\n"
        f"/verwerfen {reply_id}   selbst antworten / ignorieren\n"
        f"Der Entwurf liegt auch im Postfach unter Entwürfe."
    )


def handle_incoming(events: list[dict], profile: dict) -> None:
    """Wird nach mailer.check_replies() aufgerufen."""
    for ev in events:
        rid = db.add_reply(domain=ev["domain"], sender=ev["von"], subject=ev["betreff"], body=ev["text"],
                           message_id=ev["message_id"], refs=ev["refs"], kind=ev["typ"], status="info")
        if ev["typ"] == "auto":
            telegram.send(f"🤖 Automatische Antwort von {ev['von']} ({ev['betreff']}), kein Vorschlag nötig.")
            continue
        if ev["typ"] == "abmeldung":
            telegram.send(f"🚫 Abmeldung von {ev['von']} ({ev['domain']}), ist gesperrt.\n\n{ev['auszug']}")
            continue
        try:
            d = _draft(rid, profile)
            _announce(rid, d)
        except Exception as e:
            log.exception("Antwortvorschlag fehlgeschlagen")
            telegram.send(f"📬 ANTWORT #{rid} von {ev['von']} ({ev['domain']})\n"
                          f"Betreff: {ev['betreff']}\n\n{ev['auszug']}\n\n"
                          f"⚠️ Kein Vorschlag möglich ({e}). Neu versuchen: /neu {rid}")


def command(cmd: str, arg: str, profile: dict) -> bool:
    """Telegram-Befehle rund um Antworten. Gibt True zurück, wenn der Befehl hierher gehörte."""
    if cmd == "/antworten":
        rows = db.open_replies(10)
        telegram.send("\n".join(f"#{r['id']} {ICONS.get(r['kind'], '')} {r['sender']}: {r['summary'] or r['subject']}"
                                for r in rows) or "Keine offenen Antworten.")
        return True
    if cmd not in ("/senden", "/neu", "/verwerfen", "/vorschlag"):
        return False

    parts = arg.split(maxsplit=1)
    if not parts or not parts[0].lstrip("#").isdigit():
        telegram.send(f"Bitte mit Nummer, z.B. {cmd} 3")
        return True
    rid = int(parts[0].lstrip("#"))
    rest = parts[1] if len(parts) > 1 else ""
    r = db.get_reply(rid)
    if not r:
        telegram.send(f"Antwort #{rid} gibt es nicht.")
        return True

    if cmd == "/vorschlag":
        if r["draft_body"]:
            _announce(rid, {"art": r["kind"], "zusammenfassung": r["summary"],
                            "betreff": r["draft_subject"], "text": r["draft_body"]})
        else:
            telegram.send(f"Für #{rid} gibt es noch keinen Vorschlag. Erstellen: /neu {rid}")
    elif cmd == "/neu":
        try:
            telegram.send(f"✏️ Schreibe #{rid} neu ...")
            _announce(rid, _draft(rid, profile, rest), neu=True)
        except Exception as e:
            telegram.send(f"⚠️ Fehler beim Neuschreiben: {e}")
    elif cmd == "/verwerfen":
        db.update_reply(rid, status="verworfen", handled_at=db.now())
        telegram.send(f"🗑 #{rid} verworfen. Der Entwurf im Postfach bleibt liegen, falls du ihn doch nutzen willst.")
    elif cmd == "/senden":
        if r["status"] == "gesendet":
            telegram.send(f"#{rid} wurde schon gesendet.")
        elif not r["draft_body"]:
            telegram.send(f"Für #{rid} gibt es keinen Vorschlag. Erstellen: /neu {rid}")
        else:
            try:
                mailer.send_reply(r["sender"], r["draft_subject"], r["draft_body"], r["message_id"], r["refs"])
                db.update_reply(rid, status="gesendet", handled_at=db.now())
                telegram.send(f"✅ Antwort an {r['sender']} gesendet (liegt in Gesendete Objekte). "
                              f"Den Entwurf im Postfach kannst du löschen.")
            except Exception as e:
                telegram.send(f"⚠️ Senden fehlgeschlagen: {e}")
    return True
