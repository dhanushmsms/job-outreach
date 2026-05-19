"""
API key rotator — round-robin across multiple keys, auto-fallback on rate limit.
Persists current key index to disk so rotation survives restarts.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = Path("credentials/key_rotation_state.json")


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


class KeyRotator:
    """
    Round-robin key rotator with rate-limit fallback.

    Usage:
        rotator = KeyRotator("anthropic", ["key1", "key2", "key3"])
        key = rotator.get()           # get current key
        rotator.mark_failed(key)      # mark as rate-limited, move to next
    """

    def __init__(self, name: str, keys: list[str]):
        self.name = name
        self.keys = [k for k in keys if k and not k.startswith("ANTHROPIC") and not k.startswith("ADZUNA")]
        self._state = _load_state()
        if self.name not in self._state:
            self._state[self.name] = {"index": 0, "failed": {}}
            _save_state(self._state)

    def _current_index(self) -> int:
        return self._state[self.name].get("index", 0) % max(len(self.keys), 1)

    def get(self) -> Optional[str]:
        if not self.keys:
            return None
        return self.keys[self._current_index()]

    def next(self) -> Optional[str]:
        """Advance to next key and return it."""
        if not self.keys:
            return None
        idx = (self._current_index() + 1) % len(self.keys)
        self._state[self.name]["index"] = idx
        _save_state(self._state)
        logger.info(f"[KeyRotator:{self.name}] Rotated to key index {idx}")
        return self.keys[idx]

    def mark_failed(self, key: str, retry_after_seconds: int = 3600) -> Optional[str]:
        """Mark a key as rate-limited and rotate to the next one."""
        failed = self._state[self.name].setdefault("failed", {})
        failed[key[:8]] = time.time() + retry_after_seconds
        _save_state(self._state)
        logger.warning(f"[KeyRotator:{self.name}] Key ...{key[-4:]} rate-limited, rotating")
        return self.next()

    def is_failed(self, key: str) -> bool:
        failed = self._state[self.name].get("failed", {})
        expiry = failed.get(key[:8], 0)
        if time.time() > expiry:
            return False
        return True

    def get_working(self) -> Optional[str]:
        """Return the first non-failed key, rotating as needed."""
        if not self.keys:
            return None
        for _ in range(len(self.keys)):
            key = self.keys[self._current_index()]
            if not self.is_failed(key):
                return key
            self.next()
        logger.error(f"[KeyRotator:{self.name}] All keys are rate-limited!")
        return self.keys[0]  # return first key anyway and let caller handle error


class AdzunaRotator:
    """Rotates across multiple Adzuna app_id/api_key pairs."""

    def __init__(self, adzuna_keys: list[dict]):
        # Filter out placeholder entries
        self.pairs = [
            k for k in adzuna_keys
            if k.get("app_id") and not k["app_id"].startswith("ADZUNA")
        ]
        self._state = _load_state()
        if "adzuna" not in self._state:
            self._state["adzuna"] = {"index": 0}
            _save_state(self._state)

    def get(self) -> tuple[str, str]:
        """Return (app_id, api_key) for current key."""
        if not self.pairs:
            return "", ""
        idx = self._state["adzuna"].get("index", 0) % len(self.pairs)
        pair = self.pairs[idx]
        return pair["app_id"], pair["api_key"]

    def next(self) -> tuple[str, str]:
        if not self.pairs:
            return "", ""
        idx = (self._state["adzuna"].get("index", 0) + 1) % len(self.pairs)
        self._state["adzuna"]["index"] = idx
        _save_state(self._state)
        logger.info(f"[AdzunaRotator] Rotated to key index {idx}")
        return self.pairs[idx]["app_id"], self.pairs[idx]["api_key"]

    def mark_failed(self) -> tuple[str, str]:
        logger.warning("[AdzunaRotator] Key exhausted, rotating")
        return self.next()
