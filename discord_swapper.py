"""Discord library swapper for Timetable Bot.

Allows seamless switching between official discord.py (for bot mode)
and discord.py-self (for selfbot mode), keeping both installed without conflict.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys

log = logging.getLogger(__name__)

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_DISCORDPY_LIB = os.path.join(_PROJECT_DIR, "_discordpy_lib")
if not os.path.isdir(_DISCORDPY_LIB):
    _FALLBACK = r"C:\Users\yajath krushna\Documents\vscode projects\MUSIC_BOT\potato network bot\_discordpy_lib"
    if os.path.isdir(_FALLBACK):
        _DISCORDPY_LIB = _FALLBACK

_active: str | None = None


def _flush_discord_modules() -> None:
    """Remove every discord.* module from the import cache."""
    to_remove = [k for k in sys.modules if k == "discord" or k.startswith("discord.")]
    for k in to_remove:
        del sys.modules[k]


def swap_to_discordpy() -> tuple:
    """Activate the official discord.py (Rapptz) from _discordpy_lib/.

    Returns (discord_module, commands_module).
    """
    global _active
    if _active == "bot" and "discord" in sys.modules:
        return sys.modules["discord"], sys.modules["discord.ext.commands"]

    _flush_discord_modules()

    if os.path.isdir(_DISCORDPY_LIB) and _DISCORDPY_LIB not in sys.path:
        sys.path.insert(0, _DISCORDPY_LIB)

    _discord = importlib.import_module("discord")
    _commands = importlib.import_module("discord.ext.commands")

    log.info("discord.py loaded: v%s from %s", getattr(_discord, "__version__", "?"), getattr(_discord, "__file__", "?"))
    _active = "bot"
    return _discord, _commands


def swap_to_discordpy_self() -> tuple:
    """Activate discord.py-self (the editable install).

    Returns (discord_module, commands_module).
    """
    global _active
    if _active == "selfbot" and "discord" in sys.modules:
        return sys.modules["discord"], sys.modules["discord.ext.commands"]

    _flush_discord_modules()

    if _DISCORDPY_LIB in sys.path:
        sys.path.remove(_DISCORDPY_LIB)

    _discord = importlib.import_module("discord")
    _commands = importlib.import_module("discord.ext.commands")

    log.info("discord.py-self loaded: v%s from %s", getattr(_discord, "__version__", "?"), getattr(_discord, "__file__", "?"))
    _active = "selfbot"
    return _discord, _commands


# Automatically configure discord.py on import
ensure_discordpy = swap_to_discordpy
