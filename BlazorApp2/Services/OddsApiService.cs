using System.Globalization;
using System.Net;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using OddsViewerApp.Models;
 

namespace OddsViewerApp.Services;
 

public sealed class OddsApiService
{
    private readonly HttpClient _http;
    private readonly OddsApiOptions _options;
    private readonly ILogger<OddsApiService> _logger;


    public OddsApiService(HttpClient http, IOptions<OddsApiOptions> options, ILogger<OddsApiService> logger)
    {
        _http = http;
        _options = options.Value;
        _logger = logger;
    }

    public async Task<List<SportOption>> GetSportsAsync()
    {
        var url = $"sports?apiKey={Uri.EscapeDataString(_options.ApiKey)}";
        using var response = await _http.GetAsync(url);
        response.EnsureSuccessStatusCode();

        var json = await response.Content.ReadAsStringAsync();
        var root = JsonNode.Parse(json);

        var sports = new List<SportOption>();
        if (root is not JsonArray arr)
            return sports;

        foreach (var node in arr)
        {
            if (node is null)
                continue;

            var key = GetString(node, "key");
            var title = GetString(node, "title");
            var details = GetString(node, "details");
            var displayName = string.IsNullOrWhiteSpace(title)
                ? (string.IsNullOrWhiteSpace(details) ? key : details)
                : title;

            if (string.IsNullOrWhiteSpace(key))
                continue;

            sports.Add(new SportOption
            {
                Key = key,
                DisplayName = displayName
            });
        }

        return sports
            .OrderBy(s => s.DisplayName, StringComparer.OrdinalIgnoreCase)
            .ToList();
    }
 

    public async Task<List<OddsRow>> GetOddsForSportAsync(string sport)
    {
        if (string.IsNullOrWhiteSpace(sport))
            return new List<OddsRow>();

        var url = BuildOddsUrl(sport, _options.Markets);
        using var response = await _http.GetAsync(url);
        var responseBody = await response.Content.ReadAsStringAsync();

        _logger.LogInformation("Odds API request: {Url} returned {StatusCode}", url, response.StatusCode);
        _logger.LogDebug("Odds API response body: {Body}", responseBody);

        if (response.StatusCode == HttpStatusCode.UnprocessableEntity && !string.IsNullOrWhiteSpace(_options.Markets))
        {
            _logger.LogWarning("Odds API returned 422 for {Sport} with markets={Markets}; retrying without markets.", sport, _options.Markets);
            var fallbackUrl = BuildOddsUrl(sport, null);
            using var fallbackResponse = await _http.GetAsync(fallbackUrl);
            var fallbackBody = await fallbackResponse.Content.ReadAsStringAsync();
            _logger.LogInformation("Fallback odds API request: {Url} returned {StatusCode}", fallbackUrl, fallbackResponse.StatusCode);
            _logger.LogDebug("Fallback odds API response body: {Body}", fallbackBody);
            fallbackResponse.EnsureSuccessStatusCode();
            return await ParseOddsResponseBodyAsync(fallbackBody);
        }

        response.EnsureSuccessStatusCode();
        return await ParseOddsResponseBodyAsync(responseBody);
    }

    private string BuildOddsUrl(string sport, string? markets)
    {
        var url = $"sports/{Uri.EscapeDataString(sport)}/odds?regions={Uri.EscapeDataString(_options.Regions)}&oddsFormat={Uri.EscapeDataString(_options.OddsFormat)}&apiKey={Uri.EscapeDataString(_options.ApiKey)}";

        if (!string.IsNullOrWhiteSpace(markets))
            url += $"&markets={Uri.EscapeDataString(markets)}";

        return url;
    }

    private static Task<List<OddsRow>> ParseOddsResponseBodyAsync(string body)
    {
        var root = JsonNode.Parse(body);

        if (root is not JsonArray arr)
            return Task.FromResult(new List<OddsRow>());

        return Task.FromResult(FlattenOdds(arr));
    }

    public string ConvertRowsToJson(List<OddsRow> rows)
    {
        return JsonSerializer.Serialize(rows, new JsonSerializerOptions
        {
            WriteIndented = true
        });
    }
 

