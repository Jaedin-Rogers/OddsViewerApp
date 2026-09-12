import os
import time
import argparse
import logging
from datetime import datetime
import pandas as pd
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


def log_dataframe_head(label: str, df: pd.DataFrame, limit: int = 5):
    total_count = len(df)
    logger.info(f"{label} - Total Count: {total_count}")
    if total_count > 0:
        logger.info(f"--- First {min(limit, total_count)} Sample Rows ---")
        for i, row in df.head(limit).iterrows():
            logger.info(f"  [{i + 1}] {row.to_dict()}")
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
def fetch_games_api(
    api: BalldontlieAPI,
    dates: list = None,
    start_date: str = None,
    end_date: str = None,
    seasons: list = None,
    team_ids: list = None,
    game_id: int = None
) -> pd.DataFrame:
    all_games = []
    cursor = None
    backoff = 5
    page_count = 0

    if game_id:
        logger.info(f"Fetching specific game ID: {game_id}...")
        try:
            game = api.nba.games.get(game_id)
            game_data = game.model_dump() if hasattr(game, "model_dump") else game
            return pd.json_normalize([game_data])
        except Exception as e:
            logger.error(f"Error fetching game {game_id}: {e}")
            return pd.DataFrame()

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

    if not all_games:
        return pd.DataFrame()

    return pd.json_normalize(all_games)


# ==========================================
# Transform & Upsert Game Logs
# ==========================================
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
    if df_raw.empty:
        logger.warning("No game records retrieved to sync.")
        return

    # Ensure optional columns exist in df_raw
    for col in ["datetime", "date", "home_team.id", "home_team.city", "home_team.name",
                "visitor_team.id", "visitor_team.city", "visitor_team.name",
                "home_team_score", "visitor_team_score", "postseason"]:
        if col not in df_raw.columns:
            df_raw[col] = None

    # Derive fields matching game.game_dim
    df_transformed = pd.DataFrame()
    df_transformed["gameId"] = df_raw["id"]
    df_transformed["gameDateTimeEst"] = df_raw["datetime"].fillna(df_raw["date"])
    df_transformed["gameDate"] = df_raw["date"]
    
    df_transformed["hometeamId"] = df_raw["home_team.id"]
    df_transformed["hometeamCity"] = df_raw["home_team.city"]
    df_transformed["hometeamName"] = df_raw["home_team.name"]

    df_transformed["awayteamId"] = df_raw["visitor_team.id"]
    df_transformed["awayteamCity"] = df_raw["visitor_team.city"]
    df_transformed["awayteamName"] = df_raw["visitor_team.name"]

    df_transformed["homeScore"] = df_raw["home_team_score"]
    df_transformed["awayScore"] = df_raw["visitor_team_score"]

    # Calculate winner if scores exist and are not equal
    def determine_winner(row):
        if pd.notnull(row["homeScore"]) and pd.notnull(row["awayScore"]):
            if row["homeScore"] > row["awayScore"]:
                return row["hometeamId"]
            elif row["awayScore"] > row["homeScore"]:
                return row["awayteamId"]
        return None

    df_transformed["winner"] = df_transformed.apply(determine_winner, axis=1)

    # Postseason check
    df_transformed["gameType"] = df_raw["postseason"].apply(lambda x: "Playoffs" if x is True else "Regular Season")

    # Staging to PostgreSQL
    with engine.begin() as conn:
        df_transformed.to_sql("_stg_game_dim", conn, schema="game", if_exists="replace", index=False)

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