import os
from pathlib import Path

import platformdirs

APP_NAME = "myfitnesspal-mcp"


def config_dir() -> Path:
    path = Path(platformdirs.user_config_dir(APP_NAME))
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    override = os.environ.get("MFP_MCP_DATA_DIR")
    if override:
        path = Path(override)
    else:
        path = Path(platformdirs.user_data_dir(APP_NAME))
    path.mkdir(parents=True, exist_ok=True)
    return path


def cookies_path() -> Path:
    return config_dir() / "cookies.json"


def database_path() -> Path:
    return data_dir() / "data.db"


def cookie_env() -> str | None:
    return os.environ.get("MFP_COOKIE")


def username_env() -> str | None:
    return os.environ.get("MFP_USERNAME")


def impersonate() -> str:
    return os.environ.get("MFP_IMPERSONATE", "chrome")


def sync_days() -> int:
    return int(os.environ.get("MFP_SYNC_DAYS", "30"))
