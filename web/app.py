"""
Dashboard fuer den Steckster Sponsor-Bot.

Liest dieselbe Datenbank wie der Bot und bietet die Telegram-Befehle als
Oberflaeche: Status, Pause/Weiter, Antworten freigeben, gesendete Mails,
Warteschlange, Quellen und Sperrliste.

Start (aus dem Repo-Ordner):  venv/bin/python -m web.app
"""

import json
import os
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from bot import config, db

BASE_DIR = Path(__file__).resolve().parent
PORT = config.env_int("DASHBOARD_PORT", 5080)
AUTH_FILE = Path(os.path.expanduser(config.env("DASHBOARD_AUTH_FILE", "~/pi-dashboard/auth.json")))
# Gleicher Login-Bereich wie das Pi-Dashboard -> die Pi-Control-App meldet sich automatisch an
AUTH_REALM = "Pi Dashboard"

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def load_auth():
    user, pw = config.env("DASHBOARD_USER"), config.env("DASHBOARD_PASSWORD")
    if user and pw:
        return {"username": user, "password": pw}
    if AUTH_FILE.exists():
        return json.loads(AUTH_FILE.read_text())
    return None


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        creds = load_auth()
        if creds is None:
            return jsonify({"error": "Kein Login konfiguriert (DASHBOARD_AUTH_FILE oder DASHBOARD_USER/PASSWORD)"}), 500
        auth = request.authorization
        if not auth or auth.username != creds.get("username") or auth.password != creds.get("password"):
            return jsonify({"error": "Nicht autorisiert"}), 401, {"WWW-Authenticate": f'Basic realm="{AUTH_REALM}"'}
        return f(*args, **kwargs)
    return decorated


def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


def notify(text):
    """Aktionen aus dem Dashboard auch in Telegram vermerken."""
    try:
        from bot import telegram
        telegram.send(text)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

@app.route("/")
@requires_auth
def index():
    return send_from_directory(BASE_DIR / "templates", "index.html")


@app.route("/api/overview")
@requires_auth
def api_overview():
    days = 30
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    with db.conn() as c:
        counts = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM companies GROUP BY status")}
        sent_total = c.execute("SELECT COUNT(*) FROM mails WHERE dry_run=0").fetchone()[0]
        sent_today = c.execute("SELECT COUNT(*) FROM mails WHERE dry_run=? AND sent_at LIKE ?",
                               (int(config.DRY_RUN), date.today().isoformat() + "%")).fetchone()[0]
        rep = {r["kind"] or "sonstiges": r["n"] for r in
               c.execute("SELECT kind, COUNT(*) n FROM replies GROUP BY kind")}
        open_replies = c.execute("SELECT COUNT(*) FROM replies WHERE status='offen'").fetchone()[0]
        queue = c.execute("SELECT COUNT(*) FROM companies WHERE status='angereichert' AND fit_score>=?",
                          (config.MIN_FIT_SCORE,)).fetchone()[0]
        blacklist = c.execute("SELECT COUNT(*) FROM blacklist").fetchone()[0]
        companies_answered = c.execute(
            "SELECT COUNT(DISTINCT domain) FROM replies WHERE kind NOT IN ('auto')").fetchone()[0]
        sent_per_day = {r["d"]: r["n"] for r in c.execute(
            "SELECT substr(sent_at,1,10) d, COUNT(*) n FROM mails WHERE dry_run=0 AND sent_at>=? GROUP BY d", (start,))}
        replies_per_day = {r["d"]: r["n"] for r in c.execute(
            "SELECT substr(received_at,1,10) d, COUNT(*) n FROM replies WHERE kind NOT IN ('auto') "
            "AND received_at>=? GROUP BY d", (start,))}
        sources = rows(c.execute("""
            SELECT CASE WHEN instr(c.source, ':')>0 THEN substr(c.source, 1, instr(c.source, ':')-1)
                        ELSE coalesce(nullif(c.source, ''), 'sonstige') END AS src,
                   COUNT(*) AS found,
                   SUM(c.status IN ('gesendet','antwort','abgemeldet') AND EXISTS
                       (SELECT 1 FROM mails m WHERE m.domain=c.domain AND m.dry_run=0)) AS sent,
                   SUM(EXISTS (SELECT 1 FROM replies r WHERE r.domain=c.domain AND r.kind NOT IN ('auto'))) AS replied
            FROM companies c GROUP BY src ORDER BY sent DESC, found DESC LIMIT 10"""))
    series = []
    for i in range(days):
        d = (date.today() - timedelta(days=days - 1 - i)).isoformat()
        series.append({"date": d, "sent": sent_per_day.get(d, 0), "replies": replies_per_day.get(d, 0)})

    last_disc = float(db.get_state("last_discovery", 0) or 0)
    now = datetime.now()
    in_window = (not (config.SEND_WEEKDAYS_ONLY and now.weekday() >= 5)
                 and config.SEND_START_HOUR <= now.hour < config.SEND_END_HOUR)
    interest = rep.get("interesse", 0) + rep.get("frage", 0)
    return jsonify({
        "paused": db.get_state("paused", "0") == "1",
        "dry_run": config.DRY_RUN,
        "sent_today": sent_today,
        "daily_limit": config.DAILY_LIMIT,
        "window": f"{config.SEND_START_HOUR}–{config.SEND_END_HOUR} Uhr"
                  + (", Mo–Fr" if config.SEND_WEEKDAYS_ONLY else ""),
        "in_window": in_window,
        "min_score": config.MIN_FIT_SCORE,
        "last_discovery": datetime.fromtimestamp(last_disc).isoformat(timespec="minutes") if last_disc else None,
        "search_requested": db.get_state("search_requested", "0") == "1",
        "counts": counts,
        "found_total": sum(counts.values()),
        "sent_total": sent_total,
        "replies": rep,
        "interest": interest,
        "open_replies": open_replies,
        "queue": queue,
        "blacklist": blacklist,
        "reply_rate": round(companies_answered / sent_total * 100, 1) if sent_total else None,
        "series": series,
        "sources": sources,
    })


