"""Versand per SMTP, Kopie in 'Gesendet' per IMAP, Antworten/Abmeldungen erkennen."""
import email
import imaplib
import logging
import smtplib
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

from . import config, db

log = logging.getLogger("mailer")

OPTOUT_WORDS = ("abmelden", "kein interesse", "keine weiteren", "nicht mehr kontaktieren",
                "unsubscribe", "austragen", "bitte entfernen", "keine werbung")


def send(to_email: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = formataddr((config.MAIL_FROM_NAME, config.MAIL_FROM))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=config.MAIL_FROM.split("@")[-1])
    msg["List-Unsubscribe"] = f"<mailto:{config.MAIL_FROM}?subject=Abmelden>"
    if config.MAIL_BCC:
        msg["Bcc"] = config.MAIL_BCC
    msg.set_content(body)

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as s:
        s.starttls()
        s.login(config.SMTP_USER, config.SMTP_PASS)
        s.send_message(msg)

    if config.IMAP_ENABLED:
        try:
            del msg["Bcc"]
            with imaplib.IMAP4_SSL(config.IMAP_HOST) as im:
                im.login(config.SMTP_USER, config.SMTP_PASS)
                im.append(f'"{config.IMAP_SENT_FOLDER}"', r"\Seen",
                          imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        except Exception as e:  # Versand hat trotzdem geklappt
            log.warning("Konnte Mail nicht in Gesendet ablegen: %s", e)


def _text_of(m: email.message.Message) -> str:
    if m.is_multipart():
        for part in m.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
        return ""
    return m.get_payload(decode=True).decode(m.get_content_charset() or "utf-8", "ignore")


def check_replies() -> list[dict]:
    """Neue Antworten von angeschriebenen Firmen. Abmeldungen landen auf der Blacklist."""
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
            _, msgdata = im.uid("fetch", str(uid), "(RFC822)")
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
            text = _text_of(m)[:1500]
            if any(w in (subject + " " + text).lower() for w in OPTOUT_WORDS):
                db.add_blacklist(row["domain"], "Abmeldung per Antwort")
                kind = "abmeldung"
            else:
                db.update_company(row["domain"], status="antwort")
                kind = "antwort"
            events.append({"typ": kind, "domain": row["domain"], "von": sender,
                           "betreff": subject, "auszug": text[:400]})
        if uids:
            db.set_state("imap_last_uid", max(uids))
    return events
