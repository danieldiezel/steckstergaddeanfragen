"""Steckster Sponsor-Bot: sucht Sponsoren und schreibt sie vollautomatisch an.

Start:  python -m bot.main          (Dauerbetrieb, z.B. als systemd-Service)
        python -m bot.main --einmal (ein Durchlauf, zum Testen)
"""
import logging
import random
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, db, discovery, llm, mailer, scrape, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("primp", "ddgs", "httpx", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("main")
TZ = ZoneInfo(config.TIMEZONE)

DISCOVERY_EVERY = timedelta(hours=3)
ENRICH_BATCH = 6          # Firmen pro Anreicherungsrunde
REPLY_CHECK_EVERY = timedelta(minutes=30)


def local_now() -> datetime:
    return datetime.now(TZ)


def in_send_window(t: datetime) -> bool:
    if config.SEND_WEEKDAYS_ONLY and t.weekday() >= 5:
        return False
    return config.SEND_START_HOUR <= t.hour < config.SEND_END_HOUR


# ---------------- Schritte ----------------
def enrich(profile: dict, batch: int = ENRICH_BATCH) -> int:
    """Neue Firmen besuchen, Mail finden, per LLM bewerten."""
    done = 0
    for row in db.companies_by_status("neu", batch):
        domain = row["domain"]
        try:
            prof = scrape.company_profile(row["website"] or f"https://{domain}")
            if not prof:
                db.update_company(domain, status="fehler", fit_reason="Seite nicht erreichbar")
                continue
            if not prof["email"]:
                db.update_company(domain, status="kein_kontakt", name=prof["name"] or row["name"],
                                  description=prof["text"][:500])
                continue
            if db.is_blacklisted(domain=domain, email=prof["email"]) or db.already_mailed(prof["email"]):
                db.update_company(domain, status="ungeeignet", fit_reason="Blacklist oder schon angeschrieben")
                continue
            q = llm.qualify({**dict(row), **prof}, profile)
            db.update_company(
                domain, name=q.get("firmenname") or prof["name"], email=prof["email"],
                description=prof["text"][:1500], fit_score=q["score"],
                fit_reason=f"{q.get('branche', '')}: {q.get('grund', '')} || {q.get('aufhaenger', '')}",
                status="angereichert" if q["score"] >= config.MIN_FIT_SCORE else "ungeeignet",
            )
            done += 1
        except Exception as e:
            log.exception("Anreicherung %s fehlgeschlagen", domain)
            db.update_company(domain, status="fehler", fit_reason=str(e)[:200])
    return done


def _mail_for(row, profile) -> dict:
    branche, _, rest = (row["fit_reason"] or "").partition(":")
    grund, _, aufhaenger = rest.partition("||")
    qual = {"firmenname": row["name"], "branche": branche.strip(),
            "grund": grund.strip(), "aufhaenger": aufhaenger.strip()}
    return llm.write_mail(dict(row), qual, profile)


def send_one(profile: dict) -> str | None:
    """Schreibt die beste offene Firma an. Gibt eine Kurzinfo zurück."""
    rows = db.next_to_contact(1)
    if not rows:
        return None
    row = rows[0]
    if db.is_blacklisted(domain=row["domain"], email=row["email"]) or db.already_mailed(row["email"]):
        db.update_company(row["domain"], status="ungeeignet")
        return None
    mail = _mail_for(row, profile)
    if config.DRY_RUN:
        db.log_mail(row["domain"], row["email"], mail["betreff"], mail["text"], True)
        db.update_company(row["domain"], status="vorschau")
        telegram.send(f"🧪 TESTMODUS (nicht gesendet) an {row['email']}\n"
                      f"Betreff: {mail['betreff']}\n\n{mail['text']}")
        return f"(Test) {row['name']}"
    mailer.send(row["email"], mail["betreff"], mail["text"])
    db.log_mail(row["domain"], row["email"], mail["betreff"], mail["text"], False)
    db.update_company(row["domain"], status="gesendet")
    return f"{row['name']} <{row['email']}> (Score {row['fit_score']}, {row['source']})"


# ---------------- Telegram-Befehle ----------------
HILFE = """Steckster Sponsor-Bot
/status   Zahlen und heutiger Stand
/pause    Versand pausieren
/weiter   Versand fortsetzen
/letzte   letzte 5 Mails (Empfänger + Betreff)
/mail N   Text der N-letzten Mail (1 = neueste)
/queue    nächste Firmen in der Warteschlange
/sperren  domain.de oder mail@x.de dauerhaft sperren
/suche    Suche jetzt starten
/hilfe    diese Übersicht"""


def handle_command(cmd: str, arg: str, profile: dict) -> None:
    if cmd in ("/start", "/hilfe", "/help"):
        telegram.send(HILFE)
    elif cmd == "/status":
        c = db.counts()
        paused = db.get_state("paused", "0") == "1"
        telegram.send(
            f"{'⏸ PAUSIERT' if paused else '▶️ aktiv'}{' · TESTMODUS' if config.DRY_RUN else ''}\n"
            f"Heute gesendet: {db.sent_today()}/{config.DAILY_LIMIT}\n"
            + "\n".join(f"{k}: {v}" for k, v in sorted(c.items())))
    elif cmd == "/pause":
        db.set_state("paused", "1")
        telegram.send("⏸ Versand pausiert. Suche und Anreicherung laufen weiter.")
    elif cmd == "/weiter":
        db.set_state("paused", "0")
        telegram.send("▶️ Versand läuft wieder.")
    elif cmd == "/letzte":
        rows = db.last_mails(5)
        telegram.send("\n".join(f"{r['sent_at'][:16]} {'(Test) ' if r['dry_run'] else ''}"
                                f"{r['to_email']}: {r['subject']}" for r in rows) or "Noch keine Mails.")
    elif cmd == "/mail":
        n = int(arg) if arg.isdigit() else 1
        rows = db.last_mails(n)
        if len(rows) >= n:
            r = rows[n - 1]
            telegram.send(f"An: {r['to_email']}\nBetreff: {r['subject']}\n\n{r['body']}")
        else:
            telegram.send("Nicht gefunden.")
    elif cmd == "/queue":
        rows = db.next_to_contact(8)
        telegram.send("\n".join(f"{r['fit_score']}/10 {r['name']} ({r['domain']}) via {r['source']}"
                                for r in rows) or "Warteschlange leer.")
    elif cmd == "/sperren" and arg:
        db.add_blacklist(arg.strip(), "per Telegram gesperrt")
        telegram.send(f"🚫 {arg.strip()} gesperrt.")
    elif cmd == "/suche":
        telegram.send("🔎 Suche läuft ...")
        stats = discovery.run_discovery(profile)
        telegram.send(f"Neue Kandidaten: {stats}")
        db.set_state("last_discovery", time.time())


# ---------------- Hauptschleife ----------------
def next_send_time(t: datetime) -> float:
    """Verteilt das Tageslimit gleichmäßig mit Zufall über das Versandfenster."""
    window_min = (config.SEND_END_HOUR - config.SEND_START_HOUR) * 60
    gap = window_min / max(config.DAILY_LIMIT, 1)
    return t.timestamp() + random.uniform(0.6, 1.2) * gap * 60


def heartbeat(activity: str) -> None:
    db.set_state("heartbeat", time.time())
    db.set_state("activity", activity)


def sync_settings() -> None:
    changed = config.apply_overrides(db.get_state)
    if "DRY_RUN" in changed:
        if not config.DRY_RUN:
            db.reset_previews()
        telegram.send("Testmodus " + ("AN 🧪" if config.DRY_RUN else "AUS, Mails gehen jetzt echt raus ✉️"))
    if changed:
        log.info("Einstellungen vom Dashboard übernommen: %s", changed)


def process_dashboard_commands(profile: dict) -> None:
    """Befehle, die das Dashboard in die commands-Tabelle schreibt."""
    for c in db.pending_commands():
        cmd, result = c["cmd"], ""
        try:
            if cmd == "suche":
                heartbeat("Suche (vom Dashboard)")
                result = discovery.run_discovery(profile)
                db.set_state("last_discovery", time.time())
            elif cmd == "anreichern":
                heartbeat("Bewerte Firmen (vom Dashboard)")
                result = f"{enrich(profile, 10)} bewertet"
            elif cmd == "jetzt_senden":
                if db.sent_today() >= config.DAILY_LIMIT:
                    result = "Tageslimit erreicht"
                else:
                    heartbeat("Sende Mail (vom Dashboard)")
                    result = send_one(profile) or "keine Firma in der Warteschlange"
                    if result:
                        telegram.send(f"✉️ Gesendet per Dashboard ({db.sent_today()}/{config.DAILY_LIMIT}): {result}")
            elif cmd == "antworten_pruefen":
                evs = mailer.check_replies()
                for ev in evs:
                    db.log_reply(ev)
                result = f"{len(evs)} neue Antworten"
            else:
                result = "unbekannter Befehl"
        except Exception as e:
            log.exception("Dashboard-Befehl %s", cmd)
            result = f"Fehler: {e}"
        db.finish_command(c["id"], result)


def run_once(profile: dict) -> None:
    stats = discovery.run_discovery(profile)
    n = enrich(profile, 15)
    info = send_one(profile)
    telegram.send(f"Einzeldurchlauf: neu {stats}, bewertet {n}, Mail: {info or 'keine'}")


def main() -> None:
    db.init()
    config.apply_overrides(db.get_state)
    if not config.DRY_RUN:
        db.reset_previews()
    profile = config.load_profile()
    if "--einmal" in sys.argv:
        run_once(profile)
        return

    telegram.send("🤖 Steckster Sponsor-Bot gestartet" + (" (TESTMODUS)" if config.DRY_RUN else "")
                  + "\n/hilfe für Befehle")
    next_send = 0.0
    last_reply_check = 0.0

    while True:
        try:
            heartbeat("wartet")
            sync_settings()
            process_dashboard_commands(profile)
            for cmd, arg in telegram.poll(timeout=10):
                handle_command(cmd, arg, profile)

            now = local_now()
            ts = now.timestamp()

            if ts - float(db.get_state("last_discovery", 0)) > DISCOVERY_EVERY.total_seconds():
                heartbeat("Suche nach neuen Firmen")
                discovery.run_discovery(profile)
                db.set_state("last_discovery", ts)

            if db.companies_by_status("neu", 1) and len(db.next_to_contact(config.DAILY_LIMIT)) < config.DAILY_LIMIT * 2:
                heartbeat("Bewerte Firmen")
                enrich(profile)

            if ts - last_reply_check > REPLY_CHECK_EVERY.total_seconds():
                last_reply_check = ts
                try:
                    for ev in mailer.check_replies():
                        db.log_reply(ev)
                        icon = "🚫 Abmeldung" if ev["typ"] == "abmeldung" else "📬 ANTWORT"
                        telegram.send(f"{icon} von {ev['von']} ({ev['domain']})\n"
                                      f"Betreff: {ev['betreff']}\n\n{ev['auszug']}")
                except Exception as e:
                    log.warning("Antwortprüfung fehlgeschlagen: %s", e)

            paused = db.get_state("paused", "0") == "1"
            if (not paused and in_send_window(now) and ts >= next_send
                    and db.sent_today() < config.DAILY_LIMIT):
                heartbeat("Sende Mail")
                info = send_one(profile)
                if info:
                    telegram.send(f"✉️ Gesendet ({db.sent_today()}/{config.DAILY_LIMIT}): {info}")
                next_send = next_send_time(now)
                db.set_state("next_send", next_send)

            if now.hour == config.SEND_END_HOUR and db.get_state("report_day") != now.date().isoformat():
                db.set_state("report_day", now.date().isoformat())
                c = db.counts()
                telegram.send(f"📊 Tagesbericht: {db.sent_today()} Mails raus, "
                              f"{c.get('angereichert', 0)} in der Warteschlange, "
                              f"{c.get('antwort', 0)} Antworten insgesamt.")
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.exception("Fehler in der Hauptschleife")
            telegram.send(f"⚠️ Fehler: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
