# OpenAcquis

**Open, AI-assisted map of how EU directives are written into national law: provision by provision, country by country.**
Spot gaps, gold-plating and national choices, with links to the source texts.

> Status: **v1.0 preview (v0.5)**. For **12 high-profile directives** (NIS2, CER, Accessibility Act, Whistleblower,
> Pillar Two, Copyright DSM, Representative Actions, Pay Transparency, Minimum Wages, Single-Use Plastics, EECC,
> Women on Boards) the tool lists the national laws each member state notified, fetches the directive and the
> main national act, splits both into citable provisions with stable IDs, and proposes provision-level links in
> **Czechia, Slovakia, Austria and Germany**, with gap and extra-rule candidates.
>
> **How good is it?** Seven official correlation tables (4 Czech, 3 Slovak) serve as tests. The matcher was
> tuned on one of them (NIS2 × CZ) only. On the six held-out tables, a provision the government lists is
> among the top 5 machine candidates for 60–90 % of EU provisions, and the right § for 77–96 %
> ([docs/evaluation.md](docs/evaluation.md#v05-held-out-generalisation-six-more-official-tables)). Austria and
> Germany have no table to check against. The LLM judge has been **run once, for NIS2 × Czechia**: Claude Opus 5.5
> judged the top 3 candidates per paragraph (606 pairs) inside Claude Code, with no API key. Pairs it calls related agree with
> the official table 76 % of the time, against 45 % for the machine's top suggestion, but it can only pick from those 3
> candidates ([details and caveats](docs/evaluation.md#llm-judge-nis2--cz)). There are **no human reviews yet**: everything
> at provision level, judge labels included, is an unreviewed machine suggestion.
>
> Website: **https://jerrywoof.github.io/OpenAcquis/**, rebuilt weekly by GitHub Actions. You can see:
> - notified national measures for 14 directives, with a map and a searchable table;
> - for each of the 12 registry directives, a page per country, provision by provision, with the official
>   correlation table alongside where one exists (e.g. [CER × Slovakia](https://jerrywoof.github.io/OpenAcquis/alignment.html?id=32022L2557_sk)),
>   an [article × country matrix](https://jerrywoof.github.io/OpenAcquis/matrix.html?celex=32022L2555), and
>   "not notified" or "nothing to align" where that is the case;
> - a [review page](https://jerrywoof.github.io/OpenAcquis/review.html?id=32022L2555_sk) for every pair;
> - [data and API](https://jerrywoof.github.io/OpenAcquis/data.html): the open dataset (2,736 links from official
>   tables), a static JSON API and an Atom feed of changes to the watched laws.

---

## Why

An EU directive sets goals; each member state writes its own law to reach them. The EU records *which* national laws a country says implement a directive, but nobody publishes *which section implements which article*, or where a country left something out or added extra rules ("gold-plating").

That knowledge is currently sold by law firms one directive at a time. OpenAcquis aims to make it an open public good.

Example: for the NIS2 cybersecurity directive, the EU database lists **304 national measures from 25 member states**. Czechia alone notified 83, from the new Cybersecurity Act 264/2025 Sb. to amendments of the Criminal Code. Which of them implements Article 23 (incident reporting)? Today you have to read them all.

## What it will look like

| NIS2 article | 🇨🇿 CZ | 🇸🇰 SK | 🇦🇹 AT | 🇩🇪 DE |
|---|---|---|---|---|
| Art. 23(4)(a): early warning within 24 h | ✅ same | ⚠️ stricter | 🔍 review | ❌ not found |

Every cell links to the exact text on both sides. Every AI suggestion is labelled as a suggestion until a human reviews it. *(Illustrative; not real results. The real, machine-only first version is the [NIS2 matrix](https://jerrywoof.github.io/OpenAcquis/matrix.html?celex=32022L2555).)*

## How it works

```
EUR-Lex / CELLAR ──► which national acts implement directive X   (v0.1)
        │                └─► mapped to fetchable acts per country     (v0.4: CZ, SK, AT, DE)
        ▼
Directive text (24 languages) + national law texts (national portals)   (v0.2 CZ; v0.4 SK, AT, DE)
        │
        ▼
Split into provisions ──► candidate matching ──► judge per pair (LLM run: NIS2 × CZ only)
        │                  lexical / hybrid / embeddings   (same / partial / different value / stricter / option chosen)
        ▼
Leftovers: gap candidates (EU provisions with nothing similar) and extra-rule candidates
(national rules matching nothing), filtered by scope, margin and structure    (v0.4)
        │
        ▼
Evaluation against official correlation tables (7: CZ and SK; 6 held out)
        │
        ▼
Human review (review page → GitHub) ──► open dataset (CC-BY) ──► website, static API, change alerts (v0.5)
```

More detail: [docs/how-it-works.md](docs/how-it-works.md) · Evaluation: [docs/evaluation.md](docs/evaluation.md) · Review: [docs/review.md](docs/review.md) · Dataset: [docs/dataset.md](docs/dataset.md) · Judge: [docs/judge.md](docs/judge.md) · API: [docs/api.md](docs/api.md) · Alerts: [docs/alerts.md](docs/alerts.md) · Data: [docs/data-sources.md](docs/data-sources.md) · Provision IDs: [docs/provisions.md](docs/provisions.md) · Website: [docs/website.md](docs/website.md) · All docs: [docs/README.md](docs/README.md) · Plan: [ROADMAP.md](ROADMAP.md) · Changes: [CHANGELOG.md](CHANGELOG.md)

## Quickstart

Requires Python 3.10+. No API keys; no dependencies. Optional extras:

- `pip install -e '.[embeddings]'`: dense multilingual embeddings (sentence-transformers); `align` then uses
  the `hybrid` matcher.
- `pip install -e '.[llm]'`: LLM judge (Anthropic SDK and credentials, e.g. `ANTHROPIC_API_KEY`).
- `pip install -e '.[tls]'`: certifi, if your Python cannot verify the Austrian RIS certificate.

```bash
git clone https://github.com/JerryWoof/OpenAcquis.git
cd OpenAcquis
pip install -e .

# Overview: title, deadline, number of national measures per country
openacquis summary 32022L2555

# List the Czech measures for NIS2 (also: -f json | -f csv)
openacquis measures 32022L2555 --country CZ
```

Example output:

```
32022L2555  Directive (EU) 2022/2555 ... (NIS 2 Directive)
Transposition deadline: 2024-10-17, 2024-10-18
National measures notified: 304 from 25 member states
EUR-Lex: https://eur-lex.europa.eu/legal-content/EN/NIM/?uri=CELEX:32022L2555

  CZE Czechia        83 ████████████████████████████████████████████████████████████
  HUN Hungary        39 ███████████████████████████████████████
  ...
  ESP Spain           0  (none notified)
```

CELEX numbers identify EU acts: `3` + year + `L` (directive) + number. NIS2 = `32022L2555`, Accessibility Act = `32019L0882`, Pay Transparency = `32023L0970`.

### Texts, split into provisions (v0.2)

```bash
# NIS2 Article 23 in Czech (any of the 24 EU languages: en, de, sk, ...)
openacquis text 32022L2555 --lang cs --article 23

# The Czech Cybersecurity Act 264/2025 Sb., § 12 (current consolidated version from e-Sbírka)
openacquis law cz 264/2025 --section 12

# Whole documents as JSON or one provision per line (JSON lines)
openacquis text 32022L2555 --lang cs -f jsonl > nis2_cs.jsonl
openacquis law cz 264/2025 -f json > zkb.json
```

Example output (shortened):

```
$ openacquis text 32022L2555 --lang cs --article 23
32022L2555  SMĚRNICE EVROPSKÉHO PARLAMENTU A RADY (EU) 2022/2555 ze dne 14. prosince 2022 …
https://eur-lex.europa.eu/legal-content/CS/TXT/?uri=CELEX:32022L2555

Článek 23 Oznamovací povinnosti
  1. Členské státy zajistí, aby základní a důležité subjekty oznamovaly bez zbytečného odkladu …
  ...
  4. Členské státy zajistí, aby za účelem oznámení podle odstavce 1 dotčené subjekty předložily …
    a) bez zbytečného odkladu, nejpozději však do 24 hodin po zjištění významného incidentu, včasné varování, …
  ...

$ openacquis law cz 264/2025 --section 12
264/2025  Zákon č. 264/2025 Sb., o kybernetické bezpečnosti
https://e-sbirka.gov.cz/sb/2025/264/2025-11-01

§ 12 Stanovení rozsahu řízení kybernetické bezpečnosti
  (1) Součástí rozsahu řízení kybernetické bezpečnosti (dále jen „stanovený rozsah“) jsou aktiva související s poskytováním regulované služby.
  (2) Za účelem vymezení stanoveného rozsahu poskytovatel regulované služby
    a) určí všechna svá primární aktiva,
    b) posoudí, zda primární aktiva souvisí s poskytováním regulované služby, a
    c) u primárních aktiv podle písmene b) určí podpůrná aktiva.
  ...

9 provisions (par 1, odst 5, pism 3)
```

Every provision has a stable ID and a link to the official text, e.g. NIS2 Art. 23(4)(a) in Czech is `eu:32022L2555:cs:art23.par4.pt_a`, and § 12 odst. 2 písm. b) of Act 264/2025 Sb. is `cz:264/2025:par12.odst2.pism_b`. Use `--id art23.par4` (or `par12.odst2`) to print a single provision. The whole of NIS2 is 509 provisions with the same IDs in the ten languages checked (Greek and Bulgarian point letters are mapped to Latin IDs); Act 264/2025 Sb. is 736 provisions. Downloads are cached in `~/.cache/openacquis` (`OPENACQUIS_CACHE` overrides it).

From Python:

```python
from openacquis.eurlex import EurLexClient
from openacquis.adapters import fetch_act

nis2 = EurLexClient().document("32022L2555", "cs")
zkb = fetch_act("264/2025")
print(nis2.full_text("eu:32022L2555:cs:art23.par4"))
```

### Four countries (v0.4)

```bash
# National law from each adapter: cz (e-Sbírka), sk (Slov-Lex), at (RIS), de (gesetze-im-internet.de)
openacquis law sk 69/2018 --section 20                   # Slovak Cybersecurity Act, current version
openacquis law sk 69/2018-366/2024 -f json               # the version CELLAR lists as notified (as amended by 366/2024)
openacquis law at "NISG 2026" --section 32               # Austrian NISG 2026 (also: 20013065, "BGBl. I Nr. 94/2025")
openacquis law de bsig_2025 --section 30                 # German BSIG (current text only)

# Which notified measures each adapter can fetch
openacquis measures 32022L2555 -c SK --map

# Align NIS2 with the main transposing act; the directive is fetched in the act's language
openacquis align 32022L2555 sk -o nis2_sk.json           # also: at, de, cz; --measures adds every notified measure
#   --embedder auto (default): hybrid with openacquis[embeddings], else the stdlib hybrid-lex
```

Real output (2026-10-12, `hybrid-lex`):

```
$ openacquis align 32022L2555 at -e hybrid-lex -o nis2_at.json
32022L2555 x 20013065 (hybrid:…, threshold 0.17): 473 EU units, 463 with a link, 1 gap candidates in 1 articles,
13 extra-rule candidates in 7 sections of 545 units of the main act 20013065 -> nis2_at.json
Machine suggestions for review, not findings.
```

IDs follow each country's citation: `sk:69/2018:par24.ods3.pism_a` (§ 24 ods. 3 písm. a)),
`at:20013065:par32.abs4.lit_a` (§ 32 Abs. 4 lit. a), `de:bsig_2025:par28.abs1.nr3` (§ 28 Abs. 1 Nr. 3); see
[docs/provisions.md](docs/provisions.md).

### Alignment and evaluation (v0.3, NIS2 × Czechia)

```bash
# Candidate links between NIS2 (Czech text) and Act 264/2025 Sb., with gap and extra-rule candidates
openacquis align 32022L2555 cz -o nis2_cz.json
#   options: --embedder auto|lexical|hybrid-lex|hybrid|e5|e5-sym|bge-m3|bm25   --judge none|rule|anthropic
#            --threshold 0.17   --gap-margin 0.01   -k 5   --measures

# Score matching against the official correlation table (data/eval/nis2_cz.jsonl)
openacquis eval nis2-cz -e lexical         # the v0.3 tables below; also: -e hybrid-lex, --k 1,5,10, --judge rule
```

Real output (2026-10-12):

```
$ openacquis align 32022L2555 cz -o nis2_cz.json
matching 32022L2555 against 264/2025 (lexical:char3-5)
32022L2555 x 264/2025 (lexical:char3-5, threshold 0.2): 473 EU units, 355 with a link, 118 gap candidates, 195 extra-rule candidates of 629 national units -> nis2_cz.json
Machine suggestions for review, not findings.

$ openacquis eval nis2-cz
32022L2555 x 264/2025  method=lexical:char3-5
gold: 569 links (568 pairs); units: 473 EU, 629 national

Retrieval (share of gold EU items with a gold national provision in the top k)
  level                                  R@1     R@5    R@10     MRR  items
  EU provision -> national provision   0.350   0.563   0.635   0.445    197
  EU provision -> odstavec             0.411   0.655   0.736   0.516    197
  EU provision -> §                    0.492   0.787   0.858   0.615    197
  EU article -> §                      0.636   0.909   0.939   0.750     33

Predicted links (best candidate per EU unit, score >= 0.200)
  provision  P=0.318 (in scope 0.414)  R=0.130  F1=0.185 (in scope 0.198)  [355 predicted, 74/568 gold pairs found; 73 gold EU provisions with a correct link]
  section    P=0.437 (in scope 0.568)  R=0.325  F1=0.373 (in scope 0.414)  [355 predicted, 95/292 gold pairs found; 93 gold EU provisions with a correct link]
  ...
```

*(v0.3 output with the lexical embedder. In v0.4, `hybrid-lex` with the new filters gives 16 gap candidates (3 of them
listed in the table) and 33 extra-rule candidates in 11 §§; the eval report has a step-by-step table.)*

Every entry in `nis2_cz.json` keeps the provision IDs, scores, texts and links to EUR-Lex and e-Sbírka; for NIS2 Art. 23(4)(a) (the 24-hour early warning) the first candidate is `cz:264/2025:par16.odst1` (score 0.58), the 24-hour initial report. Everything is labelled `status: "candidate"`: a gap candidate means "nothing similar found in the acts searched", not a transposition failure. The multilingual e5 model scores lower than the stdlib lexical baseline on this test (R@5 0.442 vs 0.563); the LLM judge has been run once on NIS2 × CZ: see [docs/evaluation.md](docs/evaluation.md#llm-judge-nis2--cz). Details and error analysis: [docs/evaluation.md](docs/evaluation.md).

### Review and dataset (v0.4)

Anyone with a GitHub account can check machine suggestions:

1. Open a review page, e.g.
   [NIS2 × Slovakia](https://jerrywoof.github.io/OpenAcquis/review.html?id=32022L2555_sk).
2. Read the two texts side by side.
3. Confirm, reject or correct the suggestion. Corrections take the right provision IDs.
4. Send it as a prefilled GitHub issue, or download a batch as JSON lines for a pull request.

A workflow checks each review issue, including that the record's `reviewer` is the GitHub user who opened it.
Maintainers add valid records to `data/reviews/`.

```bash
openacquis review validate my_reviews.jsonl     # check records (also issue bodies with ```json blocks)
openacquis review add my_reviews.jsonl --author LOGIN   # validate, check the reviewer, append to data/reviews/
openacquis dataset build                        # dataset/: links.csv, links.jsonl, reviews.jsonl, datapackage.json
```

The dataset (CC-BY-4.0) holds the official correlation-table links (568 for NIS2 × CZ) and reviewed links. It is
downloadable from the website. See [docs/review.md](docs/review.md) and [docs/dataset.md](docs/dataset.md).

### Twelve directives, seven official tables (v0.5)

```bash
openacquis directives                      # the registry: main act per directive and country, gold sets
openacquis align 32022L2557 sk -o cer_sk.json   # any registry directive x country (acts from data/directives.json)
openacquis eval --list                     # nis2-cz (tuning set), cer-cz, eaa-cz, whistleblower-cz, nis2-sk, cer-sk, pay-sk
openacquis eval 32023L0970 sk              # held-out test: Pay Transparency x Slovakia
openacquis alerts check --dry-run          # change alerts for the watched sources (also: alerts feed)
openacquis judge-batch run site/static-data/32022L2557_cz.json --dry-run   # cost estimate, no key needed
python scripts/judge_session.py prep site/static-data/32022L2555_cz.json cz work/nis2_cz   # judge in Claude Code, no key
python scripts/judge_session.py evaluate data/judgments/32022L2555_cz.jsonl                # judge vs the official table

python scripts/build_alignments.py --only 32022L2557   # refresh the site's precomputed alignments (cached, resumable)
python scripts/build_api.py --check        # static API from the built site, schema-checked
```

Real output (2026-10-12, `hybrid-lex`, thresholds tuned on NIS2 × CZ, not retuned):

```
$ openacquis eval 32023L0970 sk
evaluation set pay-sk (held out: thresholds were tuned on nis2-cz only)
...
  EU provision -> national provision   0.793   0.902   0.913   0.837     92
```

### Website

The site will be published at https://jerrywoof.github.io/OpenAcquis/ and rebuilt weekly from CELLAR by GitHub Actions (once Pages is enabled, see [docs/website.md](docs/website.md)). To preview it locally:

```bash
python scripts/build_site.py --offline     # or without --offline for live data (NIS2 alignments live, the rest precomputed)
python scripts/build_api.py                # optional: the static API under site/api/v1/
python -m http.server 8000 -d site         # then open http://localhost:8000/
```

## Contributing

There is a lot to do, and much of it is welcoming to newcomers:

- **Country adapters**: fetch and split national law for your country (each country is a separate module in `openacquis.adapters`; Czechia is the worked example, Slovakia, Austria and Germany followed in v0.4).
- **Legal review**: law students and practitioners checking AI suggestions on the review pages (e.g. [NIS2 × Austria](https://jerrywoof.github.io/OpenAcquis/review.html?id=32022L2555_at)), no coding needed (how: [docs/review.md](docs/review.md)).
- **Evaluation data**: Czech and Slovak government bills include official correlation tables (*rozdílové tabulky*, *tabuľky zhody*) that pair national sections with EU articles. Seven are done (`data/eval/`); other directives, companion bills and other countries' tables are open.

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Disclaimer

OpenAcquis is a research and transparency tool, **not legal advice**. Outputs are candidates with evidence, not findings that a state has breached EU law. Always check the official texts.

## License

Code: [MIT](LICENSE). Published datasets and reviews: CC-BY-4.0. EUR-Lex data is reused under the European Commission's reuse policy (Decision 2011/833/EU); source: © European Union, https://eur-lex.europa.eu.
