using Dapper;
using Microsoft.Extensions.Options;
using Npgsql;
using OddsViewerApp.Models;

namespace OddsViewerApp.Services;

public class BoxScoreService
{
    // One row per game, built from the player-level view; home is stored as 1/0.
    private const string GameSummarySelect = @"
        SELECT
            gameid AS GameId,
            MIN(gamedatetimeest) AS GameDate,
            COALESCE(MAX(gametype), '') AS GameType,
            COALESCE(MAX(CASE WHEN home = 1 THEN COALESCE(NULLIF(playerteamname, ''), playerteamcity) END), '') AS HomeTeam,
            MAX(homescore) AS HomeScore,
            COALESCE(MAX(CASE WHEN home = 0 THEN COALESCE(NULLIF(playerteamname, ''), playerteamcity) END), '') AS AwayTeam,
            MAX(awayscore) AS AwayScore
        FROM ml.player_game_base";

    private const string GameSummaryGroup = " GROUP BY gameid";

    private readonly NbaDbOptions _options;
    private readonly ILogger<BoxScoreService> _logger;

    public BoxScoreService(IOptions<NbaDbOptions> options, ILogger<BoxScoreService> logger)
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

    // Most recent games first. Team matches name/city by substring; date matches the calendar day.
    public async Task<List<GameSummary>> GetRecentGamesAsync(string? team = null, DateTime? gameDate = null, int limit = 50, int offset = 0)
    {
        try
        {
            await using var conn = CreateConnection();

            var sql = GameSummarySelect;
            var parameters = new DynamicParameters();

            if (!string.IsNullOrWhiteSpace(team))
            {
                sql += @" WHERE gameid IN (
                    SELECT gameid FROM ml.player_game_base
                    WHERE CONCAT(playerteamcity, ' ', playerteamname) ILIKE @Team
                       OR playerteamname ILIKE @Team)";
                parameters.Add("Team", "%" + team.Trim().Replace("\\", "\\\\").Replace("%", "\\%").Replace("_", "\\_") + "%");
            }

            sql += GameSummaryGroup;

            if (gameDate.HasValue)
            {
                sql += " HAVING MIN(gamedatetimeest)::date = @GameDate";
                parameters.Add("GameDate", gameDate.Value.Date);
            }

            sql += @" ORDER BY MIN(gamedatetimeest) DESC NULLS LAST, gameid DESC LIMIT @Limit OFFSET @Offset;";
            parameters.Add("Limit", limit);
            parameters.Add("Offset", offset);

            return (await conn.QueryAsync<GameSummary>(sql, parameters)).ToList();
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error fetching recent games.");
            throw;
        }
    }

    public async Task<GameSummary?> GetGameAsync(long gameId)
    {
        try
        {
            await using var conn = CreateConnection();
            return await conn.QueryFirstOrDefaultAsync<GameSummary>(
                GameSummarySelect + " WHERE gameid = @GameId" + GameSummaryGroup + ";", new { GameId = gameId });
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error fetching game {GameId}.", gameId);
            throw;
        }
    }

    public async Task<List<PlayerBoxScore>> GetPlayerBoxScoresAsync(long gameId)
    {
        try
        {
            await using var conn = CreateConnection();

            var sql = @"
                SELECT
                    gameid AS GameId,
                    gamedatetimeest AS GameDate,
                    COALESCE(NULLIF(playerteamname, ''), playerteamcity, '') AS TeamName,
                    home = 1 AS Home,
                    playerid AS PlayerId,
                    TRIM(CONCAT(COALESCE(firstname, ''), ' ', COALESCE(lastname, ''))) AS PlayerName,
                    COALESCE(startingposition, '') AS StartingPosition,
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
                WHERE gameid = @GameId
                ORDER BY points DESC NULLS LAST, lastname;
            ";

            return (await conn.QueryAsync<PlayerBoxScore>(sql, new { GameId = gameId })).ToList();
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Error fetching player box scores for game {GameId}.", gameId);
            throw;
        }
    }

    // Game summary plus all player lines; the dialog toggles teams via HomePlayers/AwayPlayers.
    public async Task<GameBoxScore?> GetBoxScoreAsync(long gameId)
    {
        var game = await GetGameAsync(gameId);
        if (game is null) return null;

        var players = await GetPlayerBoxScoresAsync(gameId);
        return new GameBoxScore { Game = game, Players = players };
    }
}
