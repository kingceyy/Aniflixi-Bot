"""
Claviers inline pour le bot.
"""
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List, Dict


def search_results_keyboard(results: List[Dict]) -> InlineKeyboardMarkup:
    """Résultats de recherche /anime."""
    buttons = []
    for r in results:
        title = r.get("title", "Inconnu")[:30]
        slug = r.get("slug", "")
        anime_id = r.get("anime_id", "")
        callback = f"search|{slug}|{anime_id}"
        buttons.append([InlineKeyboardButton(text=title, callback_data=callback)])
    return InlineKeyboardMarkup(buttons)


def seasons_keyboard(saisons: List[str], slug: str, anime_id: str) -> InlineKeyboardMarkup:
    """Boutons des saisons disponibles."""
    buttons = []
    row = []
    for saison in saisons:
        s_val = saison.replace("Saison", "").strip()
        callback = f"season|{slug}|{anime_id}|{s_val}"
        row.append(InlineKeyboardButton(text=saison, callback_data=callback))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def languages_keyboard(languages: List[str], slug: str, anime_id: str, season: str) -> InlineKeyboardMarkup:
    """Boutons des langues disponibles."""
    buttons = []
    row = []
    lang_map = {"VOSTFR": "🌐 VOSTFR", "VF": "🇫🇷 VF", "VA": "🇯🇵 VA", "VJ": "🇯🇵 VJ"}
    for lang in languages:
        label = lang_map.get(lang, lang)
        callback = f"lang|{slug}|{anime_id}|{season}|{lang.lower()}"
        row.append(InlineKeyboardButton(text=label, callback_data=callback))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def episodes_keyboard(episodes: List[Dict], slug: str, anime_id: str, season: str, lang: str) -> InlineKeyboardMarkup:
    """Grille d'épisodes (5 par ligne max)."""
    buttons = []
    row = []
    for ep in episodes:
        num = ep.get("numero")
        if not num:
            continue
        callback = f"ep|{slug}|{anime_id}|{season}|{lang}|{num}"
        row.append(InlineKeyboardButton(text=f"Ep {num}", callback_data=callback))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Bouton "Télécharger toute la page" (placeholder)
    buttons.append([InlineKeyboardButton(text="📥 Télécharger toute la saison", callback_data=f"dlall|{slug}|{anime_id}|{season}|{lang}")])
    return InlineKeyboardMarkup(buttons)


def episode_actions_keyboard(slug: str, anime_id: str, season: str, lang: str, ep_num: str) -> InlineKeyboardMarkup:
    """Actions pour un épisode : Télécharger ou Streaming."""
    buttons = [
        [
            InlineKeyboardButton(text="⬇️ Télécharger", callback_data=f"dl|{slug}|{anime_id}|{season}|{lang}|{ep_num}"),
            InlineKeyboardButton(text="📡 Streaming", callback_data=f"stream|{slug}|{anime_id}|{season}|{lang}|{ep_num}"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)
