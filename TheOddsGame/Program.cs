using TheOddsGame.Components;
using OddsViewerApp.Services;
using MudBlazor.Services;
var builder = WebApplication.CreateBuilder(args);

builder.Configuration.AddEnvironmentVariables();

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
