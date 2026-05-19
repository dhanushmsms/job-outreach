"""Load user configs and global settings — local YAML files or Streamlit secrets."""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
_ON_STREAMLIT_CLOUD = Path("/mount/src").exists()


def _st_secrets():
    """Return st.secrets dict, or empty dict if not in Streamlit context."""
    try:
        import streamlit as st
        return st.secrets
    except Exception:
        return {}


# ── Settings ───────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    path = BASE_DIR / "config" / "settings.yaml"
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f)

    # Streamlit Cloud — build from st.secrets
    secrets = _st_secrets()
    s = dict(secrets.get("settings", {}))

    # apify_keys stored as JSON string in secrets
    if "apify_keys_json" in s:
        s["apify_keys"] = json.loads(s.pop("apify_keys_json"))

    # service account — point to a temp file we write on the fly
    s["google_service_account_file"] = _ensure_service_account_file(secrets)
    s.setdefault("email_model", "claude-haiku-4-5-20251001")
    s.setdefault("monitor_interval_minutes", 15)
    return s


def _ensure_service_account_file(secrets) -> str:
    """Write service account JSON from secrets to a temp file; return its path."""
    sa = secrets.get("gcp_service_account", {})
    if not sa:
        raise ValueError("No gcp_service_account section found in Streamlit secrets")
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, prefix="sa_"
    )
    json.dump(dict(sa), tmp)
    tmp.close()
    return tmp.name


# ── User configs ───────────────────────────────────────────────────────────────

def load_user_configs(users_dir: Optional[Path] = None) -> list[dict]:
    """Load all non-example user YAML configs, or fall back to Streamlit secrets."""
    if users_dir is None:
        users_dir = BASE_DIR / "config" / "users"

    yaml_files = [
        f for f in sorted(users_dir.glob("*.yaml"))
        if not f.name.endswith(".example.yaml")
    ] if users_dir.exists() else []

    if yaml_files:
        configs = []
        for filepath in yaml_files:
            try:
                with open(filepath) as f:
                    cfg = yaml.safe_load(f)
                cfg["_config_file"] = str(filepath)
                configs.append(cfg)
            except Exception as e:
                logger.warning(f"Failed to load {filepath}: {e}")
        return configs

    # Streamlit Cloud — build from st.secrets [users.dhanush], [users.cindrella], etc.
    secrets = _st_secrets()
    users_secret = secrets.get("users", {})
    configs = []
    for _key, u in users_secret.items():
        cfg = dict(u)
        # Parse JSON string arrays stored in secrets
        for field in ("target_roles", "target_countries"):
            json_key = f"{field}_json"
            if json_key in cfg:
                cfg[field] = json.loads(cfg.pop(json_key))
        configs.append(cfg)
    return configs


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_user_by_name(name: str, configs: list[dict]) -> Optional[dict]:
    for c in configs:
        if c.get("name") == name or c.get("email") == name:
            return c
    return None


def get_country_config(user_config: dict, country_name: str) -> Optional[dict]:
    for c in user_config.get("target_countries", []):
        if c.get("name", "").lower() == country_name.lower():
            return c
    return None


def save_user_config(user_config: dict) -> None:
    filepath = user_config.get("_config_file")
    if not filepath:
        raise ValueError("No _config_file key in user config")
    data = {k: v for k, v in user_config.items() if not k.startswith("_")}
    with open(filepath, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
