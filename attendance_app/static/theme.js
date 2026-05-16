(() => {
    const DARK_THEME_HOUR = 18;
    const LIGHT_THEME_HOUR = 6;

    function currentTheme() {
        const hour = new Date().getHours();
        return hour >= DARK_THEME_HOUR || hour < LIGHT_THEME_HOUR ? "dark" : "light";
    }

    function themeColor(theme) {
        return theme === "dark" ? "#081426" : "#f8fbff";
    }

    function applyTheme() {
        const theme = currentTheme();
        const root = document.documentElement;
        if (root.dataset.theme !== theme) {
            root.dataset.theme = theme;
        }
        root.style.colorScheme = theme;

        const meta = document.querySelector('meta[name="theme-color"]');
        if (meta) {
            meta.setAttribute("content", themeColor(theme));
        }
    }

    applyTheme();
    window.setInterval(applyTheme, 60000);
})();
