import sys
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "TheOddsGame" / "ETLService"))
from config import get_setting  # noqa: E402


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    host = get_setting("DB_HOST")
    port = get_setting("DB_PORT", "5432")
    database = get_setting("DB_NAME")
    username = get_setting("DB_USER")
    password = get_setting("DB_PASSWORD")

    if not all([host, database, username, password]):
        raise ValueError("Missing required DB settings (DB_HOST, DB_NAME, DB_USER, DB_PASSWORD).")

    url = URL.create(
        "postgresql+psycopg",
        username=username,
        password=password,
        host=host,
        port=int(port),
        database=database,
    )
    return create_engine(url, pool_pre_ping=True, connect_args={"sslmode": "require"})