@app.route("/api/replies")
@requires_auth
def api_replies():
    only_open = request.args.get("filter", "offen") == "offen"
    q = """SELECT r.*, c.name AS company_name, c.website
           FROM replies r LEFT JOIN companies c ON c.domain=r.domain
           WHERE r.kind NOT IN ('auto') {} ORDER BY (r.status='offen') DESC, r.id DESC LIMIT 100"""
    with db.conn() as c:
        return jsonify(rows(c.execute(q.format("AND r.status='offen'" if only_open else ""))))


@app.route("/api/mails")
@requires_auth
def api_mails():
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))
    search = request.args.get("q", "").strip().lower()
    where, params = "m.dry_run=0", []
    if search:
        where += " AND (lower(m.to_email) LIKE ? OR lower(c.name) LIKE ? OR lower(m.domain) LIKE ?)"
        params += [f"%{search}%"] * 3
    with db.conn() as c:
        data = rows(c.execute(f"""
            SELECT m.id, m.domain, m.to_email, m.subject, m.body, m.sent_at,
                   c.name, c.fit_score, c.fit_reason, c.source, c.source_detail, c.status, c.website,
                   EXISTS (SELECT 1 FROM replies r WHERE r.domain=m.domain AND r.kind NOT IN ('auto')) AS replied
            FROM mails m LEFT JOIN companies c ON c.domain=m.domain
            WHERE {where} ORDER BY m.id DESC LIMIT ? OFFSET ?""", (*params, limit, offset)))
    return jsonify(data)


@app.route("/api/queue")
@requires_auth
def api_queue():
    with db.conn() as c:
        return jsonify(rows(c.execute(
            "SELECT domain, name, email, website, fit_score, fit_reason, source, source_detail, created_at "
            "FROM companies WHERE status='angereichert' AND fit_score>=? "
            "ORDER BY fit_score DESC, created_at LIMIT 50", (config.MIN_FIT_SCORE,))))


@app.route("/api/blacklist")
@requires_auth
def api_blacklist():
    with db.conn() as c:
        return jsonify(rows(c.execute("SELECT * FROM blacklist ORDER BY added_at DESC LIMIT 300")))


