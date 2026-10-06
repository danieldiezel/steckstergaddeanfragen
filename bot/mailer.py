"""Versand per SMTP, Ablage per IMAP (Gesendet, Entwürfe), Antworten erkennen."""
import base64
import email
import html
import imaplib
import logging
import re
import smtplib
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

from . import config, db

log = logging.getLogger("mailer")

OPTOUT_WORDS = ("abmelden", "kein interesse", "keine weiteren", "nicht mehr kontaktieren",
                "unsubscribe", "austragen", "bitte entfernen", "keine werbung")
AUTO_SUBJECT = re.compile(r"abwesen|out of office|automatische antwort|auto(matic)?[- ]?reply|"
                          r"autoreply|urlaub|nicht im büro|delivery status|unzustellbar|undeliverable|"
                          r"mail delivery|returned mail", re.I)

# Zeilen, ab denen in einer Antwort die zitierte Originalmail beginnt
QUOTE_START = re.compile(
    r"^\s*(>|-{2,}\s*(original|ursprüngliche)|von:\s|from:\s|gesendet:\s|sent:\s|"
    r"am .{4,80}schrieb|on .{4,80}wrote|_{5,})", re.I)


# ---------------------------------------------------------------- Versand
def _new_message(to_email: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((config.MAIL_FROM_NAME, config.MAIL_FROM))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=config.MAIL_FROM.split("@")[-1])
    msg.set_content(body)
    return msg


def _thread_headers(msg: EmailMessage, in_reply_to: str = "", refs: str = "") -> None:
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = f"{refs} {in_reply_to}".strip()


def _smtp_send(msg: EmailMessage) -> None:
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as s:
        s.starttls()
        s.login(config.SMTP_USER, config.SMTP_PASS)
        s.send_message(msg)


def send(to_email: str, subject: str, body: str) -> None:
    msg = _new_message(to_email, subject, body)
    msg["List-Unsubscribe"] = f"<mailto:{config.MAIL_FROM}?subject=Abmelden>"
    if config.MAIL_BCC:
        msg["Bcc"] = config.MAIL_BCC
    _smtp_send(msg)
    if "Bcc" in msg:
        del msg["Bcc"]
    _imap_store(msg, "sent")


def send_reply(to_email: str, subject: str, body: str, in_reply_to: str = "", refs: str = "") -> None:
    """Antwort im selben Mailverlauf (In-Reply-To/References)."""
    msg = _new_message(to_email, subject, body)
    _thread_headers(msg, in_reply_to, refs)
    _smtp_send(msg)
    _imap_store(msg, "sent")


def save_draft(to_email: str, subject: str, body: str, in_reply_to: str = "", refs: str = "") -> bool:
    """Legt den Antwortvorschlag im Postfach unter 'Entwürfe' ab."""
    msg = _new_message(to_email, subject, body)
    _thread_headers(msg, in_reply_to, refs)
    return _imap_store(msg, "drafts")


# ---------------------------------------------------------------- IMAP-Ordner
def _utf7_imap(name: str) -> str:
    """Ordnernamen mit Umlauten für IMAP kodieren (modified UTF-7, RFC 3501)."""
    out, buf = [], ""

    def flush():
        nonlocal buf
        if buf:
            b = base64.b64encode(buf.encode("utf-16-be")).decode().rstrip("=").replace("/", ",")
            out.append(f"&{b}-")
            buf = ""
    for ch in name:
        if 0x20 <= ord(ch) <= 0x7e:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf += ch
    flush()
    return "".join(out)


def _find_folder(im: imaplib.IMAP4_SSL, kind: str) -> str:
    """Sucht den Ordner über die Kennzeichnung des Servers (\\Sent, \\Drafts), sonst den Namen aus .env."""
    flag = {"sent": r"\Sent", "drafts": r"\Drafts"}[kind]
    try:
        _, folders = im.list()
        for line in folders or []:
            line = line.decode(errors="ignore")
            if flag.lower() in line.lower():
                m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    name = config.IMAP_SENT_FOLDER if kind == "sent" else config.IMAP_DRAFTS_FOLDER
    return _utf7_imap(name)


