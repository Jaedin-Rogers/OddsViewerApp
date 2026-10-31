import os
import time
import argparse
import logging
from datetime import datetime
import polars as pl
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from balldontlie import BalldontlieAPI
from config import get_setting

load_dotenv()

# ==========================================
# Logging Configuration
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("GameSync")


def log_dataframe_head(label: str, df: pl.DataFrame, limit: int = 5):
    total_count = df.height
    logger.info(f"{label} - Total Count: {total_count}")
    if total_count > 0:
        logger.info(f"--- First {min(limit, total_count)} Sample Rows ---")
        for i, row in enumerate(df.head(limit).iter_rows(named=True)):
            logger.info(f"  [{i + 1}] {row}")
    else:
        logger.info("  (0 rows)")


def log_sample_results(label: str, result_proxy, limit: int = 5):
    rows = result_proxy.fetchall()
    total_count = len(rows)
    logger.info(f"{label} - Total Processed Rows: {total_count}")
    if total_count > 0:
        logger.info(f"--- First {min(limit, total_count)} Sample Rows ---")
        for i, row in enumerate(rows[:limit]):
            logger.info(f"  [{i + 1}] {dict(row._mapping)}")
    else:
        logger.info("  (No rows affected/returned)")


# ==========================================
# Fetch Games from API
# ==========================================
def _flatten(record: dict, parent: str = "") -> dict:
    """Flattens nested dicts into dotted keys (e.g. home_team.id), like pandas.json_normalize."""
    flat = {}
    for key, value in record.items():
        name = f"{parent}.{key}" if parent else key
        if isinstance(value, dict):
            flat.update(_flatten(value, name))
        else:
            flat[name] = value
    return flat


def _games_frame(records: list) -> pl.DataFrame:
    if not records:
        return pl.DataFrame()
    return pl.DataFrame([_flatten(r) for r in records], infer_schema_length=None)


def fetch_games_api(
    api: BalldontlieAPI,
    dates: list = None,
    start_date: str = None,
    end_date: str = None,
    seasons: list = None,
    team_ids: list = None,
    game_id: int = None
) -> pl.DataFrame:
    all_games = []
    cursor = None
    backoff = 5
    page_count = 0

    if game_id:
        logger.info(f"Fetching specific game ID: {game_id}...")
        try:
            game = api.nba.games.get(game_id)
            game_data = game.model_dump() if hasattr(game, "model_dump") else game
            return _games_frame([game_data])
        except Exception as e:
            logger.error(f"Error fetching game {game_id}: {e}")
            return pl.DataFrame()

    logger.info(f"Fetching games from BallDontLie (dates: {dates}, start_date: {start_date}, end_date: {end_date}, seasons: {seasons}, teams: {team_ids})...")

    while True:
        try:
            params = {"per_page": 100}
            if cursor:
                params["cursor"] = cursor
            if dates:
                params["dates"] = dates
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date
            if seasons:
                params["seasons"] = seasons
            if team_ids:
                params["team_ids"] = team_ids

            resp = api.nba.games.list(**params)
            page_records = [g.model_dump() if hasattr(g, "model_dump") else g for g in resp.data]
            all_games.extend(page_records)
            page_count += 1

            logger.info(f"Fetched page {page_count} ({len(page_records)} games, total: {len(all_games)})")

            backoff = 5
            cursor = getattr(resp.meta, "next_cursor", None)
            if not cursor:
                break

            time.sleep(2.2)

        except Exception as e:
            if "Too Many Requests" in str(e) or "429" in str(e):
                logger.warning(f"Rate limit hit. Waiting {backoff} seconds before retrying...")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            logger.error(f"Stopped fetching games: {e}")
            break

    return _games_frame(all_games)


# ==========================================
# Transform & Upsert Game Logs

OPTIONAL_RAW_COLUMNS = [
    "id", "datetime", "date", "home_team.id", "home_team.city", "home_team.name",
    "visitor_team.id", "visitor_team.city", "visitor_team.name",
    "home_team_score", "visitor_team_score", "postseason",
]

STAGING_COLUMNS = {
    "gameId": "bigint", "gameDate": "text", "gameDateTimeEst": "text",
    "hometeamId": "bigint", "hometeamCity": "text", "hometeamName": "text",
    "awayteamId": "bigint", "awayteamCity": "text", "awayteamName": "text",
    "homeScore": "bigint", "awayScore": "bigint", "winner": "bigint", "gameType": "text",
}


