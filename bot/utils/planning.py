"""
Rendu partagé du planning (texte + clavier de pagination par jour).
Utilisé par la commande /planning et le callback de navigation.
"""
from typing import Dict, List, Tuple

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

DAYS_ORDER = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
DAYS_LABEL = {
    "lundi": "LUNDI", "mardi": "MARDI", "mercredi": "MERCREDI",
    "jeudi": "JEUDI", "vendredi": "VENDREDI", "samedi": "SAMEDI", "dimanche": "DIMANCHE",
}


def render_planning_day(planning: Dict[str, List[Dict]], day_key: str) -> Tuple[str, InlineKeyboardMarkup]:
    """Construit le texte HTML et le clavier de pagination pour un jour donné."""
    releases = planning.get(day_key, [])
    label = DAYS_LABEL.get(day_key, day_key.upper())

    if not releases:
        text = f"📅 <b>Planning</b> — {label}\n\nAucune sortie prévue ce jour-là."
    else:
        lines = [f"📅 <b>Planning</b> — {label}\n"]
        for r in releases:
            lines.append(
                f"• <b>{r.get('titre', 'Inconnu')}</b> — "
                f"Ep {r.get('episode', '?')} — "
                f"{r.get('heure', '?')} — "
                f"[{r.get('lang', 'vostfr').upper()}]"
            )
        text = "\n".join(lines)

    idx = DAYS_ORDER.index(day_key)
    prev_day = DAYS_ORDER[(idx - 1) % len(DAYS_ORDER)]
    next_day = DAYS_ORDER[(idx + 1) % len(DAYS_ORDER)]

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(text=f"◀️ {DAYS_LABEL[prev_day].title()}", callback_data=f"planning|{prev_day}"),
        InlineKeyboardButton(text=f"{DAYS_LABEL[next_day].title()} ▶️", callback_data=f"planning|{next_day}"),
    ]])

    return text, keyboard
