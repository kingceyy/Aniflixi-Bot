import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    API_ID = int(os.getenv("API_ID", "35153062"))
    API_HASH = os.getenv("API_HASH", "40679f449bb1e4afa28836490d9410df")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "8975236267:AAHE7nA9Nt44MdJR1BU9MGJVnjnl2ApbD-c")
    TMDB_API_KEY = os.getenv("TMDB_API_KEY", "f2bed62b5977bce26540055276d0046c")
    ZENROWS_API_KEY = os.getenv("ZENROWS_API_KEY", "4396358d958e2b3a491f5cc96ef8d5062941d195")
    OWNER_ID = int(os.getenv("OWNER_ID", "8627938220"))
    CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003818932495")
    TIMEZONE = "Europe/Paris"
    DATA_DIR = "/app/data"
    QUEUE_FILE = os.path.join(DATA_DIR, "queue.json")
    CACHE_DIR = os.path.join(DATA_DIR, "cache")
    FRANIME_BASE = "https://franime.fr"
    TMDB_BASE = "https://api.themoviedb.org/3"
    DOWNLOAD_DIR = "/tmp/anime_bot"
