"""
Scheduler — Publication automatique hebdomadaire.
Gère queue.json, scan à minuit, posters H-2, releases H+0.
"""
import os
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Optional

from pyrogram.enums import ParseMode

from bot.config import Config
from bot.scrapers.franime import FranimeScraper
from bot.scrapers.tmdb import TMDBClient
from bot.utils.downloader import download_file, convert_to_480p, cleanup_files


TZ = ZoneInfo(Config.TIMEZONE)


def _ensure_queue():
    os.makedirs(Config.DATA_DIR, exist_ok=True)
    if not os.path.exists(Config.QUEUE_FILE):
        with open(Config.QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump({"schedule": []}, f)


def load_queue() -> Dict:
    _ensure_queue()
    with open(Config.QUEUE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_queue(data: Dict):
    with open(Config.QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _parse_time(heure_str: str) -> Optional[datetime.time]:
    """Parse '17h20' ou '17:20' en time object."""
    heure_str = heure_str.strip().lower().replace("h", ":")
    try:
        return datetime.strptime(heure_str, "%H:%M").time()
    except ValueError:
        return None


def _today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


async def scan_calendar(franime: FranimeScraper, tmdb: TMDBClient):
    """
    Appelé à 00:00.
    Scrape le calendrier FRAnime, extrait les sorties du jour,
    enrichit avec TMDB, et stocke dans queue.json.
    """
    queue = load_queue()
    # On garde seulement les items d'aujourd'hui qui ne sont pas encore publiés
    # ou on les reset pour le nouveau jour
    queue["schedule"] = []

    planning = franime.get_calendar()
    today_fr = datetime.now(TZ).strftime("%A").lower()
    # mapping anglais -> français si nécessaire, mais get_calendar retourne déjà en français
    day_map = {
        "monday": "lundi", "tuesday": "mardi", "wednesday": "mercredi",
        "thursday": "jeudi", "friday": "vendredi", "saturday": "samedi", "sunday": "dimanche"
    }
    today_key = day_map.get(today_fr, today_fr)
    releases = planning.get(today_key, [])

    for rel in releases:
        heure = rel.get("heure")
        if not heure:
            continue
        # Enrichir avec TMDB poster
        poster = await tmdb.get_poster_url(rel.get("titre", ""))
        queue["schedule"].append({
            "titre": rel.get("titre"),
            "heure": heure,
            "episode": rel.get("episode"),
            "saison": rel.get("saison") or "1",
            "lang": rel.get("lang") or "vostfr",
            "slug": rel.get("slug"),
            "anime_id": rel.get("anime_id"),
            "poster_url": poster,
            "published_poster": False,
            "published_episode": False,
            "date": _today_str(),
        })

    save_queue(queue)
    print(f"[Scheduler] {len(queue['schedule'])} sorties ajoutées pour {today_key}")


async def check_posters(client, tmdb: TMDBClient):
    """
    Appelé toutes les minutes.
    Vérifie si H-2min pour chaque sortie du jour.
    """
    queue = load_queue()
    now = datetime.now(TZ)
    now_time = now.time()

    updated = False
    for item in queue.get("schedule", []):
        if item.get("published_poster") or item.get("date") != _today_str():
            continue
        rel_time = _parse_time(item.get("heure", ""))
        if not rel_time:
            continue
        rel_dt = datetime.combine(now.date(), rel_time)
        poster_time = rel_dt - timedelta(minutes=2)

        if now >= poster_time:
            poster = item.get("poster_url")
            if not poster:
                poster = await tmdb.get_poster_url(item.get("titre", ""))
            caption = (
                f"🎌 <b>{item['titre']}</b> — Saison {item['saison']} — Épisode à venir\n"
                f"🕐 Sortie à {item['heure']}"
            )
            try:
                if poster:
                    await client.send_photo(
                        Config.CHANNEL_ID,
                        photo=poster,
                        caption=caption,
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await client.send_message(
                        Config.CHANNEL_ID,
                        caption,
                        parse_mode=ParseMode.HTML
                    )
                item["published_poster"] = True
                updated = True
                print(f"[Scheduler] Poster envoyé: {item['titre']}")
            except Exception as e:
                print(f"[Scheduler] Erreur envoi poster: {e}")

    if updated:
        save_queue(queue)


async def check_releases(client, franime: FranimeScraper):
    """
    Appelé toutes les 2 minutes.
    Vérifie si H+0 pour chaque sortie du jour.
    Télécharge, convertit, upload, cleanup.
    """
    queue = load_queue()
    now = datetime.now(TZ)
    now_time = now.time()

    updated = False
    for item in queue.get("schedule", []):
        if item.get("published_episode") or item.get("date") != _today_str():
            continue
        rel_time = _parse_time(item.get("heure", ""))
        if not rel_time:
            continue
        rel_dt = datetime.combine(now.date(), rel_time)

        if now >= rel_dt:
            slug = item.get("slug")
            anime_id = item.get("anime_id")
            saison = item.get("saison", "1")
            episode = item.get("episode")
            lang = item.get("lang", "vostfr")
            titre = item.get("titre", "Anime")

            if not slug or not anime_id or episode is None:
                print(f"[Scheduler] Données incomplètes pour {titre}, skip")
                continue

            try:
                # 1. Récupération des liens
                links = franime.get_episode_links(slug, anime_id, str(saison), str(episode), lang)
                direct_url = None
                for link in links:
                    resolved = franime.resolve_direct_link(link)
                    if resolved:
                        direct_url = resolved
                        break

                if not direct_url:
                    print(f"[Scheduler] Aucun lien direct trouvé pour {titre} ep{episode}")
                    continue

                # 2. Téléchargement
                hd_path = os.path.join(Config.DOWNLOAD_DIR, f"{slug}_s{saison}e{episode}_hd.mp4")
                low_path = os.path.join(Config.DOWNLOAD_DIR, f"{slug}_s{saison}e{episode}_480p.mp4")
                os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)

                status_msg = await client.send_message(
                    Config.CHANNEL_ID,
                    f"⬇️ Téléchargement de <b>{titre}</b> S{saison}E{episode}...",
                    parse_mode=ParseMode.HTML
                )

                await download_file(direct_url, hd_path)

                # 3. Conversion 480p
                await client.edit_message_text(
                    Config.CHANNEL_ID,
                    status_msg.id,
                    f"🔄 Conversion 480p de <b>{titre}</b> S{saison}E{episode}...",
                    parse_mode=ParseMode.HTML
                )
                await convert_to_480p(hd_path, low_path)

                # 4. Upload 480p
                await client.edit_message_text(
                    Config.CHANNEL_ID,
                    status_msg.id,
                    f"📤 Upload 480p de <b>{titre}</b> S{saison}E{episode}...",
                    parse_mode=ParseMode.HTML
                )
                await client.send_video(
                    Config.CHANNEL_ID,
                    video=low_path,
                    caption=f"🎬 {titre} — S{saison}E{episode} [{lang.upper()}] (480p)",
                    parse_mode=ParseMode.HTML,
                    supports_streaming=True
                )

                # 5. Upload HD
                await client.edit_message_text(
                    Config.CHANNEL_ID,
                    status_msg.id,
                    f"📤 Upload HD de <b>{titre}</b> S{saison}E{episode}...",
                    parse_mode=ParseMode.HTML
                )
                await client.send_video(
                    Config.CHANNEL_ID,
                    video=hd_path,
                    caption=f"🎬 {titre} — S{saison}E{episode} [{lang.upper()}] (HD)",
                    parse_mode=ParseMode.HTML,
                    supports_streaming=True
                )

                # 6. Cleanup
                await cleanup_files(hd_path, low_path)
                await client.delete_messages(Config.CHANNEL_ID, status_msg.id)

                item["published_episode"] = True
                updated = True
                print(f"[Scheduler] Épisode publié: {titre} S{saison}E{episode}")

            except Exception as e:
                print(f"[Scheduler] Erreur publication épisode {titre}: {e}")

    if updated:
        save_queue(queue)
