// Light/dark toggle. The initial theme is applied by an inline script in
// base.html <head>; this only handles the button and remembers the choice.
// The key and JSON format match the admin's (Unfold's "adminTheme"), so the
// app and the admin share one setting.
document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-theme-toggle]");
    if (!button) return;
    var dark = document.documentElement.classList.toggle("dark");
    try {
        localStorage.setItem("adminTheme", JSON.stringify(dark ? "dark" : "light"));
        localStorage.removeItem("theme"); // the old key
    } catch (e) {}
});
