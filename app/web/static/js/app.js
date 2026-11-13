// GE-EHR progressive enhancement. Every page works without this file; it only adds motion
// and convenience. No framework, no network calls.
(function () {
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- fail-safe for the fade-in: never leave content invisible
  setTimeout(function () { document.documentElement.classList.add("reveal-done"); }, 1200);

  // ---- theme toggle (light / dark), remembered per browser
  var toggle = document.querySelector("[data-theme-toggle]");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var root = document.documentElement;
      var current = root.getAttribute("data-theme") ||
        (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      var next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("ge-ehr-theme", next); } catch (e) { /* ignore */ }
    });
  }

  // ---- count-up for stat values: <span data-count="1458">1,458</span>
  function countUp(el) {
    var target = parseFloat(el.getAttribute("data-count"));
    if (!isFinite(target) || reduce) return;
    var start = performance.now(), dur = 900;
    var fmt = new Intl.NumberFormat();
    function frame(now) {
      var p = Math.min(1, (now - start) / dur);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = fmt.format(Math.round(target * eased));
      if (p < 1) requestAnimationFrame(frame);
      else el.textContent = fmt.format(target);
    }
    el.textContent = "0";
    requestAnimationFrame(frame);
  }
  var counters = document.querySelectorAll("[data-count]");
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { countUp(e.target); io.unobserve(e.target); }
      });
    }, { threshold: 0.4 });
    counters.forEach(function (c) { io.observe(c); });
  }

  // ---- filter chips: <div data-filter-group="variants"> buttons with data-filter="all|actionable|vus|benign|other"
  document.querySelectorAll("[data-filter-group]").forEach(function (bar) {
    var target = document.getElementById(bar.getAttribute("data-filter-group"));
    if (!target) return;
    var rows = target.querySelectorAll("[data-band]");
    bar.querySelectorAll("[data-filter]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var f = btn.getAttribute("data-filter");
        bar.querySelectorAll("[data-filter]").forEach(function (b) { b.setAttribute("aria-pressed", String(b === btn)); });
        rows.forEach(function (r) { r.hidden = !(f === "all" || r.getAttribute("data-band") === f); });
      });
    });
  });

  // ---- drag-and-drop VCF upload
  document.querySelectorAll(".dropzone").forEach(function (zone) {
    var input = zone.querySelector("input[type=file]");
    var label = zone.querySelector(".file");
    if (!input) return;
    ["dragenter", "dragover"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add("drag"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      zone.addEventListener(ev, function () { zone.classList.remove("drag"); });
    });
    zone.addEventListener("drop", function (e) {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files.length) { input.files = e.dataTransfer.files; input.dispatchEvent(new Event("change")); }
    });
    input.addEventListener("change", function () {
      if (label && input.files.length) label.textContent = input.files[0].name;
      var btn = zone.parentElement.querySelector("[data-upload-submit]");
      if (btn) btn.disabled = !input.files.length;
    });
  });
})();
