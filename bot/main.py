"""
Anime Bot — pyrogram + FRAnime + TMDB + APScheduler
"""
import asyncio
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from pyrogram import Client, idle

from bot.config import Config
from bot.scrapers.franime import FranimeScraper
from bot.scrapers.tmdb import TMDBClient
from bot.utils.scheduler import scan_calendar, check_posters, check_releases

# Import handlers pour les enregistrer
import bot.handlers.commands
import bot.handlers.callbacks

franime = FranimeScraper()
tmdb = TMDBClient()

app = Client(
    "anime_bot",
    bot_token=Config.BOT_TOKEN,
    workers=4,
)


async def main():
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

    await app.start()
    print("[Bot] Bot démarré")
    await idle()

    await app.stop()
    await tmdb.close()
    scheduler.shutdown()
    print("[Bot] Arrêté")


if __name__ == "__main__":
    app.run(main())
