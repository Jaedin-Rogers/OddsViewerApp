using Dapper;
using Microsoft.Extensions.Options;
using Npgsql;
using OddsViewerApp.Models;

namespace OddsViewerApp.Services;

// Reads play.player_dim / play.player_log / game.game_dim directly instead of the slower
// ml.player_game_base view. Dates use ml.parse_game_ts, the same normalization the ml views use.
public class PlayerHistoryService
{
    private readonly NbaDbOptions _options;
    private readonly ILogger<PlayerHistoryService> _logger;

    public PlayerHistoryService(IOptions<NbaDbOptions> options, ILogger<PlayerHistoryService> logger)
    {
        _options = options.Value;
        _logger = logger;
    }

    private NpgsqlConnection CreateConnection()
    {
        var connStr = _options.BuildConnectionString();
        if (string.IsNullOrWhiteSpace(connStr))
        {
            throw new InvalidOperationException(
                "Database connection string is not configured. Please check projectsettings.json or appsettings.json.");
        }
        return new NpgsqlConnection(connStr);
    }

    // Teams that currently have players assigned in player_dim.
    public async Task<List<string>> GetCurrentTeamsAsync()
    {
        try
        {
            await using var conn = CreateConnection();
            const string sql = @"
                SELECT DISTINCT COALESCE(NULLIF(t.""teamName"", ''), t.""teamCity"") AS TeamName
                FROM team.team_dim t
                JOIN play.player_dim p ON p.playerteamid = t.""teamId""
                ORDER BY 1;";
            return (await conn.QueryAsync<string>(sql)).ToList();
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error fetching teams.");
            throw;
        }
    }

    // Name search covers any player in player_dim; the team filter lists that team's roster.
    public async Task<List<PlayerListItem>> SearchPlayersAsync(string? name, string? team, int limit = 100)
    {
        try
        {
            await using var conn = CreateConnection();

            var sql = @"
                SELECT
                    p.playerindex AS PlayerId,
                    TRIM(CONCAT(COALESCE(p.""firstName"", ''), ' ', COALESCE(p.""lastName"", ''))) AS PlayerName,
                    COALESCE(NULLIF(t.""teamName"", ''), t.""teamCity"", '') AS TeamName
                FROM play.player_dim p
                LEFT JOIN team.team_dim t ON t.""teamId"" = p.playerteamid
                WHERE 1 = 1";

            var parameters = new DynamicParameters();

            if (!string.IsNullOrWhiteSpace(name))
            {
                sql += @" AND TRIM(CONCAT(COALESCE(p.""firstName"", ''), ' ', COALESCE(p.""lastName"", ''))) ILIKE @Name";
                parameters.Add("Name", "%" + name.Trim().Replace("\\", "\\\\").Replace("%", "\\%").Replace("_", "\\_") + "%");
            }

            if (!string.IsNullOrWhiteSpace(team))
            {
                sql += @" AND COALESCE(NULLIF(t.""teamName"", ''), t.""teamCity"") = @Team";
                parameters.Add("Team", team);
            }

            sql += @" ORDER BY p.""nbaFlag"" DESC NULLS LAST, p.""toYear"" DESC NULLS FIRST, p.""lastName"", p.""firstName"" LIMIT @Limit;";
            parameters.Add("Limit", limit);

            return (await conn.QueryAsync<PlayerListItem>(sql, parameters)).ToList();
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error searching players.");
            throw;
        }
    }

    public async Task<PlayerListItem?> GetPlayerAsync(long playerId)
    {
        try
        {
            await using var conn = CreateConnection();
            const string sql = @"
                SELECT
                    p.playerindex AS PlayerId,
                    TRIM(CONCAT(COALESCE(p.""firstName"", ''), ' ', COALESCE(p.""lastName"", ''))) AS PlayerName,
                    COALESCE(NULLIF(t.""teamName"", ''), t.""teamCity"", '') AS TeamName
                FROM play.player_dim p
                LEFT JOIN team.team_dim t ON t.""teamId"" = p.playerteamid
                WHERE p.playerindex = @PlayerId;";
            return await conn.QueryFirstOrDefaultAsync<PlayerListItem>(sql, new { PlayerId = playerId });
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error fetching player {PlayerId}.", playerId);
            throw;
        }
    }

    public async Task<int> GetPlayerGameCountAsync(long playerId, DateTime? gameDate = null)
    {
        await using var conn = CreateConnection();
        var sql = "SELECT COUNT(*) FROM play.player_log pl WHERE pl.playerindex = @PlayerId";
        if (gameDate.HasValue)
            sql += @" AND ml.parse_game_ts(pl.""gameDateTimeEst"")::date <= @GameDate";
        return await conn.ExecuteScalarAsync<int>(sql + ";", new { PlayerId = playerId, GameDate = gameDate?.Date });
    }

    // Most recent games first. gameDate filters to that calendar day.
    public async Task<List<PlayerGameLog>> GetPlayerGamesAsync(long playerId, int limit = 10, int offset = 0, DateTime? gameDate = null)
    {
        try
        {
            await using var conn = CreateConnection();

            const string sql = @"
                SELECT
                    pl.""gameId"" AS GameId,
                    ml.parse_game_ts(pl.""gameDateTimeEst"") AS GameDate,
                    COALESCE(NULLIF(pl.""playerteamName"", ''), pl.""playerteamCity"", '') AS TeamName,
                    COALESCE(NULLIF(pl.""opponentteamName"", ''), pl.""opponentteamCity"", '') AS OpponentName,
                    pl.home = 1 AS Home,
                    CASE WHEN pl.win = 1 THEN true WHEN pl.win = 0 THEN false END AS Win,
                    CASE WHEN pl.home = 1 THEN g.""homeScore"" ELSE g.""awayScore"" END AS TeamScore,
                    CASE WHEN pl.home = 1 THEN g.""awayScore"" ELSE g.""homeScore"" END AS OpponentScore,
                    pl.""numMinutes""::numeric AS Minutes,
                    pl.points AS Points,
                    pl.""reboundsTotal"" AS Rebounds,
                    pl.assists AS Assists,
                    pl.steals AS Steals,
                    pl.blocks AS Blocks,
                    pl.turnovers AS Turnovers,
                    pl.plusminus AS PlusMinus,
                    pl.""fieldGoalsMade"" AS FieldGoalsMade,
                    pl.""fieldGoalsAttempted"" AS FieldGoalsAttempted,
                    pl.""threePointersMade"" AS ThreePointersMade,
                    pl.""threePointersAttempted"" AS ThreePointersAttempted,
                    pl.""freeThrowsMade"" AS FreeThrowsMade,
                    pl.""freeThrowsAttempted"" AS FreeThrowsAttempted
                FROM play.player_log pl
                LEFT JOIN game.game_dim g ON g.""gameId"" = pl.""gameId""
                WHERE pl.playerindex = @PlayerId
                  AND (@GameDate::date IS NULL OR ml.parse_game_ts(pl.""gameDateTimeEst"")::date <= @GameDate::date)
                ORDER BY ml.parse_game_ts(pl.""gameDateTimeEst"") DESC NULLS LAST, pl.""gameId"" DESC
                LIMIT @Limit OFFSET @Offset;";

            return (await conn.QueryAsync<PlayerGameLog>(sql, new { PlayerId = playerId, Limit = limit, Offset = offset, GameDate = gameDate?.Date })).ToList();
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error fetching games for player {PlayerId}.", playerId);
            throw;
        }
    }
}
