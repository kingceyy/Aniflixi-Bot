"""
Commandes du bot (owner only).
"""
import json
from functools import wraps

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

from bot.config import Config
from bot.scrapers import catalog
from bot.scrapers.franime import FranimeScraper
from bot.scrapers.tmdb import TMDBClient
from bot.utils.keyboards import search_results_keyboard
from bot.utils.planning import render_planning_day
from bot.utils.scheduler import load_queue

franime = FranimeScraper()
tmdb = TMDBClient()


def owner_only(func):
    @wraps(func)
    async def wrapper(client: Client, message: Message):
        if not message.from_user or message.from_user.id != Config.OWNER_ID:
            await message.reply("⛔ Accès refusé. Seul le propriétaire peut utiliser ce bot.")
            return
        return await func(client, message)
    return wrapper


@Client.on_message(filters.command("start") & filters.private)
@owner_only
async def start_cmd(client: Client, message: Message):
    await message.reply(
        "👋 <b>Anime Bot</b> prêt !\n"
        "Commandes disponibles :\n"
        "• /anime &lt;nom&gt; — Rechercher un anime\n"
        "• /planning — Planning du jour\n"
        "• /status — État de la file auto\n"
        "• /importcatalog — Importer un catalogue (en réponse à un fichier .json)",
        parse_mode=ParseMode.HTML
    )


@Client.on_message(filters.command("anime") & filters.private)
@owner_only
async def anime_cmd(client: Client, message: Message):
    query = message.text.split(maxsplit=1)
    if len(query) < 2:
        await message.reply("❌ Usage: /anime &lt;nom&gt;\nEx: /anime Naruto")
        return

    q = query[1].strip()
    await message.reply(f"🔍 Recherche de <b>{q}</b>...", parse_mode=ParseMode.HTML)

    try:
        results = franime.search_anime(q, limit=5)
    except Exception as e:
        await message.reply(
            f"⚠️ Recherche indisponible : {e}\n\n"
            f"Envoie un catalogue à jour avec /importcatalog si le problème persiste.",
        )
        return

    if not results:
        await message.reply("😕 Aucun résultat trouvé.")
        return

    text = f"🔍 <b>{len(results)} résultat(s)</b> pour « {q} »\nChoisis un anime :"
    keyboard = search_results_keyboard(results)
    await message.reply(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@Client.on_message(filters.command("importcatalog") & filters.private)
@owner_only
async def import_catalog_cmd(client: Client, message: Message):
    """
    Importe manuellement le catalogue FRAnime depuis un fichier .json envoyé
    en pièce jointe (réponds à ce fichier avec /importcatalog).

    Utile quand le serveur du bot (IP datacenter) se fait bloquer par
    Cloudflare sur api.franime.fr : génère le fichier depuis un réseau non
    bloqué (téléphone, PC perso) avec le script fetch_catalog(force=True),
    envoie-le au bot sur Telegram, puis réponds-y avec cette commande.
    """
    target = message.reply_to_message
    if not target or not target.document:
        await message.reply(
            "❌ Réponds à un fichier <b>.json</b> (le catalogue téléchargé) avec /importcatalog.",
            parse_mode=ParseMode.HTML,
        )
        return

    status = await message.reply("⬇️ Téléchargement et validation du fichier...")

    try:
        file_path = await target.download()
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        count = catalog.set_catalog(data)
        await status.edit_text(f"✅ Catalogue importé : <b>{count}</b> animes.", parse_mode=ParseMode.HTML)
    except json.JSONDecodeError:
        await status.edit_text("❌ Le fichier n'est pas un JSON valide.")
    except ValueError as e:
        await status.edit_text(f"❌ {e}")
    except Exception as e:
        await status.edit_text(f"❌ Erreur lors de l'import : {e}")


@Client.on_message(filters.command("planning") & filters.private)
@owner_only
async def planning_cmd(client: Client, message: Message):
    planning = franime.get_calendar()
    today = __import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo(Config.TIMEZONE)).strftime("%A").lower()
    day_map = {
        "monday": "lundi", "tuesday": "mardi", "wednesday": "mercredi",
        "thursday": "jeudi", "friday": "vendredi", "saturday": "samedi", "sunday": "dimanche"
    }
    today_key = day_map.get(today, today)

    text, keyboard = render_planning_day(planning, today_key)
    await message.reply(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@Client.on_message(filters.command("status") & filters.private)
@owner_only
async def status_cmd(client: Client, message: Message):
    queue = load_queue()
    items = queue.get("schedule", [])
    if not items:
        await message.reply("📭 File d'attente vide.")
        return

    lines = ["📊 <b>File d'attente</b>\n"]
    for item in items:
        status = "✅" if item.get("published_episode") else ("🖼️" if item.get("published_poster") else "⏳")
        lines.append(
            f"{status} <b>{item['titre']}</b> S{item['saison']}E{item.get('episode', '?')} "
            f"à {item['heure']} [{item['lang'].upper()}]"
        )
    await message.reply("\n".join(lines), parse_mode=ParseMode.HTML)
