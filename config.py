import base64
import json
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Check for legacy appsettings.json if values not found in env
appsettings_path = Path("appsettings.json")
legacy_config = {}
if appsettings_path.exists():
    try:
        with open(appsettings_path, "r", encoding="utf-8") as f:
            legacy_config = json.load(f)
    except Exception:
        pass

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN") or legacy_config.get("Token", "")

guild_id_str = os.getenv("GUILD_ID") or str(legacy_config.get("GuildId", ""))
GUILD_ID = int(guild_id_str) if guild_id_str and guild_id_str.isdigit() else None

AES_KEY_B64 = os.getenv("AES_KEY", "")
DB_PATH = os.getenv("DB_PATH", "ValoTracker.db")

bot_owner_str = os.getenv("BOT_OWNER_ID") or ""
BOT_OWNER_ID = int(bot_owner_str) if bot_owner_str and bot_owner_str.isdigit() else None

# HenrikDev Valorant API Key (clean quotes, spaces, and accidental trailing % from terminal copy-paste)
HENRIK_API_KEY = (os.getenv("HENRIK_API_KEY") or "").strip().strip("\"'").rstrip("%")

# Default Tracked Friends list for /checkrank
DEFAULT_FRIENDS = [
    "cannabiz#99999",
    "wendyy#00000",
    "Zzzz#LK100",
    "Makinsense#March",
    "ควาย#3920",
    "KissMe#4245",
]

# Valorant Logo URL for Discord embeds
VALORANT_LOGO_URL = (
    "https://cdn.iconscout.com/icon/free/png-256/free-valorant-logo-icon-svg-download-png-2724649.png"
)


def get_aes_key() -> bytes:
    """Decodes and validates the 32-byte AES key from base64."""
    raw_key = (os.getenv("AES_KEY") or "").strip().strip("\"'")
    # Remove accidental duplicated 'AES_KEY=' prefix if pasted by user
    while raw_key.startswith("AES_KEY="):
        raw_key = raw_key[8:].strip().strip("\"'")

    if not raw_key:
        raise ValueError(
            "AES_KEY is not set in environment or .env file. "
            "Please generate a 32-byte key using base64 encoding."
        )
    key_bytes = base64.b64decode(raw_key)
    if len(key_bytes) != 32:
        raise ValueError(f"AES_KEY must be exactly 32 bytes (256-bit). Got {len(key_bytes)} bytes.")
    return key_bytes
