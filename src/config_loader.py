"""Load user configs and global settings from YAML files."""

import logging
import os
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent


def load_settings() -> dict:
    path = BASE_DIR / "config" / "settings.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def load_user_configs(users_dir: Optional[Path] = None) -> list[dict]:
    """Load all non-example user yaml configs from config/users/."""
    if users_dir is None:
        users_dir = BASE_DIR / "config" / "users"

    configs = []
    for filepath in sorted(users_dir.glob("*.yaml")):
        if filepath.name.endswith(".example.yaml"):
            continue
        try:
            with open(filepath) as f:
                cfg = yaml.safe_load(f)
            cfg["_config_file"] = str(filepath)
            configs.append(cfg)
        except Exception as e:
            logger.warning(f"Failed to load {filepath}: {e}")

    return configs


def get_user_by_name(name: str, configs: list[dict]) -> Optional[dict]:
    for c in configs:
        if c.get("name") == name or c.get("email") == name:
            return c
    return None


def get_country_config(user_config: dict, country_name: str) -> Optional[dict]:
    """Return the country config dict for a given country name."""
    for c in user_config.get("target_countries", []):
        if c.get("name", "").lower() == country_name.lower():
            return c
    return None


def save_user_config(user_config: dict) -> None:
    """Write updated user config back to its YAML file."""
    filepath = user_config.get("_config_file")
    if not filepath:
        raise ValueError("No _config_file key in user config")
    data = {k: v for k, v in user_config.items() if not k.startswith("_")}
    with open(filepath, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
