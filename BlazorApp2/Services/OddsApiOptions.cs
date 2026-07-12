
namespace OddsViewerApp.Services;

public sealed class OddsApiOptions
{
    public string ApiKey { get; set; } = "";
    public string BaseUrl { get; set; } = "https://api.the-odds-api.com/v4";
    public string Regions { get; set; } = "us";
    public string Markets { get; set; } = "h2h,totals";
    public string OddsFormat { get; set; } = "american";
    public string DefaultBookmarkers { get; set; } = "DraftKings, FanDuel, BetMGM";
}