def transform_games(df_raw: pl.DataFrame) -> pl.DataFrame:
    """Maps the flattened BallDontLie game records onto the game.game_dim columns."""
    missing = [c for c in OPTIONAL_RAW_COLUMNS if c not in df_raw.columns]
    df_raw = df_raw.with_columns([pl.lit(None).alias(c) for c in missing])

    date_str = pl.col("date").cast(pl.Utf8)
    datetime_str = pl.col("datetime").cast(pl.Utf8)

    def parse_ts(s: pl.Expr) -> pl.Expr:
        # Accepts 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS' and ISO 'YYYY-MM-DDTHH:MM:SS.fffZ' (wall clock as given);
        # anything unparseable becomes null.
        full = s.str.slice(0, 19).str.replace("T", " ").str.to_datetime("%Y-%m-%d %H:%M:%S", strict=False)
        day = s.str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False).cast(pl.Datetime)
        return pl.coalesce(full, day)

    home_score = pl.col("home_team_score").cast(pl.Int64, strict=False)
    away_score = pl.col("visitor_team_score").cast(pl.Int64, strict=False)
    home_id = pl.col("home_team.id").cast(pl.Int64, strict=False)
    away_id = pl.col("visitor_team.id").cast(pl.Int64, strict=False)

    return df_raw.select(
        pl.col("id").cast(pl.Int64, strict=False).alias("gameId"),
        # gameDate is strictly YYYY-MM-DD, falling back to the raw value if it cannot be parsed
        pl.coalesce(
            date_str.str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False).dt.strftime("%Y-%m-%d"),
            date_str,
        ).alias("gameDate"),
        # gameDateTimeEst is 'YYYY-MM-DD HH:MM:SS' from datetime, then date, then the raw date text
        pl.coalesce(
            parse_ts(datetime_str).dt.strftime("%Y-%m-%d %H:%M:%S"),
            parse_ts(date_str).dt.strftime("%Y-%m-%d %H:%M:%S"),
            date_str,
        ).alias("gameDateTimeEst"),
        home_id.alias("hometeamId"),
        pl.col("home_team.city").cast(pl.Utf8).alias("hometeamCity"),
        pl.col("home_team.name").cast(pl.Utf8).alias("hometeamName"),
        away_id.alias("awayteamId"),
        pl.col("visitor_team.city").cast(pl.Utf8).alias("awayteamCity"),
        pl.col("visitor_team.name").cast(pl.Utf8).alias("awayteamName"),
        home_score.alias("homeScore"),
        away_score.alias("awayScore"),
        # winner stays null when a score is missing or the scores are tied
        pl.when(home_score > away_score).then(home_id)
        .when(away_score > home_score).then(away_id)
        .otherwise(None)
        .alias("winner"),
        pl.when(pl.col("postseason").cast(pl.Boolean, strict=False).fill_null(False))
        .then(pl.lit("Playoffs"))
        .otherwise(pl.lit("Regular Season"))
        .alias("gameType"),
    )


def stage_games(conn, df: pl.DataFrame):
    """Recreates game._stg_game_dim and loads df into it (no pandas needed)."""
    columns = ", ".join(f'"{name}" {sql_type}' for name, sql_type in STAGING_COLUMNS.items())
    conn.execute(text("DROP TABLE IF EXISTS game._stg_game_dim;"))
    conn.execute(text(f"CREATE TABLE game._stg_game_dim ({columns});"))

    rows = df.select(list(STAGING_COLUMNS)).to_dicts()
    if rows:
        names = ", ".join(f'"{name}"' for name in STAGING_COLUMNS)
        params = ", ".join(f":{name}" for name in STAGING_COLUMNS)
        conn.execute(text(f"INSERT INTO game._stg_game_dim ({names}) VALUES ({params});"), rows)

