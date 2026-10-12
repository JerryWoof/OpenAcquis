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

  // Allow-list: http(s) URLs and scheme-less relative links. URL parsing strips tabs, newlines and
  // leading control characters, so "java\tscript:" must be judged by the parsed protocol, not a regex.
  function safeHref(v) {
    var s = String(v);
    var u;
    try { u = new URL(s, location.href); } catch (e) { return false; }
    if (u.protocol === "http:" || u.protocol === "https:") return true;
    // a relative link (no scheme of its own) stays on the page's own scheme, e.g. file: when opened locally
    var bare = s.replace(/[\t\n\r]/g, "").replace(/^[\x00-\x20]+/, "");
    return !/^[a-z][a-z0-9+.\-]*:/i.test(bare) && u.protocol === location.protocol;
  }

  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "href" && !safeHref(v)) return; // only data from our build, but be safe
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
        var aligned = (d.alignments || []).map(function (a) {
          return el("a", { className: "card-link", href: a.href, text: "Provision-level: " + a.country_name + " →" });
        });
        cards.appendChild(el("li", { className: "card" + (d.error ? " failed" : "") }, [
          el("span", { className: "celex", text: d.celex + " · " + (d.topic || "") }),
          el("h3", {}, [el("a", { href: href, text: d.short })]),
          el("span", { className: "title", text: shortTitle(d.title) }),
          stats
        ].concat(aligned)));
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
    (d.alignments || []).forEach(function (a) {
      $("d-notes").appendChild(el("div", { className: "note info", role: "note" }, [
        el("strong", { text: "Provision by provision: " }),
        el("a", { href: a.href, text: d.short + " × " + a.country_name }),
        ": each article next to the official correlation table and the top machine suggestion, with both texts side by side."
      ]));
    });
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

  // ------------------------------------------------------------ alignment page

  var AGREE = {
    match: { text: "matches table", cls: "ok" },
    same_section: { text: "same § as table", cls: "near" },
    differs: { text: "differs from table", cls: "off" },
    article_level: { text: "", cls: "" },
    no_table_entry: { text: "", cls: "" }
  };

  function fmtPct(x) { return Math.round(100 * (x || 0)) + "%"; }

  function nationalRef(nat, id) {
    var n = nat[id] || { label: id, act: "" };
    return n.label + (n.act ? " (" + n.act + " Sb.)" : "");
  }

  function natLink(nat, id) {
    var n = nat[id];
    var text = n ? n.label : id;
    return n && n.url ? el("a", { href: n.url, title: "Open in e-Sbírka", text: text }) : el("span", { text: text });
  }

  function textPanel(title, cls, items) {
    var box = el("div", { className: "tpanel " + cls }, [el("h4", { text: title })]);
    if (!items.length) box.appendChild(el("p", { className: "small muted", text: "None." }));
    items.forEach(function (it) {
      box.appendChild(el("div", { className: "titem" }, [
        el("div", { className: "thead" }, [
          it.url ? el("a", { href: it.url, text: it.label }) : el("strong", { text: it.label }),
          it.extra ? el("span", { className: "small muted", text: " " + it.extra }) : null
        ]),
        el("p", { className: "ptext", text: it.text || "(text not available)" })
      ]));
    });
    return box;
  }

  function renderAlignment() {
    var params = new URLSearchParams(location.search);
    var id = params.get("id") || "32022L2555_cz";
    if (!/^3\d{4}L\d{4}_[a-z]{2}$/.test(id)) {
      $("a-title").textContent = "Alignment not found";
      showError($("a-notes"), new Error("Missing or invalid ?id= parameter (expected e.g. 32022L2555_cz)"));
      return;
    }
    getJSON("data/align/" + id + ".json").then(function (d) {
      drawAlignment(d, params);
    }).catch(function (err) {
      $("a-title").textContent = id;
      showError($("a-notes"), err);
    });
  }

  function drawAlignment(d, params) {
    var nat = d.national_texts || {};
    var acts = (d.national || []).map(function (n) { return n.act_id + " Sb."; }).join(", ");
    document.title = d.short + " × " + d.country_name + " · OpenAcquis";
    $("a-title").textContent = d.short + " × " + d.country_name + ", provision by provision";
    $("a-lede").textContent = "For each paragraph of " + d.short + " (Czech language version): the provisions of " + acts +
      " that the government's official correlation table lists for it, and the most similar provision found by the machine. " +
      "Open a row to read the texts side by side.";
    var back = $("a-back");
    back.href = "directive.html?celex=" + encodeURIComponent(d.celex);
    back.textContent = "← " + d.short + ": notified measures";
    var built = "Alignment computed " + fmtBuilt(d.built_at) + (d.mode === "live" ? "" : " (precomputed copy)") + ".";
    $("footer-built").textContent = built;

    var notes = $("a-notes");
    notes.appendChild(el("div", { className: "note", role: "note" }, [
      el("strong", { text: "Suggestions, not findings. " }),
      "A machine suggestion is the national provision whose wording is most similar; nobody has reviewed it. " +
      "A difference from the table does not mean either is wrong, and neither column says whether " + d.country_name +
      " transposed " + d.short + " correctly. " + built
    ]));

    var src = d.official_source || {};
    var os = $("a-official-src");
    os.appendChild(document.createTextNode(src.label + ": the government's own list of which bill provision implements which article. "));
    if (src.url) os.appendChild(el("a", { href: src.url, text: "Table (psp.cz)" }));
    if (src.history_url) { os.appendChild(document.createTextNode(" · ")); os.appendChild(el("a", { href: src.history_url, text: "Bill history" })); }
    os.appendChild(document.createTextNode(". It describes the 2024 bill; its numbering was mapped to the act as published."));
    $("a-machine-src").textContent = "Top candidate from OpenAcquis matching (" + d.method + ", text similarity of the Czech wording; " +
      "scores are not probabilities). Marked weak when below the link threshold of " + d.threshold + ". Unreviewed.";

    var s = d.summary || {};
    var kpis = $("a-kpis");
    function kpi(v, l) { kpis.appendChild(el("div", { className: "kpi" }, [el("div", { className: "v", text: v }), el("div", { className: "l", text: l })])); }
    kpi(s.rows_with_official + " / " + s.rows, "paragraphs with an official correlation");
    kpi(String(s.match), "top suggestion matches the table");
    kpi(String(s.same_section), "top suggestion in the same § as the table");
    kpi(fmtPct(s.recall_at_5), "table's provision among the top 5 suggestions");

    var sel = $("f-art");
    (d.articles || []).forEach(function (a) {
      sel.appendChild(el("option", { value: a.id, text: a.label + (a.heading ? " · " + a.heading : "") }));
    });
    var state = { art: params.get("art") || "", show: params.get("show") || "", q: params.get("q") || "" };
    sel.value = state.art; if (sel.value !== state.art) state.art = "";
    var show = $("f-show"); show.value = state.show; if (show.value !== state.show) state.show = "";
    var q = $("f-q"); q.value = state.q;
    sel.addEventListener("change", function () { state.art = sel.value; update(); });
    show.addEventListener("change", function () { state.show = show.value; update(); });
    q.addEventListener("input", function () { state.q = q.value; update(); });
    $("f-reset").addEventListener("click", function () {
      state.art = state.show = state.q = ""; sel.value = show.value = q.value = ""; update();
    });

    (d.articles || []).forEach(function (a) {
      a.rows.forEach(function (r) {
        var hay = [r.label, r.text].concat(r.official.map(function (o) { return nationalRef(nat, o) + " " + ((nat[o] || {}).text || ""); }))
          .concat(r.machine.map(function (m) { return nationalRef(nat, m.id) + " " + ((nat[m.id] || {}).text || ""); }));
        r._hay = hay.join(" ").toLowerCase();
      });
    });

    function keep(r) {
      var listed = r.official.length > 0 || r.agreement === "article_level";
      if (state.show === "official" && !listed) return false;
      if (state.show === "none" && listed) return false;
      if (state.show === "differs" && r.agreement !== "differs") return false;
      var needle = state.q.trim().toLowerCase();
      return !needle || r._hay.indexOf(needle) >= 0;
    }

    function rowFor(a, r) {
      var tr = el("tr", {});
      var off = el("td", { className: "off-cell" });
      if (r.official.length) {
        var list = el("ul", { className: "refs" });
        r.official.forEach(function (o) { list.appendChild(el("li", {}, [natLink(nat, o)])); });
        off.appendChild(list);
        if (r.official_eu.length) {
          off.appendChild(el("span", { className: "small muted", text: "for " + r.official_eu.map(function (x) {
            return x.indexOf(r.label + " ") === 0 ? x.slice(r.label.length + 1) : x;
          }).join(", ") }));
        }
      } else if (r.agreement === "article_level") {
        off.appendChild(el("span", { className: "muted small", text: "listed for the whole article (above)" }));
      } else {
        off.appendChild(el("span", { className: "muted small", text: "not listed" }));
      }
      var mach = el("td", { className: "mach-cell" });
      if (r.machine.length) {
        var m = r.machine[0];
        mach.appendChild(el("div", {}, [natLink(nat, m.id)]));
        var ag = AGREE[r.agreement] || AGREE.no_table_entry;
        mach.appendChild(el("div", { className: "small" }, [
          el("span", { className: "muted", text: "score " + m.score.toFixed(2) + (m.above_threshold ? "" : " · weak") }),
          ag.text ? el("span", { className: "agree " + ag.cls, text: ag.text }) : null
        ]));
      } else {
        mach.appendChild(el("span", { className: "muted small", text: "no candidate" }));
      }
      var btn = el("button", { type: "button", className: "btn small-btn", "aria-expanded": "false", text: "Texts" });
      var label = el("td", { className: "prov" }, [
        r.url ? el("a", { href: r.url, title: "Open in EUR-Lex", text: r.label }) : el("span", { text: r.label })
      ]);
      tr.appendChild(label); tr.appendChild(off); tr.appendChild(mach); tr.appendChild(el("td", { className: "act" }, [btn]));
      var detail = el("tr", { className: "texts", hidden: true });
      var cell = el("td", { colspan: "4" });
      detail.appendChild(cell);
      btn.setAttribute("aria-label", "Show texts for " + r.label);
      btn.addEventListener("click", function () {
        var open = btn.getAttribute("aria-expanded") === "true";
        if (!open && !cell.firstChild) {
          cell.appendChild(el("div", { className: "tgrid" }, [
            textPanel(d.short + " " + r.label, "eu", [{ label: "EUR-Lex", url: r.url, text: r.text }]),
            textPanel("Official correlation table", "official", r.official.map(function (o) {
              return { label: nationalRef(nat, o), url: (nat[o] || {}).url, text: (nat[o] || {}).text };
            })),
            textPanel("Machine suggestions", "machine", r.machine.map(function (x, i) {
              return { label: (i + 1) + ". " + nationalRef(nat, x.id), url: (nat[x.id] || {}).url, text: (nat[x.id] || {}).text,
                extra: "score " + x.score.toFixed(2) + (x.above_threshold ? "" : " (weak)") };
            }))
          ]));
        }
        detail.hidden = open;
        btn.setAttribute("aria-expanded", String(!open));
        btn.textContent = open ? "Texts" : "Hide";
      });
      return [tr, detail];
    }

    function syncURL() {
      var p = new URLSearchParams();
      p.set("id", d.id);
      if (state.art) p.set("art", state.art);
      if (state.show) p.set("show", state.show);
      if (state.q) p.set("q", state.q);
      try { history.replaceState(null, "", "?" + p.toString()); } catch (e) { /* ignore */ }
    }

    function update() {
      var host = $("a-articles");
      host.textContent = "";
      var shown = 0, total = 0;
      var frag = document.createDocumentFragment();
      (d.articles || []).forEach(function (a) {
        total += a.rows.length;
        if (state.art && a.id !== state.art) return;
        var rows = a.rows.filter(keep);
        if (!rows.length) return;
        shown += rows.length;
        var sec = el("section", { className: "art", "aria-label": a.label });
        sec.appendChild(el("h2", {}, [
          a.url ? el("a", { href: a.url, title: "Open in EUR-Lex", text: a.label }) : el("span", { text: a.label }),
          a.heading ? el("span", { className: "art-h", text: " · " + a.heading }) : null
        ]));
        if (a.official && a.official.length) {
          var p = el("p", { className: "small" }, [el("span", { className: "pill official", text: "Official correlation table" }), " whole article: "]);
          a.official.forEach(function (o, i) { if (i) p.appendChild(document.createTextNode(", ")); p.appendChild(natLink(nat, o)); });
          sec.appendChild(p);
        }
        var tbody = el("tbody");
        rows.forEach(function (r) { rowFor(a, r).forEach(function (x) { tbody.appendChild(x); }); });
        sec.appendChild(el("div", { className: "table-wrap" }, [el("table", { className: "align-table" }, [
          el("thead", {}, [el("tr", {}, [
            el("th", { scope: "col", text: d.short + " (EUR-Lex)" }),
            el("th", { scope: "col" }, [el("span", { className: "pill official", text: "Official correlation table" })]),
            el("th", { scope: "col" }, [el("span", { className: "pill machine", text: "Machine suggestion" })]),
            el("th", { scope: "col" }, [el("span", { className: "sr-only", text: "Texts" })])
          ])]),
          tbody
        ])]));
        frag.appendChild(sec);
      });
      if (!shown) frag.appendChild(el("p", { className: "muted", text: "No provisions match these filters." }));
      host.appendChild(frag);
      $("a-count").textContent = "Showing " + shown + " of " + total + " provisions.";
      syncURL();
    }

    $("a-body").hidden = false;
    update();
  }

  window.OpenAcquis = { renderIndex: renderIndex, renderDirective: renderDirective, renderAlignment: renderAlignment, _binOf: binOf };
})();
