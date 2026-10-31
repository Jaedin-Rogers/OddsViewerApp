using Dapper;
using Microsoft.Extensions.Options;
using Npgsql;
using OddsViewerApp.Models;

namespace OddsViewerApp.Services;

// Reads game.game_dim / play.player_log directly. ml.player_game_base computes a window
// function over every player row, which makes per-game lookups take 15-20 s.
// Dates use ml.parse_game_ts, the same normalization the ml views use.
public class BoxScoreService
{
    // game_dim also lists upcoming games with 0-0 scores, so only completed games are returned.
    private const string GameSummarySelect = @"
        SELECT
            g.""gameId"" AS GameId,
            ml.parse_game_ts(g.""gameDateTimeEst"") AS GameDate,
            COALESCE(g.""gameType"", '') AS GameType,
            COALESCE(NULLIF(g.""hometeamName"", ''), g.""hometeamCity"", '') AS HomeTeam,
            g.""homeScore"" AS HomeScore,
            COALESCE(NULLIF(g.""awayteamName"", ''), g.""awayteamCity"", '') AS AwayTeam,
            g.""awayScore"" AS AwayScore
        FROM game.game_dim g
        WHERE (COALESCE(g.""homeScore"", 0) > 0 OR COALESCE(g.""awayScore"", 0) > 0)";

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
                sql += @" AND (
                    CONCAT(g.""hometeamCity"", ' ', g.""hometeamName"") ILIKE @Team
                    OR CONCAT(g.""awayteamCity"", ' ', g.""awayteamName"") ILIKE @Team)";
                parameters.Add("Team", "%" + team.Trim().Replace("\\", "\\\\").Replace("%", "\\%").Replace("_", "\\_") + "%");
            }

            if (gameDate.HasValue)
            {
                sql += @" AND g.""gameDate""::date <= @GameDate";
                parameters.Add("GameDate", gameDate.Value.Date);
            }

            sql += @" ORDER BY g.""gameDate""::date DESC, g.""gameId"" DESC LIMIT @Limit OFFSET @Offset;";
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
                GameSummarySelect + @" AND g.""gameId"" = @GameId;", new { GameId = gameId });
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
                    pl.""gameId"" AS GameId,
                    ml.parse_game_ts(pl.""gameDateTimeEst"") AS GameDate,
                    COALESCE(NULLIF(pl.""playerteamName"", ''), pl.""playerteamCity"", '') AS TeamName,
                    pl.home = 1 AS Home,
                    pl.playerindex AS PlayerId,
                    TRIM(CONCAT(COALESCE(pl.""firstName"", ''), ' ', COALESCE(pl.""lastName"", ''))) AS PlayerName,
                    COALESCE(pl.""startingPosition"", '') AS StartingPosition,
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
                WHERE pl.""gameId"" = @GameId
                ORDER BY pl.points DESC NULLS LAST, pl.""lastName"";
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
