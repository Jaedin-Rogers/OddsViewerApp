using System.Data;
using Dapper;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Npgsql;
using OddsViewerApp.Models;

namespace OddsViewerApp.Services;

public class NBADBService
{
    private readonly NbaDbOptions _options;
    private readonly ILogger<NBADBService> _logger;

    public NBADBService(IOptions<NbaDbOptions> options, ILogger<NBADBService> logger)
    {
        _options = options.Value;
        _logger = logger;
    }

    private IDbConnection CreateConnection()
    {
        var connStr = _options.BuildConnectionString();
        if (string.IsNullOrWhiteSpace(connStr))
        {
            throw new InvalidOperationException(
                "Database connection string is not configured. Please check projectsettings.json or appsettings.json for DB_HOST, DB_NAME, DB_USER, DB_PASSWORD or ConnectionStrings:NbaDb.");
        }
        return new NpgsqlConnection(connStr);
    }

    public async Task<bool> TestConnectionAsync()
    {
        try
        {
            using var conn = CreateConnection();
            if (conn is NpgsqlConnection npgsqlConn)
            {
                await npgsqlConn.OpenAsync();
            }
            else
            {
                conn.Open();
            }

            var result = await conn.ExecuteScalarAsync<int>("SELECT 1;");
            return result == 1;
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to connect to PostgreSQL NBA Database.");
            return false;
        }
    }

    // Converts an "YYYY-YY" NBA season string (e.g. "2023-24") to its Oct 1 - Jun 30 date bounds.
    private static (DateTime Start, DateTime End)? GetSeasonDateRange(string? season)
    {
        if (string.IsNullOrWhiteSpace(season))
            return null;

        var startPart = season.Split('-')[0];
        if (!int.TryParse(startPart, out var startYear))
            return null;

        return (new DateTime(startYear, 10, 1), new DateTime(startYear + 1, 6, 30));
    }

    public async Task<List<GameLogRow>> GetGameLogsAsync(string? season = null, string? team = null, int limit = 1000)
    {
        try
        {
            using var conn = CreateConnection();

            var sql = @"
                SELECT 
                    tl.""gameId"" AS GameId,
                    COALESCE(NULLIF(tl.""teamName"", ''), NULLIF(tl.""teamCity"", ''), tl.""teamAbbrev"", '') AS Team,
                    COALESCE(tl.""teamAbbrev"", '') AS TeamAbbrev,
                    COALESCE(
                        tl.""gameDate""::date,
                        g.""gameDate""::date,
                        CASE 
                            WHEN tl.""gameDateTimeEst"" ~ '^\d{4}-\d{2}-\d{2}' THEN SUBSTRING(tl.""gameDateTimeEst"" FROM 1 FOR 10)::date 
                            ELSE '1970-01-01'::date 
                        END
                    ) AS GameDate,
                    COALESCE(NULLIF(tl.""opponentTeamName"", ''), NULLIF(tl.""opponentTeamCity"", ''), tl.""oppAbbrev"", '') AS Opponent,
                    COALESCE(tl.""oppAbbrev"", '') AS OpponentAbbrev,
                    CASE 
                        WHEN tl.win = 1 THEN CONCAT('W ', COALESCE(tl.""teamScore""::text, ''), '-', COALESCE(tl.""opponentScore""::text, ''))
                        WHEN tl.win = 0 THEN CONCAT('L ', COALESCE(tl.""teamScore""::text, ''), '-', COALESCE(tl.""opponentScore""::text, ''))
                        ELSE ''
                    END AS Result,
                    CASE WHEN tl.home = 1 THEN true ELSE false END AS Home,
                    tl.""teamScore"" AS Points,
                    tl.""opponentScore"" AS OpponentPoints,
                    tl.""reboundsTotal"" AS Rebounds,
                    tl.assists AS Assists,
                    tl.steals AS Steals,
                    tl.blocks AS Blocks,
                    tl.turnovers AS Turnovers,
                    tl.""plusMinusPoints"" AS PlusMinus,
                    tl.""fieldGoalsMade"" AS FieldGoalsMade,
                    tl.""fieldGoalsAttempted"" AS FieldGoalsAttempted,
                    tl.""fieldGoalsPercentage"" AS FieldGoalsPercentage,
                    tl.""threePointersMade"" AS ThreePointersMade,
                    tl.""threePointersAttempted"" AS ThreePointersAttempted,
                    tl.""threePointersPercentage"" AS ThreePointersPercentage,
                    tl.""freeThrowsMade"" AS FreeThrowsMade,
                    tl.""freeThrowsAttempted"" AS FreeThrowsAttempted,
                    tl.""freeThrowsPercentage"" AS FreeThrowsPercentage,
                    COALESCE(tl.""gameType"", '') AS GameType
                FROM team.team_log tl
                LEFT JOIN game.game_dim g ON tl.""gameId"" = g.""gameId""
                WHERE 1 = 1
            ";

            var parameters = new DynamicParameters();

            if (!string.IsNullOrWhiteSpace(team))
            {
                sql += @" AND (
                    LOWER(tl.""teamName"") = LOWER(@Team) 
                    OR LOWER(tl.""teamAbbrev"") = LOWER(@Team)
                    OR LOWER(tl.""teamCity"") = LOWER(@Team)
                )";
                parameters.Add("Team", team);
            }

            var seasonRange = GetSeasonDateRange(season);
            if (seasonRange.HasValue)
            {
                sql += @" AND COALESCE(
                    tl.""gameDate""::date,
                    g.""gameDate""::date,
                    CASE 
                        WHEN tl.""gameDateTimeEst"" ~ '^\d{4}-\d{2}-\d{2}' THEN SUBSTRING(tl.""gameDateTimeEst"" FROM 1 FOR 10)::date 
                        ELSE '1970-01-01'::date 
                    END
                ) BETWEEN @SeasonStart AND @SeasonEnd";
                parameters.Add("SeasonStart", seasonRange.Value.Start);
                parameters.Add("SeasonEnd", seasonRange.Value.End);
            }

