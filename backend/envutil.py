# envutil.py - read numeric settings from the environment with an error that names the
# variable (code review M-31).
#
# A bare int(os.getenv("DV_X", "5")) raises "invalid literal for int() with base 10: '5s'"
# at import time, which names neither the variable nor the file it came from. These
# helpers fail the same way (at start-up, as a ValueError) but say which setting is wrong.
# A blank value counts as unset, so `DV_X=` in .env falls back to the default instead of
# crashing.

from __future__ import annotations

import os


class ConfigError(ValueError):
    """A setting in the environment cannot be used."""


def _raw(name: str, default: str | int | float) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        return str(default)
    return value.strip()


def env_int(name: str, default: str | int = 0) -> int:
    text = _raw(name, default)
    try:
        return int(text)
    except ValueError:
        raise ConfigError(f"{name}={text!r} is not a whole number (check .env or the server environment)") from None


def env_float(name: str, default: str | int | float = 0.0) -> float:
    text = _raw(name, default)
    try:
        return float(text)
    except ValueError:
        raise ConfigError(f"{name}={text!r} is not a number (check .env or the server environment)") from None
