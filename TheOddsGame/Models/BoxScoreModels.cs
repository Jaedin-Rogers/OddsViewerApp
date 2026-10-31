namespace OddsViewerApp.Models;

public class GameSummary
{
    public long GameId { get; set; }
    public DateTime GameDate { get; set; }
    public string GameType { get; set; } = string.Empty;
    public string HomeTeam { get; set; } = string.Empty;
    public decimal? HomeScore { get; set; }
    public string AwayTeam { get; set; } = string.Empty;
    public decimal? AwayScore { get; set; }

    public string Matchup => $"{AwayTeam} @ {HomeTeam}";
    public string Score => $"{AwayScore:0} - {HomeScore:0}";
}

public class GameBoxScore
{
    public GameSummary Game { get; set; } = new();
    public List<PlayerBoxScore> Players { get; set; } = new();

    public IEnumerable<PlayerBoxScore> HomePlayers => Players.Where(p => p.Home);
    public IEnumerable<PlayerBoxScore> AwayPlayers => Players.Where(p => !p.Home);
}