            sql += @" ORDER BY 
                COALESCE(
                    tl.""gameDate""::date,
                    g.""gameDate""::date,
                    CASE 
                        WHEN tl.""gameDateTimeEst"" ~ '^\d{4}-\d{2}-\d{2}' THEN SUBSTRING(tl.""gameDateTimeEst"" FROM 1 FOR 10)::date 
                        ELSE '1970-01-01'::date 
                    END
                ) DESC, 
                tl.""gameId"" DESC 
                LIMIT @Limit;";
            parameters.Add("Limit", limit);

            var rows = (await conn.QueryAsync<GameLogRow>(sql, parameters)).ToList();

            return rows;
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error fetching game logs from database.");
            throw;
        }
    }

    public async Task<List<PlayerLogRow>> GetPlayerLogsAsync(string? season = null, string? team = null, string? player = null, int limit = 2000)
    {
        try
        {
            using var conn = CreateConnection();

            var sql = @"
                SELECT 
                    pl.""gameId"" AS GameId,
                    pl.playerid AS PlayerId,
                    pl.playerindex AS PlayerIndex,
                    TRIM(CONCAT(COALESCE(pl.""firstName"", ''), ' ', COALESCE(pl.""lastName"", ''))) AS Player,
                    COALESCE(pl.""firstName"", '') AS FirstName,
                    COALESCE(pl.""lastName"", '') AS LastName,
                    COALESCE(NULLIF(pl.""playerteamName"", ''), NULLIF(pl.""playerteamCity"", ''), '') AS Team,
                    COALESCE(NULLIF(pl.""opponentteamName"", ''), NULLIF(pl.""opponentteamCity"", ''), '') AS Opponent,
                    COALESCE(
                        g.""gameDate""::date,
                        CASE 
                            WHEN pl.""gameDateTimeEst"" ~ '^\d{4}-\d{2}-\d{2}' THEN SUBSTRING(pl.""gameDateTimeEst"" FROM 1 FOR 10)::date 
                            ELSE '1970-01-01'::date 
                        END
                    ) AS GameDate,
                    CASE 
                        WHEN pl.win = 1 THEN 'W'
                        WHEN pl.win = 0 THEN 'L'
                        ELSE ''
                    END AS Result,
                    CASE WHEN pl.home = 1 THEN true ELSE false END AS Home,
                    COALESCE(pl.""numMinutes"", '') AS Minutes,
                    pl.points AS Points,
                    pl.""reboundsTotal"" AS Rebounds,
                    pl.assists AS Assists,
                    pl.steals AS Steals,
                    pl.blocks AS Blocks,
                    pl.turnovers AS Turnovers,
                    pl.plusminus AS PlusMinus,
                    pl.""reboundsOffensive"" AS ReboundsOffensive,
                    pl.""reboundsDefensive"" AS ReboundsDefensive,
                    pl.""fieldGoalsMade"" AS FieldGoalsMade,
                    pl.""fieldGoalsAttempted"" AS FieldGoalsAttempted,
                    pl.""fieldGoalsPercentage"" AS FieldGoalsPercentage,
                    pl.""threePointersMade"" AS ThreePointersMade,
                    pl.""threePointersAttempted"" AS ThreePointersAttempted,
                    pl.""threePointersPercentage"" AS ThreePointersPercentage,
                    pl.""freeThrowsMade"" AS FreeThrowsMade,
                    pl.""freeThrowsAttempted"" AS FreeThrowsAttempted,
                    pl.""freeThrowsPercentage"" AS FreeThrowsPercentage,
                    COALESCE(pl.""gameType"", '') AS GameType
                FROM play.player_log pl
                LEFT JOIN game.game_dim g ON pl.""gameId"" = g.""gameId""
                WHERE 1 = 1
            ";

            var parameters = new DynamicParameters();

            if (!string.IsNullOrWhiteSpace(team))
            {
                sql += @" AND (
                    LOWER(pl.""playerteamName"") = LOWER(@Team) 
                    OR LOWER(pl.""playerteamCity"") = LOWER(@Team)
                )";
                parameters.Add("Team", team);
            }

            if (!string.IsNullOrWhiteSpace(player))
            {
                sql += @" AND (
                    LOWER(TRIM(CONCAT(COALESCE(pl.""firstName"", ''), ' ', COALESCE(pl.""lastName"", '')))) = LOWER(TRIM(@Player))
                    OR LOWER(pl.""lastName"") = LOWER(TRIM(@Player))
                )";
                parameters.Add("Player", player);
            }

            var seasonRange = GetSeasonDateRange(season);
            if (seasonRange.HasValue)
            {
                sql += @" AND COALESCE(
                    g.""gameDate""::date,
                    CASE 
                        WHEN pl.""gameDateTimeEst"" ~ '^\d{4}-\d{2}-\d{2}' THEN SUBSTRING(pl.""gameDateTimeEst"" FROM 1 FOR 10)::date 
                        ELSE '1970-01-01'::date 
                    END
                ) BETWEEN @SeasonStart AND @SeasonEnd";
                parameters.Add("SeasonStart", seasonRange.Value.Start);
                parameters.Add("SeasonEnd", seasonRange.Value.End);
            }

            sql += @" ORDER BY 
                COALESCE(
                    g.""gameDate""::date,
                    CASE 
                        WHEN pl.""gameDateTimeEst"" ~ '^\d{4}-\d{2}-\d{2}' THEN SUBSTRING(pl.""gameDateTimeEst"" FROM 1 FOR 10)::date 
                        ELSE '1970-01-01'::date 
                    END
                ) DESC, 
                pl.points DESC NULLS LAST 
                LIMIT @Limit;";
            parameters.Add("Limit", limit);

            var rows = (await conn.QueryAsync<PlayerLogRow>(sql, parameters)).ToList();

            return rows;
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error fetching player logs from database.");
            throw;
        }
    }

    public async Task<List<string>> GetTeamsAsync()
    {
        try
        {
            using var conn = CreateConnection();

            var sql = @"
                SELECT DISTINCT ""teamName"" 
                FROM team.team_dim 
                WHERE ""teamName"" IS NOT NULL AND TRIM(""teamName"") <> ''
                ORDER BY ""teamName"";
            ";

            var teams = (await conn.QueryAsync<string>(sql)).ToList();
            if (teams.Count > 0)
            {
                return teams;
            }

            // Fallback to team_log
            var fallbackSql = @"
                SELECT DISTINCT COALESCE(NULLIF(""teamName"", ''), NULLIF(""teamCity"", ''), ""teamAbbrev"") AS team
                FROM team.team_log
                WHERE ""teamName"" IS NOT NULL OR ""teamCity"" IS NOT NULL OR ""teamAbbrev"" IS NOT NULL
                ORDER BY team;
            ";
            return (await conn.QueryAsync<string>(fallbackSql)).Where(t => !string.IsNullOrWhiteSpace(t)).ToList();
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to load teams from database.");
            return new List<string>();
        }
    }

    public async Task<List<string>> GetPlayersAsync(string? team = null)
    {
        try
        {
            using var conn = CreateConnection();

            var sql = @"
                SELECT DISTINCT TRIM(CONCAT(COALESCE(""firstName"", ''), ' ', COALESCE(""lastName"", ''))) AS PlayerName
                FROM play.player_log
                WHERE ""firstName"" IS NOT NULL OR ""lastName"" IS NOT NULL
            ";

            var parameters = new DynamicParameters();
            if (!string.IsNullOrWhiteSpace(team))
            {
                sql += @" AND (
                    LOWER(""playerteamName"") = LOWER(@Team) 
                    OR LOWER(""playerteamCity"") = LOWER(@Team)
                )";
                parameters.Add("Team", team);
            }

            sql += " ORDER BY PlayerName;";

            var players = (await conn.QueryAsync<string>(sql, parameters)).Where(p => !string.IsNullOrWhiteSpace(p)).ToList();
            if (players.Count > 0)
            {
                return players;
            }

            // Fallback to player_dim
            if (string.IsNullOrWhiteSpace(team))
            {
                var dimSql = @"SELECT DISTINCT TRIM(CONCAT(COALESCE(""firstName"", ''), ' ', COALESCE(""lastName"", ''))) AS PlayerName FROM play.player_dim WHERE ""firstName"" IS NOT NULL OR ""lastName"" IS NOT NULL ORDER BY PlayerName;";
                return (await conn.QueryAsync<string>(dimSql)).Where(p => !string.IsNullOrWhiteSpace(p)).ToList();
            }

            return players;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to load players from database.");
            return new List<string>();
        }
    }
}
