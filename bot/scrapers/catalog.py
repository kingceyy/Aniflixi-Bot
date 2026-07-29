"""
Catalogue local FRAnime — remplace le scraping de /recherche.

Principe (identique à AnimeSamaApi de TMCooper) :
- On télécharge le catalogue complet une fois via l'API interne
  https://api.franime.fr/api/animes (2488 animes, JSON pur).
- On le met en cache sur disque (TTL 24h).
- La recherche se fait ENSUITE en local avec rapidfuzz : aucune requête
  réseau supplémentaire par recherche.

Contrainte connue : Cloudflare bloque (403) les IP datacenter (Koyeb,
etc.) sur cet endpoint, et le payload complet (plusieurs Mo) dépasse
la limite de taille de réponse des proxys ZenRows (413) — donc pas de
fallback réseau fiable depuis le serveur. Deux filets de sécurité :

1. fetch_catalog() retombe sur le cache disque existant (même périmé)
   si le refresh échoue, au lieu de faire planter la recherche.
2. set_catalog() permet d'importer manuellement un catalogue téléchargé
   depuis un réseau non bloqué (ex: téléphone via Termux) et envoyé au
   bot en pièce jointe Telegram — voir la commande /importcatalog.
"""
import os
import json
import time
import re
import logging
import unicodedata
from typing import Optional

import requests
import cloudscraper
from rapidfuzz import process, fuzz

from bot.config import Config

logger = logging.getLogger(__name__)

CATALOG_PATH = os.path.join(Config.DATA_DIR, "animes_catalog.json")
CATALOG_TTL = 24 * 3600  # 24h
CATALOG_URL = "https://api.franime.fr/api/animes"

LANG_CATALOG_TO_LABEL = {"vo": "VOSTFR", "vf": "VF"}
LANG_LABEL_TO_CATALOG = {"vostfr": "vo", "vf": "vf"}

_catalog_cache: Optional[list] = None
_catalog_cache_ts: float = 0
_scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)


def slugify(title: str) -> str:
    """
    Reproduit (approximativement) la génération de slug de franime.fr.
    Vérifié fonctionnel en pratique : franime.fr résout les pages via
    anime_id, pas via le slug — un slug imparfait n'empêche donc PAS
    la page de s'afficher correctement.
    """
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    title = title.lower()
    title = re.sub(r"[':!,.]", "", title)
    title = re.sub(r"[^a-z0-9]+", "-", title)
    return title.strip("-")


def _download_catalog() -> list:
    """Tente un accès direct (cloudscraper), puis ZenRows en dernier
    recours. Peut échouer entièrement depuis une IP datacenter — c'est
    à fetch_catalog() de gérer la dégradation."""
    try:
        resp = _scraper.get(
            CATALOG_URL,
            headers={"Referer": "https://franime.fr/", "Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status != 403 or not Config.ZENROWS_API_KEY:
            raise
        zr_resp = requests.get(
            "https://api.zenrows.com/v1/",
            params={
                "url": CATALOG_URL,
                "apikey": Config.ZENROWS_API_KEY,
                "premium_proxy": "true",
            },
            timeout=45,
        )
        zr_resp.raise_for_status()
        return zr_resp.json()


def _load_disk_cache() -> Optional[list]:
    if os.path.exists(CATALOG_PATH):
        with open(CATALOG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return None


def fetch_catalog(force: bool = False) -> list:
    """Retourne le catalogue complet. Ordre de priorité :
    cache mémoire frais -> cache disque frais -> retéléchargement ->
    (si le téléchargement échoue) cache disque même périmé.
    Ne lève une exception que si aucune source n'est disponible."""
    global _catalog_cache, _catalog_cache_ts

    now = time.time()
    if not force and _catalog_cache is not None and (now - _catalog_cache_ts) < CATALOG_TTL:
        return _catalog_cache

    if not force and os.path.exists(CATALOG_PATH):
        age = now - os.path.getmtime(CATALOG_PATH)
        if age < CATALOG_TTL:
            data = _load_disk_cache()
            _catalog_cache, _catalog_cache_ts = data, now
            return data

    try:
        data = _download_catalog()
    except Exception as e:
        stale = _load_disk_cache()
        if stale is not None:
            logger.warning(
                "Échec du rafraîchissement du catalogue (%s) — utilisation du "
                "cache disque existant, potentiellement périmé.", e
            )
            _catalog_cache, _catalog_cache_ts = stale, now
            return stale
        raise RuntimeError(
            f"Impossible de récupérer le catalogue ({e}) et aucun cache local "
            f"disponible. Utilise /importcatalog pour en importer un manuellement."
        ) from e

    os.makedirs(Config.DATA_DIR, exist_ok=True)
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    _catalog_cache, _catalog_cache_ts = data, now
    return data


def set_catalog(data: list) -> int:
    """Importe manuellement un catalogue (ex: depuis /importcatalog).
    Écrase le cache mémoire et le cache disque. Retourne le nombre
    d'animes importés."""
    global _catalog_cache, _catalog_cache_ts

    if not isinstance(data, list) or not data:
        raise ValueError("Le catalogue importé doit être une liste JSON non vide.")

    os.makedirs(Config.DATA_DIR, exist_ok=True)
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    _catalog_cache, _catalog_cache_ts = data, time.time()
    return len(data)


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
