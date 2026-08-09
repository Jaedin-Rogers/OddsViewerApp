namespace OddsViewerApp.Models;

public sealed class SportOption
{
    public string Key { get; set; } = "";
    public string DisplayName { get; set; } = "";
}

public sealed class EventOption
{
    public string Id { get; set;} = "";
    public string Sport { get; set; } = "";
    public string League { get; set; } = "";
    public string HomeTeam { get; set; } = "";
    public string AwayTeam { get; set; } = "";
    public DateTimeOffset? StartTime { get; set; } 

    public string DisplayName =>
        string.IsNullOrWhiteSpace(HomeTeam) &&
        string.IsNullOrWhiteSpace(AwayTeam) 
        ? Id
        : $"{HomeTeam} vs {AwayTeam}";
}

public sealed class OddsRow
{
    public DateTimeOffset? MatchDate { get; set; }
    public string Sport { get; set; } = "";
    public string League { get; set; } = "";
    public string EventId { get; set; } = "";
    public string EventName { get; set; } = "";
    public string HomeTeam { get; set; } = "";
    public string AwayTeam { get; set; } = "";

    public string Bookmaker { get; set; } = "";
    public string Market { get; set; } = "";

    public string Team { get; set; } = "";
    public string Player { get; set; } = "";
    public string PropCategory { get; set; } = "";
    public string Selection { get; set; } = "";

    public decimal? Handicap { get; set; }
    public decimal? Price { get; set; }
    public decimal? OverPrice { get; set; }
    public decimal? UnderPrice { get; set; }

    public DateTimeOffset? LastUpdated { get; set; }
}