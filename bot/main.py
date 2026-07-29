"""
Anime Bot — Kurigram + FRAnime + TMDB + APScheduler
"""
import asyncio
import os

from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from pyrogram import Client, idle

from bot.config import Config
from bot.scrapers.franime import FranimeScraper
from bot.scrapers.tmdb import TMDBClient
from bot.utils.scheduler import scan_calendar, check_posters, check_releases

franime = FranimeScraper()
tmdb = TMDBClient()

app = Client(
    "anime_bot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    workers=4,
    plugins=dict(root="bot/handlers"),
)


async def health(request):
    return web.Response(text="OK")


async def start_health_server():
    """Serveur HTTP minimal pour les health checks Koyeb (bot Telegram = pas de port par défaut)."""
    web_app = web.Application()
    web_app.router.add_get("/health", health)
    web_app.router.add_get("/", health)
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.getenv("PORT", "5000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[Bot] Health server démarré sur le port {port}")


async def main():
    if not Config.API_ID or not Config.API_HASH:
        raise RuntimeError(
            "API_ID et API_HASH sont requis. Récupère-les sur https://my.telegram.org "
            "et définis-les comme variables d'environnement sur Koyeb."
        )

    os.makedirs(Config.DATA_DIR, exist_ok=True)
    os.makedirs(Config.CACHE_DIR, exist_ok=True)
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)

    scheduler = AsyncIOScheduler(timezone=Config.TIMEZONE)

    # 00:00 — Scan du calendrier
    scheduler.add_job(
        scan_calendar,
        CronTrigger(hour=0, minute=0),
        args=[franime, tmdb],
        id="scan_calendar",
        replace_existing=True,
    )

    # Toutes les minutes — Posters H-2
    scheduler.add_job(
        check_posters,
        IntervalTrigger(minutes=1),
        args=[app, tmdb],
        id="check_posters",
        replace_existing=True,
    )

    # Toutes les 2 minutes — Releases H+0
    scheduler.add_job(
        check_releases,
        IntervalTrigger(minutes=2),
        args=[app, franime],
        id="check_releases",
        replace_existing=True,
    )

    scheduler.start()
    print("[Bot] Scheduler démarré — Europe/Paris")

    await start_health_server()

    await app.start()
    print("[Bot] Bot démarré")

    # Scan immédiat au démarrage : si le bot redémarre en cours de journée
    # (redeploy, crash, etc.), on ne veut pas attendre minuit pour recharger
    # les sorties du jour dans queue.json. Sans ça, un redémarrage à 14h
    # ferait perdre toutes les hebdo du jour restantes.
    # check_posters/check_releases tournant déjà toutes les 1-2 min, tout
    # créneau déjà passé au moment du scan sera rattrapé au prochain cycle.
    try:
        print("[Bot] Scan immédiat du calendrier du jour (rattrapage post-redémarrage)...")
        await scan_calendar(franime, tmdb)
    except Exception as e:
        print(f"[Bot] Échec du scan immédiat au démarrage: {e}")

    await idle()

    await app.stop()
    await tmdb.close()
    scheduler.shutdown()
    print("[Bot] Arrêté")


if __name__ == "__main__":
    asyncio.run(main())
