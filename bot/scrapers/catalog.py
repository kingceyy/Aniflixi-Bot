"""
Catalogue local FRAnime — remplace le scraping de /recherche.

Principe (identique à AnimeSamaApi de TMCooper) :
- On télécharge le catalogue complet une fois via l'API interne
  https://api.franime.fr/api/animes (2488 animes, JSON pur).
- On le met en cache sur disque (TTL 24h).
- La recherche se fait ENSUITE en local avec rapidfuzz : aucune requête
  réseau supplémentaire par recherche, aucune dépendance à ZenRows ou
  au rendu JS de /recherche.
"""
import os
import json
import time
import re
import unicodedata
from typing import Optional

import cloudscraper
from rapidfuzz import process, fuzz

from bot.config import Config

CATALOG_PATH = os.path.join(Config.DATA_DIR, "animes_catalog.json")
CATALOG_TTL = 24 * 3600  # 24h

# Mapping entre les codes langue du catalogue ("vo"/"vf") et les labels
# affichés dans les claviers Telegram existants ("VOSTFR"/"VF").
LANG_CATALOG_TO_LABEL = {"vo": "VOSTFR", "vf": "VF"}
LANG_LABEL_TO_CATALOG = {"vostfr": "vo", "vf": "vf"}

_catalog_cache: Optional[list] = None
_catalog_cache_ts: float = 0


def slugify(title: str) -> str:
    """
    Reproduit (approximativement) la génération de slug de franime.fr.
    Vérifié fonctionnel en pratique : franime.fr résout les pages via
    anime_id, pas via le slug — un slug imparfait (accents, orthographe
    Kitsu différente comme "shippuuden" vs "shippuden") n'empêche donc
    PAS la page de s'afficher correctement.
    """
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    title = title.lower()
    title = re.sub(r"[':!,.]", "", title)
    title = re.sub(r"[^a-z0-9]+", "-", title)
    return title.strip("-")


def fetch_catalog(force: bool = False) -> list:
    """Retourne le catalogue complet, depuis le cache mémoire, le cache
    disque, ou en le retéléchargeant si trop vieux."""
    global _catalog_cache, _catalog_cache_ts

    now = time.time()
    if not force and _catalog_cache is not None and (now - _catalog_cache_ts) < CATALOG_TTL:
        return _catalog_cache

    if not force and os.path.exists(CATALOG_PATH):
        age = now - os.path.getmtime(CATALOG_PATH)
        if age < CATALOG_TTL:
            with open(CATALOG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            _catalog_cache, _catalog_cache_ts = data, now
            return data

    os.makedirs(Config.DATA_DIR, exist_ok=True)
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    resp = scraper.get(
        "https://api.franime.fr/api/animes",
        headers={"Referer": "https://franime.fr/", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    _catalog_cache, _catalog_cache_ts = data, now
    return data


def search_catalog(query: str, limit: int = 5) -> list[dict]:
    """Recherche floue locale. Retourne une liste d'animes bruts du
    catalogue (pas encore mis en forme pour Telegram)."""
    catalog = fetch_catalog()

    choices: dict[str, dict] = {}
    for anime in catalog:
        names = {anime.get("titleO", ""), anime.get("title", "")}
        names.update((anime.get("titles") or {}).values())
        for name in names:
            if name:
                choices[name] = anime

    matches = process.extract(query, choices.keys(), scorer=fuzz.WRatio, limit=limit * 3)

    results = []
    seen_ids = set()
    for name, score, _ in matches:
        anime = choices[name]
        if anime["id"] in seen_ids or score < 60:
            continue
        seen_ids.add(anime["id"])
        results.append(anime)
        if len(results) >= limit:
            break
    return results


def get_anime_by_id(anime_id) -> Optional[dict]:
    """Lookup direct par id (int ou str) dans le catalogue en cache."""
    catalog = fetch_catalog()
    anime_id = str(anime_id)
    for anime in catalog:
        if str(anime.get("id")) == anime_id:
            return anime
    return None
