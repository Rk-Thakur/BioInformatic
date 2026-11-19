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
        }, { threshold: 0.15 });
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
    var versions = buttons.map(function (x) { return x.getAttribute("data-release"); });
    var nodes = root.querySelectorAll("[data-states]");
    var flows = root.querySelectorAll(".ped-flow[data-to]");
    var summary = root.querySelector("[data-ped-summary]");
    var fill = root.querySelector(".ped-track-fill");
    var parse = function (attr) { try { return JSON.parse(root.getAttribute(attr) || "{}"); } catch (e) { return {}; } };
    var texts = parse("data-summary"), stats = parse("data-stats");
    var play = root.querySelector("[data-ped-play]");
    var timer = null;
    var LEVELS = ["high", "mild", "uncertain", "normal", "untested"];

    function sizePill(pill) {
      var t = pill.querySelector("text"), r = pill.querySelector("rect");
      if (!t || !r || !t.getComputedTextLength) return;
      var w = t.getComputedTextLength() + 20;
      r.setAttribute("width", w.toFixed(1));
      r.setAttribute("x", (-w / 2).toFixed(1));
    }
    function setStats(version, animate) {
      var s = stats[version] || {};
      Object.keys(s).forEach(function (k) {
        var el = root.querySelector('[data-stat="' + k + '"]');
        if (!el || el.textContent === String(s[k])) return;
        el.textContent = s[k];
        if (animate && !reduce) { el.classList.add("bump"); setTimeout(function () { el.classList.remove("bump"); }, 260); }
      });
    }
    function show(version, animate) {
      var at = versions.indexOf(version);
      buttons.forEach(function (x, i) {
        x.setAttribute("aria-pressed", String(i === at));
        x.classList.toggle("done", i < at);
      });
      if (fill) fill.style.setProperty("--fill", versions.length > 1 ? (100 * at / (versions.length - 1)) + "%" : "100%");
      if (summary) summary.textContent = texts[version] || "";
      setStats(version, animate);
      var carriers = {};
      nodes.forEach(function (n) {
        var st;
        try { st = JSON.parse(n.getAttribute("data-states"))[version]; } catch (e) { return; }
        if (!st) return;
        var before = n.getAttribute("data-level") || "";
        var level = st.level || "";
        carriers[n.getAttribute("data-key")] = !!st.carrier;
        n.classList.toggle("is-carrier", !!st.carrier);
        if (n.querySelector(".ped-ring")) {
          LEVELS.forEach(function (lv) { n.classList.remove("lvl-" + lv); });
          if (level) n.classList.add("lvl-" + level);
          n.querySelector(".ped-ring").setAttribute("class", "ped-ring risk-" + level);
          var pill = n.querySelector(".ped-pill");
          if (pill) {
            pill.setAttribute("class", "ped-pill ped-level risk-" + level);
            pill.querySelector("text").textContent = st.label;
            sizePill(pill);
          }
          var ripple = n.querySelector(".ped-ripple");
          if (animate && ripple && before && before !== level && !reduce) {
            ripple.setAttribute("class", "ped-ripple risk-" + level);
            void ripple.getBoundingClientRect();  // restart the animation
            ripple.setAttribute("class", "ped-ripple risk-" + level + " go");
          }
        }
        n.setAttribute("data-level", level);
      });
      flows.forEach(function (f) { f.classList.toggle("on", !!carriers[f.getAttribute("data-to")]); });
    }
    function stop() {
      if (timer) { clearTimeout(timer); timer = null; }
      root.classList.remove("playing");
      if (play) play.disabled = false;
    }
    root.querySelectorAll(".ped-pill").forEach(sizePill);
    show(root.getAttribute("data-current") || versions[versions.length - 1], false);
    buttons.forEach(function (x) {
      x.addEventListener("click", function () { stop(); show(x.getAttribute("data-release"), true); });
    });
    if (play) {
      play.addEventListener("click", function () {
        stop();
        root.classList.add("playing");
        play.disabled = true;
        var i = 0;
        (function step() {
          show(versions[i], i > 0);
          i += 1;
          if (i < versions.length) timer = setTimeout(step, 1900);
          else timer = setTimeout(stop, 700);
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
  // ================================================================ motion layer (DD49)
  // ---- top bar: lift after scrolling, progress line
  var bar = document.querySelector(".topbar");
  if (bar) {
    var prog = document.createElement("span");
    prog.className = "scroll-progress";
    prog.setAttribute("aria-hidden", "true");
    bar.appendChild(prog);
    var ticking = false;
    var onScroll = function () {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        var max = document.documentElement.scrollHeight - innerHeight;
        bar.classList.toggle("scrolled", scrollY > 8);
        prog.style.transform = "scaleX(" + (max > 0 ? Math.min(1, scrollY / max) : 0) + ")";
        ticking = false;
      });
    };
    addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  // ---- sliding highlight under the menu
  document.querySelectorAll(".nav").forEach(function (nav) {
    var links = nav.querySelectorAll("a");
    var active = nav.querySelector("a.active");
    if (!links.length) return;
    var glider = document.createElement("span");
    glider.className = "nav-glider";
    glider.setAttribute("aria-hidden", "true");
    nav.insertBefore(glider, nav.firstChild);
    nav.classList.add("has-glider");
    var moveTo = function (a, instant) {
      if (!a) { glider.style.opacity = "0"; return; }
      if (instant) glider.style.transition = "none";
      glider.style.opacity = "1";
      glider.style.width = a.offsetWidth + "px";
      glider.style.transform = "translateX(" + a.offsetLeft + "px)";
      if (instant) { void glider.offsetWidth; glider.style.transition = ""; }
    };
    moveTo(active, true);
    links.forEach(function (a) {
      a.addEventListener("mouseenter", function () { moveTo(a); });
      a.addEventListener("focus", function () { moveTo(a); });
    });
    nav.addEventListener("mouseleave", function () { moveTo(active); });
    addEventListener("resize", function () { moveTo(active, true); });
  });

  // ---- cards: spotlight that follows the pointer
  if (!reduce && matchMedia("(hover: hover)").matches) {
    document.querySelectorAll(".card").forEach(function (card) {
      if (card.classList.contains("flat")) return;
      var spot = document.createElement("span");
      spot.className = "spot";
      spot.setAttribute("aria-hidden", "true");
      card.insertBefore(spot, card.firstChild);
      card.addEventListener("pointermove", function (e) {
        var r = card.getBoundingClientRect();
        card.style.setProperty("--mx", (e.clientX - r.left) + "px");
        card.style.setProperty("--my", (e.clientY - r.top) + "px");
      });
    });
  }

  // ---- buttons: click ripple
  document.addEventListener("pointerdown", function (e) {
    var btn = e.target.closest && e.target.closest(".btn");
    if (!btn || reduce) return;
    var r = btn.getBoundingClientRect(), d = Math.max(r.width, r.height);
    var dot = document.createElement("span");
    dot.className = "ripple";
    dot.style.width = dot.style.height = d + "px";
    dot.style.left = (e.clientX - r.left - d / 2) + "px";
    dot.style.top = (e.clientY - r.top - d / 2) + "px";
    btn.appendChild(dot);
    setTimeout(function () { dot.remove(); }, 650);
  });

  // ---- a distinct colour per person for avatars
  document.querySelectorAll(".avatar").forEach(function (av) {
    var host = av.closest("a, tr, .page-head") || av;
    var seed = (host.textContent || av.textContent).trim(), h = 0;
    for (var i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) % 360;
    av.style.background = "linear-gradient(135deg, hsl(" + h + " 62% 46%), hsl(" + ((h + 48) % 360) + " 70% 56%))";
  });

  // ---- scroll reveal for content below the fold; table rows glide in
  if (!reduce && "IntersectionObserver" in window) {
    var below = function (el) { return el.getBoundingClientRect().top > innerHeight * 0.92; };
    var targets = [];
    document.querySelectorAll("main .section, main .grid > .card, .band .card, .band-head").forEach(function (el) {
      if (el.closest(".reveal") || el.classList.contains("reveal") || el.closest(".sr")) return;
      if (!below(el)) return;
      el.classList.add("sr");
      targets.push(el);
    });
    document.querySelectorAll("main .table-wrap").forEach(function (t) {
      t.querySelectorAll("tbody tr").forEach(function (tr, i) { tr.style.setProperty("--r", i); });
      t.classList.add("sr-rows");
      targets.push(t);
    });
    var io2 = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add("sr-in");
        io2.unobserve(e.target);
      });
    }, { threshold: 0.08, rootMargin: "0px 0px -6% 0px" });
    targets.forEach(function (t, i) { t.style.setProperty("--sr-d", (i % 3) * 70 + "ms"); io2.observe(t); });
    // charts animate when they come into view, wherever they are
    var io3 = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("on"); io3.unobserve(e.target); } });
    }, { threshold: 0.3 });
    document.querySelectorAll(".donut").forEach(function (d) { io3.observe(d); });
    // fail-safe: nothing may stay hidden (e.g. printing, odd layouts)
    setTimeout(function () { targets.forEach(function (t) { if (t.getBoundingClientRect().top < innerHeight) t.classList.add("sr-in"); }); }, 2500);
  } else {
    document.querySelectorAll(".donut").forEach(function (d) { d.classList.add("on"); });
  }
})();
