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

  // Language of national texts per member state (WCAG 3.1.2); multilingual states are left out.
  var LANGS = {
    AUT: "de", BGR: "bg", HRV: "hr", CYP: "el", CZE: "cs", DNK: "da", EST: "et", FIN: "fi", FRA: "fr",
    DEU: "de", GRC: "el", HUN: "hu", ITA: "it", LVA: "lv", LTU: "lt", NLD: "nl", POL: "pl", PRT: "pt",
    ROU: "ro", SVK: "sk", SVN: "sl", ESP: "es", SWE: "sv"
  };
  // adapter code -> language (fallback for data built before national_language existed)
  var CC_LANG = { cz: "cs", sk: "sk", at: "de", de: "de" };
  // "eu:32022L2555:cs:art1.par1" -> "cs"
  function euLangOf(id) { var p = String(id || "").split(":"); return p[0] === "eu" && /^[a-z]{2}$/.test(p[2] || "") ? p[2] : null; }

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
      if (!r.ok) { var e = new Error("HTTP " + r.status + " for " + url); e.status = r.status; throw e; }
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

  // A 404 for provision-level data: that page is simply not part of this build (e.g. --no-alignment).
  function notBuilt(err) { return !!err && err.status === 404 && location.protocol !== "file:"; }

  function showMissing(host, what) {
    host.appendChild(el("div", { className: "note", role: "note" }, [
      el("strong", { text: "Not part of this build. " }),
      "The data for " + what + " was not built for this copy of the site. ",
      el("a", { href: "./#directives", text: "All directives" }), "."
    ]));
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

  // ------------------------------------------------- links to data not in this build

  // Replace links to alignment, review and matrix pages and dataset downloads whose data is not
  // part of this build (index.json lists what was built) with plain text.
  function pruneLinks(idx) {
    var align = {}, review = {}, matrix = {}, any = false;
    (idx.directives || []).forEach(function (d) {
      (d.alignments || []).forEach(function (a) {
        var id = (a.href.split("id=")[1] || "");
        align[id] = true; any = true;
        if (a.review_href !== null) review[id] = true;
      });
      if (d.matrix_href) { matrix[d.celex] = true; any = true; }
    });
    var dataset = idx.dataset !== false; // older index.json without the flag: assume built
    Array.prototype.forEach.call(document.querySelectorAll("a[href]"), function (a) {
      var u;
      try { u = new URL(a.getAttribute("href"), location.href); } catch (e) { return; }
      if (u.origin !== location.origin) return;
      var page = u.pathname.split("/").pop(), p = u.searchParams, dead = false;
      if (page === "alignment.html") dead = !align[p.get("id") || "32022L2555_cz"];
      else if (page === "review.html") dead = !review[p.get("id") || "32022L2555_cz"];
      else if (page === "matrix.html") dead = !matrix[(p.get("celex") || "32022L2555").toUpperCase()];
      else if (/\/data\/dataset\//.test(u.pathname)) dead = !dataset;
      if (!dead) return;
      var span = el("span", { className: "muted", title: "Not part of this build", text: a.textContent });
      a.parentNode.replaceChild(span, a);
    });
    var note = $("v04-note");
    if (note && !any) note.hidden = true;
  }

  function checkLinks() {
    getJSON("data/index.json").then(pruneLinks).catch(function () { /* optional */ });
  }

  // ---------------------------------------------------------------- provision-level states

  // One chip per country of a directive: a link to its alignment page, marked when an official
  // correlation table is shown next to it, when the act aligned was not notified for the directive
  // (baseline), or when there is nothing to align (no act).
  var CC_ORDER = { cz: 0, sk: 1, at: 2, de: 3 };
  function countryRows(d) {
    var rows = (d.alignments || []).map(function (a) {
      return { kind: a.status === "baseline" ? "baseline" : "ok", code: a.country_code || (a.href.split("_")[1] || ""), a: a,
        country: a.country, name: a.country_name };
    }).concat((d.not_aligned || []).map(function (n) {
      return { kind: "none", code: n.country_code, country: n.country, name: n.country_name, note: n.note };
    }));
    function ord(r) { return r.code in CC_ORDER ? CC_ORDER[r.code] : 9; }
    rows.sort(function (x, y) { return ord(x) - ord(y); });
    return rows;
  }

  // A "baseline" pair: the act aligned was not notified for the directive and is compared as existing
  // law. Whether the country notified other measures (e.g. Land laws) is said from the measure count,
  // never assumed: "not notified" would be false for a country with notified measures.
  function baselineText(name, act, measures) {
    var n = Number(measures);
    var head = (act || "The act aligned") + " was not notified for this directive";
    if (measures === null || measures === undefined || isNaN(n)) return head + "; it is compared as existing law.";
    if (n === 0) return head + " (" + name + " notified no measure); it is compared as existing law.";
    return head + " (" + name + " notified " + fmtInt(n) + (n === 1 ? " measure" : " measures") +
      ", none of them the act aligned here); it is compared as existing law.";
  }

  function countryChips(d) {
    var line = el("span", { className: "card-link chips" }, ["Provision level: "]);
    countryRows(d).forEach(function (r) {
      var code = ALPHA2[r.country] || r.code.toUpperCase();
      if (r.kind === "none") {
        // a generic span cannot be named with aria-label: the explanation is visually hidden text
        line.appendChild(el("span", { className: "chip none", title: r.name + ": no national act to align" + (r.note ? " (" + r.note + ")" : "") }, [
          code, el("span", { "aria-hidden": "true", text: " –" }), el("span", { className: "sr-only", text: " " + r.name + ": no act to align" })]));
        return;
      }
      var extra = (r.a.official ? ", official correlation table" : "") + (r.kind === "baseline" ? ", compared with existing law (act not notified for this directive)" : "");
      line.appendChild(el("a", { href: r.a.href, className: "chip" + (r.a.official ? " official" : "") + (r.kind === "baseline" ? " baseline" : ""),
        title: d.short + " × " + r.name + extra, "aria-label": code + " " + r.name + extra,
        text: code + (r.a.official ? " ✓" : "") + (r.kind === "baseline" ? " *" : "") }));
    });
    if (d.matrix_href) line.appendChild(el("a", { href: d.matrix_href, className: "chip matrix", text: "matrix →" }));
    return line;
  }

  // The directive page's "provision by provision" table: one row per country of the registry.
  function drawProvisionLevel(d, host) {
    if (!host) return;
    var rows = countryRows(d);
    if (!rows.length) {
      if (d.provision_level === false) {
        host.appendChild(el("p", { className: "small muted", text: "Act level only: OpenAcquis lists the notified measures for " +
          d.short + " but does not align it provision by provision yet." }));
      }
      return;
    }
    host.setAttribute("aria-labelledby", "prov-h");
    host.appendChild(el("h2", { id: "prov-h", text: "Provision by provision" }));
    host.appendChild(el("p", { className: "small muted" }, [
      "Each EU paragraph next to the most similar provision of the main national act (unreviewed machine suggestions)" +
      ", and next to the government's official correlation table where one is available.",
      d.matrix_href ? el("span", {}, [" All countries at once: ", el("a", { href: d.matrix_href, text: "article × country matrix" }), "."]) : null
    ]));
    var tbody = el("tbody");
    rows.forEach(function (r) {
      var state, links;
      if (r.kind === "none") {
        state = el("span", { className: "muted", text: "No act aligned" });
        links = el("span", { className: "small muted", text: r.note || "Nothing to align for this country." });
      } else {
        state = el("span", {}, [r.kind === "baseline"
          ? el("span", { className: "pill baseline", text: "existing law" })
          : el("span", { text: "Main act (registry)" }),
          r.a.official ? el("span", {}, [" ", el("span", { className: "pill official", text: "official table" })]) : null]);
        links = el("span", {}, [el("a", { href: r.a.href, text: "Alignment" }),
          r.a.review_href ? el("span", {}, [" · ", el("a", { href: r.a.review_href, text: "Review" })]) : null,
          r.kind === "baseline" || r.a.note ? el("div", { className: "small muted", text: r.kind === "baseline"
            ? baselineText(r.name, r.a.act, d.measures_available === false || d.error ? null : (d.counts || {})[r.country]) +
              (r.a.note ? " Note: " + r.a.note + "." : "")
            : r.a.note }) : null]);
      }
      tbody.appendChild(el("tr", {}, [
        el("th", { scope: "row", text: r.name }),
        el("td", { "data-label": "Main act", lang: LANGS[r.country] || null, text: r.kind === "none" ? "—" : (r.a.act || "") }),
        el("td", { "data-label": "Status" }, [state]),
        el("td", { "data-label": "Pages" }, [links])
      ]));
    });
    host.appendChild(el("div", { className: "table-wrap" }, [el("table", { className: "prov" }, [
      el("thead", {}, [el("tr", {}, [el("th", { scope: "col", text: "Country" }), el("th", { scope: "col", text: "Main act" }),
        el("th", { scope: "col", text: "Status" }), el("th", { scope: "col", text: "Pages" })])]),
      tbody
    ])]));
  }

  // ---------------------------------------------------------------- index page

  function renderIndex() {
    var cards = $("cards");
    getJSON("data/index.json").then(function (data) {
      $("built").textContent = setBuilt(data) + " Source: EU Publications Office (CELLAR).";
      pruneLinks(data);
      sampleNote(data, $("sample-note"));
      var total = Object.keys(data.member_states || ALPHA2).length;
      data.directives.forEach(function (d) {
        var href = "directive.html?celex=" + encodeURIComponent(d.celex);
        var stats;
        if (d.error) {
          stats = el("div", { className: "stats" }, [el("span", { text: "Data unavailable in this build." })]);
        } else if (d.measures_available === false) {
          stats = el("div", { className: "stats" }, [el("span", { text: "Notified measures: not in this sample build." })]);
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
        var aligned = [];
        if ((d.alignments || []).length || (d.not_aligned || []).length) {
          aligned.push(countryChips(d));
        } else if (d.provision_level === false) {
          aligned.push(el("span", { className: "small muted", text: "Act level only (notified measures)." }));
        }
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
    checkLinks();
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
    if (d.eurlex_nim_url && safeHref(d.eurlex_nim_url)) $("nim-link").href = d.eurlex_nim_url;
    else $("nim-link").removeAttribute("href");
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
    drawProvisionLevel(d, $("d-provision"));
    if (d.measures_available === false) {
      $("d-notes").appendChild(el("div", { className: "note", role: "note" }, [
        el("strong", { text: "Notified measures are not part of this build. " }),
        "This local sample build has no CELLAR data for " + d.short + ". See the ",
        el("a", { href: d.eurlex_nim_url, text: "EUR-Lex national transposition page" }), "."
      ]));
      return;
    }
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
            el("a", { href: d.eurlex_nim_url, title: "Open the EUR-Lex national transposition page", lang: m.title ? LANGS[m.country] : null, text: m.title || "(untitled measure)" }),
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
    other_act: { text: "table cites an act not searched", cls: "" },
    article_level: { text: "", cls: "" },
    no_table_entry: { text: "", cls: "" }
  };
  var SLUG = /^3\d{4}L\d{4}_[a-z]{2}$/;

  function fmtPct(x) { return Math.round(100 * (x || 0)) + "%"; }

  function actLabels(d) {
    var m = {};
    (d.national || []).forEach(function (n) { m[n.act_id] = n.label || n.act_id; });
    return m;
  }

  function nationalRef(nat, id, acts) {
    var n = nat[id] || { label: id, act: "" };
    var act = n.act ? (acts && acts[n.act]) || n.act : "";
    return n.label + (act ? " (" + act + ")" : "");
  }

  function natLink(nat, id, portal, lang) {
    var n = nat[id];
    var text = n ? n.label : id;
    return n && n.url ? el("a", { href: n.url, title: "Open in " + (portal || "the official portal"), lang: lang, text: text })
      : el("span", { lang: lang, text: text });
  }

  // lang: language of the items' labels and texts (the panel title stays English)
  function textPanel(title, cls, items, lang) {
    var box = el("div", { className: "tpanel " + cls }, [el("h4", { text: title })]);
    if (!items.length) box.appendChild(el("p", { className: "small muted", text: "None." }));
    items.forEach(function (it) {
      box.appendChild(el("div", { className: "titem" }, [
        el("div", { className: "thead" }, [
          it.url ? el("a", { href: it.url, lang: lang, text: it.label }) : el("strong", { lang: lang, text: it.label }),
          it.extra ? el("span", { className: "small muted", text: " " + it.extra }) : null
        ]),
        it.text ? el("p", { className: "ptext", lang: lang, text: it.text }) : el("p", { className: "ptext", text: "(text not available)" })
      ]));
    });
    return box;
  }

  // "Other countries" links for one directive, from index.json (quietly skipped if unavailable)
  function siblingLinks(host, celex, currentId, makeHref, label) {
    getJSON("data/index.json").then(function (idx) {
      var dir = (idx.directives || []).filter(function (x) { return x.celex === celex; })[0];
      if (!dir || !(dir.alignments || []).length) return;
      var p = el("p", { className: "small" }, [label || "Countries: "]);
      dir.alignments.forEach(function (a, i) {
        if (i) p.appendChild(document.createTextNode(" · "));
        var id = (a.href.split("id=")[1] || "");
        p.appendChild(id === currentId ? el("strong", { text: a.country_name })
          : el("a", { href: makeHref(id), text: a.country_name }));
      });
      if (dir.matrix_href) {
        p.appendChild(document.createTextNode(" · "));
        p.appendChild(el("a", { href: dir.matrix_href, text: "Article × country matrix" }));
      }
      host.appendChild(p);
    }).catch(function () { /* optional */ });
  }

  function renderAlignment() {
    var params = new URLSearchParams(location.search);
    var id = params.get("id") || "32022L2555_cz";
    if (!SLUG.test(id)) {
      $("a-title").textContent = "Alignment not found";
      showError($("a-notes"), new Error("Missing or invalid ?id= parameter (expected e.g. 32022L2555_cz)"));
      return;
    }
    checkLinks();
    getJSON("data/align/" + id + ".json").then(function (d) {
      drawAlignment(d, params);
    }).catch(function (err) {
      if (notBuilt(err)) {
        $("a-title").textContent = "Provision-level page not available";
        showMissing($("a-notes"), "this provision-level page (" + id + ")");
        return;
      }
      $("a-title").textContent = id;
      showError($("a-notes"), err);
    });
  }

  function drawAlignment(d, params) {
    var nat = d.national_texts || {};
    var acts = actLabels(d);
    var official = !!d.has_official;
    var actList = (d.national || []).map(function (n) { return n.label || n.act_id; }).join(", ");
    var lang = (d.eu && d.eu.language_name) || "";
    var euLang = (d.eu && d.eu.language) || null;
    var natLang = d.national_language || CC_LANG[d.country_code] || null;
    document.title = d.short + " × " + d.country_name + " · OpenAcquis";
    $("a-title").textContent = d.short + " × " + d.country_name + ", provision by provision";
    $("a-lede").textContent = "For each paragraph of " + d.short + (lang ? " (" + lang + " language version)" : "") +
      ": the most similar provision of " + actList + " found by the machine" +
      (official ? ", next to the provisions the government's official correlation table lists for it" : "") +
      ". Open a row to read the texts side by side. Below: gap and extra-rule candidates.";
    var back = $("a-back");
    back.href = "directive.html?celex=" + encodeURIComponent(d.celex);
    back.textContent = "← " + d.short + ": notified measures";
    var built = "Alignment computed " + fmtBuilt(d.built_at) + (d.mode === "live" ? "" : " (precomputed copy)") + ".";
    $("footer-built").textContent = built;
    var portal = d.portal || "the official portal";
    var fsrc = $("footer-sources");
    if (fsrc) fsrc.textContent = "National law: " + portal + "." + (official ? " Correlation table: official government document." : "");

    var notes = $("a-notes");
    siblingLinks(notes, d.celex, d.id, function (x) { return "alignment.html?id=" + x; });
    notes.appendChild(el("div", { className: "note", role: "note" }, [
      el("strong", { text: "Suggestions, not findings. " }),
      "A machine suggestion is the national provision whose wording is most similar; nobody has reviewed it. " +
      (official ? "A difference from the table does not mean either is wrong, and neither column says" : "It does not say") +
      " whether " + d.country_name + " transposed " + d.short + " correctly. " + built + " ",
      el("a", { href: "review.html?id=" + encodeURIComponent(d.id), text: "Help review these suggestions →" })
    ]));

    var reg = d.registry || {};
    if (reg.status === "baseline") {
      notes.appendChild(el("div", { className: "note", role: "note" }, [
        el("strong", { text: "Existing law, not a notified transposition. " }),
        actList + " was not notified by " + d.country_name + " for " + d.short + " (any measures it notified are not aligned " +
        "here), so this page compares the directive with existing law on the subject. A difference here is not a finding that " +
        "the directive is missing from " + d.country_name + "'s law." + (reg.note ? " Note: " + reg.note + "." : "")
      ]));
    } else if (reg.note) {
      notes.appendChild(el("p", { className: "small muted", text: "Note: " + reg.note + "." }));
    }

    var src = d.official_source || {};
    var os = $("a-official-src");
    if (official) {
      os.appendChild(document.createTextNode(src.label + ": the government's own list of which bill provision implements which article. "));
      if (src.url) os.appendChild(el("a", { href: src.url, text: "Table" }));
      if (src.history_url) { os.appendChild(document.createTextNode(" · ")); os.appendChild(el("a", { href: src.history_url, text: "Bill history" })); }
      os.appendChild(document.createTextNode(". It describes the bill; its numbering was mapped to the act as published."));
    } else {
      os.appendChild(document.createTextNode("No official correlation table is available for " + d.country_name +
        " yet, so the machine suggestions cannot be checked against one. Reviews can fill this gap."));
    }
    var msrc = $("a-machine-src");
    msrc.textContent = "";
    // the method string has no spaces: in <code> it wraps instead of widening the page on a phone
    [ "Top candidate from OpenAcquis matching (", el("code", { className: "method", text: d.method }), ", text similarity of the " +
      (lang || "national") + " wording; scores are not probabilities). Marked weak when below the link threshold of " +
      d.threshold + ". Unreviewed."
    ].forEach(function (x) { msrc.appendChild(typeof x === "string" ? document.createTextNode(x) : x); });

    var s = d.summary || {};
    var kpis = $("a-kpis");
    function kpi(v, l) { kpis.appendChild(el("div", { className: "kpi" }, [el("div", { className: "v", text: v }), el("div", { className: "l", text: l })])); }
    if (official) {
      kpi(s.rows_with_official + " / " + s.rows, "paragraphs with an official correlation");
      kpi(String(s.match), "top suggestion matches the table");
      kpi(String(s.same_section), "top suggestion in the same § as the table");
      kpi(fmtPct(s.recall_at_5), "table's provision among the top 5 suggestions");
    } else {
      kpi((s.eu_units_with_link || 0) + " / " + (s.eu_units || 0), "EU provisions with a suggestion above the threshold");
    }
    kpi(String(s.gap_candidates || 0), "gap candidates" + (s.gap_articles ? " in " + s.gap_articles + " articles" : ""));
    if (d.extra_note) kpi("–", "extra-rule list not reported (see below)");
    else kpi(String(s.extra_rule_sections || 0), "§§ with extra-rule candidates");

    var show = $("f-show");
    if (!official) {
      Array.prototype.slice.call(show.options).forEach(function (o) {
        if (o.value === "official" || o.value === "differs" || o.value === "none") show.removeChild(o);
      });
    }
    var sel = $("f-art");
    (d.articles || []).forEach(function (a) {
      sel.appendChild(el("option", { value: a.id, text: a.label + (a.heading ? " · " + a.heading : "") }));
    });
    // links from the matrix use the article ID of this language version, or a language-neutral key (art23)
    var wantArt = params.get("art") || "";
    (d.articles || []).forEach(function (a) { if (a.key && a.key === wantArt) wantArt = a.id; });
    var state = { art: wantArt, show: params.get("show") || "", q: params.get("q") || "" };
    sel.value = state.art; if (sel.value !== state.art) state.art = "";
    show.value = state.show; if (show.value !== state.show) state.show = "";
    var q = $("f-q"); q.value = state.q;
    sel.addEventListener("change", function () { state.art = sel.value; update(); });
    show.addEventListener("change", function () { state.show = show.value; update(); });
    q.addEventListener("input", function () { state.q = q.value; update(); });
    $("f-reset").addEventListener("click", function () {
      state.art = state.show = state.q = ""; sel.value = show.value = q.value = ""; update();
    });

    (d.articles || []).forEach(function (a) {
      a.rows.forEach(function (r) {
        var hay = [r.label, r.text].concat(r.official.map(function (o) { return nationalRef(nat, o, acts) + " " + ((nat[o] || {}).text || ""); }))
          .concat(r.machine.map(function (m) { return nationalRef(nat, m.id, acts) + " " + ((nat[m.id] || {}).text || ""); }));
        r._hay = hay.join(" ").toLowerCase();
      });
    });

    function keep(r) {
      var listed = r.official.length > 0 || r.agreement === "article_level";
      if (state.show === "official" && !listed) return false;
      if (state.show === "none" && listed) return false;
      if (state.show === "differs" && r.agreement !== "differs") return false;
      if (state.show === "gaps" && !(r.gaps || []).length) return false;
      var needle = state.q.trim().toLowerCase();
      return !needle || r._hay.indexOf(needle) >= 0;
    }

    function rowFor(a, r) {
      var tr = el("tr", {});
      var off = null;
      if (official) {
        off = el("td", { className: "off-cell" });
        if (r.official.length) {
          var list = el("ul", { className: "refs" });
          r.official.forEach(function (o) { list.appendChild(el("li", {}, [natLink(nat, o, portal, natLang)])); });
          off.appendChild(list);
          if (r.official_eu.length) {
            off.appendChild(el("span", { className: "small muted" }, ["for ", el("span", { lang: euLang, text: r.official_eu.map(function (x) {
              return x.indexOf(r.label + " ") === 0 ? x.slice(r.label.length + 1) : x;
            }).join(", ") })]));
          }
        } else if (r.agreement === "article_level") {
          off.appendChild(el("span", { className: "muted small", text: "listed for the whole article (above)" }));
        } else {
          off.appendChild(el("span", { className: "muted small", text: "not listed" }));
        }
      }
      var mach = el("td", { className: "mach-cell" });
      if (r.machine.length) {
        var m = r.machine[0];
        mach.appendChild(el("div", {}, [natLink(nat, m.id, portal, natLang)]));
        var ag = AGREE[r.agreement] || AGREE.no_table_entry;
        mach.appendChild(el("div", { className: "small" }, [
          el("span", { className: "muted", text: "score " + m.score.toFixed(2) + (m.above_threshold ? "" : " · weak") }),
          official && ag.text ? el("span", { className: "agree " + ag.cls, text: ag.text }) : null
        ]));
      } else {
        mach.appendChild(el("span", { className: "muted small", text: "no candidate" }));
      }
      if ((r.gaps || []).length) {
        mach.appendChild(el("div", { className: "small" }, [
          el("span", { className: "pill gap", text: "gap candidate" }), " ",
          el("span", { lang: euLang, text: r.gaps.map(function (x) { return x.indexOf(r.label + " ") === 0 ? x.slice(r.label.length + 1) : x; }).join(", ") })
        ]));
      } else if ((r.out_of_scope || []).length && !r.linked_units) {
        mach.appendChild(el("div", { className: "small muted", text: r.out_of_scope.indexOf("final_provisions") >= 0
          ? "final provision: no national transposition expected" : "addressed to the Commission or ENISA only" }));
      }
      var detailId = "tx-" + String(r.id).replace(/[^A-Za-z0-9_-]/g, "_");
      var btn = el("button", { type: "button", className: "btn small-btn", "aria-expanded": "false", "aria-controls": detailId, text: "Texts" });
      var label = el("td", { className: "prov", lang: euLang }, [
        r.url ? el("a", { href: r.url, title: "Open in EUR-Lex", text: r.label }) : el("span", { text: r.label })
      ]);
      tr.appendChild(label); if (off) tr.appendChild(off); tr.appendChild(mach); tr.appendChild(el("td", { className: "act" }, [btn]));
      var detail = el("tr", { className: "texts", id: detailId, hidden: true });
      var cell = el("td", { colspan: official ? "4" : "3" });
      detail.appendChild(cell);
      btn.setAttribute("aria-label", "Show texts for " + r.label);
      btn.addEventListener("click", function () {
        var open = btn.getAttribute("aria-expanded") === "true";
        if (!open && !cell.firstChild) {
          var panels = [textPanel(d.short + " " + r.label, "eu", [{ label: "EUR-Lex", url: r.url, text: r.text }], euLang)];
          if (official) {
            panels.push(textPanel("Official correlation table", "official", r.official.map(function (o) {
              var n = nat[o] || {};
              return { label: nationalRef(nat, o, acts), url: n.url, text: n.text,
                extra: n.not_searched ? "(in an act not searched here: no text shown)" : "" };
            }), natLang));
          }
          panels.push(textPanel("Machine suggestions", "machine", r.machine.map(function (x, i) {
            return { label: (i + 1) + ". " + nationalRef(nat, x.id, acts), url: (nat[x.id] || {}).url, text: (nat[x.id] || {}).text,
              extra: "score " + x.score.toFixed(2) + (x.above_threshold ? "" : " (weak)") };
          }), natLang));
          cell.appendChild(el("div", { className: "tgrid" + (official ? "" : " two") }, panels));
        }
        detail.hidden = open;
        btn.setAttribute("aria-expanded", String(!open));
        // the accessible name keeps the visible word (WCAG 2.5.3)
        btn.textContent = open ? "Texts" : "Hide";
        btn.setAttribute("aria-label", (open ? "Show texts for " : "Hide texts for ") + r.label);
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
        var hid = "art-" + String(a.id).replace(/[^A-Za-z0-9_-]/g, "_");
        var sec = el("section", { className: "art", "aria-labelledby": hid });
        sec.appendChild(el("h2", { id: hid, lang: euLang }, [
          a.url ? el("a", { href: a.url, title: "Open in EUR-Lex", text: a.label }) : el("span", { text: a.label }),
          a.heading ? el("span", { className: "art-h", text: " · " + a.heading }) : null
        ]));
        if (official && a.official && a.official.length) {
          var p = el("p", { className: "small" }, [el("span", { className: "pill official", text: "Official correlation table" }), " whole article: "]);
          a.official.forEach(function (o, i) { if (i) p.appendChild(document.createTextNode(", ")); p.appendChild(natLink(nat, o, portal, natLang)); });
          sec.appendChild(p);
        }
        var tbody = el("tbody");
        rows.forEach(function (r) { rowFor(a, r).forEach(function (x) { tbody.appendChild(x); }); });
        var heads = [el("th", { scope: "col", text: d.short + " (EUR-Lex)" })];
        if (official) heads.push(el("th", { scope: "col" }, [el("span", { className: "pill official", text: "Official correlation table" })]));
        heads.push(el("th", { scope: "col" }, [el("span", { className: "pill machine", text: "Machine suggestion" })]));
        heads.push(el("th", { scope: "col" }, [el("span", { className: "sr-only", text: "Texts" })]));
        sec.appendChild(el("div", { className: "table-wrap" }, [el("table", { className: "align-table" + (official ? "" : " no-official") }, [
          el("thead", {}, [el("tr", {}, heads)]),
          tbody
        ])]));
        frag.appendChild(sec);
      });
      if (!shown) frag.appendChild(el("p", { className: "muted", text: "No provisions match these filters." }));
      host.appendChild(frag);
      $("a-count").textContent = "Showing " + shown + " of " + total + " provisions.";
      syncURL();
    }

    drawLeftovers(d, nat, acts, portal, euLang, natLang);
    $("a-body").hidden = false;
    update();
  }

  // gap and extra-rule candidates below the table
  function drawLeftovers(d, nat, acts, portal, euLang, natLang) {
    var host = $("a-leftovers");
    if (!host) return;
    host.textContent = "";
    var gaps = d.gap_articles || [], extras = d.extra_rule_sections || [];
    host.appendChild(el("h2", { id: "gaps", text: "Gap candidates" }));
    host.appendChild(el("p", { className: "small muted", text: "EU provisions for which nothing similar was found in the acts searched " +
      "(best score below " + (d.gap_threshold != null ? d.gap_threshold : d.threshold) + "), after setting aside final provisions, " +
      "provisions addressed only to the Commission or ENISA, and parts of lists that are linked. A candidate may be transposed in another act, " +
      "in other words, or not at all: it is a reading list for reviewers, not a finding." }));
    if (!gaps.length) host.appendChild(el("p", { className: "muted", text: d.gap_note ? d.gap_note : "None at the current thresholds." }));
    gaps.forEach(function (g) {
      var p = el("p", {}, [
        el("a", { href: "alignment.html?id=" + encodeURIComponent(d.id) + "&art=" + encodeURIComponent(g.id), lang: euLang, text: g.label }),
        g.whole_article ? el("span", { className: "pill gap", text: "whole article" }) : null,
        ": ", el("span", { lang: euLang, text: g.units.map(function (u) { return u.label; }).join("; ") })
      ]);
      host.appendChild(p);
    });
    host.appendChild(el("h2", { id: "extras", text: "Extra-rule candidates" }));
    host.appendChild(el("p", { className: "small muted", text: "Provisions of the main act (" + ((d.national || [])[0] || {}).label +
      ") in §§ with no link to the directive whose wording matches nothing in it, after setting aside definitions, penalty lists, " +
      "transitional, repeal and entry-into-force clauses and amendments of other acts. Often ordinary national procedure or " +
      "organisation; possibly rules that go beyond the directive. Not a finding of gold-plating." }));
    if (!extras.length) {
      var sup = (d.summary || {}).extra_rule_candidates_suppressed;
      host.appendChild(el("p", { className: "muted", text: d.extra_note
        ? d.extra_note.charAt(0).toUpperCase() + d.extra_note.slice(1) + (sup && d.extra_note.indexOf(String(sup)) < 0 ? " (" + sup + (sup === 1 ? " provision" : " provisions") + " set aside)." : ".")
        : "None at the current thresholds." }));
    }
    extras.forEach(function (x) {
      var det = el("details", { className: "extra" });
      det.appendChild(el("summary", {}, [
        el("strong", { lang: natLang, text: x.label }), x.heading ? el("span", { lang: natLang, text: " · " + x.heading }) : "",
        el("span", { className: "small muted", text: " (" + x.units.length + (x.units.length === 1 ? " provision" : " provisions") +
          (x.whole_section ? ", whole section" : "") + ")" })
      ]));
      x.units.forEach(function (u) {
        var n = nat[u] || {};
        det.appendChild(el("div", { className: "titem" }, [
          el("div", { className: "thead" }, [natLink(nat, u, portal, natLang),
            n.closest_eu ? el("span", { className: "small muted" }, [" closest in " + d.short + ": ", el("span", { lang: euLang, text: n.closest_eu }),
              (n.closest_score != null ? " (score " + Number(n.closest_score).toFixed(2) + ")" : "")]) : null]),
          el("p", { className: "ptext", lang: natLang, text: n.text || "" })
        ]));
      });
      host.appendChild(det);
    });
  }

  // ------------------------------------------------------------ matrix page

  function renderMatrix() {
    var params = new URLSearchParams(location.search);
    var celex = (params.get("celex") || "32022L2555").toUpperCase();
    if (!/^3\d{4}L\d{4}$/.test(celex)) {
      $("m-title").textContent = "Matrix not found";
      showError($("m-notes"), new Error("Missing or invalid ?celex= parameter (expected e.g. 32022L2555)"));
      return;
    }
    checkLinks();
    getJSON("data/matrix/" + celex + ".json").then(function (m) { drawMatrix(m, params); }).catch(function (err) {
      if (notBuilt(err)) {
        $("m-title").textContent = "Matrix not available";
        showMissing($("m-notes"), "the article × country matrix of " + celex);
        return;
      }
      $("m-title").textContent = celex;
      showError($("m-notes"), err);
    });
  }

  function drawMatrix(m, params) {
    document.title = m.short + ": article × country · OpenAcquis";
    $("m-title").textContent = m.short + ": article × country";
    $("m-back").href = "directive.html?celex=" + encodeURIComponent(m.celex);
    $("m-back").textContent = "← " + m.short + ": notified measures";
    $("footer-built").textContent = "Matrix built " + fmtBuilt(m.built_at) + ".";
    var cols = m.countries || [];
    var withTable = cols.filter(function (c) { return c.has_official; }).map(function (c) { return c.name; });
    $("m-official").textContent = withTable.length
      ? "Official correlation tables: " + withTable.join(", ") + "; the other countries have machine suggestions only."
      : "No official correlation table is available for this directive yet; every cell is a machine suggestion.";
    (m.not_aligned || []).forEach(function (n) {
      $("m-notes").appendChild(el("p", { className: "small muted", text: n.name + ": no national act aligned" + (n.note ? " (" + n.note + ")" : "") + "." }));
    });
    cols.forEach(function (c) {
      if (c.status === "baseline") {
        $("m-notes").appendChild(el("p", { className: "small muted", text: c.name + ": " + c.act + " was not notified for " + m.short +
          "; the column (marked “existing law”) compares the directive with existing law, not with a transposition." }));
      }
    });
    $("m-lede").textContent = "Each row is an article of " + m.short + "; each column a country. A cell shows the national section the " +
      "machine most often suggests for the article's paragraphs, how many of the article's provisions have a suggestion, gap " +
      "candidates, and, where a government published one, the sections its official correlation table lists. Click a cell to " +
      "read the texts side by side.";
    var cap = $("m-cols");
    cols.forEach(function (c, i) {
      if (i) cap.appendChild(document.createTextNode(" · "));
      cap.appendChild(el("span", {}, [el("a", { href: "alignment.html?id=" + encodeURIComponent(c.id), text: c.name }),
        el("span", { className: "muted", text: " (" + c.act + ", " + (c.mode === "live" ? "computed " : "precomputed ") +
          fmtBuilt(c.built_at).replace(/,? \d\d:\d\d UTC$/, "") + ")" })]));
    });
    var state = { q: params.get("q") || "", gaps: params.get("gaps") === "1" };
    var q = $("m-q"); q.value = state.q;
    var g = $("m-gaps"); g.checked = state.gaps;
    q.addEventListener("input", function () { state.q = q.value; update(); });
    g.addEventListener("change", function () { state.gaps = g.checked; update(); });

    function cell(c, country) {
      var td = el("td", { className: "mcell" + (country.status === "baseline" ? " baseline" : "") });
      if (!c) { td.appendChild(el("span", { className: "muted", text: "—" })); return td; }
      var lang = CC_LANG[country.code] || null;
      var top = c.top ? el("a", { href: c.href, className: "mtop", lang: lang, text: c.top,
        title: "Top machine suggestion; open " + country.name + " for this article" }) : el("a", { href: c.href, className: "muted", text: "no suggestion" });
      // visible labels (no hover on touch screens): which value is the machine's, which is a score
      td.appendChild(el("div", {}, [c.top ? el("span", { className: "pill machine", text: "machine" }) : null, c.top ? " " : null, top,
        c.top_score != null ? el("span", { className: "small muted", text: " score " + c.top_score.toFixed(2) }) : null]));
      if (c.others && c.others.length) td.appendChild(el("div", { className: "small muted" }, ["also ", el("span", { lang: lang, text: c.others.join(", ") })]));
      if (c.official) {
        td.appendChild(el("div", { className: "small" }, c.official.length
          ? [el("span", { className: "pill official", text: "table" }), " ", el("span", { lang: lang, text: c.official.slice(0, 4).join(", ") + (c.official.length > 4 ? " …" : "") })]
          : [el("span", { className: "muted", text: "table: not listed" })]));
      }
      td.appendChild(el("div", { className: "small muted", text: c.linked_units + "/" + c.units + " linked" }));
      if (c.gaps) td.appendChild(el("div", { className: "small" }, [el("span", { className: "pill gap", text: c.gaps + " gap" + (c.gaps === 1 ? "" : "s") })]));
      return td;
    }

    function update() {
      var tbody = $("m-rows");
      tbody.textContent = "";
      var needle = state.q.trim().toLowerCase();
      var shown = 0;
      (m.articles || []).forEach(function (a) {
        if (needle && (a.label + " " + a.heading).toLowerCase().indexOf(needle) < 0) return;
        if (state.gaps && !cols.some(function (c) { return (a.cells[c.code] || {}).gaps; })) return;
        shown++;
        var tr = el("tr", {}, [el("th", { scope: "row", className: "mart" }, [
          el("span", { className: "mlabel", text: a.label }), a.heading ? el("span", { className: "small muted mhead", text: a.heading }) : null
        ])]);
        cols.forEach(function (c) { tr.appendChild(cell(a.cells[c.code], c)); });
        tbody.appendChild(tr);
      });
      $("m-count").textContent = "Showing " + shown + " of " + (m.articles || []).length + " articles and annexes.";
      var p = new URLSearchParams();
      p.set("celex", m.celex);
      if (state.q) p.set("q", state.q);
      if (state.gaps) p.set("gaps", "1");
      try { history.replaceState(null, "", "?" + p.toString()); } catch (e) { /* ignore */ }
    }
    var head = $("m-head");
    head.appendChild(el("th", { scope: "col", className: "mart", text: "Article" }));
    cols.forEach(function (c) {
      head.appendChild(el("th", { scope: "col", className: c.status === "baseline" ? "baseline" : null }, [
        el("a", { href: "alignment.html?id=" + encodeURIComponent(c.id), text: c.name }),
        el("span", { className: "small muted", text: " " + c.act }),
        c.status === "baseline" ? el("span", {}, [" ", el("span", { className: "pill baseline", text: "existing law" }),
          el("span", { className: "sr-only", text: " (not notified for this directive)" })]) : null]));
    });
    $("m-body").hidden = false;
    update();
  }

  // ------------------------------------------------------------ review page

  var REPO = "https://github.com/JerryWoof/OpenAcquis";
  var HANDLE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
  var KIND_TEXT = {
    link: "Suggested link: does this national provision implement this EU provision?",
    gap: "Gap candidate: is it right that no provision of the acts searched implements this EU provision?",
    extra_rule: "Extra-rule candidate: is it right that this national provision implements nothing in the directive?"
  };

  // the question for a baseline pair: the act was not notified, so a link means "covers", not "implements"
  var KIND_TEXT_BASELINE = {
    link: "Suggested link: does this provision of existing national law cover this EU provision? (The act was not notified as a transposition.)",
    gap: KIND_TEXT.gap,
    extra_rule: "Extra-rule candidate: is it right that this provision of existing national law corresponds to nothing in the directive?"
  };

  // review article filter: art1 < art2 < art10 < art10a < annexes (anx_i, anx_ii, ...) in numeric order
  var ROMAN = { i: 1, v: 5, x: 10, l: 50, c: 100 };
  function romanVal(r) {
    var t = 0, s = String(r).toLowerCase();
    for (var i = 0; i < s.length; i++) {
      var v = ROMAN[s[i]] || 0, nx = ROMAN[s[i + 1]] || 0;
      t += v < nx ? -v : v;
    }
    return t;
  }
  function artSortKey(k) {
    var m = /^art(\d+)(.*)$/.exec(k);
    if (m) return [0, Number(m[1]), m[2]];
    m = /^anx_([a-z]+)(.*)$/.exec(k);
    if (m) return [1, romanVal(m[1]) || 0, m[2]];
    return [2, 0, k];
  }
  function sortArts(keys) {
    return keys.slice().sort(function (a, b) {
      var x = artSortKey(a), y = artSortKey(b);
      for (var i = 0; i < 3; i++) { if (x[i] < y[i]) return -1; if (x[i] > y[i]) return 1; }
      return 0;
    });
  }
  function artLabel(k) {
    if (k.indexOf("art") === 0) return "Art. " + k.slice(3);
    if (k.indexOf("anx_") === 0) return "Annex " + k.slice(4).toUpperCase();
    return k;
  }

  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, value);
    } catch (e) { /* private mode: no persistence */ }
    return null;
  }

  function reviewRecord(d, sg, form) {
    var corrected = form.verdict === "correct"
      ? form.corrected.split(/[\s,;]+/).filter(Boolean) : [];
    var machine = { label: sg.kind === "link" ? "candidate" : sg.kind + "_candidate", method: d.method, generator: d.generator };
    if (sg.score != null) machine.score = sg.score;
    if (sg.rank != null) machine.rank = sg.rank;
    return {
      schema: 1, suggestion_id: sg.id, directive: d.celex, country: d.country_code, kind: sg.kind,
      eu_id: sg.eu_id, national_id: sg.national_id, machine: machine, verdict: form.verdict,
      corrected_ids: corrected, reviewer: form.reviewer, date: new Date().toISOString().slice(0, 10), note: form.note
    };
  }

  var MAX_CORRECTED = 20; // openacquis.review.MAX_CORRECTED
  var MAX_NOTE = 2000; // openacquis.review.MAX_NOTE
  // GitHub answers HTTP 414 somewhere between 6k and 12k characters; stay well below.
  var ISSUE_URL_MAX = 7500;
  var PATH = "[a-z0-9_]+(?:\\.[a-z0-9_]+)*"; // dotted path, as _EU_ID / _NAT_ID in openacquis.review

  // mirrors openacquis.review.validate_record for the fields a reviewer types
  function checkRecord(rec) {
    var errs = [];
    if (["confirm", "reject", "correct"].indexOf(rec.verdict) < 0) errs.push("Pick a verdict.");
    if (!HANDLE.test(rec.reviewer || "")) errs.push("Enter your GitHub user name (letters, digits, hyphens).");
    if (rec.verdict === "correct") {
      if (!rec.corrected_ids.length) errs.push("A correction needs the right provision IDs.");
      if (rec.corrected_ids.length > MAX_CORRECTED) errs.push("At most " + MAX_CORRECTED + " corrected provision IDs.");
      // EU IDs must belong to this directive; national IDs to this country
      var want = rec.kind === "extra_rule" ? new RegExp("^eu:" + rec.directive + ":[a-z]{2}:" + PATH + "$")
        : new RegExp("^" + rec.country + ":[A-Za-z0-9_./-]{1,40}:" + PATH + "$");
      rec.corrected_ids.forEach(function (x) { if (!want.test(x)) errs.push("Not a provision ID of the right kind: " + x); });
    }
    if ((rec.note || "").length > MAX_NOTE) errs.push("The note is longer than " + MAX_NOTE + " characters.");
    return errs;
  }

  function issueURL(rec) {
    var p = new URLSearchParams();
    p.set("template", "review.yml");
    p.set("title", "Review: " + rec.verdict + " " + rec.suggestion_id);
    p.set("record", JSON.stringify(rec, null, 1));
    return REPO + "/issues/new?" + p.toString();
  }

  // the prefilled issue URL, or null when it is too long for GitHub (long or heavily escaped note)
  function issueLink(rec) {
    var url = issueURL(rec);
    return url.length > ISSUE_URL_MAX ? null : url;
  }

  function renderReview() {
    var params = new URLSearchParams(location.search);
    var id = params.get("id") || "32022L2555_cz";
    if (!SLUG.test(id)) {
      $("r-title").textContent = "Review queue not found";
      showError($("r-notes"), new Error("Missing or invalid ?id= parameter (expected e.g. 32022L2555_cz)"));
      return;
    }
    checkLinks();
    getJSON("data/review/" + id + ".json").then(function (d) { drawReview(d, params); }).catch(function (err) {
      if (notBuilt(err)) {
        $("r-title").textContent = "Review queue not available";
        showMissing($("r-notes"), "this review queue (" + id + ")");
        return;
      }
      $("r-title").textContent = id;
      showError($("r-notes"), err);
    });
  }

  function drawReview(d, params) {
    document.title = "Review: " + d.short + " × " + d.country_name + " · OpenAcquis";
    $("r-title").textContent = "Review: " + d.short + " × " + d.country_name;
    $("footer-built").textContent = "Suggestions computed " + fmtBuilt(d.built_at) + (d.mode === "live" ? "" : " (precomputed copy)") + ".";
    siblingLinks($("r-notes"), d.celex, d.id, function (x) { return "review.html?id=" + x; }, "Review another country: ");
    $("r-back").href = "alignment.html?id=" + encodeURIComponent(d.id);
    $("r-back").textContent = "← " + d.short + " × " + d.country_name + ", provision by provision";
    var reg = d.registry || {};
    var baseline = reg.status === "baseline";
    if (baseline) {
      $("r-notes").appendChild(el("div", { className: "note", role: "note" }, [
        el("strong", { text: "Existing law, not a notified transposition. " }),
        "The national act here was not notified by " + d.country_name + " for " + d.short + "; it is compared as existing law. " +
        "Judge whether a provision covers the EU provision, not whether it transposes it." + (reg.note ? " Note: " + reg.note + "." : "")
      ]));
    }
    var eu = d.eu_texts || {}, nat = d.national_texts || {};
    var natLang = d.national_language || CC_LANG[d.country_code] || null;
    function euLangFor(sg) {
      return d.eu_language || euLangOf(sg.eu_id) || euLangOf((sg.alternatives || [])[0]) || null;
    }
    var reviewed = d.reviewed || {};
    var batchKey = "openacquis-review-batch-" + d.id;
    var batch = [];
    try { batch = JSON.parse(store(batchKey) || "[]") || []; } catch (e) { batch = []; }
    var inBatch = function (sid) { return batch.some(function (r) { return r.suggestion_id === sid; }); };

    var state = { kind: params.get("kind") || "", art: params.get("art") || "", all: params.get("all") === "1", i: 0 };
    var kinds = $("r-kind"), art = $("r-art"), all = $("r-all");
    kinds.value = state.kind; if (kinds.value !== state.kind) state.kind = "";
    all.checked = state.all;
    var arts = [];
    d.suggestions.forEach(function (s) {
      var key = s.eu_id ? s.eu_id.split(":")[3].split(".")[0] : "";
      s._art = key;
      if (key && arts.indexOf(key) < 0) arts.push(key);
    });
    sortArts(arts).forEach(function (k) {
      art.appendChild(el("option", { value: k, text: artLabel(k) }));
    });
    art.value = state.art; if (art.value !== state.art) state.art = "";

    var queue = [];
    function rebuild() {
      queue = d.suggestions.filter(function (s) {
        if (state.kind && s.kind !== state.kind) return false;
        if (state.art && s._art !== state.art) return false;
        if (!state.all && (reviewed[s.id] || inBatch(s.id))) return false;
        return true;
      });
      if (state.i >= queue.length) state.i = Math.max(0, queue.length - 1);
      var p = new URLSearchParams();
      p.set("id", d.id);
      if (state.kind) p.set("kind", state.kind);
      if (state.art) p.set("art", state.art);
      if (state.all) p.set("all", "1");
      try { history.replaceState(null, "", "?" + p.toString()); } catch (e) { /* ignore */ }
      var nrev = Object.keys(reviewed).length;
      $("r-progress").textContent = d.suggestions.length + " suggestions; " + nrev + " reviewed in the dataset, " + batch.length +
        " in your batch; " + queue.length + " in this queue.";
      show();
    }
    kinds.addEventListener("change", function () { state.kind = kinds.value; state.i = 0; rebuild(); });
    art.addEventListener("change", function () { state.art = art.value; state.i = 0; rebuild(); });
    all.addEventListener("change", function () { state.all = all.checked; state.i = 0; rebuild(); });

    var handle = $("r-handle");
    handle.value = store("openacquis-reviewer") || "";
    handle.addEventListener("change", function () { store("openacquis-reviewer", handle.value.trim()); });

    function form() {
      var v = document.querySelector("input[name=verdict]:checked");
      return { verdict: v ? v.value : "", corrected: $("r-corrected").value, reviewer: handle.value.trim(), note: $("r-note").value };
    }

    function panelFor(title, cls, idmap, ids, lang) {
      return textPanel(title, cls, ids.filter(Boolean).map(function (x) {
        var t = idmap[x] || {};
        return { label: (t.label || x), url: t.url, text: t.text };
      }), lang);
    }

    function show() {
      var host = $("r-card");
      host.textContent = "";
      $("r-issue").removeAttribute("href");
      $("r-json").textContent = "";
      $("r-errors").textContent = "";
      Array.prototype.forEach.call(document.querySelectorAll("input[name=verdict]"), function (x) { x.checked = false; });
      $("r-corrected").value = ""; $("r-note").value = "";
      if (!queue.length) {
        $("r-status").textContent = "Nothing left to review with these filters.";
        host.appendChild(el("p", { className: "muted", text: "Nothing left to review with these filters. Thank you!" }));
        $("r-form").hidden = true;
        return;
      }
      $("r-form").hidden = false;
      var sg = queue[state.i];
      var st = reviewed[sg.id];
      var euL = euLangFor(sg);
      // short announcement for screen readers (the card itself is not a live region: it is long)
      var what = [sg.eu_id ? (eu[sg.eu_id] || {}).label || sg.eu_id : null, sg.national_id ? (nat[sg.national_id] || {}).label || sg.national_id : null]
        .filter(Boolean).join(" → ");
      $("r-status").textContent = "Suggestion " + (state.i + 1) + " of " + queue.length + ": " +
        { link: "link", gap: "gap candidate", extra_rule: "extra-rule candidate" }[sg.kind] + (what ? ", " + what : "") + ".";
      host.appendChild(el("p", { className: "small muted" }, [
        "Suggestion " + (state.i + 1) + " of " + queue.length + " · ",
        el("code", { text: sg.id }),
        st ? el("span", { className: "pill reviewed", text: "already " + st.status + " by " + st.reviewers.join(", ") }) : null
      ]));
      host.appendChild(el("h2", { className: "rq", tabindex: "-1", text: (baseline ? KIND_TEXT_BASELINE : KIND_TEXT)[sg.kind] }));
      if (sg.score != null) host.appendChild(el("p", { className: "small muted" }, ["Machine score " + Number(sg.score).toFixed(3) + " (",
        el("code", { className: "method", text: d.method }), "; threshold " + d.threshold + "). Scores are similarity, not probability."]));
      var panels = [];
      // all panels hold machine output except the EU text: alternatives are lower-ranked machine candidates
      if (sg.kind === "extra_rule") {
        panels.push(panelFor("National provision", "machine", nat, [sg.national_id], natLang));
        panels.push(panelFor("Closest EU provision (below the threshold)", "eu", eu, sg.alternatives, euL));
      } else {
        panels.push(panelFor(d.short + " (EUR-Lex)", "eu", eu, [sg.eu_id], euL));
        if (sg.kind === "link") panels.push(panelFor("Suggested national provision", "machine", nat, [sg.national_id], natLang));
        panels.push(panelFor(sg.kind === "link" ? "Other candidates" : "Closest national provisions (below the threshold)",
          "machine alt", nat, sg.alternatives, natLang));
      }
      host.appendChild(el("div", { className: "tgrid" }, panels));
      var dl = $("r-ids");
      dl.textContent = "";
      (sg.kind === "extra_rule" ? sg.alternatives : sg.alternatives.concat(sg.national_id ? [sg.national_id] : [])).forEach(function (x) {
        if (x) dl.appendChild(el("option", { value: x }));
      });
      $("r-corrected-hint").textContent = sg.kind === "extra_rule"
        ? "EU provision IDs it implements, e.g. " + (sg.alternatives[0] || "eu:32022L2555:cs:art21.par2.pt_a")
        : "National provision IDs that implement it, e.g. " + (sg.alternatives[0] || d.country_code + ":…:par12.odst1");
    }

    function current() {
      if (!queue.length) return null;
      var rec = reviewRecord(d, queue[state.i], form());
      var errs = checkRecord(rec);
      $("r-errors").textContent = errs.join(" ");
      $("r-json").textContent = JSON.stringify(rec, null, 1);
      if (errs.length) { $("r-issue").removeAttribute("href"); return null; }
      var url = issueLink(rec);
      if (url) $("r-issue").href = url;
      else {
        // the record is valid; only the prefilled link is too long for GitHub (HTTP 414)
        $("r-issue").removeAttribute("href");
        $("r-errors").textContent = "The note is too long for a prefilled GitHub issue. Shorten it, or add the record " +
          "to your batch and send the downloaded file instead.";
      }
      return rec;
    }
    ["change", "input"].forEach(function (ev) {
      $("r-form").addEventListener(ev, function () { current(); });
    });

    function refreshBatch() {
      store(batchKey, JSON.stringify(batch));
      $("r-batch-n").textContent = batch.length + (batch.length === 1 ? " record" : " records");
      $("r-download").disabled = !batch.length;
      $("r-clear").disabled = !batch.length;
    }
    $("r-add").addEventListener("click", function () {
      var rec = current();
      if (!rec) return;
      batch = batch.filter(function (r) { return r.suggestion_id !== rec.suggestion_id; }).concat([rec]);
      refreshBatch();
      // with "Include reviewed" the item stays in the queue: move on to the next one
      if (state.all && queue.length) state.i = (state.i + 1) % queue.length;
      rebuild();
    });
    $("r-skip").addEventListener("click", function () { if (queue.length) { state.i = (state.i + 1) % queue.length; show(); } });
    $("r-prev").addEventListener("click", function () { if (queue.length) { state.i = (state.i - 1 + queue.length) % queue.length; show(); } });
    $("r-download").addEventListener("click", function () {
      var text = batch.map(function (r) { return JSON.stringify(r); }).join("\n") + "\n";
      var url = URL.createObjectURL(new Blob([text], { type: "application/jsonl" }));
      var a = document.createElement("a"); // a blob: URL we just made (el() only allows http(s) links)
      a.href = url;
      a.download = d.id + "_reviews.jsonl";
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    });
    $("r-clear").addEventListener("click", function () { batch = []; refreshBatch(); rebuild(); });
    $("r-body").hidden = false;
    refreshBatch();
    rebuild();
  }

  // ------------------------------------------------------------ data & API page

  // links to api/v1/ files become plain text when the API is not part of this build
  function pruneApiLinks() {
    Array.prototype.forEach.call(document.querySelectorAll("a[href^='api/']"), function (a) {
      a.parentNode.replaceChild(el("span", { className: "muted", title: "Not part of this build", text: a.textContent }), a);
    });
  }

  function renderData() {
    var names = {}; // "32019L1937_cz" -> { short, country, href }
    var idxP = getJSON("data/index.json").then(function (idx) {
      pruneLinks(idx);
      $("x-built").textContent = setBuilt(idx);
      (idx.directives || []).forEach(function (d) {
        (d.alignments || []).forEach(function (a) {
          names[d.celex + "_" + a.country_code] = { short: d.short, country: a.country_name, href: a.href };
        });
      });
    }).catch(function () { $("x-built").textContent = ""; });
    // dataset counts per pair from the API index (optional: a build without the API just shows the links)
    Promise.all([getJSON("api/v1/index.json"), idxP]).then(function (res) {
      var api = res[0];
      var d = api.data || {}, c = d.counts || {};
      var kpis = $("x-kpis");
      function kpi(v, l) { kpis.appendChild(el("div", { className: "kpi" }, [el("div", { className: "v", text: v }), el("div", { className: "l", text: l })])); }
      if (c.links != null) kpi(fmtInt(c.links), "links (official tables + reviews)");
      if (c.reviews != null) kpi(fmtInt(c.reviews), "human review records");
      if (c.alignment_pairs != null) kpi(fmtInt(c.alignment_pairs), "directive × country alignments");
      if (c.directives != null) kpi(fmtInt(c.directives), "directives");
      var tb = $("x-pairs");
      tb.textContent = "";
      (d.pairs || []).forEach(function (p) {
        var n = names[p.directive + "_" + p.country] || {};
        var label = (n.short || p.directive) + " × " + (n.country || String(p.country).toUpperCase());
        var links = typeof p.links === "string" ? "api/v1/" + p.links : null;
        tb.appendChild(el("tr", {}, [
          el("td", {}, [n.href ? el("a", { href: n.href, text: label }) : label]),
          el("td", {}, [links ? el("a", { href: links, title: "links.json of " + label, text: fmtInt(p.links_count) }) : fmtInt(p.links_count)])
        ]));
      });
      if (!tb.firstChild) tb.appendChild(el("tr", {}, [el("td", { colspan: "2", className: "muted", text: "No links in this build." })]));
      var meta = $("x-api");
      [["API version", d.api_version || api.api_version], ["Dataset version", api.dataset_version], ["Built", api.built_at ? fmtBuilt(api.built_at) : null],
        ["Mode", api.mode]].forEach(function (kv) {
        if (kv[1]) meta.appendChild(el("div", {}, [el("dt", { text: kv[0] }), el("dd", { text: String(kv[1]) })]));
      });
    }).catch(function (err) {
      var tb = $("x-pairs");
      tb.textContent = "";
      if (notBuilt(err)) pruneApiLinks();
      tb.appendChild(el("tr", {}, [el("td", { colspan: "2", className: "muted", text: notBuilt(err)
        ? "The API is not part of this build (run python scripts/build_api.py)." : "Counts unavailable." })]));
    });
  }

  window.OpenAcquis = {
    renderData: renderData,
    renderIndex: renderIndex, renderDirective: renderDirective, renderAlignment: renderAlignment,
    renderMatrix: renderMatrix, renderReview: renderReview, _binOf: binOf, _checkRecord: checkRecord,
    _issueURL: issueURL, _issueLink: issueLink, _baselineText: baselineText, _sortArts: sortArts, _artLabel: artLabel
  };
})();
