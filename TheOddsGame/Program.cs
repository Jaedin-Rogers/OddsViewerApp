using TheOddsGame.Components;
using OddsViewerApp.Services;
using MudBlazor.Services;

var builder = WebApplication.CreateBuilder(args);

builder.Configuration
    .AddJsonFile("projectsettings.json", optional: true, reloadOnChange: true)
    .AddJsonFile("../projectsettings.json", optional: true, reloadOnChange: true)
    .AddEnvironmentVariables();

var secretApiKey = builder.Configuration["THE_ODDS_API_KEY"]
    ?? builder.Configuration["OddsApi__ApiKey"];

// Add services to the container.
builder.Services.AddRazorComponents()
    .AddInteractiveServerComponents();

builder.Services.AddMudServices();

builder.Services.Configure<OddsApiOptions>(options =>
{
    builder.Configuration.GetSection("OddsApi").Bind(options);

    if (string.IsNullOrWhiteSpace(options.ApiKey) && !string.IsNullOrWhiteSpace(secretApiKey))
    {
        options.ApiKey = secretApiKey;
    }
});

builder.Services.AddHttpClient<OddsApiService>((serviceProvider, client) =>
{
    var options = serviceProvider.GetRequiredService<Microsoft.Extensions.Options.
        IOptions<OddsApiOptions>>().Value;

    client.BaseAddress = new Uri(options.BaseUrl.TrimEnd('/') + "/");
});

builder.Services.Configure<NbaDbOptions>(options =>
{
    builder.Configuration.GetSection("NbaDb").Bind(options);

    var connectionString = builder.Configuration.GetConnectionString("NbaDb")
        ?? builder.Configuration.GetConnectionString("DefaultConnection");
    if (!string.IsNullOrWhiteSpace(connectionString))
    {
        options.ConnectionString = connectionString;
    }

    var dbHost = builder.Configuration["DB_HOST"];
    var dbPort = builder.Configuration["DB_PORT"];
    var dbName = builder.Configuration["DB_NAME"];
    var dbUser = builder.Configuration["DB_USER"];
    var dbPassword = builder.Configuration["DB_PASSWORD"];

    if (!string.IsNullOrWhiteSpace(dbHost)) options.Host = dbHost;
    if (int.TryParse(dbPort, out var p)) options.Port = p;
    if (!string.IsNullOrWhiteSpace(dbName)) options.Database = dbName;
    if (!string.IsNullOrWhiteSpace(dbUser)) options.Username = dbUser;
    if (!string.IsNullOrWhiteSpace(dbPassword)) options.Password = dbPassword;
});

builder.Services.AddScoped<NBADBService>();

var app = builder.Build();

// Configure the HTTP request pipeline.
if (!app.Environment.IsDevelopment())
{
    app.UseExceptionHandler("/Error", createScopeForErrors: true);
    // The default HSTS value is 30 days. You may want to change this for production scenarios, see https://aka.ms/aspnetcore-hsts.
    app.UseHsts();
}
app.UseStatusCodePagesWithReExecute("/not-found", createScopeForStatusCodePages: true);
app.UseHttpsRedirection();

app.UseAntiforgery();

app.MapStaticAssets();
app.MapRazorComponents<App>()
    .AddInteractiveServerRenderMode();

app.Run();
