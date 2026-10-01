"""Webseiten laden und auswerten: Links, Partnerseiten, Mailadressen, Impressum."""
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.6",
}

# Domains, die nie Sponsor-Kandidaten sind (Plattformen, Socials, Shops, Ligen ...)
IGNORE_DOMAINS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com", "tiktok.com", "youtube.com",
    "youtu.be", "twitch.tv", "discord.gg", "discord.com", "linkedin.com", "xing.com",
    "google.com", "goo.gl", "apple.com", "play.google.com", "wikipedia.org", "github.com",
    "faceit.com", "esea.net", "hltv.org", "liquipedia.net", "steamcommunity.com",
    "steampowered.com", "dachcs.de", "99damage.de", "esl.com", "eslgaming.com",
    "start.gg", "challengermode.com", "battlefy.com", "spotify.com", "reddit.com",
    "wordpress.com", "wix.com", "jimdo.com", "shopify.com", "paypal.com", "paypal.me",
    "cloudflare.com", "gstatic.com", "googleapis.com", "fonts.googleapis.com",
    "bit.ly", "linktr.ee", "vimeo.com", "pinterest.com", "whatsapp.com", "t.me",
    "amazon.de", "amazon.com", "ebay.de", "streamlabs.com", "streamelements.com",
    "cookiebot.com", "usercentrics.eu", "matomo.org", "w3.org", "schema.org",
}

PARTNER_WORDS = ("sponsor", "partner", "unterstützer", "unterstuetzer", "supporter", "förderer")
IMPRESSUM_WORDS = ("impressum", "imprint", "kontakt", "contact", "legal")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*(?:\[at\]|\(at\)|\{at\}|\s at \s|\[@\]|\(@\))\s*"
    r"([A-Za-z0-9\-]+(?:\s*(?:\[dot\]|\(dot\)|\.|\s dot \s)\s*[A-Za-z0-9\-]+)+)",
    re.I,
)
BAD_EMAIL_PARTS = ("example", "sentry", "wixpress", "domain.", "email.", "@2x", ".png",
                   ".jpg", ".webp", ".svg", "noreply", "no-reply", "datenschutz", "privacy",
                   "dsb@", "bewerbung", "jobs@", "karriere")

# Bevorzugte Postfächer für Sponsoring-Anfragen, in dieser Reihenfolge
PREFERRED_LOCAL = ("sponsoring", "sponsor", "marketing", "partner", "kooperation", "pr",
                   "presse", "kommunikation", "info", "kontakt", "hallo", "hello", "office", "mail")

_session = requests.Session()
_session.headers.update(HEADERS)


def root_domain(url_or_host: str) -> str:
    host = urlparse(url_or_host).netloc if "://" in url_or_host else url_or_host
    host = host.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    # einfache Heuristik für co.uk & Co.
    if len(parts) > 2 and parts[-2] in ("co", "com", "org", "net") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def is_ignored(url: str) -> bool:
    d = root_domain(url)
    return not d or d in IGNORE_DOMAINS or "." not in d


def fetch(url: str, timeout: int = 15) -> tuple[str, str] | None:
    """Gibt (finale_url, html) zurück oder None."""
    try:
        r = _session.get(url, timeout=timeout, allow_redirects=True)
        ctype = r.headers.get("content-type", "")
        if r.status_code >= 400 or "html" not in ctype:
            return None
        time.sleep(1.0)  # höflich bleiben
        return r.url, r.text
    except requests.RequestException:
        return None


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def page_text(html: str, limit: int = 4000) -> str:
    s = soup_of(html)
    for t in s(["script", "style", "noscript", "svg"]):
        t.decompose()
    text = re.sub(r"\s+", " ", s.get_text(" ")).strip()
    return text[:limit]


def page_title(html: str) -> str:
    s = soup_of(html)
    og = s.find("meta", property="og:site_name")
    if og and og.get("content"):
        return og["content"].strip()
    return (s.title.string or "").strip() if s.title else ""


def links(base_url: str, html: str) -> list[tuple[str, str]]:
    """Alle Links als (absolute_url, sichtbarer_text_oder_alt)."""
    out = []
    for a in soup_of(html).find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        label = a.get_text(" ", strip=True)
        if not label:
            img = a.find("img")
            if img:
                label = img.get("alt") or img.get("title") or ""
        out.append((urljoin(base_url, href), label.strip()))
    return out


def find_subpages(base_url: str, html: str, words) -> list[str]:
    own = root_domain(base_url)
    found = []
    for url, label in links(base_url, html):
        if root_domain(url) != own:
            continue
        hay = (url + " " + label).lower()
        if any(w in hay for w in words) and url not in found:
            found.append(url)
    return found[:4]


def external_company_links(base_url: str, html: str) -> list[tuple[str, str]]:
    """Externe Links, die Firmen sein könnten (keine Socials/Plattformen)."""
    own = root_domain(base_url)
    seen, out = set(), []
    for url, label in links(base_url, html):
        if not url.startswith("http"):
            continue
        d = root_domain(url)
        if d == own or is_ignored(url) or d in seen:
            continue
        seen.add(d)
        out.append((f"https://{urlparse(url).netloc}", label))
    return out


def extract_emails(html: str) -> list[str]:
    text = html
    found = set(m.group(0) for m in EMAIL_RE.finditer(text))
    for m in soup_of(html).select('a[href^="mailto:"]'):
        found.add(m["href"][7:].split("?")[0])
    for m in OBFUSCATED_RE.finditer(page_text(html, 50000)):
        dom = re.sub(r"\s*(\[dot\]|\(dot\)|\s dot \s)\s*", ".", m.group(2), flags=re.I).replace(" ", "")
        found.add(f"{m.group(1)}@{dom}")
    clean = []
    for e in found:
        e = e.strip().strip(".").lower()
        if any(b in e for b in BAD_EMAIL_PARTS):
            continue
        if EMAIL_RE.fullmatch(e):
            clean.append(e)
    return sorted(set(clean))


def best_email(emails: list[str], domain: str) -> str:
    """Wählt die passendste Adresse; eigene Domain bevorzugt."""
    if not emails:
        return ""
    own = [e for e in emails if root_domain(e.split("@", 1)[1]) == domain] or emails

    def rank(e):
        local = e.split("@", 1)[0]
        for i, p in enumerate(PREFERRED_LOCAL):
            if local.startswith(p):
                return i
        return len(PREFERRED_LOCAL)

    return sorted(own, key=rank)[0]


def company_profile(website: str) -> dict | None:
    """Lädt Startseite + Impressum/Kontakt und liefert Name, Text, Mail."""
    res = fetch(website)
    if not res:
        return None
    final_url, html = res
    domain = root_domain(final_url)
    emails = extract_emails(html)
    texts = [page_text(html, 3000)]
    for sub in find_subpages(final_url, html, IMPRESSUM_WORDS)[:2]:
        r2 = fetch(sub)
        if r2:
            emails += extract_emails(r2[1])
            texts.append(page_text(r2[1], 1500))
    return {
        "domain": domain,
        "website": f"https://{urlparse(final_url).netloc}",
        "name": page_title(html),
        "email": best_email(sorted(set(emails)), domain),
        "text": "\n".join(texts)[:5000],
    }