def _imap_store(msg: EmailMessage, kind: str) -> bool:
    if not config.IMAP_ENABLED:
        return False
    try:
        with imaplib.IMAP4_SSL(config.IMAP_HOST) as im:
            im.login(config.SMTP_USER, config.SMTP_PASS)
            folder = _find_folder(im, kind)
            flags = r"(\Seen)" if kind == "sent" else r"(\Seen \Draft)"
            typ, _ = im.append(f'"{folder}"', flags, imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            return typ == "OK"
    except Exception as e:
        log.warning("Konnte Mail nicht in %s ablegen: %s", kind, e)
        return False


# ---------------------------------------------------------------- Antworten lesen
def _decode(part) -> str:
    payload = part.get_payload(decode=True) or b""
    return payload.decode(part.get_content_charset() or "utf-8", "ignore")


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    return re.sub(r"\n\s*\n+", "\n\n", html.unescape(raw)).strip()


def text_of(m: email.message.Message) -> str:
    plain, htm = "", ""
    for part in (m.walk() if m.is_multipart() else [m]):
        if part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and not plain:
            plain = _decode(part)
        elif ctype == "text/html" and not htm:
            htm = _decode(part)
    return plain or _html_to_text(htm)


def strip_quote(text: str) -> str:
    """Nur den neuen Teil der Antwort, ohne die zitierte Originalmail."""
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if QUOTE_START.match(line) and len("\n".join(lines).strip()) > 0:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _is_auto(m: email.message.Message, subject: str) -> bool:
    if m.get("Auto-Submitted", "no").lower() != "no":
        return True
    if m.get("X-Autoreply") or m.get("X-Autorespond") or m.get("Precedence", "").lower() in ("auto_reply", "bulk", "junk"):
        return True
    return bool(AUTO_SUBJECT.search(subject))


def check_replies() -> list[dict]:
    """Neue Mails von angeschriebenen Firmen. Speichert sie in der DB, Abmeldungen gehen auf die Sperrliste."""
    if not config.IMAP_ENABLED:
        return []
    events = []
    last_uid = int(db.get_state("imap_last_uid", 0))
    with imaplib.IMAP4_SSL(config.IMAP_HOST) as im:
        im.login(config.SMTP_USER, config.SMTP_PASS)
        im.select("INBOX", readonly=True)
        _, data = im.uid("search", None, f"UID {last_uid + 1}:*")
        uids = [int(u) for u in data[0].split() if int(u) > last_uid]
        for uid in uids[-100:]:
            _, msgdata = im.uid("fetch", str(uid), "(BODY.PEEK[])")  # PEEK: Mail bleibt ungelesen
            m = email.message_from_bytes(msgdata[0][1])
            sender = parseaddr(m.get("From", ""))[1].lower()
            sender_domain = sender.split("@")[-1]
            with db.conn() as c:
                row = c.execute(
                    "SELECT domain FROM companies WHERE status IN ('gesendet','antwort') "
                    "AND (lower(email)=? OR domain=? OR ? LIKE '%.' || domain)",
                    (sender, sender_domain, sender_domain)).fetchone()
            if not row:
                continue
            subject = str(make_header(decode_header(m.get("Subject", ""))))
            full = text_of(m)
            new_part = strip_quote(full) or full
            if _is_auto(m, subject):
                kind = "auto"
            elif any(w in (subject + " " + new_part).lower() for w in OPTOUT_WORDS):
                db.add_blacklist(row["domain"], "Abmeldung per Antwort")
                kind = "abmeldung"
            else:
                db.update_company(row["domain"], status="antwort")
                kind = "antwort"
            events.append({
                "typ": kind, "domain": row["domain"], "von": sender, "betreff": subject,
                "text": new_part[:4000], "auszug": new_part[:400],
                "message_id": m.get("Message-ID", "").strip(),
                "refs": " ".join((m.get("References", "") or "").split()),
            })
        if uids:
            db.set_state("imap_last_uid", max(uids))
    return events
