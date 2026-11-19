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

  // ---- family tree (Stage 7, DD48): entrance animation and the ClinVar time switch
  document.querySelectorAll("[data-pedigree]").forEach(function (root) {
    if (!reduce) {
      root.classList.add("animate");
      var start = function () { root.classList.add("in-view"); };
      if ("IntersectionObserver" in window) {
        var seen = new IntersectionObserver(function (entries) {
          entries.forEach(function (e) { if (e.isIntersecting) { start(); seen.disconnect(); } });
        }, { threshold: 0.2 });
        seen.observe(root);
      } else { start(); }
    }
    // On narrow screens the tree scrolls sideways: start with the viewed person centred.
    var box = root.querySelector(".ped-scroll"), self = root.querySelector(".is-self");
    if (box && self && box.scrollWidth > box.clientWidth) {
      var b = self.getBoundingClientRect(), c = box.getBoundingClientRect();
      box.scrollLeft += (b.left + b.width / 2) - (c.left + c.width / 2);
    }
    var buttons = Array.prototype.slice.call(root.querySelectorAll("[data-release]"));
    if (!buttons.length) return;
    var nodes = root.querySelectorAll("[data-states]");
    var summary = root.querySelector("[data-ped-summary]");
    var texts = {};
    try { texts = JSON.parse(root.getAttribute("data-summary") || "{}"); } catch (e) { /* keep empty */ }
    var play = root.querySelector("[data-ped-play]");
    var timer = null;

    function show(version, animate) {
      buttons.forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-release") === version)); });
      if (summary) summary.textContent = texts[version] || "";
      nodes.forEach(function (n) {
        var st;
        try { st = JSON.parse(n.getAttribute("data-states"))[version]; } catch (e) { return; }
        if (!st) return;
        var before = n.getAttribute("data-level") || "";
        var level = st.level || "";
        n.classList.toggle("is-carrier", !!st.carrier);
        var ring = n.querySelector(".ped-ring");
        if (ring && level) ring.setAttribute("class", "ped-ring risk-" + level);
        var label = n.querySelector(".ped-level");
        if (label && level) { label.textContent = st.label; label.setAttribute("class", "ped-level risk-" + level); }
        var ripple = n.querySelector(".ped-ripple");
        if (animate && ripple && level && before && before !== level && !reduce) {
          ripple.setAttribute("class", "ped-ripple risk-" + level);
          void ripple.getBoundingClientRect();  // restart the animation
          ripple.setAttribute("class", "ped-ripple risk-" + level + " go");
        }
        n.setAttribute("data-level", level);
      });
    }
    function stop() {
      if (timer) { clearTimeout(timer); timer = null; }
      root.classList.remove("playing");
      if (play) play.disabled = false;
    }
    buttons.forEach(function (b) {
      b.addEventListener("click", function () { stop(); show(b.getAttribute("data-release"), true); });
    });
    if (play) {
      play.addEventListener("click", function () {
        stop();
        root.classList.add("playing");
        play.disabled = true;
        var versions = buttons.map(function (b) { return b.getAttribute("data-release"); });
        var i = 0;
        (function step() {
          show(versions[i], i > 0);
          i += 1;
          if (i < versions.length) timer = setTimeout(step, 1800);
          else timer = setTimeout(stop, 600);
        })();
      });
    }
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
