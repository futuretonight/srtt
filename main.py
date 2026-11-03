#!/usr/bin/env python3
"""Timetable bot — entry point.

Run:
    export DISCORD_TOKEN="your-token"
    python main.py                    # normal start
    python main.py --prefix "?"       # set + save a new prefix from the terminal
    python main.py --no-sync          # skip slash-command sync on startup

Settings live in data/config.json (auto-created on first run).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

BASE_DIR = Path(__file__).resolve().parent
COGS_DIR = BASE_DIR / "cogs"
CONFIG_FILE = BASE_DIR / "data" / "config.json"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

log = logging.getLogger("timetable_bot")

DEFAULT_CONFIG: dict[str, Any] = {
    "prefix": "!",
    "timezone": "Asia/Kolkata",
    "sync_on_start": True,
    "token": "",  # prefer the DISCORD_TOKEN environment variable
}

COLOR_ERROR = 0xED4245
COLOR_WARN = 0xFEE75C


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def save_config(config: dict[str, Any]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CONFIG_FILE.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, CONFIG_FILE)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def load_config() -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    try:
        loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            config.update(loaded)
    except FileNotFoundError:
        try:
            save_config(config)
            log.info("Created %s", CONFIG_FILE)
        except OSError:
            log.warning("Couldn't create %s; using defaults.", CONFIG_FILE)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        log.warning("Couldn't read config (%s); using defaults.", exc)
    return config


def validate_prefix(prefix: str) -> str:
    if not prefix or len(prefix) > 5 or any(ch.isspace() for ch in prefix):
        raise ValueError("A prefix must be 1–5 characters long with no spaces.")
    return prefix


# --------------------------------------------------------------------------- #
# Bot
# --------------------------------------------------------------------------- #
def _dynamic_prefix(bot: "TimetableBot", message: discord.Message):
    return commands.when_mentioned_or(bot.prefix)(bot, message)


class TimetableBot(commands.Bot):
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        intents = discord.Intents.default()
        intents.message_content = True  # also enable it in the Developer Portal
        super().__init__(
            command_prefix=_dynamic_prefix,
            intents=intents,
            case_insensitive=True,
            strip_after_prefix=True,
        )
        self.tree.on_error = self.on_app_command_error  # type: ignore[assignment]

    @property
    def prefix(self) -> str:
        return str(self.config.get("prefix") or DEFAULT_CONFIG["prefix"])

    async def change_prefix(self, new_prefix: str) -> None:
        """Validate, persist and apply a new prefix. Raises ValueError if invalid."""
        self.config["prefix"] = validate_prefix(new_prefix)
        save_config(self.config)
        await self.refresh_presence()

    async def refresh_presence(self) -> None:
        if not self.is_ready():
            return
        with contextlib.suppress(discord.HTTPException):
            await self.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name=f"your timetable • {self.prefix}timetable",
                )
            )

    async def setup_hook(self) -> None:
        if self.config.get("sync_on_start", True):
            try:
                synced = await self.tree.sync()
                log.info("Synced %d slash command(s).", len(synced))
            except discord.HTTPException:
                log.exception("Slash command sync failed (use the sync command later).")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id %s) | prefix: %r", self.user, getattr(self.user, "id", "?"), self.prefix)
        await self.refresh_presence()

    # ----- error handling -------------------------------------------------- #
    @staticmethod
    def _unwrap(error: BaseException) -> BaseException:
        for _ in range(6):
            inner = getattr(error, "original", None)
            if inner is None:
                break
            error = inner
        return error

    @staticmethod
    def _embed(title: str, description: str, color: int = COLOR_ERROR) -> discord.Embed:
        return discord.Embed(title=title, description=description, color=color)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        cause = self._unwrap(error)
        embed: discord.Embed

        user_message = getattr(cause, "user_message", None)
        if user_message:
            embed = self._embed("⚠️ Heads up", user_message, COLOR_WARN)
        elif isinstance(error, (commands.CheckFailure, discord.app_commands.CheckFailure)):
            embed = self._embed("🔒 Owner only", "That command is restricted to the bot owner.")
        elif isinstance(error, commands.MissingRequiredArgument) and ctx.command:
            usage = f"{ctx.clean_prefix}{ctx.command.qualified_name} {ctx.command.signature}"
            embed = self._embed(
                "⚠️ Missing argument",
                f"You forgot **{error.param.name}**.\nUsage: `{usage}`",
                COLOR_WARN,
            )
        elif isinstance(error, commands.UserInputError):
            embed = self._embed("⚠️ Invalid input", str(error)[:500] or "Check your arguments.", COLOR_WARN)
        elif isinstance(error, commands.CommandOnCooldown):
            embed = self._embed("⏳ Slow down", f"Try again in {error.retry_after:.1f}s.", COLOR_WARN)
        else:
            name = ctx.command.qualified_name if ctx.command else "unknown"
            log.error("Unhandled error in command %s", name, exc_info=cause)
            embed = self._embed(
                "💥 Something went wrong",
                f"`{type(cause).__name__}` — it's been logged. The bot is still running.",
            )

        with contextlib.suppress(discord.HTTPException):
            await ctx.send(embed=embed, ephemeral=True)

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError
    ) -> None:
        cause = self._unwrap(error)
        log.error("Unhandled slash-command error", exc_info=cause)
        embed = self._embed("💥 Something went wrong", f"`{type(cause).__name__}` — it's been logged.")
        with contextlib.suppress(discord.HTTPException):
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)


# --------------------------------------------------------------------------- #
# Boot sequence
# --------------------------------------------------------------------------- #
async def load_cogs(bot: commands.Bot) -> None:
    if not COGS_DIR.is_dir():
        log.error("Cogs folder not found: %s", COGS_DIR)
        return
    for path in sorted(COGS_DIR.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        ext = f"cogs.{path.stem}"
        try:
            await bot.load_extension(ext)
            log.info("Loaded %s", ext)
        except commands.ExtensionError:
            log.exception("Failed to load %s — continuing without it.", ext)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Timetable Discord bot")
    parser.add_argument("--prefix", help="set and save a new command prefix, then start")
    parser.add_argument("--no-sync", action="store_true", help="skip slash-command sync on startup")
    return parser.parse_args()


async def amain(args: argparse.Namespace) -> int:
    config = load_config()

    if args.prefix is not None:
        try:
            config["prefix"] = validate_prefix(args.prefix)
            save_config(config)
            log.info("Prefix saved: %r", config["prefix"])
        except ValueError as exc:
            log.critical("%s", exc)
            return 2
        except OSError:
            log.warning("Couldn't save the prefix to disk; using it for this run only.")
    if args.no_sync:
        config["sync_on_start"] = False

    token = (os.environ.get("DISCORD_TOKEN") or str(config.get("token") or "")).strip()
    if not token:
        log.critical("No token found. Run: export DISCORD_TOKEN='your-token'  (or set it in data/config.json)")
        return 1

    discord.VoiceClient.warn_nacl = False  # no voice features, silence the PyNaCl warning
    bot = TimetableBot(config)
    async with bot:
        await load_cogs(bot)
        try:
            await bot.start(token)
        except discord.LoginFailure:
            log.critical("Discord rejected the token. Check DISCORD_TOKEN.")
            return 1
        except discord.PrivilegedIntentsRequired:
            log.critical("Enable 'Message Content Intent' in the Developer Portal → Bot → Privileged Gateway Intents.")
            return 1
    return 0


def main() -> None:
    discord.utils.setup_logging(level=logging.INFO)
    args = parse_args()
    try:
        code = asyncio.run(amain(args))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
