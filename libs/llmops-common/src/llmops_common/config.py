"""Environment-based configuration (12-factor).

Every setting comes from an environment variable. Secrets may instead come from a file:
if NAME is unset or empty and NAME_FILE points to a file, the file's content (stripped) is used.
That is how Kubernetes Secrets mounted as files and Vault agent templates are consumed.
"""
from __future__ import annotations

import os
from typing import Iterable

__all__ = ["ConfigError", "env", "env_bool", "env_int", "env_float", "env_list", "component_name"]


class ConfigError(RuntimeError):
    """A required setting is missing or malformed."""


_MISSING = object()


def env(name: str, default=_MISSING) -> str | None:
    """Return the setting NAME (or the content of NAME_FILE). Raise ConfigError if required and missing."""
    value = os.environ.get(name)
    if value is None or value == "":
        path = os.environ.get(name + "_FILE")
        if path:
            try:
                with open(path, encoding="utf-8") as fh:
                    value = fh.read().strip()
            except OSError as exc:  # pragma: no cover - depends on the filesystem
                raise ConfigError(f"{name}_FILE={path} cannot be read: {exc}") from exc
    if value is None or value == "":
        if default is _MISSING:
            raise ConfigError(f"missing required setting {name} (or {name}_FILE)")
        return default
    return value


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name, None)
    if value is None:
        return default
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be a boolean, got {value!r}")


def env_int(name: str, default: int | None = None) -> int:
    value = env(name, None)
    if value is None:
        if default is None:
            raise ConfigError(f"missing required setting {name}")
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from exc


def env_float(name: str, default: float | None = None) -> float:
    value = env(name, None)
    if value is None:
        if default is None:
            raise ConfigError(f"missing required setting {name}")
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {value!r}") from exc


def env_list(name: str, default: Iterable[str] = ()) -> tuple[str, ...]:
    """Comma-separated list; blanks dropped."""
    value = env(name, None)
    if value is None:
        return tuple(default)
    return tuple(part.strip() for part in value.split(",") if part.strip())


def component_name() -> str:
    return os.environ.get("COMPONENT", "unknown")
