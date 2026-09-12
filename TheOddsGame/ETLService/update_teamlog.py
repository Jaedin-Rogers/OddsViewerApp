import os
import time
import argparse
import logging
from datetime import datetime
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from nba_api.stats.endpoints import leaguegamelog
from config import get_setting

load_dotenv()

# ==========================================
# Logging Configuration
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("TeamLogSync")


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
# Helper: Format Season String
# ==========================================
def format_nba_season(season_input: str = None, date_str: str = None) -> str:
    """
    Normalizes season input to NBA format 'YYYY-YY'.
    Accepts '2024', '2024-25', '2024-2025', or infers from a date (YYYY-MM-DD).
    """
    if season_input:
        s = str(season_input).strip()
        if len(s) == 4 and s.isdigit():
            start_yr = int(s)
            end_yr_short = str(start_yr + 1)[-2:]
            return f"{start_yr}-{end_yr_short}"
        if len(s) == 9 and "-" in s:
            parts = s.split("-")
            return f"{parts[0]}-{parts[1][-2:]}"
        return s

    if date_str:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        start_year = dt.year if dt.month >= 10 else dt.year - 1
        return f"{start_year}-{str(start_year + 1)[-2:]}"

    current_dt = datetime.now()
    start_year = current_dt.year if current_dt.month >= 10 else current_dt.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


# ==========================================
# Fetch Team Box Scores from NBA API
# ==========================================
def fetch_nba_api_team_logs(
    season: str = None,
    season_type: str = "Regular Season",
    start_date: str = None,
    end_date: str = None
) -> pd.DataFrame:
    """
    Fetches team box score logs using nba_api LeagueGameLog.
    """
    formatted_season = format_nba_season(season, start_date or end_date)
    logger.info(f"Fetching team logs via nba_api (Season: {formatted_season}, Type: {season_type}, Range: {start_date} to {end_date})...")

    kwargs = {
        "season": formatted_season,
        "season_type_all_star": season_type,
        "player_or_team_abbreviation": "T"  # 'T' for team logs
    }
    if start_date:
        kwargs["date_from_nullable"] = datetime.strptime(start_date, "%Y-%m-%d").strftime("%m/%d/%Y")
    if end_date:
        kwargs["date_to_nullable"] = datetime.strptime(end_date, "%Y-%m-%d").strftime("%m/%d/%Y")

    try:
        log_endpoint = leaguegamelog.LeagueGameLog(**kwargs)
        df = log_endpoint.get_data_frames()[0]
        logger.info(f"Successfully retrieved {len(df)} team game log records from NBA.com.")
        return df
    except Exception as e:
        logger.error(f"Error querying NBA API for team logs: {e}")
        return pd.DataFrame()


