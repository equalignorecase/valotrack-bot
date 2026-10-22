import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
import discord
from discord.ext import commands
import config
from database.db import init_db

# Windows Console UTF-8 & Event Loop compatibility
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if sys.version_info < (3, 11):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ValoTracker")


class ValoTrackerBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.start_time = datetime.now(timezone.utc)

    async def setup_hook(self):
        # 1. Initialize SQLite database
        await init_db()

        # 2. Load Cogs
        cog_extensions = ["cogs.auth", "cogs.shop", "cogs.general", "cogs.stats", "cogs.live"]
        for ext in cog_extensions:
            try:
                await self.load_extension(ext)
                logger.info("Loaded extension: %s", ext)
            except Exception as e:
                logger.exception("Failed to load extension %s: %s", ext, e)

        # 3. Synchronize Slash Commands
        try:
            if config.GUILD_ID:
                guild_obj = discord.Object(id=config.GUILD_ID)
                self.tree.copy_global_to(guild=guild_obj)
                synced_guild = await self.tree.sync(guild=guild_obj)
                logger.info("Synced %d command(s) to Guild ID %s", len(synced_guild), config.GUILD_ID)

            synced_global = await self.tree.sync()
            logger.info("Synced %d global slash command(s)", len(synced_global))
        except Exception as e:
            logger.exception("Failed to synchronize application commands: %s", e)

    async def on_ready(self):
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        logger.info("Bot is ready and serving %d guild(s)", len(self.guilds))


def main():
    if not config.DISCORD_TOKEN:
        logger.error(
            "ERROR: Discord Bot Token is missing! "
            "Please provide DISCORD_TOKEN in your .env or appsettings.json file."
        )
        sys.exit(1)

    try:
        # Validate that AES key is accessible
        config.get_aes_key()
    except Exception as e:
        logger.error("ERROR in AES configuration: %s", e)
        logger.info(
            "You can generate a valid 32-byte AES key using:\n"
            "  python3 -c 'import os, base64; print(base64.b64encode(os.urandom(32)).decode())'"
        )
        sys.exit(1)

    bot = ValoTrackerBot()
    try:
        bot.run(config.DISCORD_TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot shutting down gracefully.")


if __name__ == "__main__":
    main()
