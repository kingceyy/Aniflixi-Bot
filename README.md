# Anime Bot — FRAnime + TMDB + Kurigram

Bot Telegram de publication automatique d'animes hebdomadaires et de recherche interactive.

## Stack
- **Kurigram** — Client Telegram async
- **FRAnime Scraper** — Calendrier, recherche, liens épisodes
- **TMDB API** — Posters HD & métadonnées
- **APScheduler** — Publication auto (00:00, H-2, H+0)
- **FFmpeg** — Conversion 480p

## Déploiement Koyeb

1. **Créer un repo GitHub** et push ce projet :
```bash
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/VOTRE_USER/anime-bot.git
git push -u origin main
```

2. **Créer l'app sur Koyeb** :
- Type : GitHub repository
- Builder : Dockerfile
- Port : 5000 (ou laissez par défaut, le bot n'expose pas d'HTTP)
- Variables d'environnement : copiez depuis `.env.example`

3. **Activer le volume persistant** `/app/data` sur Koyeb (optionnel mais recommandé pour `queue.json`)

## Variables d'environnement

| Variable | Description |
|----------|-------------|
| `BOT_TOKEN` | Token du bot Telegram (@BotFather) |
| `TMDB_API_KEY` | Clé API TMDB |
| `OWNER_ID` | Votre ID Telegram (seul utilisateur autorisé) |
| `CHANNEL_ID` | ID ou @username du canal de publication |

## Commandes

| Commande | Description |
|----------|-------------|
| `/anime <nom>` | Recherche un anime (tolère les fautes) |
| `/planning` | Affiche le planning du jour |
| `/status` | État de la file d'attente auto |

## Architecture

```
Koyeb Container
├── Bot Kurigram (async)
│   ├── FRAnime Scraper (calendrier + recherche + liens)
│   ├── TMDB Client (posters + métadonnées)
│   └── APScheduler (minuit, H-2, H+0)
└── Volume /app/data/ (queue.json + cache posters)
```