# ---------------------------------------------------------------------------
# Aktionen (wie die Telegram-Befehle)
# ---------------------------------------------------------------------------

@app.route("/api/pause", methods=["POST"])
@requires_auth
def api_pause():
    paused = bool((request.get_json(silent=True) or {}).get("paused"))
    db.set_state("paused", "1" if paused else "0")
    notify("⏸ Versand pausiert (Dashboard)." if paused else "▶️ Versand läuft wieder (Dashboard).")
    return jsonify({"ok": True, "paused": paused})


@app.route("/api/search", methods=["POST"])
@requires_auth
def api_search():
    # Die Suche selbst laeuft im Bot-Prozess (siehe bot/main.py), hier nur anstossen
    db.set_state("search_requested", "1")
    return jsonify({"ok": True})


@app.route("/api/blacklist", methods=["POST"])
@requires_auth
def api_blacklist_add():
    value = str((request.get_json(silent=True) or {}).get("value", "")).strip().lower()
    if not value or " " in value or "." not in value:
        return jsonify({"ok": False, "error": "Bitte eine Domain oder Mailadresse eingeben"}), 400
    db.add_blacklist(value, "im Dashboard gesperrt")
    return jsonify({"ok": True})


@app.route("/api/blacklist/<path:value>", methods=["DELETE"])
@requires_auth
def api_blacklist_remove(value):
    with db.conn() as c:
        c.execute("DELETE FROM blacklist WHERE value=?", (value.lower(),))
    return jsonify({"ok": True})


@app.route("/api/companies/<domain>/block", methods=["POST"])
@requires_auth
def api_block_company(domain):
    db.add_blacklist(domain, "im Dashboard aus der Warteschlange genommen")
    return jsonify({"ok": True})


def _reply_or_404(rid):
    r = db.get_reply(rid)
    if not r:
        return None, (jsonify({"ok": False, "error": f"Antwort #{rid} gibt es nicht"}), 404)
    return r, None


@app.route("/api/replies/<int:rid>/send", methods=["POST"])
@requires_auth
def api_reply_send(rid):
    r, err = _reply_or_404(rid)
    if err:
        return err
    if r["status"] == "gesendet":
        return jsonify({"ok": False, "error": "Wurde schon gesendet"}), 409
    body = request.get_json(silent=True) or {}
    subject = (body.get("subject") or r["draft_subject"] or "").strip()
    text = (body.get("body") or r["draft_body"] or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Kein Antworttext"}), 400
    from bot import mailer
    try:
        mailer.send_reply(r["sender"], subject, text, r["message_id"], r["refs"])
    except Exception as e:
        return jsonify({"ok": False, "error": f"Senden fehlgeschlagen: {e}"}), 502
    db.update_reply(rid, draft_subject=subject, draft_body=text, status="gesendet", handled_at=db.now())
    notify(f"✅ Antwort #{rid} an {r['sender']} gesendet (Dashboard).")
    return jsonify({"ok": True})


@app.route("/api/replies/<int:rid>/rewrite", methods=["POST"])
@requires_auth
def api_reply_rewrite(rid):
    r, err = _reply_or_404(rid)
    if err:
        return err
    hint = str((request.get_json(silent=True) or {}).get("hint", "")).strip()
    from bot import replies
    try:
        d = replies._draft(rid, config.load_profile(), hint)
    except Exception as e:
        return jsonify({"ok": False, "error": f"KI-Fehler: {e}"}), 502
    return jsonify({"ok": True, "subject": d["betreff"], "body": d["text"], "kind": d["art"],
                    "summary": d["zusammenfassung"]})


@app.route("/api/replies/<int:rid>/discard", methods=["POST"])
@requires_auth
def api_reply_discard(rid):
    r, err = _reply_or_404(rid)
    if err:
        return err
    db.update_reply(rid, status="verworfen", handled_at=db.now())
    return jsonify({"ok": True})


if __name__ == "__main__":
    db.init()
    app.run(host="0.0.0.0", port=PORT, debug=False)
