(function () {
  var root = document.documentElement;
  var settings = window.__AGENT_FRIEND_SETTINGS__ || {};
  var theme = settings.theme === "dark" ? "dark" : "light";

  root.setAttribute("theme", theme);
  root.style.colorScheme = theme;

  if (theme === "dark") {
    root.setAttribute("theme-mode", "dark");
  } else {
    root.removeAttribute("theme-mode");
  }
})();
