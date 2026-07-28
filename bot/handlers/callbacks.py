"""
Callbacks InlineKeyboard — Flux complet /anime.
"""
import os

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery

from bot.config import Config
from bot.scrapers.franime import FranimeScraper
from bot.scrapers.tmdb import TMDBClient
from bot.utils.keyboards import (
    seasons_keyboard,
    languages_keyboard,
    episodes_keyboard,
    episode_actions_keyboard,
)
from bot.utils.downloader import download_file, convert_to_480p, cleanup_files

franime = FranimeScraper()
tmdb = TMDBClient()


@Client.on_callback_query(filters.regex(r"^search\|"))
async def on_search_result(client: Client, query: CallbackQuery):
    _, slug, anime_id = query.data.split("|", 2)
    await query.answer("Chargement de la fiche...")

    info = franime.get_anime_info(f"https://franime.fr/anime/{slug}?anime_id={anime_id}")
    poster = await tmdb.get_poster_url(info.get("titre", ""))

    text = (
        f"🎬 <b>{info.get('titre', 'Inconnu')}</b>\n"
        f"📊 {info.get('nb_saisons', '?')} saison(s) — {info.get('nb_episodes', '?')} épisode(s)\n"
        f"📝 {info.get('synopsis', 'Pas de synopsis.')[:300]}..."
    )

    keyboard = seasons_keyboard(info.get("saisons_disponibles", []), slug, anime_id)
    if poster:
        await query.message.reply_photo(photo=poster, caption=text, reply_markup=keyboard, parse_mode="html")
    else:
        await query.message.reply(text, reply_markup=keyboard, parse_mode="html")
    await query.message.delete()


@Client.on_callback_query(filters.regex(r"^season\|"))
async def on_season_selected(client: Client, query: CallbackQuery):
    _, slug, anime_id, season = query.data.split("|", 3)
    await query.answer(f"Saison {season}")

    info = franime.get_anime_info(f"https://franime.fr/anime/{slug}?s={season}&anime_id={anime_id}")
    langs = info.get("langues_disponibles", ["VOSTFR"])

    text = f"🎬 <b>{info.get('titre')}</b> — Saison {season}\nChoisis la langue :"
    keyboard = languages_keyboard(langs, slug, anime_id, season)
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="html")


@Client.on_callback_query(filters.regex(r"^lang\|"))
async def on_lang_selected(client: Client, query: CallbackQuery):
    _, slug, anime_id, season, lang = query.data.split("|", 4)
    await query.answer(f"Langue {lang.upper()}")

    info = franime.get_anime_info(f"https://franime.fr/anime/{slug}?s={season}&lang={lang}&anime_id={anime_id}")
    eps = info.get("episodes_par_saison", {}).get(f"Saison {season}", [])

    text = f"📺 <b>{info.get('titre')}</b> — Saison {season} [{lang.upper()}]\nChoisis un épisode :"
    keyboard = episodes_keyboard(eps, slug, anime_id, season, lang)
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="html")


@Client.on_callback_query(filters.regex(r"^ep\|"))
async def on_episode_selected(client: Client, query: CallbackQuery):
    _, slug, anime_id, season, lang, ep_num = query.data.split("|", 5)
    await query.answer(f"Épisode {ep_num}")

    text = f"🎬 <b>{slug.replace('-', ' ').title()}</b> — S{season}E{ep_num} [{lang.upper()}]\nQue veux-tu faire ?"
    keyboard = episode_actions_keyboard(slug, anime_id, season, lang, ep_num)
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="html")


@Client.on_callback_query(filters.regex(r"^dl\|"))
async def on_download(client: Client, query: CallbackQuery):
    _, slug, anime_id, season, lang, ep_num = query.data.split("|", 5)
    await query.answer("Téléchargement en cours...")

    status_msg = await query.message.reply(
        f"⬇️ Résolution des liens pour <b>{slug}</b> S{season}E{ep_num}...",
        parse_mode="html"
    )

    try:
        links = franime.get_episode_links(slug, anime_id, season, ep_num, lang)
        direct_url = None
        for link in links:
            resolved = franime.resolve_direct_link(link)
            if resolved:
                direct_url = resolved
                break

        if not direct_url:
            await status_msg.edit_text("❌ Aucun lien direct trouvé pour cet épisode.")
            return

        hd_path = os.path.join(Config.DOWNLOAD_DIR, f"{slug}_s{season}e{ep_num}_hd.mp4")
        low_path = os.path.join(Config.DOWNLOAD_DIR, f"{slug}_s{season}e{ep_num}_480p.mp4")
        os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)

        await status_msg.edit_text("⬇️ Téléchargement...", parse_mode="html")
        await download_file(direct_url, hd_path)

        await status_msg.edit_text("🔄 Conversion 480p...", parse_mode="html")
        await convert_to_480p(hd_path, low_path)

        await status_msg.edit_text("📤 Upload 480p...", parse_mode="html")
        await client.send_video(
            query.message.chat.id,
            video=low_path,
            caption=f"🎬 {slug.replace('-', ' ').title()} — S{season}E{ep_num} [{lang.upper()}] (480p)",
            parse_mode="html",
            supports_streaming=True,
        )

        await status_msg.edit_text("📤 Upload HD...", parse_mode="html")
        await client.send_video(
            query.message.chat.id,
            video=hd_path,
            caption=f"🎬 {slug.replace('-', ' ').title()} — S{season}E{ep_num} [{lang.upper()}] (HD)",
            parse_mode="html",
            supports_streaming=True,
        )

        await cleanup_files(hd_path, low_path)
        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"❌ Erreur : {e}")


@Client.on_callback_query(filters.regex(r"^stream\|"))
async def on_streaming(client: Client, query: CallbackQuery):
    _, slug, anime_id, season, lang, ep_num = query.data.split("|", 5)
    await query.answer("Récupération des liens...")

    links = franime.get_episode_links(slug, anime_id, season, ep_num, lang)
    if not links:
        await query.message.reply("❌ Aucun lien de streaming trouvé.")
        return

    lines = [f"📡 <b>Liens streaming</b> — {slug.replace('-', ' ').title()} S{season}E{ep_num}\n"]
    for link in links:
        lines.append(f"• <a href='{link['url']}'>{link['host'].upper()}</a>")
    await query.message.reply("\n".join(lines), parse_mode="html", disable_web_page_preview=True)
