"""
Scraper FRAnime (franime.fr)
- Calendrier hebdomadaire
- Recherche d'anime (avec rapidfuzz)
- Fiche anime (saisons, langues, épisodes)
- Résolution des liens de streaming
"""
import re
import time
import json
from typing import List, Dict, Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
import cloudscraper
from bs4 import BeautifulSoup, Tag
from rapidfuzz import fuzz

from bot.config import Config

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

    def _fetch(self, url: str) -> str:
        if Config.ZENROWS_API_KEY:
            try:
                resp = requests.get(
                    "https://api.zenrows.com/v1/",
                    params={
                        "url": url,
                        "apikey": Config.ZENROWS_API_KEY,
                        "premium_proxy": "true",
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.text
            except requests.exceptions.RequestException:
                # Bascule sur cloudscraper en direct si ZenRows échoue
                # (quota dépassé, timeout, panne du service, etc.)
                pass

        resp = self.session.get(url, timeout=20)
        resp.raise_for_status()
        return resp.text

    # ────────────────────────────────
    # 1. Calendrier hebdomadaire
    # ────────────────────────────────
    def get_calendar(self) -> Dict[str, List[Dict]]:
        """
        Scrape franime.fr/calendrier
        Retourne: {"lundi": [{titre, heure, episode, lang, slug, anime_id, poster}], ...}
        """
        html = self._fetch(f"{self.base}/calendrier")
        soup = BeautifulSoup(html, "html.parser")

        # Mapping jour français -> clé normalisée
        day_map = {
            "lundi": "lundi", "mardi": "mardi", "mercredi": "mercredi",
            "jeudi": "jeudi", "vendredi": "vendredi", "samedi": "samedi", "dimanche": "dimanche"
        }

        planning = {d: [] for d in day_map.values()}

        # Le calendrier est probablement structuré avec des sections par jour.
        # On cherche les éléments qui contiennent le nom du jour en majuscules.
        # Stratégie : on parcourt tous les éléments et on regroupe les cards
        # qui suivent un titre de jour.

        # Approche : chercher les containers qui ont un enfant texte avec le jour
        # ou chercher les balises avec classe contenant le jour.
        # Comme on ne connait pas les classes exactes, on utilise les ancres de texte.

        body = soup.find("body")
        if not body:
            return planning

        # On récupère tous les éléments significatifs (div, section, article, a, img, span)
        # et on les parcourt pour détecter les jours et les cards.
        # Simplification : on cherche les textes de jours, puis on regarde les
        # éléments frères/suivants pour trouver les cards.

        # Méthode alternative : chercher tous les liens qui ressemblent à des liens d'anime
        # avec un parent qui contient une heure.
        # On va parser plus finement.

        # Détection des jours via regex sur les textes des éléments
        all_elements = body.find_all(["div", "section", "article", "a", "span", "h2", "h3"])
        current_day = None

        for elem in all_elements:
            text = elem.get_text(strip=True).lower()
            # Détecter le jour (ex: "lundi", "lundi 27/07", "mardi 28/07")
            for day_fr, day_key in day_map.items():
                if text.startswith(day_fr) and len(text) < 20:
                    current_day = day_key
                    break

            if current_day and elem.name == "a":
                href = elem.get("href", "")
                # Vérifier si c'est un lien vers une fiche anime
                if "/anime/" in href:
                    card = self._parse_calendar_card(elem, href)
                    if card:
                        planning[current_day].append(card)

        # Si la méthode ci-dessus ne trouve rien (structure trop imbriquée),
        # on tente une approche plus robuste : chercher toutes les cards par pattern.
        if all(len(v) == 0 for v in planning.values()):
            planning = self._get_calendar_fallback(soup)

        return planning

    def _parse_calendar_card(self, elem: Tag, href: str) -> Optional[Dict]:
        """Extrait les infos d'une card du calendrier à partir de l'élément <a>."""
        # Cherche l'heure dans les descendants (ex: "17h20", "17:20", "01h44")
        texts = [t.get_text(strip=True) for t in elem.find_all(True)]
        heure = None
        for t in texts:
            m = re.match(r"^(\d{1,2})[h:](\d{2})$", t)
            if m:
                heure = f"{m.group(1).zfill(2)}:{m.group(2)}"
                break

        # Titre : souvent dans un span ou h3/h4
        titre = None
        for t in texts:
            if len(t) > 3 and not re.match(r"^(\d{1,2})[h:](\d{2})$", t) and t not in ["VF", "VOSTFR", "VA", "VJ"]:
                # On prend le texte le plus long comme titre
                if not titre or len(t) > len(titre):
                    titre = t

        # Numéro d'épisode (ex: E5, Ep 5, Episode 5)
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

        # Langue (VF/VOSTFR) via drapeau ou texte
        lang = "vostfr"
        all_text = " ".join(texts).lower()
        if "vf" in all_text and "vostfr" not in all_text:
            lang = "vf"
        elif "vostfr" in all_text:
            lang = "vostfr"

        # Poster
        poster = None
        img = elem.find("img")
        if img:
            poster = img.get("src") or img.get("data-src")

        # Slug et anime_id
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
        """
        Fallback si les cards ne sont pas des <a> directs.
        On cherche toutes les images avec un alt ou un parent contenant une heure.
        """
        planning = {d: [] for d in ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]}
        # Cette méthode est un placeholder ; l'utilisateur pourra ajuster
        # selon la structure HTML réelle observée.
        return planning

    # ────────────────────────────────
    # 2. Recherche d'anime
    # ────────────────────────────────
    def search_anime(self, query: str, limit: int = 5) -> List[Dict]:
        """
        Recherche un anime sur FRAnime.
        Tente d'abord l'API interne, puis fallback sur scraping HTML.
        Utilise rapidfuzz pour le scoring.
        """
        results = []
        # Tentative API interne (si elle existe)
        try:
            api_url = f"{self.base}/api/animes/search"
            resp = self.session.get(api_url, params={"q": query}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for item in data[:limit]:
                        results.append({
                            "title": item.get("title") or item.get("name"),
                            "slug": item.get("slug"),
                            "anime_id": str(item.get("id", "")),
                            "poster": item.get("poster") or item.get("image"),
                            "url": f"{self.base}/anime/{item.get('slug')}",
                        })
        except Exception:
            pass

        # Fallback scraping
        if not results:
            try:
                search_url = f"{self.base}/search"
                html = self._fetch(f"{search_url}?q={requests.utils.quote(query)}")
                soup = BeautifulSoup(html, "html.parser")
                # Chercher les liens vers /anime/
                seen = set()
                for a in soup.find_all("a", href=re.compile(r"/anime/")):
                    href = a.get("href", "")
                    slug = href.split("/anime/")[-1].split("?")[0].strip("/")
                    if slug in seen or not slug:
                        continue
                    seen.add(slug)
                    title = a.get_text(strip=True)
                    if not title:
                        title = slug.replace("-", " ").title()
                    img = a.find("img")
                    poster = img.get("src") or img.get("data-src") if img else None
                    qs = parse_qs(urlparse(href).query)
                    anime_id = (qs.get("anime_id") or [None])[0]
                    results.append({
                        "title": title,
                        "slug": slug,
                        "anime_id": anime_id,
                        "poster": poster,
                        "url": f"{self.base}/anime/{slug}",
                    })
            except Exception:
                pass

        # Scoring rapidfuzz
        scored = []
        for r in results:
            score = fuzz.token_set_ratio(query.lower(), r["title"].lower())
            # Bonus spécificité
            if abs(len(query) - len(r["title"])) < 5:
                score += 10
            if score >= 50:
                scored.append({**r, "score": score})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    # ────────────────────────────────
    # 3. Fiche anime (saisons, langues, épisodes)
    # ────────────────────────────────
    def get_anime_info(self, url_or_slug: str) -> Dict:
        """
        Scrape une fiche anime FRAnime.
        Retourne: titre, poster, genres, status, format, diffusion, synopsis,
                  langues_disponibles, saisons_disponibles, episodes_par_saison, anime_id, slug
        """
        if not url_or_slug.startswith("http"):
            url = f"{self.base}/anime/{url_or_slug}"
        else:
            url = url_or_slug

        html = self._fetch(url)
        return self._parse_anime_page(html, url)

    def _parse_anime_page(self, html: str, source_url: str) -> Dict:
        soup = BeautifulSoup(html, "html.parser")
        qs = parse_qs(urlparse(source_url).query)
        anime_id = (qs.get("anime_id") or [None])[0]
        slug = urlparse(source_url).path.split("/anime/")[-1].strip("/").split("?")[0]

        sheet = {
            "anime_id": anime_id,
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

        leaves = self._leaves(soup)

        # Titre (h1)
        h1 = soup.find("h1")
        if h1:
            sheet["titre"] = h1.get_text(strip=True)
        idx_title = self._find_leaf(leaves, r"^" + re.escape(sheet["titre"] or "\x00") + r"$") if sheet["titre"] else None

        # Poster : premier <img> qui n'est ni logo ni bannière
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if any(k in src.lower() for k in ["logo", "banner", "franime_landing"]):
                continue
            if src:
                sheet["poster"] = src
                break

        # Compteurs saisons / épisodes
        idx_saisons_count = self._find_leaf(leaves, r"^\d+\s*saisons?$")
        idx_episodes_count = self._find_leaf(leaves, r"^\d+\s*épisodes?$")
        if idx_saisons_count is not None:
            m = re.match(r"^(\d+)", leaves[idx_saisons_count].get_text(strip=True))
            if m:
                sheet["nb_saisons"] = m.group(1)
        if idx_episodes_count is not None:
            m = re.match(r"^(\d+)", leaves[idx_episodes_count].get_text(strip=True))
            if m:
                sheet["nb_episodes"] = m.group(1)

        # Statut / Format / Diffusion
        idx_status = self._find_leaf(leaves, r"^Status\s*:")
        if idx_status is not None:
            end_status = idx_title if idx_title is not None else idx_status + 6
            status_join = " ".join(self._texts_between(leaves, idx_status - 1, end_status) or [leaves[idx_status].get_text(strip=True)])
            m_status = re.search(r"Status\s*:\s*(.+?)\s*Format\s*:", status_join)
            m_format = re.search(r"Format\s*:\s*(\S+)\s*Diffusion\s*:", status_join)
            m_diff = re.search(r"Diffusion\s*:\s*(.+)$", status_join)
            sheet["status"] = m_status.group(1).strip() if m_status else None
            sheet["format"] = m_format.group(1).strip() if m_format else None
            sheet["diffusion"] = m_diff.group(1).strip() if m_diff else None

        # Genres
        if idx_episodes_count is not None and idx_status is not None:
            genres = self._texts_between(leaves, idx_episodes_count, idx_status)
            sheet["genres"] = [g for g in genres if g and not re.match(r"^[\d.,]+$", g)]

        # Synopsis
        idx_trailer = self._find_leaf(leaves, r"^Voir la bande annonce$")
        if idx_title is not None:
            desc_parts = self._texts_between(leaves, idx_title, idx_trailer)
            sheet["synopsis"] = " ".join(desc_parts).strip() or None

        # Langues disponibles
        idx_langue_h = self._find_leaf(leaves, r"^Langue$")
        idx_saison_h = self._find_leaf(leaves, r"^Saison$")
        if idx_langue_h is not None:
            sheet["langues_disponibles"] = [
                t for t in self._texts_between(leaves, idx_langue_h, idx_saison_h)
                if t in ("VOSTFR", "VF", "VA", "VJ")
            ]

        # Saisons disponibles
        idx_episodes_h = self._find_leaf(leaves, r"^Épisodes$")
        if idx_saison_h is not None:
            sheet["saisons_disponibles"] = [
                t for t in self._texts_between(leaves, idx_saison_h, idx_episodes_h)
                if re.match(r"^Saison\s+[\d.]+$", t)
            ]

        # Épisodes de la saison courante
        idx_footer = self._find_leaf(leaves, r"aucun fichier vidéo", re.IGNORECASE)
        ep_tokens = self._texts_between(leaves, idx_episodes_h, idx_footer)

        episodes = []
        current_lang = None
        for tok in ep_tokens:
            if tok in ("Flouter", "Charger les informations des épisodes"):
                continue
            if tok in ("VOSTFR", "VF", "VA", "VJ"):
                current_lang = tok
                continue
            m_ep = re.match(r"^[EÉ]pisode\s+(\d+)$", tok)
            if m_ep:
                episodes.append({"numero": m_ep.group(1), "langue": current_lang})
                current_lang = None
                continue
            m_ago = re.match(r"^Il y a .+$", tok)
            if m_ago and episodes:
                episodes[-1]["publie_il_y_a"] = tok

        season_label = None
        s_param = (qs.get("s") or [None])[0]
        if s_param:
            season_label = f"Saison {s_param}"
        sheet["episodes_par_saison"][season_label or "inconnue"] = episodes

        return sheet

    def get_all_seasons(self, slug: str, anime_id: str, delay: float = 1.0) -> Dict:
        """
        Scrape toutes les saisons d'un anime en partant de la saison 1.
        Retourne la fiche complète avec tous les épisodes par saison.
        """
        url = f"{self.base}/anime/{slug}?s=1&ep=&lang=vo&anime_id={anime_id}"
        html = self._fetch(url)
        sheet = self._parse_anime_page(html, url)

        if not sheet["saisons_disponibles"] or not anime_id:
            return sheet

        for season_label in sheet["saisons_disponibles"]:
            s_value = season_label.replace("Saison", "").strip()
            key = f"Saison {s_value}"
            if key in sheet["episodes_par_saison"] and sheet["episodes_par_saison"][key]:
                continue
            season_url = f"{self.base}/anime/{slug}?s={s_value}&ep=&lang=vo&anime_id={anime_id}"
            time.sleep(delay)
            try:
                season_html = self._fetch(season_url)
                season_sheet = self._parse_anime_page(season_html, season_url)
                for label, eps in season_sheet["episodes_par_saison"].items():
                    sheet["episodes_par_saison"][label] = eps
            except Exception:
                continue
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

        # 1. Chercher les iframes
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src")
            if src and src not in seen:
                seen.add(src)
                host = self._detect_host(src)
                links.append({"host": host, "url": src})

        # 2. Chercher les balises <video>
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

        # 3. Chercher dans les scripts JS des URLs .mp4 / .m3u8 / lecteurs connus
        for script in soup.find_all("script"):
            text = script.string or ""
            # Patterns classiques
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

        # 4. Chercher des <a> avec texte contenant le nom d'un hôte
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
        """
        Tente de résoudre un lien iframe en lien direct MP4/M3U8.
        À surcharger / étendre selon les hôtes supportés.
        """
        host = link["host"]
        url = link["url"]

        if host in ("direct_mp4", "direct_m3u8"):
            return url

        # Sibnet : l'iframe contient souvent un player avec le mp4 dans une balise <source>
        if host == "sibnet":
            try:
                html = self._fetch(url)
                soup = BeautifulSoup(html, "html.parser")
                # Chercher <source src="..."> ou <video src="...">
                for source in soup.find_all("source"):
                    src = source.get("src")
                    if src and ".mp4" in src:
                        return src
                video = soup.find("video")
                if video and video.get("src"):
                    return video["src"]
                # Chercher dans le JS
                for script in soup.find_all("script"):
                    text = script.string or ""
                    m = re.search(r'["\'](https?://[^"\']+\.mp4)["\']', text)
                    if m:
                        return m.group(1)
            except Exception:
                pass

        # Vidmoly : souvent un iframe qui redirige vers une page player
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

        # Sendvid : l'URL directe est parfois l'URL de la page + .mp4 ou dans le HTML
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
