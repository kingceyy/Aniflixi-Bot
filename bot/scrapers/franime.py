"""
Scraper FRAnime (franime.fr)
- Calendrier hebdomadaire
- Recherche d'anime (catalogue local + rapidfuzz)
- Fiche anime (saisons, langues, épisodes)
- Résolution des liens de streaming
"""
import re
import time
import json
import logging
from typing import List, Dict, Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
import cloudscraper
from bs4 import BeautifulSoup, Tag
from rapidfuzz import fuzz

from bot.config import Config
from bot.scrapers.catalog import (
    fetch_catalog,
    search_catalog,
    get_anime_by_id,
    slugify,
    LANG_CATALOG_TO_LABEL,
    LANG_LABEL_TO_CATALOG,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": "https://franime.fr/",
}


class FranimeScraper:
    def __init__(self):
        self.session = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        # On n'écrase PAS le User-Agent : cloudscraper le calibre précisément
        # sur l'empreinte TLS (JA3) qu'il simule pour le navigateur choisi ci-dessus.
        # Le remplacer casserait cette cohérence et re-déclencherait le blocage anti-bot.
        self.session.headers.update(HEADERS)
        self.base = Config.FRANIME_BASE
        self._calendar_cache: Optional[Dict[str, List[Dict]]] = None
        self._calendar_cache_ts: float = 0
        self._calendar_cache_ttl: int = 900  # 15 min : évite de re-solliciter ZenRows à chaque clic de pagination

    # ────────────────────────────────
    # Helpers parsing
    # ────────────────────────────────
    @staticmethod
    def _leaves(soup: BeautifulSoup) -> List[Tag]:
        """Tags feuilles (sans enfant) dans l'ordre du document."""
        return [t for t in soup.find_all(True) if t.find(True) is None]

    @staticmethod
    def _find_leaf(leaves: List[Tag], pattern: str, flags=0) -> Optional[int]:
        rx = re.compile(pattern, flags)
        for i, t in enumerate(leaves):
            if rx.match(t.get_text(strip=True)):
                return i
        return None

    @staticmethod
    def _texts_between(leaves: List[Tag], start: Optional[int], end: Optional[int]) -> List[str]:
        if start is None:
            return []
        end = end if end is not None else len(leaves)
        out = []
        for i in range(start + 1, end):
            txt = leaves[i].get_text(strip=True)
            if txt:
                out.append(txt)
        return out

    def _fetch(self, url: str, js_render: bool = False) -> str:
        if Config.ZENROWS_API_KEY:
            try:
                params = {
                    "url": url,
                    "apikey": Config.ZENROWS_API_KEY,
                    "premium_proxy": "true",
                }
                if js_render:
                    params["js_render"] = "true"
                    params["wait"] = "3000"
                resp = requests.get(
                    "https://api.zenrows.com/v1/",
                    params=params,
                    timeout=45 if js_render else 30,
                )
                resp.raise_for_status()
                return resp.text
            except requests.exceptions.RequestException as e:
                logger.warning("ZenRows fetch failed for %s (js_render=%s): %s", url, js_render, e)

        resp = self.session.get(url, timeout=20)
        resp.raise_for_status()
        return resp.text

    # ────────────────────────────────
    # 1. Calendrier hebdomadaire
    # ────────────────────────────────
    def get_calendar(self, force_refresh: bool = False) -> Dict[str, List[Dict]]:
        """
        Scrape franime.fr/calendrier
        Retourne: {"lundi": [{titre, heure, episode, lang, slug, anime_id, poster}], ...}
        """
        now = time.time()
        if (
            not force_refresh
            and self._calendar_cache is not None
            and (now - self._calendar_cache_ts) < self._calendar_cache_ttl
        ):
            return self._calendar_cache

        html = self._fetch(f"{self.base}/calendrier")
        soup = BeautifulSoup(html, "html.parser")

        day_map = {
            "lundi": "lundi", "mardi": "mardi", "mercredi": "mercredi",
            "jeudi": "jeudi", "vendredi": "vendredi", "samedi": "samedi", "dimanche": "dimanche"
        }

        planning = {d: [] for d in day_map.values()}

        body = soup.find("body")
        if not body:
            return planning

        all_elements = body.find_all(["div", "section", "article", "a", "span", "h2", "h3"])
        current_day = None

        for elem in all_elements:
            text = elem.get_text(strip=True).lower()
            for day_fr, day_key in day_map.items():
                if text.startswith(day_fr) and len(text) < 20:
                    current_day = day_key
                    break

            if current_day and elem.name == "a":
                href = elem.get("href", "")
                if "/anime/" in href:
                    card = self._parse_calendar_card(elem, href)
                    if card:
                        planning[current_day].append(card)

        if all(len(v) == 0 for v in planning.values()):
            planning = self._get_calendar_fallback(soup)

        self._calendar_cache = planning
        self._calendar_cache_ts = time.time()
        return planning

    def _parse_calendar_card(self, elem: Tag, href: str) -> Optional[Dict]:
        """Extrait les infos d'une card du calendrier à partir de l'élément <a>."""
        texts = [
            t.get_text(strip=True)
            for t in elem.find_all(True)
            if not t.find_all(True) and t.get_text(strip=True)
        ]
        heure = None
        for t in texts:
            m = re.match(r"^(\d{1,2})[h:](\d{2})$", t)
            if m:
                heure = f"{m.group(1).zfill(2)}:{m.group(2)}"
                break

        titre = None
        for t in texts:
            if len(t) > 3 and not re.match(r"^(\d{1,2})[h:](\d{2})$", t) and t not in ["VF", "VOSTFR", "VA", "VJ"]:
                if not titre or len(t) > len(titre):
                    titre = t

        episode = None
        for t in texts:
            m = re.match(r"^[Ee](\d+)$", t)
            if m:
                episode = int(m.group(1))
                break
            m = re.match(r"^[Eé]p(?:isode)?\s*(\d+)$", t, re.I)
            if m:
                episode = int(m.group(1))
                break

        lang = "vostfr"
        all_text = " ".join(texts).lower()
        if "vf" in all_text and "vostfr" not in all_text:
            lang = "vf"
        elif "vostfr" in all_text:
            lang = "vostfr"

        poster = None
        img = elem.find("img")
        if img:
            poster = img.get("src") or img.get("data-src")

        slug = href.split("/anime/")[-1].split("?")[0].strip("/")
        qs = parse_qs(urlparse(href).query)
        anime_id = (qs.get("anime_id") or [None])[0]

        if not titre:
            return None

        return {
            "titre": titre,
            "heure": heure,
            "episode": episode,
            "lang": lang,
            "slug": slug,
            "anime_id": anime_id,
            "poster": poster,
        }

    def _get_calendar_fallback(self, soup: BeautifulSoup) -> Dict[str, List[Dict]]:
        planning = {d: [] for d in ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]}
        return planning

    # ────────────────────────────────
    # 2. Recherche d'anime — catalogue local (plus de scraping /recherche)
    # ────────────────────────────────
    def search_anime(self, query: str, limit: int = 5) -> List[Dict]:
        """
        Recherche floue sur le catalogue local (mis en cache 24h, voir
        bot/scrapers/catalog.py). Zéro appel réseau si le cache est chaud.
        """
        try:
            raw_results = search_catalog(query, limit=limit)
        except Exception as e:
            logger.warning("Échec de la recherche catalogue pour %r: %s", query, e)
            return []

        results = []
        for anime in raw_results:
            title = anime.get("title") or anime.get("titleO") or ""
            score = fuzz.token_set_ratio(query.lower(), title.lower())
            results.append({
                "title": title,
                "slug": slugify(title),
                "anime_id": str(anime.get("id", "")),
                "poster": anime.get("affiche") or anime.get("affiche_small"),
                "score": score,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    # ────────────────────────────────
    # 3. Fiche anime (saisons, langues, épisodes) — catalogue local
    # ────────────────────────────────
    def get_anime_info(self, url_or_slug: str) -> Dict:
        """
        Retourne la fiche anime à partir du catalogue local. Accepte soit
        une URL complète (avec anime_id, et éventuellement s=/lang= en
        query string, comme construite par les callbacks Telegram), soit
        un slug seul (moins fiable, cherche par correspondance de slug).
        """
        anime_id = None
        s_param = None
        lang_param = None
        slug = url_or_slug

        if url_or_slug.startswith("http"):
            parsed = urlparse(url_or_slug)
            qs = parse_qs(parsed.query)
            anime_id = (qs.get("anime_id") or [None])[0]
            s_param = (qs.get("s") or [None])[0]
            lang_param = (qs.get("lang") or [None])[0]
            slug = parsed.path.split("/anime/")[-1].strip("/").split("?")[0]

        anime = None
        if anime_id:
            anime = get_anime_by_id(anime_id)
        if anime is None:
            # Fallback : recherche par slug si l'anime_id est absent/invalide
            for candidate in fetch_catalog():
                if slugify(candidate.get("title", "")) == slug:
                    anime = candidate
                    break

        sheet = {
            "anime_id": str(anime.get("id")) if anime else anime_id,
            "slug": slug,
            "titre": None,
            "poster": None,
            "note": None,
            "genres": [],
            "nb_saisons": None,
            "nb_episodes": None,
            "status": None,
            "format": None,
            "diffusion": None,
            "synopsis": None,
            "langues_disponibles": [],
            "saisons_disponibles": [],
            "episodes_par_saison": {},
        }

        if anime is None:
            logger.warning("get_anime_info: anime introuvable dans le catalogue (id=%s, slug=%s)", anime_id, slug)
            return sheet

        saisons = anime.get("saisons") or []

        sheet["titre"] = anime.get("title") or anime.get("titleO")
        sheet["poster"] = anime.get("affiche") or anime.get("affiche_small")
        sheet["note"] = anime.get("note")
        sheet["genres"] = anime.get("themes") or []
        sheet["nb_saisons"] = str(len(saisons))
        sheet["nb_episodes"] = str(sum(len(s.get("episodes") or []) for s in saisons))
        sheet["status"] = anime.get("status")
        sheet["format"] = anime.get("format")
        sheet["diffusion"] = f"{anime.get('startDate') or '?'} / {anime.get('endDate') or '?'}"
        sheet["synopsis"] = anime.get("description")
        sheet["saisons_disponibles"] = [s.get("title") for s in saisons if s.get("title")]

        # Saison ciblée par la requête (s=...), sinon la première disponible
        target_season = None
        if s_param:
            target_season = next((s for s in saisons if s.get("title") == f"Saison {s_param}"), None)
        elif saisons:
            target_season = saisons[0]

        if target_season:
            episodes = target_season.get("episodes") or []

            # Langues réellement disponibles sur cette saison
            catalog_langs = set()
            for ep in episodes:
                catalog_langs.update((ep.get("lang") or {}).keys())
            sheet["langues_disponibles"] = [
                LANG_CATALOG_TO_LABEL[c] for c in ("vo", "vf") if c in catalog_langs
            ]

            # Épisodes disponibles dans la langue demandée (lang=...)
            if lang_param:
                catalog_lang = LANG_LABEL_TO_CATALOG.get(lang_param.lower(), lang_param.lower())
                numeros = []
                for i, ep in enumerate(episodes, start=1):
                    if catalog_lang in (ep.get("lang") or {}):
                        m = re.search(r"(\d+)", ep.get("title") or "")
                        numeros.append({"numero": m.group(1) if m else str(i)})
                if s_param:
                    sheet["episodes_par_saison"][f"Saison {s_param}"] = numeros

        return sheet

    # ────────────────────────────────
    # 4. Liens de streaming / téléchargement
    # ────────────────────────────────
    def get_episode_links(self, slug: str, anime_id: str, season: str, episode: str, lang: str = "vo") -> List[Dict]:
        """
        Scrape la page d'un épisode spécifique et extrait les liens de lecteurs.
        Retourne: [{"host": "sibnet", "url": "https://..."}, ...]
        """
        url = f"{self.base}/anime/{slug}?s={season}&ep={episode}&lang={lang}&anime_id={anime_id}"
        html = self._fetch(url)
        soup = BeautifulSoup(html, "html.parser")

        links = []
        seen = set()

        for iframe in soup.find_all("iframe"):
            src = iframe.get("src")
            if src and src not in seen:
                seen.add(src)
                host = self._detect_host(src)
                links.append({"host": host, "url": src})

        for video in soup.find_all("video"):
            src = video.get("src")
            if src and src not in seen:
                seen.add(src)
                links.append({"host": "direct", "url": src})
            for source in video.find_all("source"):
                src = source.get("src")
                if src and src not in seen:
                    seen.add(src)
                    links.append({"host": "direct", "url": src})

        for script in soup.find_all("script"):
            text = script.string or ""
            for pattern in [
                r'https?://[^\s"\'<>]+\.mp4',
                r'https?://[^\s"\'<>]+\.m3u8',
                r'https?://[^\s"\'<>]+sibnet\.ru[^\s"\'<>]*',
                r'https?://[^\s"\'<>]+vidmoly\.[a-z]+[^\s"\'<>]*',
                r'https?://[^\s"\'<>]+sendvid\.com[^\s"\'<>]*',
                r'https?://[^\s"\'<>]+doodstream\.[^\s"\'<>]*',
                r'https?://[^\s"\'<>]+voe\.sx[^\s"\'<>]*',
                r'https?://[^\s"\'<>]+streamtape\.[^\s"\'<>]*',
            ]:
                for match in re.findall(pattern, text):
                    if match not in seen:
                        seen.add(match)
                        host = self._detect_host(match)
                        links.append({"host": host, "url": match})

        host_names = ["sibnet", "vidmoly", "sendvid", "doodstream", "voe", "streamtape", "yourupload", "mega", "mp4upload"]
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True).lower()
            if any(h in href.lower() or h in text for h in host_names):
                if href not in seen:
                    seen.add(href)
                    host = self._detect_host(href)
                    links.append({"host": host, "url": href})

        return links

    @staticmethod
    def _detect_host(url: str) -> str:
        url_l = url.lower()
        if "sibnet.ru" in url_l:
            return "sibnet"
        if "vidmoly" in url_l:
            return "vidmoly"
        if "sendvid" in url_l:
            return "sendvid"
        if "doodstream" in url_l:
            return "doodstream"
        if "voe" in url_l:
            return "voe"
        if "streamtape" in url_l:
            return "streamtape"
        if "yourupload" in url_l:
            return "yourupload"
        if "mega" in url_l:
            return "mega"
        if "mp4upload" in url_l:
            return "mp4upload"
        if ".mp4" in url_l:
            return "direct_mp4"
        if ".m3u8" in url_l:
            return "direct_m3u8"
        return "unknown"

    # ────────────────────────────────
    # 5. Résolution directe (MP4) pour certains hôtes
    # ────────────────────────────────
    def resolve_direct_link(self, link: Dict) -> Optional[str]:
        host = link["host"]
        url = link["url"]

        if host in ("direct_mp4", "direct_m3u8"):
            return url

        if host == "sibnet":
            try:
                html = self._fetch(url)
                soup = BeautifulSoup(html, "html.parser")
                for source in soup.find_all("source"):
                    src = source.get("src")
                    if src and ".mp4" in src:
                        return src
                video = soup.find("video")
                if video and video.get("src"):
                    return video["src"]
                for script in soup.find_all("script"):
                    text = script.string or ""
                    m = re.search(r'["\'](https?://[^"\']+\.mp4)["\']', text)
                    if m:
                        return m.group(1)
            except Exception:
                pass

        if host == "vidmoly":
            try:
                html = self._fetch(url)
                soup = BeautifulSoup(html, "html.parser")
                for script in soup.find_all("script"):
                    text = script.string or ""
                    m = re.search(r'["\'](https?://[^"\']+\.mp4)["\']', text)
                    if m:
                        return m.group(1)
                    m = re.search(r'["\'](https?://[^"\']+\.m3u8)["\']', text)
                    if m:
                        return m.group(1)
            except Exception:
                pass

        if host == "sendvid":
            try:
                html = self._fetch(url)
                soup = BeautifulSoup(html, "html.parser")
                for meta in soup.find_all("meta", property="og:video"):
                    src = meta.get("content")
                    if src:
                        return src
                for video in soup.find_all("video"):
                    src = video.get("src")
                    if src:
                        return src
            except Exception:
                pass

        return None
