import discord
from discord.ext import commands
import asyncio
import logging
import os
import json
from pathlib import Path
from dotenv import load_dotenv
from utils.database import DatabaseManager

# ── Load .env before anything else ───────────────────────────────────────────
# Looks for .env in the same directory as this file, so the bot can be started
# from any working directory (e.g. via systemd with WorkingDirectory set).
_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=_BASE_DIR / ".env")

# ── Logging ───────────────────────────────────────────────────────────────────
# Read LOG_LEVEL from environment (set in .env or exported in shell).
# Falls back to INFO if not set or invalid.
_log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)

# Write log file to <project_root>/logs/bot.log — directory is created if
# it doesn't exist, so the bot never crashes on a fresh VPS clone.
_log_dir = _BASE_DIR / "logs"
_log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=_log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),                                          # stdout → systemd journal
        logging.FileHandler(_log_dir / "bot.log", encoding="utf-8"),    # persistent log file
    ],
)
log = logging.getLogger("MaltaSMP")

# ── Config ────────────────────────────────────────────────────────────────────
def load_config() -> dict:
    config_path = _BASE_DIR / "config" / "config.json"
    if config_path.exists():
        with open(config_path, "r") as f:
            return json.load(f)
    return {}

# ── Bot ───────────────────────────────────────────────────────────────────────
class MaltaSMP(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            case_insensitive=True,
        )
        self.config = load_config()
        self.db: DatabaseManager = None

    async def setup_hook(self):
        # Init database
        self.db = DatabaseManager()
        await self.db.initialize()
        await self.db.migrate_v2()   # add AI/security tables
        log.info("Database initialised.")

        # Load cogs
        cogs = [
            "cogs.admin",
            "cogs.tickets",
            "cogs.moderation",
            "cogs.logs",
            "cogs.invites",
            "cogs.welcome",
            "cogs.automod",
            "cogs.security",
            # ── AI & security cogs ──────────────────────────────────────────
            "cogs.ai_chat",           # AI chatbot (GitHub Models / GPT-4o)
            "cogs.ai_moderation",     # AI-powered content moderation
            "cogs.spam_detection",    # Enhanced hybrid spam detection
            "cogs.phishing",          # Phishing / scam link detection
            "cogs.raid_detection",    # Multi-level raid detection
            "cogs.announcements",     # Announcement system with scheduling & templates
            "cogs.help",              # Interactive /help command
        ]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                log.info(f"Loaded cog: {cog}")
            except Exception as e:
                log.error(f"Failed to load cog {cog}: {e}", exc_info=True)

        # Sync slash commands
        try:
            synced = await self.tree.sync()
            log.info(f"Synced {len(synced)} slash commands globally.")
        except Exception as e:
            log.error(f"Failed to sync commands: {e}", exc_info=True)

    async def on_ready(self):
        log.info(f"Logged in as {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Malta SMP",
            )
        )

    async def on_command_error(self, ctx, error):
        log.error(f"Command error: {error}", exc_info=True)

    async def on_error(self, event, *args, **kwargs):
        log.error(f"Unhandled event error in {event}", exc_info=True)

    async def close(self):
        # Clean up the AI service shared aiohttp session
        from utils.ai_service import close_session
        await close_session()
        await super().close()


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        log.critical("DISCORD_TOKEN environment variable not set. Exiting.")
        return

    log.info(f"Starting Malta SMP Bot (log level: {_log_level_name})")
    log.info(f"Base directory: {_BASE_DIR}")

    bot = MaltaSMP()
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
