"""Kandidaten finden.

Vier Quellen, jeweils in kleinen Häppchen pro Lauf (rotierend):
  1. DACH CS Liga: Teams aus den Liga-Seiten, deren Websites, deren Sponsoren
  2. Größere DACH-Esports-Teams: per Suche, deren Partnerseiten
  3. Lokale Firmen Rhein-Main: per Suche direkt
  4. Branchen, die generell zu Esports passen: per Suche direkt
"""
import logging
from urllib.parse import urlparse

from . import db, scrape
from .search import search

log = logging.getLogger("discovery")


def _rotate(key: str, items: list, n: int) -> list:
    """Gibt n Elemente zurück und merkt sich die Position für den nächsten Lauf."""
    if not items:
        return []
    start = int(db.get_state(f"rot_{key}", 0)) % len(items)
    chunk = [items[(start + i) % len(items)] for i in range(min(n, len(items)))]
    db.set_state(f"rot_{key}", start + len(chunk))
    return chunk


def _add(url: str, label: str, source: str, detail: str) -> bool:
    if scrape.is_ignored(url):
        return False
    d = scrape.root_domain(url)
    if db.is_blacklisted(domain=d):
        return False
    return db.add_company(d, name=label[:120], website=f"https://{urlparse(url).netloc}",
                          source=source, source_detail=detail)


def sponsors_of_team(team_url: str, team_name: str, fallback: bool = False) -> int:
    """Liest die Partner-/Sponsorenseite einer Team-Website aus.

    fallback=True: ohne Partnerseite auch Links der Startseite nehmen (nur bei sicheren Teamseiten).
    """
    if db.team_scanned(team_url):
        return 0
    db.mark_team(team_url, team_name)
    res = scrape.fetch(team_url)
    if not res:
        return 0
    final, html = res
    pages = scrape.find_subpages(final, html, scrape.PARTNER_WORDS)
    candidates = []
    for p in pages:
        r = scrape.fetch(p)
        if r:
            candidates += scrape.external_company_links(r[0], r[1])
    if not pages and fallback:  # keine Partnerseite: Footer/Startseite, LLM filtert später
        candidates = scrape.external_company_links(final, html)
    new = 0
    for url, label in candidates[:25]:
        new += _add(url, label, "sponsor_von_team", team_name or scrape.root_domain(team_url))
    log.info("Team %s: %d Seiten, %d neue Kandidaten", team_name, len(pages), new)
    return new


# ---------- 1. DACH CS Liga ----------
NEWS_HINTS = ("news", "artikel", "magazin", "zeitung", "blog", "wiki", "transfer", "forum")


def _dachcs_team_name(html: str) -> str:
    s = scrape.soup_of(html)
    h = s.find(["h1", "h2"])
    name = h.get_text(" ", strip=True) if h else scrape.page_title(html)
    for sep in (" | ", " - ", " – "):
        name = name.split(sep)[0]
    return name.strip()


def discover_dachcs(profile: dict, max_teams: int = 4) -> int:
    """Teamnamen aus DACH CS (Ranking/Coverage), dann Team-Website per Suche finden."""
    team_links = []
    for liga_url in profile.get("liga_urls", []):
        res = scrape.fetch(liga_url)
        if not res:
            continue
        for url, _ in scrape.links(res[0], res[1]):
            if "dachcs.de" in url and "/team/" in url.lower() and url not in team_links:
                team_links.append(url)
    log.info("DACH CS: %d Teamseiten gefunden", len(team_links))
    own = profile.get("eigener_teamname", "steckster").lower()
    new = 0
    for team_page in _rotate("dachcs_teams", team_links, max_teams):
        if db.team_scanned(team_page):
            continue
        res = scrape.fetch(team_page)
        if not res:
            continue
        name = _dachcs_team_name(res[1])
        db.mark_team(team_page, name)
        if not name or own in name.lower():
            continue
        for r in search(f'"{name}" CS2 Team Sponsoren Partner', 6):
            url, title = r["href"], r["title"].lower()
            if not url or scrape.is_ignored(url) or any(w in url.lower() for w in NEWS_HINTS):
                continue
            if name.lower().split()[0] in (title + url.lower()):  # Treffer gehört zum Team
                new += sponsors_of_team(f"https://{urlparse(url).netloc}", name, fallback=True)
                break
    return new


# ---------- 2. Größere Esports-Teams ----------
def discover_esports_teams(profile: dict, max_queries: int = 2) -> int:
    new = 0
    for q in _rotate("esports_q", profile.get("suche_esports_teams", []), max_queries):
        for r in search(q, 8):
            url = r["href"]
            if url and not scrape.is_ignored(url) and not any(w in url.lower() for w in NEWS_HINTS):
                # nur Seiten mit echter Partner-/Sponsorenseite, keine Artikel
                new += sponsors_of_team(f"https://{urlparse(url).netloc}", r["title"][:80])
    return new


# ---------- 3./4. Direkte Firmensuche ----------
def discover_direct(profile: dict, key: str, source: str, max_queries: int = 2) -> int:
    new = 0
    for q in _rotate(key, profile.get(key, []), max_queries):
        for r in search(q, 10):
            url = r["href"]
            if url:
                new += _add(url, r["title"], source, q)
    return new


def run_discovery(profile: dict) -> dict:
    stats = {
        "dachcs": discover_dachcs(profile),
        "esports_teams": discover_esports_teams(profile),
        "lokal": discover_direct(profile, "suche_lokal", "lokal"),
        "branchen": discover_direct(profile, "suche_branchen", "branche"),
    }
    log.info("Discovery: %s", stats)
    return stats
