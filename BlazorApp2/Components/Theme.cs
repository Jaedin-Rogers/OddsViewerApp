using MudBlazor;


namespace BlazorApp2.Components;


public static class Theme
{
    public static MudTheme ApplicationTheme()
    {
        return new MudTheme
        {
            PaletteLight = new PaletteLight
            {
                Primary = "#7C3AED",
                Secondary = "#06B6D4",
                Success = "#198754",
                Info = "#06B6D4",
                Warning = "#ffc107",
                Error = "#dc3545",
                Background = "#F6F0FF",
                Surface = "#FCFAFF",
                AppbarBackground = "#FCFAFF",
                AppbarText = "#2E1065",
                DrawerBackground = "#F3ECFF",
                DrawerText = "#2E1065",
                TextPrimary = "#2E1065",
                TextSecondary = "#6D28D9",
                Divider = "#DDD6FE",
                TableLines = "#DDD6FE"
            },
            PaletteDark = new PaletteDark
            {
                Primary = "#A78BFA",
                Secondary = "#22D3EE",
                Success = "#4caf50",
                Info = "#22D3EE",
                Warning = "#ffd54f",
                Error = "#ff5252",
                Background = "#130C26",
                Surface = "#1D1238",
                AppbarBackground = "#24144A",
                AppbarText = "#f8f9fa",
                DrawerBackground = "#1B1034",
                DrawerText = "#f8f9fa",
                TextPrimary = "#f8f9fa",
                TextSecondary = "#C4B5FD",
                Divider = "#3C2A67",
                TableLines = "#3C2A67"
            },
            LayoutProperties = new LayoutProperties
            {
                DrawerWidthLeft = "260px",
                AppbarHeight = "64px",
                DefaultBorderRadius = "8px"
            }
        };
    }
}