# ==========================================
# Transform & Upsert Team Logs
# ==========================================
def sync_team_logs(
    engine: Engine,
    season: str = None,
    season_type: str = "Regular Season",
    start_date: str = None,
    end_date: str = None
):
    df_raw = fetch_nba_api_team_logs(
        season=season,
        season_type=season_type,
        start_date=start_date,
        end_date=end_date
    )

    if df_raw.empty:
        logger.warning("No team logs retrieved to sync.")
        return

    # Extract opponent abbreviation (e.g. 'LAL vs. GSW' -> 'GSW', 'BOS @ MIA' -> 'MIA')
    def extract_opp_abbrev(matchup_str):
        parts = str(matchup_str).replace("@", "vs.").split("vs.")
        if len(parts) == 2:
            return parts[1].strip()
        return None

    df_transformed = pd.DataFrame()
    df_transformed["nba_team_id"] = df_raw["TEAM_ID"]
    df_transformed["teamAbbrev"] = df_raw["TEAM_ABBREVIATION"]
    df_transformed["oppAbbrev"] = df_raw["MATCHUP"].apply(extract_opp_abbrev)
    df_transformed["gameDate"] = pd.to_datetime(df_raw["GAME_DATE"]).dt.strftime("%Y-%m-%d")
    df_transformed["home"] = df_raw["MATCHUP"].apply(lambda x: 1 if "vs." in str(x) else 0)
    df_transformed["win"] = df_raw["WL"].apply(lambda x: 1.0 if x == "W" else (0.0 if x == "L" else None))
    df_transformed["teamScore"] = pd.to_numeric(df_raw["PTS"], errors="coerce")
    
    # Derive opponent score: Team Score - Plus/Minus
    df_transformed["opponentScore"] = df_transformed["teamScore"] - pd.to_numeric(df_raw["PLUS_MINUS"], errors="coerce")
    df_transformed["plusMinusPoints"] = pd.to_numeric(df_raw["PLUS_MINUS"], errors="coerce")
    df_transformed["numMinutes"] = pd.to_numeric(df_raw["MIN"], errors="coerce")

    # Shooting stats
    df_transformed["fieldGoalsMade"] = pd.to_numeric(df_raw["FGM"], errors="coerce")
    df_transformed["fieldGoalsAttempted"] = pd.to_numeric(df_raw["FGA"], errors="coerce")
    df_transformed["fieldGoalsPercentage"] = pd.to_numeric(df_raw["FG_PCT"], errors="coerce")
    df_transformed["threePointersMade"] = pd.to_numeric(df_raw["FG3M"], errors="coerce")
    df_transformed["threePointersAttempted"] = pd.to_numeric(df_raw["FG3A"], errors="coerce")
    df_transformed["threePointersPercentage"] = pd.to_numeric(df_raw["FG3_PCT"], errors="coerce")
    df_transformed["freeThrowsMade"] = pd.to_numeric(df_raw["FTM"], errors="coerce")
    df_transformed["freeThrowsAttempted"] = pd.to_numeric(df_raw["FTA"], errors="coerce")
    df_transformed["freeThrowsPercentage"] = pd.to_numeric(df_raw["FT_PCT"], errors="coerce")

    # Box score counting stats
    df_transformed["reboundsOffensive"] = pd.to_numeric(df_raw["OREB"], errors="coerce")
    df_transformed["reboundsDefensive"] = pd.to_numeric(df_raw["DREB"], errors="coerce")
    df_transformed["reboundsTotal"] = pd.to_numeric(df_raw["REB"], errors="coerce")
    df_transformed["assists"] = pd.to_numeric(df_raw["AST"], errors="coerce")
    df_transformed["steals"] = pd.to_numeric(df_raw["STL"], errors="coerce")
    df_transformed["blocks"] = pd.to_numeric(df_raw["BLK"], errors="coerce")
    df_transformed["turnovers"] = pd.to_numeric(df_raw["TOV"], errors="coerce")
    df_transformed["foulsPersonal"] = pd.to_numeric(df_raw["PF"], errors="coerce")

    with engine.begin() as conn:
        df_transformed.to_sql("_stg_team_log", conn, schema="team", if_exists="replace", index=False)

        # 1. Enrich staging table with database metadata from team_dim and game_dim
        conn.execute(text("""
            ALTER TABLE team._stg_team_log 
            ADD COLUMN IF NOT EXISTS "db_gameId" bigint,
            ADD COLUMN IF NOT EXISTS "teamId" bigint,
            ADD COLUMN IF NOT EXISTS "teamCity" text,
            ADD COLUMN IF NOT EXISTS "teamName" text,
            ADD COLUMN IF NOT EXISTS opponentteamid bigint,
            ADD COLUMN IF NOT EXISTS "opponentTeamCity" text,
            ADD COLUMN IF NOT EXISTS "opponentTeamName" text,
            ADD COLUMN IF NOT EXISTS "gameDateTimeEst" text,
            ADD COLUMN IF NOT EXISTS "gameType" text;
        """))

        # Match team info
        conn.execute(text("""
            UPDATE team._stg_team_log stg
            SET "teamId" = t."teamId",
                "teamCity" = t."teamCity",
                "teamName" = t."teamName"
            FROM team.team_dim t
            WHERE LOWER(TRIM(t."teamAbbrev")) = LOWER(TRIM(stg."teamAbbrev"));

            UPDATE team._stg_team_log stg
            SET opponentteamid = t."teamId",
                "opponentTeamCity" = t."teamCity",
                "opponentTeamName" = t."teamName"
            FROM team.team_dim t
            WHERE LOWER(TRIM(t."teamAbbrev")) = LOWER(TRIM(stg."oppAbbrev"));
        """))

        # Match gameId from game_dim using date and home/away team combinations
        conn.execute(text("""
            UPDATE team._stg_team_log stg
            SET "db_gameId" = g."gameId",
                "gameDateTimeEst" = g."gameDateTimeEst",
                "gameType" = g."gameType"
            FROM game.game_dim g
            WHERE g."gameDate" = stg."gameDate"
              AND (
                  (g."hometeamId" = stg."teamId" AND g."awayteamId" = stg.opponentteamid)
                  OR
                  (g."awayteamId" = stg."teamId" AND g."hometeamId" = stg.opponentteamid)
              );
        """))

        # 2. Log & Remove records that couldn't match team_dim or game_dim
        res_skipped_teams = conn.execute(text("""
            DELETE FROM team._stg_team_log
            WHERE "teamId" IS NULL OR opponentteamid IS NULL
            RETURNING "teamAbbrev", "oppAbbrev", "gameDate";
        """))
        log_sample_results("Skipped Team Logs (Unmatched team_dim)", res_skipped_teams)

        res_skipped_games = conn.execute(text("""
            DELETE FROM team._stg_team_log
            WHERE "db_gameId" IS NULL
            RETURNING "teamAbbrev", "oppAbbrev", "gameDate";
        """))
        log_sample_results("Skipped Team Logs (No Matching gameId in game_dim)", res_skipped_games)

        # 3. Clear existing logs for matching ("teamId", "gameId")
        conn.execute(text("""
            DELETE FROM team.team_log tl
            USING team._stg_team_log stg
            WHERE tl."teamId" = stg."teamId"
              AND tl."gameId" = stg."db_gameId";
        """))

        # 4. Insert transformed team logs
        insert_res = conn.execute(text("""
            INSERT INTO team.team_log (
                "gameId", "gameDateTimeEst", "teamCity", "teamName", "teamId",
                "opponentTeamCity", "opponentTeamName", opponentteamid,
                home, win, "teamScore", "opponentScore",
                assists, blocks, steals,
                "fieldGoalsAttempted", "fieldGoalsMade", "fieldGoalsPercentage",
                "threePointersAttempted", "threePointersMade", "threePointersPercentage",
                "freeThrowsAttempted", "freeThrowsMade", "freeThrowsPercentage",
                "reboundsDefensive", "reboundsOffensive", "reboundsTotal",
                "foulsPersonal", turnovers, "plusMinusPoints", "numMinutes",
                "gameType", "gameDate"
            )
            SELECT DISTINCT ON (stg."teamId", stg."db_gameId")
                stg."db_gameId",
                stg."gameDateTimeEst",
                stg."teamCity",
                stg."teamName",
                stg."teamId",
                stg."opponentTeamCity",
                stg."opponentTeamName",
                stg.opponentteamid,
                stg.home,
                stg.win,
                stg."teamScore",
                stg."opponentScore",
                stg.assists,
                stg.blocks,
                stg.steals,
                stg."fieldGoalsAttempted",
                stg."fieldGoalsMade",
                stg."fieldGoalsPercentage",
                stg."threePointersAttempted",
                stg."threePointersMade",
                stg."threePointersPercentage",
                stg."freeThrowsAttempted",
                stg."freeThrowsMade",
                stg."freeThrowsPercentage",
                stg."reboundsDefensive",
                stg."reboundsOffensive",
                stg."reboundsTotal",
                stg."foulsPersonal",
                stg.turnovers,
                stg."plusMinusPoints",
                stg."numMinutes",
                stg."gameType",
                stg."gameDate"
            FROM team._stg_team_log stg
            RETURNING "gameId", "gameDate", "teamName", "opponentTeamName", "teamScore", "opponentScore", win;
        """))

        log_sample_results("team.team_log INSERTS / UPDATES", insert_res)
        conn.execute(text("DROP TABLE IF EXISTS team._stg_team_log;"))

    logger.info("Team log sync completed successfully.")


