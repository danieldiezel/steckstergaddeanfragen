"""SQLite: Firmen, Quellen, Mails, Blacklist, Status."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    domain        TEXT PRIMARY KEY,
    name          TEXT,
    website       TEXT,
    email         TEXT,
    description   TEXT,
    source        TEXT,          -- z.B. 'sponsor_von:Team XY', 'lokal', 'esports_suche'
    source_detail TEXT,
    fit_score     INTEGER,
    fit_reason    TEXT,
    status        TEXT DEFAULT 'neu',  -- neu | angereichert | ungeeignet | kein_kontakt | gesendet | antwort | abgemeldet | fehler
    created_at    TEXT,
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS mails (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    domain      TEXT,
    to_email    TEXT,
    subject     TEXT,
    body        TEXT,
    sent_at     TEXT,
    dry_run     INTEGER
);
CREATE TABLE IF NOT EXISTS blacklist (
    value  TEXT PRIMARY KEY,   -- Domain oder Mailadresse
    reason TEXT,
    added_at TEXT
);
CREATE TABLE IF NOT EXISTS teams_scanned (
    url TEXT PRIMARY KEY,
    name TEXT,
    scanned_at TEXT
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)


# --- state ---
def get_state(key, default=None):
    with conn() as c:
        r = c.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def set_state(key, value):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (key, str(value)))


# --- blacklist ---
def is_blacklisted(domain: str = "", email: str = "") -> bool:
    vals = [v.lower() for v in (domain, email) if v]
    if email and "@" in email:
        vals.append(email.split("@", 1)[1].lower())
    if not vals:
        return False
    with conn() as c:
        q = "SELECT 1 FROM blacklist WHERE value IN (%s)" % ",".join("?" * len(vals))
        return c.execute(q, vals).fetchone() is not None


def add_blacklist(value: str, reason: str = ""):
    value = value.lower().strip()
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO blacklist VALUES(?,?,?)", (value, reason, now()))
        c.execute("UPDATE companies SET status='abgemeldet', updated_at=? WHERE domain=? OR email=?",
                  (now(), value, value))


# --- companies ---
def add_company(domain, name="", website="", source="", source_detail="") -> bool:
    """Legt Firma an, falls neu. Gibt True zurück, wenn neu angelegt."""
    domain = domain.lower()
    with conn() as c:
        if c.execute("SELECT 1 FROM companies WHERE domain=?", (domain,)).fetchone():
            return False
        c.execute(
            "INSERT INTO companies(domain,name,website,source,source_detail,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'neu',?,?)",
            (domain, name, website, source, source_detail, now(), now()),
        )
        return True


def update_company(domain, **fields):
    if not fields:
        return
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE companies SET {cols} WHERE domain=?", (*fields.values(), domain))


def companies_by_status(status, limit=50):
    with conn() as c:
        return c.execute(
            "SELECT * FROM companies WHERE status=? ORDER BY created_at LIMIT ?", (status, limit)
        ).fetchall()


def next_to_contact(limit):
    with conn() as c:
        return c.execute(
            "SELECT * FROM companies WHERE status='angereichert' AND fit_score>=? "
            "ORDER BY fit_score DESC, created_at LIMIT ?",
            (config.MIN_FIT_SCORE, limit),
        ).fetchall()


def counts():
    with conn() as c:
        return {r["status"]: r["n"] for r in
                c.execute("SELECT status, COUNT(*) n FROM companies GROUP BY status")}


# --- mails ---
def log_mail(domain, to_email, subject, body, dry_run):
    with conn() as c:
        c.execute("INSERT INTO mails(domain,to_email,subject,body,sent_at,dry_run) VALUES(?,?,?,?,?,?)",
                  (domain, to_email, subject, body, now(), int(dry_run)))


def sent_today() -> int:
    """Zählt echte Mails, im Testmodus die Vorschauen (damit auch dort das Limit greift)."""
    today = datetime.now().date().isoformat()
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM mails WHERE sent_at LIKE ? AND dry_run=?",
                         (today + "%", int(config.DRY_RUN))).fetchone()[0]


def reset_previews():
    """Nach dem Testmodus: Vorschau-Firmen wieder in die Warteschlange."""
    with conn() as c:
        c.execute("UPDATE companies SET status='angereichert' WHERE status='vorschau'")


def already_mailed(email: str) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM mails WHERE lower(to_email)=? AND dry_run=0",
                         (email.lower(),)).fetchone() is not None


def last_mails(n=10):
    with conn() as c:
        return c.execute("SELECT * FROM mails ORDER BY id DESC LIMIT ?", (n,)).fetchall()


# --- teams ---
def team_scanned(url) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM teams_scanned WHERE url=?", (url,)).fetchone() is not None


def mark_team(url, name=""):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO teams_scanned VALUES(?,?,?)", (url, name, now()))