    public string ConvertRowsToCsv(List<OddsRow> rows)
    {
        var lines = new List<string>
        {
            "MatchDate,Sport,League,EventId,EventName,HomeTeam,AwayTeam,Bookmaker,Market,Team,Player,Selection,Handicap,Price,OverPrice,UnderPrice,LastUpdated"
        };
 

        foreach (var r in rows)
        {
            lines.Add(string.Join(",", new[]
            {
                Csv(r.MatchDate?.ToString("O") ?? ""),
                Csv(r.Sport),
                Csv(r.League),
                Csv(r.EventId),
                Csv(r.EventName),
                Csv(r.HomeTeam),
                Csv(r.AwayTeam),
                Csv(r.Bookmaker),
                Csv(r.Market),
                Csv(r.Team),
                Csv(r.Player),
                Csv(r.Selection),
                Csv(r.Handicap?.ToString(CultureInfo.InvariantCulture) ?? ""),
                Csv(r.Price?.ToString(CultureInfo.InvariantCulture) ?? ""),
                Csv(r.OverPrice?.ToString(CultureInfo.InvariantCulture) ?? ""),
                Csv(r.UnderPrice?.ToString(CultureInfo.InvariantCulture) ?? ""),
                Csv(r.LastUpdated?.ToString("O") ?? "")
            }));
        }
 

        return string.Join(Environment.NewLine, lines);
    }
 

    private static List<OddsRow> FlattenOdds(JsonArray events)
    {
        var rows = new List<OddsRow>();

        foreach (var eventNode in events)
        {
            if (eventNode is null)
                continue;

            var id = GetString(eventNode, "id");
            var sportNice = GetString(eventNode, "sport_nice");
            var sportKey = GetString(eventNode, "sport_key");
            var home = GetString(eventNode, "home_team");
            var away = GetString(eventNode, "away_team");
            var eventName = !string.IsNullOrWhiteSpace(home) && !string.IsNullOrWhiteSpace(away)
                ? $"{away} @ {home}"
                : GetString(eventNode, "title");
            var league = string.IsNullOrWhiteSpace(sportNice) ? sportKey : sportNice;
            var matchDate = ParseDateTimeOffset(GetString(eventNode, "commence_time"));

            var bookmakers = eventNode["bookmakers"] as JsonArray ?? eventNode["sites"] as JsonArray;
            if (bookmakers is null)
                continue;

            foreach (var bookmakerNode in bookmakers)
            {
                if (bookmakerNode is null)
                    continue;

                var bookmaker = GetString(bookmakerNode, "title");
                if (string.IsNullOrWhiteSpace(bookmaker))
                    bookmaker = GetString(bookmakerNode, "site_nice");
                if (string.IsNullOrWhiteSpace(bookmaker))
                    bookmaker = GetString(bookmakerNode, "site_key");
                if (string.IsNullOrWhiteSpace(bookmaker))
                    bookmaker = GetString(bookmakerNode, "key");

                var markets = bookmakerNode["markets"] as JsonArray;
                if (markets is not null)
                {
                    foreach (var marketNode in markets)
                    {
                        if (marketNode is null)
                            continue;

                        var marketKey = GetString(marketNode, "key");
                        var outcomes = marketNode["outcomes"] as JsonArray;
                        if (outcomes is null)
                            continue;

                       if (string.Equals(marketKey, "totals", StringComparison.OrdinalIgnoreCase))
                        {
                            var normalizedOutcomes = outcomes
                                .Where(o => o is not null)
                                .Select(o => new
                                {
                                    Name = GetString(o, "name"),
                                    PriceText = GetString(o, "price"),
                                    PointText = GetString(o, "point")
                                })
                                .ToList();


                            foreach (var totalLineGroup in normalizedOutcomes.GroupBy(x => x.PointText, StringComparer.OrdinalIgnoreCase))
                            {
                                var over = totalLineGroup.FirstOrDefault(x => string.Equals(x.Name, "Over", StringComparison.OrdinalIgnoreCase));
                                var under = totalLineGroup.FirstOrDefault(x => string.Equals(x.Name, "Under", StringComparison.OrdinalIgnoreCase));


                                if (over is null && under is null)
                                    continue;


                                rows.Add(new OddsRow
                                {
                                    MatchDate = matchDate,
                                    Sport = league,
                                    League = league,
                                    EventId = id,
                                    EventName = eventName,
                                    HomeTeam = home,
                                    AwayTeam = away,
                                    Bookmaker = bookmaker,
                                    Market = marketKey,
                                    Team = string.Empty,
                                    Player = string.Empty,
                                    Selection = "Over/Under",
                                    Handicap = ParseDecimal(FirstNonBlank(over?.PointText ?? "", under?.PointText ?? "")),
                                    Price = null,
                                    OverPrice = ParseDecimal(over?.PriceText),
                                    UnderPrice = ParseDecimal(under?.PriceText),
                                    LastUpdated = DateTimeOffset.UtcNow
                                });
                            }


                            continue;
                        }

                        for (var i = 0; i < outcomes.Count; i++)
                        {
                            var outcomeNode = outcomes[i];
                            if (outcomeNode is null)
                                continue;

                            var selection = GetString(outcomeNode, "name");
                            var price = ParseDecimal(GetString(outcomeNode, "price"));
                            var point = GetString(outcomeNode, "point");
                            var handicap = ParseDecimal(point);
                            var team = string.IsNullOrWhiteSpace(selection)
                                ? (i == 0 ? home : away)
                                : selection;

                            rows.Add(new OddsRow
                            {
                                MatchDate = matchDate,
                                Sport = league,
                                League = league,
                                EventId = id,
                                EventName = eventName,
                                HomeTeam = home,
                                AwayTeam = away,
                                Bookmaker = bookmaker,
                                Market = marketKey,
                                Team = team,
                                Player = string.Empty,
                                Selection = selection,
                                Handicap = handicap,
                                Price = price,
                                OverPrice = null,
                                UnderPrice = null,
                                LastUpdated = DateTimeOffset.UtcNow
                            });
                        }
                    }

                    continue;
                }

                var oddsObj = bookmakerNode["odds"] as JsonObject;
                if (oddsObj is null)
                    continue;

                var h2hArray = oddsObj["h2h"] as JsonArray;
                if (h2hArray is null)
                    continue;

                for (var i = 0; i < h2hArray.Count; i++)
                {
                    var priceValue = h2hArray[i]?.ToString() ?? string.Empty;
                    var price = ParseDecimal(priceValue);
                    var team = i == 0 ? home : away;

                    rows.Add(new OddsRow
                    {
                        MatchDate = matchDate,
                        Sport = league,
                        League = league,
                        EventId = id,
                        EventName = eventName,
                        HomeTeam = home,
                        AwayTeam = away,
                        Bookmaker = bookmaker,
                        Market = "H2H",
                        Team = team,
                        Player = string.Empty,
                        Selection = team,
                        Handicap = null,
                        Price = price,
                        OverPrice = null,
                        UnderPrice = null,
                        LastUpdated = DateTimeOffset.UtcNow
                    });
                }
            }
        }

        return rows;
    }

