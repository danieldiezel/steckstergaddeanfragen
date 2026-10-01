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


def sponsors_of_team(team_url: str, team_name: str) -> int:
    """Liest die Partner-/Sponsorenseite einer Team-Website aus."""
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
    if not pages:  # keine Partnerseite: Footer/Startseite nehmen, LLM filtert später
        candidates = scrape.external_company_links(final, html)
    new = 0
    for url, label in candidates[:25]:
        new += _add(url, label, "sponsor_von_team", team_name or scrape.root_domain(team_url))
    log.info("Team %s: %d Seiten, %d neue Kandidaten", team_name, len(pages), new)
    return new


# ---------- 1. DACH CS Liga ----------
def discover_dachcs(profile: dict, max_teams: int = 4) -> int:
    team_links = []
    for liga_url in profile.get("liga_urls", []):
        res = scrape.fetch(liga_url)
        if not res:
            continue
        for url, label in scrape.links(res[0], res[1]):
            if "dachcs.de" in url and "/team" in url.lower() and (url, label) not in team_links:
                team_links.append((url, label))
    new = 0
    for team_page, team_name in _rotate("dachcs_teams", team_links, max_teams):
        if db.team_scanned(team_page):
            continue
        db.mark_team(team_page, team_name)
        res = scrape.fetch(team_page)
        if not res:
            continue
        # externe Links auf der Teamseite = meist die Team-Website
        for site, _ in scrape.external_company_links(res[0], res[1])[:2]:
            new += sponsors_of_team(site, team_name)
        # zusätzlich suchen, falls das Team seine Sponsoren nur auf Socials zeigt
        for r in search(f'"{team_name}" CS2 Sponsor Partner', 5):
            if r["href"] and not scrape.is_ignored(r["href"]):
                new += sponsors_of_team(r["href"], team_name)
                break
    return new


# ---------- 2. Größere Esports-Teams ----------
def discover_esports_teams(profile: dict, max_queries: int = 2) -> int:
    new = 0
    for q in _rotate("esports_q", profile.get("suche_esports_teams", []), max_queries):
        for r in search(q, 8):
            url = r["href"]
            if url and not scrape.is_ignored(url):
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
