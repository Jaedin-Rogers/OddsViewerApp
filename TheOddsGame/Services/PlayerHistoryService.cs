using Dapper;
using Microsoft.Extensions.Options;
using Npgsql;
using OddsViewerApp.Models;

namespace OddsViewerApp.Services;

public class PlayerHistoryService
{
    private const string TeamExpr = "COALESCE(NULLIF(playerteamname, ''), playerteamcity, '')";
    private const string PlayerNameExpr = "TRIM(CONCAT(COALESCE(firstname, ''), ' ', COALESCE(lastname, '')))";

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

    // Teams that have played in the most recent season in the data.
    public async Task<List<string>> GetCurrentTeamsAsync()
    {
        try
        {
            await using var conn = CreateConnection();
            var sql = $@"
                SELECT DISTINCT {TeamExpr} AS TeamName
                FROM ml.player_game_base
                WHERE season_start_year = (SELECT MAX(season_start_year) FROM ml.player_game_base)
                  AND {TeamExpr} <> ''
                ORDER BY 1;";
            return (await conn.QueryAsync<string>(sql)).ToList();
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error fetching teams.");
            throw;
        }
    }

    // Name search matches any player ever; team filter lists that team's current roster
    // (latest team = team and played in the most recent season).
    public async Task<List<PlayerListItem>> SearchPlayersAsync(string? name, string? team, int limit = 100)
    {
        try
        {
            await using var conn = CreateConnection();

            var sql = $@"
                WITH latest AS (
                    SELECT DISTINCT ON (playerid)
                        playerid, {PlayerNameExpr} AS player_name, {TeamExpr} AS team_name,
                        gamedatetimeest, season_start_year
                    FROM ml.player_game_base
                    WHERE playerid IS NOT NULL
                    ORDER BY playerid, gamedatetimeest DESC NULLS LAST
                )
                SELECT playerid AS PlayerId, player_name AS PlayerName, team_name AS TeamName,
                       gamedatetimeest AS LastGameDate
                FROM latest
                WHERE 1 = 1";

            var parameters = new DynamicParameters();

            if (!string.IsNullOrWhiteSpace(name))
            {
                sql += " AND player_name ILIKE @Name";
                parameters.Add("Name", "%" + name.Trim().Replace("\\", "\\\\").Replace("%", "\\%").Replace("_", "\\_") + "%");
            }

            if (!string.IsNullOrWhiteSpace(team))
            {
                sql += @" AND team_name = @Team
                          AND season_start_year = (SELECT MAX(season_start_year) FROM ml.player_game_base)";
                parameters.Add("Team", team);
            }

            sql += " ORDER BY gamedatetimeest DESC NULLS LAST, player_name LIMIT @Limit;";
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
            var sql = $@"
                SELECT playerid AS PlayerId, {PlayerNameExpr} AS PlayerName, {TeamExpr} AS TeamName,
                       gamedatetimeest AS LastGameDate
                FROM ml.player_game_base
                WHERE playerid = @PlayerId
                ORDER BY gamedatetimeest DESC NULLS LAST
                LIMIT 1;";
            return await conn.QueryFirstOrDefaultAsync<PlayerListItem>(sql, new { PlayerId = playerId });
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error fetching player {PlayerId}.", playerId);
            throw;
        }
    }

    public async Task<int> GetPlayerGameCountAsync(long playerId)
    {
        await using var conn = CreateConnection();
        return await conn.ExecuteScalarAsync<int>(
            "SELECT COUNT(*) FROM ml.player_game_base WHERE playerid = @PlayerId;", new { PlayerId = playerId });
    }

    // Most recent games first.
    public async Task<List<PlayerGameLog>> GetPlayerGamesAsync(long playerId, int limit = 10, int offset = 0)
    {
        try
        {
            await using var conn = CreateConnection();

            var sql = $@"
                SELECT
                    gameid AS GameId,
                    gamedatetimeest AS GameDate,
                    {TeamExpr} AS TeamName,
                    COALESCE(NULLIF(opponentteamname, ''), opponentteamcity, '') AS OpponentName,
                    home = 1 AS Home,
                    CASE WHEN win = 1 THEN true WHEN win = 0 THEN false END AS Win,
                    player_team_score AS TeamScore,
                    opponent_score AS OpponentScore,
                    numminutes AS Minutes,
                    points AS Points,
                    reboundstotal AS Rebounds,
                    assists AS Assists,
                    steals AS Steals,
                    blocks AS Blocks,
                    turnovers AS Turnovers,
                    plusminus AS PlusMinus,
                    fieldgoalsmade AS FieldGoalsMade,
                    fieldgoalsattempted AS FieldGoalsAttempted,
                    threepointersmade AS ThreePointersMade,
                    threepointersattempted AS ThreePointersAttempted,
                    freethrowsmade AS FreeThrowsMade,
                    freethrowsattempted AS FreeThrowsAttempted
                FROM ml.player_game_base
                WHERE playerid = @PlayerId
                ORDER BY gamedatetimeest DESC NULLS LAST, gameid DESC
                LIMIT @Limit OFFSET @Offset;";

            return (await conn.QueryAsync<PlayerGameLog>(sql, new { PlayerId = playerId, Limit = limit, Offset = offset })).ToList();
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error fetching games for player {PlayerId}.", playerId);
            throw;
        }
    }
}