    private static decimal? ParseDecimal(string? text)
    {
        if (string.IsNullOrWhiteSpace(text))
            return null;

        if (decimal.TryParse(text, NumberStyles.Any, CultureInfo.InvariantCulture, out var value))
            return value;

        return null;
    }


    private static DateTimeOffset? ParseDateTimeOffset(string? text)
    {
        if (string.IsNullOrWhiteSpace(text))
            return null;

        if (DateTimeOffset.TryParse(text, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var value))
            return value;

        return null;
    }
    private static string GetString(JsonNode? node, string propertyName)
    {
        return node?[propertyName]?.ToString() ?? "";
    }
 

    private static string GetNestedString(JsonNode? node, string parentName, string childName)
    {
        return node?[parentName]?[childName]?.ToString() ?? "";
    }
 

    private static decimal? GetDecimal(JsonNode? node, string propertyName)
    {
        var text = node?[propertyName]?.ToString();
 

        if (string.IsNullOrWhiteSpace(text))
            return null;
 

        if (decimal.TryParse(text, NumberStyles.Any, CultureInfo.InvariantCulture, out var value))
            return value;
 

        return null;
    }
 

    private static string FirstNonBlank(params string[] values)
    {
        return values.FirstOrDefault(v => !string.IsNullOrWhiteSpace(v)) ?? "";
    }
 

    private static string Csv(string value)
    {
        value ??= "";
 

        if (value.Contains(',') || value.Contains('"') || value.Contains('\n'))
            return $"\"{value.Replace("\"", "\"\"")}\"";
 

        return value;
    }
}