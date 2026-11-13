// Runs in <head> before first paint: apply the saved theme so there is no flash.
(function () {
  document.documentElement.classList.add("js");
  try {
    var t = localStorage.getItem("ge-ehr-theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* storage blocked: follow the OS setting */ }
})();
