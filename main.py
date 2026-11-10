#!/usr/bin/env python3
"""Timetable bot — entry point.

Run:
    python main.py                    # normal start (prompts for token on first run and saves to .env)
    python main.py --prefix "?"       # set + save a new prefix from the terminal
    python main.py --no-sync          # skip slash-command sync on startup

Settings live in .env (bot token) and data/config.json (prefix, timezone, etc.).
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

# Ensure official discord.py (from _discordpy_lib) is used rather than discord.py-self,
# preserving both libraries on disk without interfering with each other.
_THIS_DIR = Path(__file__).resolve().parent
_DISCORDPY_LIB = _THIS_DIR / "_discordpy_lib"
if not _DISCORDPY_LIB.is_dir():
    _FALLBACK = Path(r"C:\Users\yajath krushna\Documents\vscode projects\MUSIC_BOT\potato network bot\_discordpy_lib")
    if _FALLBACK.is_dir():
        _DISCORDPY_LIB = _FALLBACK

if _DISCORDPY_LIB.is_dir() and str(_DISCORDPY_LIB) not in sys.path:
    for _mod in [m for m in list(sys.modules.keys()) if m == "discord" or m.startswith("discord.")]:
        del sys.modules[_mod]
    sys.path.insert(0, str(_DISCORDPY_LIB))

import discord
from discord.ext import commands

if not hasattr(commands, "hybrid_command"):
    commands.hybrid_command = commands.command  # type: ignore[attr-defined]

BASE_DIR = Path(__file__).resolve().parent
COGS_DIR = BASE_DIR / "cogs"
CONFIG_FILE = BASE_DIR / "data" / "config.json"
ENV_FILE = BASE_DIR / ".env"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

log = logging.getLogger("timetable_bot")

DEFAULT_CONFIG: dict[str, Any] = {
    "prefix": "!",
    "timezone": "Asia/Kolkata",
    "sync_on_start": True,
    "token": "",  # prefer the DISCORD_TOKEN environment variable
    "attendance_threshold": 75,
    "admins": [],
}

COLOR_ERROR = 0xED4245
COLOR_WARN = 0xFEE75C


# --------------------------------------------------------------------------- #
# Environment & Token Management
# --------------------------------------------------------------------------- #
def load_environment() -> None:
    """Load variables from .env file into os.environ if present."""
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=ENV_FILE)
        return
    except ImportError:
        pass

    # Fallback parser if python-dotenv is not installed
    if ENV_FILE.is_file():
        try:
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val
        except Exception as exc:
            log.warning("Could not read %s: %s", ENV_FILE, exc)


def save_token_to_env(token: str) -> None:
    """Save or update DISCORD_TOKEN in the .env file."""
    token = token.strip()
    try:
        from dotenv import set_key

        if not ENV_FILE.exists():
            ENV_FILE.touch()
        set_key(str(ENV_FILE), "DISCORD_TOKEN", token, quote_mode="never")
        return
    except ImportError:
        pass

    # Fallback writer if python-dotenv is not installed
    lines: list[str] = []
    found = False
    if ENV_FILE.is_file():
        try:
            lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []

    new_lines: list[str] = []
    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("DISCORD_TOKEN=") or trimmed.startswith("DISCORD_TOKEN "):
            new_lines.append(f"DISCORD_TOKEN={token}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append(f"DISCORD_TOKEN={token}")

    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def get_or_prompt_token(config: dict[str, Any]) -> str:
    """Retrieve the Discord token from environment/.env, or prompt the user to enter it."""
    load_environment()
    token = (os.environ.get("DISCORD_TOKEN") or str(config.get("token") or "")).strip()
    if token:
        return token

    print("\n" + "=" * 58)
    print("  Timetable Bot - Setup")
    print("=" * 58)
    print("No Discord bot token was found in .env or environment.")
    print("Please enter or paste your Discord bot token.")
    print("(It will be saved to .env and used for all future runs)")
    print("=" * 58 + "\n")

    try:
        token_input = input("Enter Discord Bot Token: ").strip().strip("\"'")
    except (KeyboardInterrupt, EOFError):
        print("\n[!] Setup cancelled.")
        return ""

    if not token_input:
        log.critical("No token was entered. Bot cannot start.")
        return ""

    try:
        save_token_to_env(token_input)
        os.environ["DISCORD_TOKEN"] = token_input
        print(f"\n[+] Token successfully saved to {ENV_FILE.name}!\n")
    except Exception as exc:
        log.error("Failed to save token to %s: %s", ENV_FILE, exc)

    return token_input


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
        kwargs: dict[str, Any] = {
            "command_prefix": _dynamic_prefix,
            "case_insensitive": True,
            "strip_after_prefix": True,
            "help_command": None,
        }
        if hasattr(discord, "Intents"):
            intents = discord.Intents.default()
            intents.message_content = True  # also enable it in the Developer Portal
            kwargs["intents"] = intents
        super().__init__(**kwargs)
        if hasattr(self, "tree"):
            self.tree.on_error = self.on_app_command_error  # type: ignore[assignment]

    @property
    def prefix(self) -> str:
        return str(self.config.get("prefix") or DEFAULT_CONFIG["prefix"])

    def get_admin_ids(self) -> set[int]:
        raw = self.config.get("admins", [])
        if not isinstance(raw, list):
            return set()
        ids: set[int] = set()
        for x in raw:
            try:
                ids.add(int(x))
            except (ValueError, TypeError):
                pass
        return ids

    def add_admin(self, user_id: int) -> bool:
        admin_ids = self.get_admin_ids()
        if user_id in admin_ids:
            return False
        admin_ids.add(user_id)
        self.config["admins"] = sorted(list(admin_ids))
        save_config(self.config)
        return True

    def remove_admin(self, user_id: int) -> bool:
        admin_ids = self.get_admin_ids()
        if user_id not in admin_ids:
            return False
        admin_ids.discard(user_id)
        self.config["admins"] = sorted(list(admin_ids))
        save_config(self.config)
        return True

    async def get_primary_owner(self) -> Optional[int]:
        if self.owner_id:
            return self.owner_id
        if self.owner_ids:
            return next(iter(self.owner_ids))
        try:
            app = await self.application_info()
            if getattr(app, "team", None) and getattr(app.team, "owner_id", None):
                return app.team.owner_id
            elif getattr(app, "owner", None):
                self.owner_id = app.owner.id
                return app.owner.id
        except Exception:
            pass
        return None

    async def is_owner(self, user: discord.abc.User, /) -> bool:
        """Override discord.py's is_owner to treat configured co-admins with identical powers."""
        if user.id in self.get_admin_ids():
            return True
        if self.owner_id:
            return user.id == self.owner_id
        if self.owner_ids:
            return user.id in self.owner_ids
        try:
            app = await self.application_info()
            if getattr(app, "team", None) and getattr(app.team, "members", None):
                self.owner_ids = {m.id for m in app.team.members}
                return user.id in self.owner_ids
            elif getattr(app, "owner", None):
                self.owner_id = app.owner.id
                return user.id == self.owner_id
        except Exception:
            pass
        return False

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
        if hasattr(self, "tree") and self.config.get("sync_on_start", True):
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
        embed = discord.Embed(title=title, description=description, color=color)
        embed.set_author(name="Timetable Bot")
        return embed

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        cause = self._unwrap(error)
        embed: discord.Embed

        user_message = getattr(cause, "user_message", None)
        _AppCheck = getattr(getattr(discord, "app_commands", None), "CheckFailure", ())
        if user_message:
            embed = self._embed("⚠️ Notice", user_message, COLOR_WARN)
        elif isinstance(error, (commands.CheckFailure, _AppCheck)) or isinstance(cause, (commands.CheckFailure, _AppCheck)):
            msg = str(cause).strip()
            if not msg or "The check" in msg or "check functions" in msg or "CheckFailure" in msg:
                msg = "You do not have permission to execute this command."
            embed = self._embed("🔒 Access Denied", msg, COLOR_ERROR)
        elif isinstance(error, commands.MissingRequiredArgument) and ctx.command:
            usage = f"{ctx.clean_prefix}{ctx.command.qualified_name} {ctx.command.signature}"
            embed = self._embed(
                "⚠️ Missing Parameter",
                f"You forgot the **`{error.param.name}`** parameter.\n\n> 💡 **Usage:** `{usage}`",
                COLOR_WARN,
            )
        elif isinstance(error, commands.UserInputError):
            embed = self._embed("⚠️ Invalid Input", str(error)[:500] or "Please check your command arguments.", COLOR_WARN)
        elif isinstance(error, commands.CommandOnCooldown):
            embed = self._embed("⏳ Cooldown Active", f"Please wait **{error.retry_after:.1f}s** before trying again.", COLOR_WARN)
        else:
            name = ctx.command.qualified_name if ctx.command else "unknown"
            log.error("Unhandled error in command %s", name, exc_info=cause)
            embed = self._embed(
                "💥 Something went wrong",
                f"An unexpected `{type(cause).__name__}` occurred.\nIt has been logged to the console.",
                COLOR_ERROR,
            )

        with contextlib.suppress(discord.HTTPException):
            await ctx.send(embed=embed, ephemeral=True)

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError
    ) -> None:
        cause = self._unwrap(error)
        user_message = getattr(cause, "user_message", None)
        _AppCheck = getattr(getattr(discord, "app_commands", None), "CheckFailure", ())
        if user_message:
            embed = self._embed("⚠️ Notice", user_message, COLOR_WARN)
        elif isinstance(error, (commands.CheckFailure, _AppCheck)) or isinstance(cause, (commands.CheckFailure, _AppCheck)):
            msg = str(cause).strip()
            if not msg or "The check" in msg or "check functions" in msg or "CheckFailure" in msg:
                msg = "You do not have permission to execute this command."
            embed = self._embed("🔒 Access Denied", msg, COLOR_ERROR)
        else:
            log.error("Unhandled slash-command error", exc_info=cause)
            embed = self._embed(
                "💥 Something went wrong",
                f"An error occurred: `{type(cause).__name__}`.\nCheck the terminal for details.",
                COLOR_ERROR,
            )
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

    token = get_or_prompt_token(config)
    if not token:
        return 1

    discord.VoiceClient.warn_nacl = False  # no voice features, silence the PyNaCl warning
    bot = TimetableBot(config)
    async with bot:
        await load_cogs(bot)
        try:
            await bot.start(token)
        except discord.LoginFailure:
            log.critical("Discord rejected the token. Please check DISCORD_TOKEN in %s.", ENV_FILE.name)
            return 1
        except discord.PrivilegedIntentsRequired:
            log.critical("Enable 'Message Content Intent' in the Developer Portal → Bot → Privileged Gateway Intents.")
            return 1
    return 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    discord.utils.setup_logging(level=logging.INFO)
    load_environment()
    args = parse_args()
    try:
        code = asyncio.run(amain(args))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
