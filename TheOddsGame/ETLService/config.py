import os
import json
from dotenv import load_dotenv

load_dotenv()

_SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "projectsettings.json")
_settings_cache = None


def load_settings() -> dict:
    """Loads projectsettings.json once and caches it. Returns empty dict if file is missing (e.g. in CI)."""
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache

    if os.path.exists(_SETTINGS_PATH):
        with open(_SETTINGS_PATH, "r") as f:
            _settings_cache = json.load(f)
    else:
        _settings_cache = {}

    return _settings_cache


def get_setting(key: str, default=None):
    """
    Fetch configuration parameter by key.
    Checks environment variables first, then searches for projectsettings.json
    in the current and parent directories. Case-insensitive key fallback.
    """
    # 1. Environment variable check
    val = os.getenv(key)
    if val is not None:
        return val

    # 2. Candidate paths for projectsettings.json
    candidates = [
        os.path.join(os.path.dirname(__file__), "projectsettings.json"),
        os.path.join(os.path.dirname(__file__), "..", "projectsettings.json"),
        os.path.join(os.getcwd(), "projectsettings.json"),
        os.path.join(os.getcwd(), "..", "projectsettings.json"),
    ]

    for p in candidates:
        if os.path.isfile(p):
            try:
                with open(p, "r") as f:
                    data = json.load(f)
                    if key in data:
                        return data[key]
                    if key.upper() in data:
                        return data[key.upper()]
                    if key.lower() in data:
                        return data[key.lower()]
            except Exception:
                pass

    return default