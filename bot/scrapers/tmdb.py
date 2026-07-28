"""
Client TMDB API — Posters, synopsis, métadonnées.
"""
import os
import aiohttp
from typing import Optional, Dict

from bot.config import Config


class TMDBClient:
    def __init__(self):
        self.api_key = Config.TMDB_API_KEY
        self.base = Config.TMDB_BASE
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def search_tv(self, query: str) -> Optional[Dict]:
        """Recherche une série TV par nom. Retourne le meilleur résultat."""
        if not self.api_key:
            return None
        session = await self._get_session()
        url = f"{self.base}/search/tv"
        params = {"api_key": self.api_key, "query": query, "language": "fr-FR"}
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            results = data.get("results", [])
            if not results:
                return None
            return results[0]

    async def get_poster_url(self, query: str) -> Optional[str]:
        """Retourne l'URL du poster HD (w500) ou None."""
        result = await self.search_tv(query)
        if not result:
            # Tentative recherche film
            result = await self._search_movie(query)
        if not result:
            return None
        path = result.get("poster_path")
        if path:
            return f"https://image.tmdb.org/t/p/w500{path}"
        return None

    async def get_backdrop_url(self, query: str) -> Optional[str]:
        result = await self.search_tv(query)
        if not result:
            result = await self._search_movie(query)
        if not result:
            return None
        path = result.get("backdrop_path")
        if path:
            return f"https://image.tmdb.org/t/p/original{path}"
        return None

    async def get_metadata(self, query: str) -> Dict:
        """Retourne synopsis, genres, date, note, etc."""
        result = await self.search_tv(query)
        if not result:
            result = await self._search_movie(query)
        if not result:
            return {}
        return {
            "title": result.get("name") or result.get("title"),
            "original_title": result.get("original_name") or result.get("original_title"),
            "synopsis": result.get("overview"),
            "poster_path": result.get("poster_path"),
            "backdrop_path": result.get("backdrop_path"),
            "vote_average": result.get("vote_average"),
            "first_air_date": result.get("first_air_date") or result.get("release_date"),
            "genres": result.get("genre_ids", []),
        }

    async def _search_movie(self, query: str) -> Optional[Dict]:
        session = await self._get_session()
        url = f"{self.base}/search/movie"
        params = {"api_key": self.api_key, "query": query, "language": "fr-FR"}
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            results = data.get("results", [])
            if not results:
                return None
            return results[0]
