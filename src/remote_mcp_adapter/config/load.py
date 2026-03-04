"""YAML config loader with environment interpolation support."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any

import yaml

from .schemas import AdapterConfig

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _interpolate_string(value: str) -> str:
    """Expand all ``${VAR}`` and ``${VAR:-default}`` placeholders in *value*.

    Args:
        value: String that may contain ``${VAR}`` or ``${VAR:-default}`` tokens.

    Returns:
        String with all placeholders resolved to their environment values or defaults.

    Raises:
        ValueError: If a placeholder variable is unset and has no declared default.
    """

    def replace(match: re.Match[str]) -> str:
        """Resolve one match group to its env value or declared default.

        Args:
            match: Regex match containing a variable name and optional default.

        Returns:
            Resolved environment variable value or default.

        Raises:
            ValueError: If the variable is unset and has no declared default.
        """
        var_name = match.group(1)
        default_value = match.group(2)
        env_value = os.getenv(var_name)
        if env_value not in (None, ""):
            return env_value
        if default_value is not None:
            return default_value
        raise ValueError(f"Missing environment variable for config interpolation: {var_name}")

    return _ENV_PATTERN.sub(replace, value)


def _interpolate_env(value: Any) -> Any:
    """Recursively expand env placeholders in dicts, lists, and strings.

    Args:
        value: Arbitrary YAML-parsed value (dict, list, str, or scalar).

    Returns:
        Value with all string placeholders resolved.
    """
    if isinstance(value, dict):
        return {key: _interpolate_env(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(child) for child in value]
    if isinstance(value, str):
        return _interpolate_string(value)
    return value


def load_config(path: str | Path) -> AdapterConfig:
    """Load YAML config and resolve ``${ENV_VAR}`` placeholders.

    Args:
        path: Filesystem path to the YAML configuration file.

    Returns:
        Validated ``AdapterConfig`` instance.

    Raises:
        ValueError: If a required environment variable is unset.
        FileNotFoundError: If the config file does not exist.
    """
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as config_file:
        raw_config: Any = yaml.safe_load(config_file) or {}
    interpolated = _interpolate_env(raw_config)
    return AdapterConfig.model_validate(interpolated)