# ==========================================
# CLI Entrypoint
# ==========================================
def main():
    parser = argparse.ArgumentParser(description="Sync team game logs using NBA stats API into team.team_log.")
    parser.add_argument("--season", type=str, default=None, help="Season format (e.g. 2024, 2024-25, 2024-2025). Auto-inferred if omitted.")
    parser.add_argument("--season_type", type=str, default="Regular Season", choices=["Regular Season", "Playoffs", "Pre Season"])
    parser.add_argument("--start_date", type=str, help="Filter from date (YYYY-MM-DD), e.g. 2026-01-01")
    parser.add_argument("--end_date", type=str, help="Filter to date (YYYY-MM-DD), e.g. 2026-03-01")

    args = parser.parse_args()

    db_user = get_setting("DB_USER") or get_setting("pg_user")
    db_pass = get_setting("DB_PASSWORD") or get_setting("pg_password")
    db_host = get_setting("DB_HOST") or get_setting("pg_host")
    db_port = get_setting("DB_PORT") or get_setting("pg_port", "5432")
    db_name = get_setting("DB_NAME") or get_setting("pg_database")

    if not all([db_user, db_pass, db_host, db_name]):
        raise ValueError("Missing database connection parameters. Ensure DB_HOST, DB_NAME, DB_USER, and DB_PASSWORD are set in config or env.")

    engine = create_engine(f"postgresql+psycopg://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}")

    sync_team_logs(
        engine=engine,
        season=args.season,
        season_type=args.season_type,
        start_date=args.start_date,
        end_date=args.end_date
    )


if __name__ == "__main__":
    main()