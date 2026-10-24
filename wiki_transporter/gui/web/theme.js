/* 主题：默认跟随系统；手动切过之后记住选择。 */
(function () {
  var root = document.documentElement;
  var KEY = "wt.theme";

  function saved() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }

  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  var picked = saved();
  var dark = picked ? picked === "dark" : !!(mq && mq.matches);
  root.setAttribute("data-bs-theme", dark ? "dark" : "light");

  function sync() {
    var b = document.getElementById("theme-toggle");
    if (!b) return;
    b.addEventListener("click", function () {
      var next = root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-bs-theme", next);
      try { localStorage.setItem(KEY, next); } catch (e) {}
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", sync);
  } else {
    sync();
  }

  /* 没手动选过的时候跟着系统走 */
  if (mq && mq.addEventListener) {
    mq.addEventListener("change", function (e) {
      if (saved()) return;
      root.setAttribute("data-bs-theme", e.matches ? "dark" : "light");
    });
  }
})();
