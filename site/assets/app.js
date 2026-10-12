/* OpenAcquis site: renders site/data/*.json produced by scripts/build_site.py.
 * Vanilla JS, no dependencies. All data is inserted with textContent (never innerHTML). */
(function () {
  "use strict";

  // ISO alpha-3 -> code shown on tiles (EU convention: Greece = EL).
  var ALPHA2 = {
    AUT: "AT", BEL: "BE", BGR: "BG", HRV: "HR", CYP: "CY", CZE: "CZ", DNK: "DK",
    EST: "EE", FIN: "FI", FRA: "FR", DEU: "DE", GRC: "EL", HUN: "HU", IRL: "IE",
    ITA: "IT", LVA: "LV", LTU: "LT", LUX: "LU", MLT: "MT", NLD: "NL", POL: "PL",
    PRT: "PT", ROU: "RO", SVK: "SK", SVN: "SI", ESP: "ES", SWE: "SE"
  };

  // Tile grid positions [row, col] approximating geography (7 columns x 8 rows).
  var TILES = {
    FIN: [0, 5],
    IRL: [1, 0], SWE: [1, 4], EST: [1, 6],
    DNK: [2, 3], LVA: [2, 6],
    NLD: [3, 2], DEU: [3, 3], POL: [3, 4], LTU: [3, 5],
    BEL: [4, 1], LUX: [4, 2], CZE: [4, 3], SVK: [4, 4],
    FRA: [5, 1], ITA: [5, 2], AUT: [5, 3], HUN: [5, 4], ROU: [5, 5],
    PRT: [6, 0], ESP: [6, 1], SVN: [6, 3], HRV: [6, 4], BGR: [6, 5],
    MLT: [7, 2], GRC: [7, 5], CYP: [7, 6]
  };

  // Count bins for the sequential colour scale. Bin 0 = none notified.
  var BINS = [
    { min: 0, max: 0, label: "None" },
    { min: 1, max: 2, label: "1–2" },
    { min: 3, max: 5, label: "3–5" },
    { min: 6, max: 10, label: "6–10" },
    { min: 11, max: 25, label: "11–25" },
    { min: 26, max: Infinity, label: "26+" }
  ];
  var PAGE = 200;

  function binOf(n) {
    for (var i = BINS.length - 1; i >= 0; i--) if (n >= BINS[i].min) return i;
    return 0;
  }

  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "text") e.textContent = v;
        else if (k === "className") e.className = v;
        else e.setAttribute(k, v === true ? "" : v);
      });
    }
    (children || []).forEach(function (c) {
      if (c === null || c === undefined) return;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return e;
  }

  function getJSON(url) {
    return fetch(url, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status + " for " + url);
      return r.json();
    });
  }

  function fmtInt(n) { return Number(n || 0).toLocaleString("en"); }

  function fmtBuilt(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString("en-GB", {
      year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC"
    }) + " UTC";
  }

  function setBuilt(data) {
    var text = "Data built " + fmtBuilt(data.built_at) + (data.mode === "live" ? "" : " (" + data.mode + ")") + ".";
    var f = $("footer-built");
    if (f) f.textContent = text;
    return text;
  }

  function sampleNote(data, host) {
    if (data.mode && data.mode !== "live") {
      host.appendChild(el("div", { className: "note", role: "note" }, [
        el("strong", { text: "Sample data. " }),
        "This build uses a small bundled sample for local preview (at most 3 measures per country), not the full CELLAR data."
      ]));
    }
  }

  function showError(host, err) {
    var msg = String(err && err.message || err);
    var hint = location.protocol === "file:"
      ? " Open the site through a web server, e.g. `python -m http.server -d site`, not as a file."
      : " Try reloading; if the problem persists, the data may not have been built yet.";
    host.appendChild(el("div", { className: "note error", role: "alert" }, [
      el("strong", { text: "Could not load data. " }), msg + "." + hint
    ]));
  }

  function deadlineText(d) {
    var ds = d.transposition_deadlines || [];
    if (!ds.length) return "—";
    return ds.length === 1 ? ds[0] : ds[0] + " (and " + (ds.length - 1) + " more)";
  }

  // "Directive (EU) 2022/2555 of the European Parliament and of the Council of 14 December 2022 on X"
  // -> "Directive (EU) 2022/2555 on X" (cards only; the directive page shows the full title).
  function shortTitle(t) {
    return (t || "").replace(/ of the European Parliament and of the Council of \d{1,2}\s\S+\s\d{4}/, "");
  }

  // ---------------------------------------------------------------- index page

  function renderIndex() {
    var cards = $("cards");
    getJSON("data/index.json").then(function (data) {
      $("built").textContent = setBuilt(data) + " Source: EU Publications Office (CELLAR).";
      sampleNote(data, $("sample-note"));
      var total = Object.keys(data.member_states || ALPHA2).length;
      data.directives.forEach(function (d) {
        var href = "directive.html?celex=" + encodeURIComponent(d.celex);
        var stats;
        if (d.error) {
          stats = el("div", { className: "stats" }, [el("span", { text: "Data unavailable in this build." })]);
        } else {
          var pct = Math.round(100 * d.countries_with_measures / total);
          stats = el("div", {}, [
            el("div", { className: "stats" }, [
              el("span", {}, [el("b", { text: fmtInt(d.total_measures) }), " measures"]),
              el("span", {}, [el("b", { text: d.countries_with_measures + "/" + total }), " member states"])
            ]),
            el("div", { className: "meter", role: "img", "aria-label": d.countries_with_measures + " of " + total + " member states notified at least one measure" }, [
              el("span", { style: "width:" + pct + "%" })
            ]),
            el("div", { className: "small muted", style: "margin-top:4px", text: "Deadline: " + deadlineText(d) })
          ]);
        }
        cards.appendChild(el("li", { className: "card" + (d.error ? " failed" : "") }, [
          el("span", { className: "celex", text: d.celex + " · " + (d.topic || "") }),
          el("h3", {}, [el("a", { href: href, text: d.short })]),
          el("span", { className: "title", text: shortTitle(d.title) }),
          stats
        ]));
      });
    }).catch(function (err) {
      $("built").textContent = "";
      showError($("sample-note"), err);
    });
  }

  // ------------------------------------------------------------ directive page

  function renderDirective() {
    var params = new URLSearchParams(location.search);
    var celex = (params.get("celex") || "").toUpperCase();
    if (!/^3\d{4}L\d{4}$/.test(celex)) {
      $("d-title").textContent = "Directive not found";
      showError($("d-notes"), new Error("Missing or invalid ?celex= parameter (expected e.g. 32022L2555)"));
      return;
    }
    getJSON("data/" + celex + ".json").then(function (d) {
      drawDirective(d, params);
    }).catch(function (err) {
      $("d-title").textContent = celex;
      showError($("d-notes"), err);
    });
  }

  function drawDirective(d, params) {
    var states = Object.keys(ALPHA2);
    // Same names as openacquis.countries.MEMBER_STATES.
    var NAMES = {
      AUT: "Austria", BEL: "Belgium", BGR: "Bulgaria", HRV: "Croatia", CYP: "Cyprus", CZE: "Czechia",
      DNK: "Denmark", EST: "Estonia", FIN: "Finland", FRA: "France", DEU: "Germany", GRC: "Greece",
      HUN: "Hungary", IRL: "Ireland", ITA: "Italy", LVA: "Latvia", LTU: "Lithuania", LUX: "Luxembourg",
      MLT: "Malta", NLD: "Netherlands", POL: "Poland", PRT: "Portugal", ROU: "Romania", SVK: "Slovakia",
      SVN: "Slovenia", ESP: "Spain", SWE: "Sweden"
    };
    function nameOf(c) { return NAMES[c] || c; }

    document.title = d.short + " · OpenAcquis";
    $("d-title").textContent = d.short;
    $("d-full").textContent = d.title || "";
    $("nim-link").href = d.eurlex_nim_url;
    setBuilt(d);

    var meta = $("d-meta");
    function metaItem(label, node) {
      meta.appendChild(el("div", {}, [el("dt", { text: label }), el("dd", {}, [node])]));
    }
    metaItem("CELEX", document.createTextNode(d.celex));
    metaItem("Topic", document.createTextNode(d.topic || "—"));
    metaItem("Transposition deadline", document.createTextNode((d.transposition_deadlines || []).join(", ") || "—"));
    metaItem("EUR-Lex", el("span", {}, [
      el("a", { href: d.eurlex_url, text: "Text" }), " · ",
      el("a", { href: d.eurlex_nim_url, text: "National transposition" })
    ]));

    sampleNote(d, $("d-notes"));
    if (d.error) {
      $("d-notes").appendChild(el("div", { className: "note error", role: "alert" }, [
        el("strong", { text: "This directive could not be fetched in the latest build. " }),
        "Error: " + d.error + ". Check EUR-Lex directly: ",
        el("a", { href: d.eurlex_nim_url, text: "national transposition page" }), "."
      ]));
      return;
    }
    $("d-body").hidden = false;

    var counts = d.counts || {};
    var none = states.filter(function (c) { return !counts[c]; });
    var kpis = $("d-kpis");
    function kpi(v, l) { kpis.appendChild(el("div", { className: "kpi" }, [el("div", { className: "v", text: v }), el("div", { className: "l", text: l })])); }
    kpi(fmtInt(d.total_measures), "national measures notified");
    kpi(d.countries_with_measures + " / " + states.length, "member states with at least one");
    kpi(String(none.length), none.length === 1 ? "member state with none" : "member states with none");

    // State: filters + sort.
    var state = {
      country: (params.get("country") || "").toUpperCase(),
      q: params.get("q") || "",
      sort: "country",
      dir: 1,
      limit: PAGE
    };
    if (states.indexOf(state.country) < 0) state.country = "";

    // Tile map
    var map = $("tilemap");
    var tiles = {};
    states.forEach(function (c) {
      var n = counts[c] || 0;
      var pos = TILES[c];
      // starts with the visible code so voice control ("click CZ") matches (WCAG 2.5.3)
      var label = ALPHA2[c] + " " + nameOf(c) + ": " + (n ? n + (n === 1 ? " measure" : " measures") : "no measures notified");
      var t = el("button", {
        type: "button",
        className: "tile b" + binOf(n),
        style: "grid-row:" + (pos[0] + 1) + ";grid-column:" + (pos[1] + 1),
        title: label,
        "aria-label": label,
        "aria-pressed": "false"
      }, [el("span", { className: "code", text: ALPHA2[c] }), el("span", { className: "n", text: n ? String(n) : "–" })]);
      t.addEventListener("click", function () { setCountry(state.country === c ? "" : c); });
      tiles[c] = t;
      map.appendChild(t);
    });

    var legend = $("legend");
    BINS.forEach(function (b, i) {
      legend.appendChild(el("li", {}, [
        el("span", { className: "sw", style: "background:var(--bin" + i + ");box-shadow:inset 0 0 0 1px var(--border)", "aria-hidden": "true" }),
        b.label
      ]));
    });

    // Ranking (doubles as the accessible table view of the map)
    var ranking = $("ranking");
    var max = Math.max.apply(null, states.map(function (c) { return counts[c] || 0; }).concat([1]));
    states.slice().sort(function (a, b) { return (counts[b] || 0) - (counts[a] || 0) || nameOf(a).localeCompare(nameOf(b)); })
      .forEach(function (c) {
        var n = counts[c] || 0;
        var btn = el("button", { type: "button", text: nameOf(c), title: "Show " + nameOf(c) + " in the table" });
        btn.addEventListener("click", function () { setCountry(c); $("tbl-h").scrollIntoView({ behavior: "smooth" }); });
        ranking.appendChild(el("li", {}, [
          btn,
          n ? el("span", { className: "bar", style: "width:" + Math.max(2, Math.round(100 * n / max)) + "%", "aria-hidden": "true" })
            : el("span", { className: "zero", text: "none notified" }),
          el("span", { className: "num", text: String(n) })
        ]));
      });

    // Filters
    var sel = $("f-country");
    states.slice().sort(function (a, b) { return nameOf(a).localeCompare(nameOf(b)); }).forEach(function (c) {
      sel.appendChild(el("option", { value: c, text: nameOf(c) + " (" + (counts[c] || 0) + ")" }));
    });
    sel.value = state.country;
    var q = $("f-q");
    q.value = state.q;
    sel.addEventListener("change", function () { setCountry(sel.value); });
    q.addEventListener("input", function () { state.q = q.value; state.limit = PAGE; update(); });
    $("f-reset").addEventListener("click", function () { state.q = ""; q.value = ""; setCountry(""); });

    Array.prototype.forEach.call(document.querySelectorAll("th button[data-sort]"), function (b) {
      b.addEventListener("click", function () {
        var key = b.getAttribute("data-sort");
        state.dir = state.sort === key ? -state.dir : (key === "country" ? 1 : -1);
        state.sort = key;
        update();
      });
    });

    function setCountry(c) {
      state.country = c;
      state.limit = PAGE;
      sel.value = c;
      update();
    }

    var measures = (d.measures || []).map(function (m) {
      m._hay = [m.title, m.reference, m.journal, m.act_type, nameOf(m.country)].join(" ").toLowerCase();
      return m;
    });

    function syncURL() {
      var p = new URLSearchParams();
      p.set("celex", d.celex);
      if (state.country) p.set("country", state.country);
      if (state.q) p.set("q", state.q);
      try { history.replaceState(null, "", "?" + p.toString()); } catch (e) { /* ignore */ }
    }

    function update() {
      Object.keys(tiles).forEach(function (c) { tiles[c].setAttribute("aria-pressed", String(state.country === c)); });
      var needle = state.q.trim().toLowerCase();
      var rows = measures.filter(function (m) {
        return (!state.country || m.country === state.country) && (!needle || m._hay.indexOf(needle) >= 0);
      });
      var key = state.sort, dir = state.dir;
      rows.sort(function (a, b) {
        var x = key === "country" ? nameOf(a.country) : (a[key] || "");
        var y = key === "country" ? nameOf(b.country) : (b[key] || "");
        if (x !== y) {
          if (!x) return 1; // blanks last
          if (!y) return -1;
          return (x < y ? -1 : 1) * dir;
        }
        return (b.journal_date || "").localeCompare(a.journal_date || "");
      });
      Array.prototype.forEach.call(document.querySelectorAll("th button[data-sort]"), function (b) {
        var th = b.parentNode;
        if (b.getAttribute("data-sort") === key) th.setAttribute("aria-sort", dir > 0 ? "ascending" : "descending");
        else th.removeAttribute("aria-sort");
      });

      var tbody = $("rows");
      tbody.textContent = "";
      var frag = document.createDocumentFragment();
      rows.slice(0, state.limit).forEach(function (m) {
        frag.appendChild(el("tr", {}, [
          el("td", { className: "c", title: nameOf(m.country), text: ALPHA2[m.country] || m.country }),
          el("td", { className: "d", text: m.journal_date || "—" }),
          el("td", { className: "d", text: m.notified || "—" }),
          el("td", { text: m.reference || "—" }),
          el("td", { text: m.act_type || "—" }),
          el("td", { className: "t" }, [
            el("a", { href: d.eurlex_nim_url, title: "Open the EUR-Lex national transposition page", text: m.title || "(untitled measure)" }),
            m.journal ? el("span", { className: "j", text: m.journal }) : null
          ])
        ]));
      });
      if (!rows.length) {
        frag.appendChild(el("tr", {}, [el("td", { colspan: "6", className: "muted", text:
          state.country && !counts[state.country] ? nameOf(state.country) + " has not notified any measures for this directive."
            : "No measures match these filters." })]));
      }
      tbody.appendChild(frag);

      var shown = Math.min(rows.length, state.limit);
      $("count").textContent = "Showing " + fmtInt(shown) + " of " + fmtInt(rows.length) + " matching measures" +
        (rows.length !== measures.length ? " (" + fmtInt(measures.length) + " in total)" : "") + ".";
      var more = $("more");
      more.textContent = "";
      if (rows.length > state.limit) {
        var b = el("button", { type: "button", className: "btn", text: "Show all " + fmtInt(rows.length) });
        b.addEventListener("click", function () { state.limit = Infinity; update(); });
        more.appendChild(b);
      }
      syncURL();
    }

    update();
  }

  window.OpenAcquis = { renderIndex: renderIndex, renderDirective: renderDirective, _binOf: binOf };
})();