def sync_games(
    api: BalldontlieAPI,
    engine: Engine,
    dates: list = None,
    start_date: str = None,
    end_date: str = None,
    seasons: list = None,
    team_ids: list = None,
    game_id: int = None
):
    df_raw = fetch_games_api(
        api,
        dates=dates,
        start_date=start_date,
        end_date=end_date,
        seasons=seasons,
        team_ids=team_ids,
        game_id=game_id
    )
    if df_raw.is_empty():
        logger.warning("No game records retrieved to sync.")
        return

    df_transformed = transform_games(df_raw)

    # Staging to PostgreSQL
    with engine.begin() as conn:
        stage_games(conn, df_transformed)
        # Log and remove games skipped due to missing team foreign keys
        res_skipped = conn.execute(text("""
            DELETE FROM game._stg_game_dim
            WHERE ("hometeamId" IS NOT NULL AND "hometeamId" NOT IN (SELECT "teamId" FROM team.team_dim))
               OR ("awayteamId" IS NOT NULL AND "awayteamId" NOT IN (SELECT "teamId" FROM team.team_dim))
            RETURNING "gameId", "gameDate", "hometeamId", "awayteamId", "hometeamName", "awayteamName";
        """))
        log_sample_results("Skipped Games (Foreign Key Mismatch)", res_skipped)

        # Upsert into game.game_dim and return affected rows
        upsert_res = conn.execute(text("""
            INSERT INTO game.game_dim (
                "gameId", "gameDateTimeEst", "gameDate", 
                "hometeamId", "hometeamCity", "hometeamName",
                "awayteamId", "awayteamCity", "awayteamName",
                "homeScore", "awayScore", winner, "gameType"
            )
            SELECT DISTINCT ON (stg."gameId")
                stg."gameId", stg."gameDateTimeEst", stg."gameDate",
                stg."hometeamId", stg."hometeamCity", stg."hometeamName",
                stg."awayteamId", stg."awayteamCity", stg."awayteamName",
                stg."homeScore", stg."awayScore", stg.winner, stg."gameType"
            FROM game._stg_game_dim stg
            ON CONFLICT ("gameId") DO UPDATE 
            SET 
                "gameDateTimeEst" = EXCLUDED."gameDateTimeEst",
                "gameDate"        = EXCLUDED."gameDate",
                "hometeamId"      = EXCLUDED."hometeamId",
                "hometeamCity"    = EXCLUDED."hometeamCity",
                "hometeamName"    = EXCLUDED."hometeamName",
                "awayteamId"      = EXCLUDED."awayteamId",
                "awayteamCity"    = EXCLUDED."awayteamCity",
                "awayteamName"    = EXCLUDED."awayteamName",
                "homeScore"       = EXCLUDED."homeScore",
                "awayScore"       = EXCLUDED."awayScore",
                "winner"          = EXCLUDED.winner,
                "gameType"        = EXCLUDED."gameType"
            RETURNING "gameId", "gameDate", "hometeamName", "awayteamName", "homeScore", "awayScore", winner;
        """))

        log_sample_results("Inserted/Updated Games", upsert_res)
        conn.execute(text("DROP TABLE IF EXISTS game._stg_game_dim;"))

    logger.info("Game sync process completed successfully.")


# ==========================================
# CLI Entrypoint
# ==========================================
def main():
    parser = argparse.ArgumentParser(description="Sync game metadata from BallDontLie into game.game_dim.")
    parser.add_argument("--start_date", type=str, help="Start date to query games (YYYY-MM-DD), e.g. --start_date 2026-01-01")
    parser.add_argument("--end_date", type=str, help="End date to query games (YYYY-MM-DD), e.g. --end_date 2026-03-01")
    parser.add_argument("--seasons", nargs="+", type=int, help="Seasons to sync, e.g. --seasons 2025")
    parser.add_argument("--team_ids", nargs="+", type=int, help="Team IDs to filter by")
    parser.add_argument("--game_id", type=int, help="Sync a single game ID")

    args = parser.parse_args()

    db_user = get_setting("DB_USER") or get_setting("pg_user")
    db_pass = get_setting("DB_PASSWORD") or get_setting("pg_password")
    db_host = get_setting("DB_HOST") or get_setting("pg_host")
    db_port = get_setting("DB_PORT") or get_setting("pg_port", "5432")
    db_name = get_setting("DB_NAME") or get_setting("pg_database")
    api_key = get_setting("BALLDONTLIE_API_KEY") or get_setting("balldontlie_api_key")

    if not all([db_user, db_pass, db_host, db_name]):
        raise ValueError("Missing database connection parameters. Ensure DB_HOST, DB_NAME, DB_USER, and DB_PASSWORD are set in config or env.")

    if not api_key:
        raise ValueError("BallDontLie API key is required. Set BALLDONTLIE_API_KEY in projectsettings.json or env.")

    engine = create_engine(f"postgresql+psycopg://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}")
    api = BalldontlieAPI(api_key=api_key)

    sync_games(
        api=api,
        engine=engine,
        start_date=args.start_date,
        end_date=args.end_date,
        seasons=args.seasons,
        team_ids=args.team_ids,
        game_id=args.game_id
    )


if __name__ == "__main__":
    main()