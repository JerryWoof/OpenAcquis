# Evaluation: NIS2 × Czechia, and six held-out tables

> **New in 0.5:** [held-out generalisation](#v05-held-out-generalisation-six-more-official-tables) on six more
> official correlation tables (Czechia: CER, Accessibility Act, Whistleblower; Slovakia: NIS2, CER, Pay
> Transparency), scored with the thresholds tuned on NIS2 × Czechia and not retuned.
>
> **New (unreleased):** the [LLM judge, run once on NIS2 × Czechia](#llm-judge-nis2--cz) (Claude Opus 5.5 in Claude
> Code, top 3 candidates): precision against the table 0.45 (machine top-1) → 0.76 (pairs the judge calls related).

*Sections up to "Take-aways" were run on 2026-10-12 with OpenAcquis 0.3.0; the v0.4 sections at the end with
0.4.0. Since 0.4.0 the default embedder of `align` and `eval` is `auto` (`hybrid` with the embeddings extra,
else `hybrid-lex`); pass `--embedder lexical` to reproduce the v0.3 tables.*

How well does automatic matching find the Czech provisions that transpose each NIS2 provision? We
compare it with the **official correlation table** (*rozdílová tabulka*) that the Czech government
attached to the bill of the Cybersecurity Act (sněmovní tisk 759/0, July 2024). The table lists which
bill provision implements which NIS2 provision. [`data/eval/README.md`](../data/eval/README.md) explains
how it became the gold set: 569 links (568 distinct pairs) between 197 NIS2 provisions in 33 articles
and 243 provisions of Act No. 264/2025 Sb.

Reproduce:

```bash
openacquis eval nis2-cz -e lexical           # lexical (stdlib), about 4 s once the texts are cached
openacquis eval nis2-cz --embedder e5        # needs pip install 'openacquis[embeddings]'
openacquis eval nis2-cz --judge rule         # adds the keyless heuristic judge
openacquis eval nis2-cz --errors 10 -o report.json
```

**What these numbers mean.** They measure agreement with one government's table, which describes the
2024 bill and is not error-free. They do not measure whether Czech law transposes NIS2 correctly.

## Setup

| | |
|---|---|
| EU text | NIS2 (32022L2555), Czech language version, CELLAR: 509 provisions, **473 matching units** (paragraph lead-ins, points, sub-points; each embedded with its headings and the start of the enclosing text) |
| National text | Act No. 264/2025 Sb., e-Sbírka version in force from 2025-11-01: 736 provisions, **629 matching units** (odstavce, písmena, body; structural units and footnotes skipped) |
| Gold | `data/eval/nis2_cz.jsonl`: 569 links, all with `status: "listed"` (the table has no compatibility column) |
| Embedders | `lexical`: TF-IDF over character 3–5-grams of stemmed, diacritic-free Czech words (stdlib). `e5`: `intfloat/multilingual-e5-base` with `query:`/`passage:` prefixes. `e5-sym`: the same model with `query:` on both sides |

A ranked national provision counts as a hit when it is the gold provision, inside it, or contains it.
The table often cites a whole § or odstavec where the matcher ranks its písmena, and the other way round.

## Results

### Retrieval: is a provision listed in the table among the top k candidates?

recall@k is the share of gold EU items with at least one gold national provision among the first *k*
candidates. National candidates are first collapsed to the level shown, so *k* counts distinct
odstavce or §§. MRR is the mean reciprocal rank of the first hit within the top 10.

| Level | Embedder | R@1 | R@5 | R@10 | MRR | Items |
|---|---|---:|---:|---:|---:|---:|
| EU provision → national provision | **lexical** | **0.350** | **0.563** | **0.635** | **0.445** | 197 |
| | e5 | 0.264 | 0.442 | 0.543 | 0.341 | 197 |
| | e5-sym | 0.294 | 0.482 | 0.584 | 0.377 | 197 |
| EU provision → odstavec | **lexical** | **0.411** | 0.655 | 0.736 | **0.516** | 197 |
| | e5 | 0.345 | 0.629 | 0.711 | 0.455 | 197 |
| | e5-sym | 0.391 | **0.665** | **0.741** | 0.506 | 197 |
| EU provision → § | **lexical** | **0.492** | **0.787** | **0.858** | **0.615** | 197 |
| | e5 | 0.396 | 0.736 | 0.822 | 0.541 | 197 |
| | e5-sym | 0.452 | 0.766 | 0.832 | 0.593 | 197 |
| EU article → § | lexical | **0.636** | 0.909 | 0.939 | **0.750** | 33 |
| | e5 | **0.636** | 0.909 | **0.970** | 0.740 | 33 |
| | e5-sym | 0.576 | **0.939** | **0.970** | 0.733 | 33 |

### Predicted links: precision, recall and F1 at the default threshold

A predicted link is the best candidate of each EU unit whose score reaches the embedder's threshold.
The thresholds are lexical 0.20, e5 0.85 and e5-sym 0.87; scores are not comparable across embedders.

- **Precision** is computed over all predictions. *In scope* counts only predictions for EU units the
  table covers. The table covers only the Cybersecurity Act, so a prediction for an article transposed
  elsewhere, or for an article addressed to the Commission, cannot be confirmed by it.
- **Recall** is over the 568 gold pairs (292 at § level).
- **EU recall** is the share of the 197 gold EU provisions that get at least one correct link.
- A link counts for a gold EU provision only when it is predicted for that provision or one of its parts. A
  link on an article lead-in or on a paragraph does not count for each point the table lists under it
  (e.g. the Art. 6 lead-in does not count for the definitions in Art. 6 points 3 and 10).

| Match level | Embedder | Predicted | Precision (in scope) | Recall | EU recall | F1 (in scope) |
|---|---|---:|---:|---:|---:|---:|
| Provision | **lexical** | 355 | **0.318 (0.414)** | **0.130** | **0.371** | **0.185 (0.198)** |
| | e5 | 341 | 0.282 (0.364) | 0.102 | 0.289 | 0.150 (0.159) |
| | e5-sym | 426 | 0.277 (0.384) | 0.114 | 0.325 | 0.162 (0.176) |
| Same § | **lexical** | 355 | **0.437** (0.568) | 0.325 | 0.472 | **0.373** (0.414) |
| | e5 | 341 | 0.419 (0.542) | 0.277 | 0.401 | 0.334 (0.367) |
| | e5-sym | 426 | 0.418 (**0.580**) | **0.329** | **0.477** | 0.368 (**0.420**) |

**How the threshold was chosen.** On this set, the lexical threshold with the best in-scope F1 is 0.185,
giving P 0.375, R 0.134 and F1 0.197. That is optimistic, because the threshold is tuned on the data it
is scored on. A less optimistic check uses two folds by article parity: pick the threshold on odd
articles, score it on even ones, and the other way round. This picks 0.226 and 0.143, with pooled
in-scope P 0.399, R 0.125 and F1 0.190, so overfitting the threshold costs little. The defaults are near
the best-F1 values: lexical 0.20, e5 0.85 (best 0.841), e5-sym 0.87 (best 0.869).

At § level, e5-sym, which predicts the most links (426), edges out lexical on recall and in-scope F1
(0.420 vs 0.414); at provision level lexical is ahead on every measure.

Recall over pairs is low by construction. Several rows of the table list many provisions: Art. 36 alone
is paired with 49 Czech provisions, and Art. 34(2) with 31. One link per EU unit can never recall them
all, so EU recall is the more telling figure.

### Coverage, gap and extra-rule candidates (lexical, threshold 0.20)

| | |
|---|---|
| Gold EU provisions with a predicted link (on the provision itself or one of its parts) | 159 / 197 (80.7 %) |
| EU units with a predicted link | 355 / 473 (the table covers 339 units) |
| Gap candidates (EU units with no link) | 118, of which **66 are listed in the table**: false alarms against the table |
| Extra-rule candidates (units of 264/2025 that match nothing) | 195 of 629, of which **49 are listed in the table** |

With e5, 154 gold EU provisions get a predicted link, and there are 132 gap candidates (75 listed in the
table) and 22 extra-rule candidates (8 listed in the table). e5's scores are squeezed into a narrow band, so almost every national unit clears the
threshold for some EU unit.

At this stage, gap and extra-rule candidates are a reading list, not a signal. More than half of the gap
candidates are provisions that the government explicitly maps to a Czech section.

### Judge

| Judge | Status | Results (pairs for EU provisions the table covers) |
|---|---|---|
| `RuleJudge` (keyless heuristics, top 3 lexical candidates) | run | 1,017 pairs judged. Of the 225 pairs that are in the table, 55.1 % were labelled related. Of the 792 other pairs, 64.4 % were labelled unrelated. Keeping only links it labels related gives P 0.225 (in scope 0.305), R 0.158 and F1 0.186, with 86 gold EU provisions getting a correct link and 177 gap candidates (117 listed in the table). **It does not help:** precision drops compared with the score threshold alone |
| `AnthropicJudge` (Claude, `--judge anthropic`) | **requires ANTHROPIC_API_KEY; not yet run** | Implemented and tested offline with a fake client only. Estimated cost for 473 units × 3 candidates (about 1,400 pairs): about $8 with Sonnet 5.5, or about $4 through the Batches API (see [judge.md](judge.md)). These are estimates, not measured spend |
| Claude Opus 5.5 with the same prompt, in Claude Code subagents (later, see [LLM judge (NIS2 × CZ)](#llm-judge-nis2--cz)) | run once, top 3 candidates of the 202 page rows | 414 pairs in scope. Related pairs: P 0.755 (§ 0.883), against 0.449 for the machine's top-1. Keeps 71 of 106 table-confirmed candidates and rejects 285 of 308 others |

## Errors: five examples

From `openacquis eval nis2-cz --errors 10` and the rankings of both embedders.

1. **Art. 23(4)(a), the 24-hour early warning, works for lexical and not for e5.** The table pairs it with
   § 16 odst. 1 ("nejpozději do 24 hodin po zjištění … předloží prvotní hlášení"). Lexical ranks it first
   because the wording is shared ("do 24 hodin po zjištění", "nezákonným zásahem", "přeshraniční dopad").
   e5 puts § 16 odst. 3 písm. a) (the 72-hour report) first and § 16 odst. 1 only ninth, because it
   barely separates the 24-hour and 72-hour reports. The same pattern holds across the set: at R@5, 36
   gold EU provisions are found only by lexical and 12 only by e5. That suggests a hybrid of the two
   rather than replacing one with the other.
2. **Art. 36, penalties, is missed by both embedders.** The directive says, in two sentences, that Member
   States lay down effective, proportionate and dissuasive sanctions. The Czech act implements this as
   lists of offences (§§ 59–63, "se dopustí přestupku tím, že … neohlásí změnu … podle § 9 odst. 1"), and
   the table pairs Art. 36 with 49 of those provisions. The wording shares almost nothing, and each
   offence refers to another § rather than to NIS2. No gold provision is in either top 10. Fixing this
   needs structure, not better similarity: for example, following the cross-references in the offence
   lists, or matching a sanctions article to a "Přestupky" heading at § level.
3. **Art. 21(2)(i) ("bezpečnost lidských zdrojů, postupy kontroly přístupu a správa aktiv") is missed
   even though the words match.** The table cites 9 provisions of § 14: odst. 1 písm. a) body 5, 8 and 11,
   odst. 1 písm. b) body 3 and 4, and odst. 2 písm. a), e), g) and h). Several are list items of two or
   three words, such as "8. bezpečnost lidských zdrojů". Each unit is embedded with its context prefix
   (the § heading and the lead-in of odstavec 1), so the shared phrase is diluted in a short item.
   Longer provisions elsewhere (§ 42, § 43, § 71, § 39) take the first six ranks. The first hit, at
   rank 7, is § 14 odst. 2 písm. e), and the short item bod 8 comes only at rank 9. Short list items need
   a different weighting, for example scoring the item text and its context separately.
4. **Art. 30(2), voluntary notifications, matches a false friend.** The paragraph says voluntary
   notifications are processed under the procedure of Art. 23. The table points to § 17 (the Office's
   response to an incident report). Lexical ranks § 44 odst. 6 first, a rule about a supervisory body
   that must *oznámit* (report) violations by Office staff. "Oznámení" in the sense of a notification and
   "oznámit" in the sense of reporting misconduct share their stems. e5 instead ranks § 66 first (the
   Office's other duties). The first gold hit is at rank 7 (lexical) or 6 (e5).
5. **Art. 2(2)(a), scope for electronic-communications and trust-service providers and DNS, is a
   disagreement where the machine is not plainly wrong.** The table pairs this point with the sector list
   in § 4 odst. 1 (registration conditions: "1. veřejná správa, 2. energetika …"). That list names
   sectors, not provider types, so there is little shared vocabulary. Lexical's top candidate is
   § 1 odst. 2: the act applies to anyone providing electronic-communications services in Czechia,
   regardless of establishment. This is related wording, but it implements the jurisdiction rule
   (Art. 26) rather than the scope rule. A reviewer would reject it; the judge step exists to do that.

**Problems in the gold set itself** are listed in `data/eval/README.md`:

- 44 links point to a sentence or subparagraph, and a link can only point to the whole paragraph.
- Two cells quote another letter than the one they cite.
- The table describes the bill, not the act as published.
- The companion act (tisk 760) and the implementing decrees transpose further parts of NIS2 and are not
  covered. This explains part of the 118 gap candidates. Articles addressed to the Commission, ENISA or
  the Cooperation Group explain another part.

## Take-aways

- Shortlisting works; picking the single right provision does not yet. About four in five gold EU
  provisions have a correct § in the lexical top 5 (0.787), and nine in ten articles have a correct §
  in the top 5 (0.909). The top-1 provision is right only about a third of the time (0.350).
- The stdlib lexical baseline beats multilingual-e5-base here. Both texts are Czech and the act often
  reuses the directive's wording, which favours character n-grams. e5 is better on some paraphrased
  definitions: Art. 6(6) ranks first with e5 and seventh with lexical.
- The pipeline's gap and extra-rule lists are not yet usable as signals. More than half of the gap
  candidates are listed in the table.
- Next steps:
  - a lexical + dense hybrid;
  - following cross-references inside the national act, for sanctions;
  - separate scoring of short list items;
  - the LLM judge on the top 3 candidates;
  - adding tisk 760 to the gold set.

## v0.4 hybrid matching

*Run on 2026-10-12 with the v0.4 development tree. Same gold set, documents and units as above.*

v0.3 found that lexical and e5 retrieve different gold provisions. v0.4 adds score fusion to
`openacquis.match`:

- `BM25Embedder`: Okapi BM25 over the same stemmed, diacritic-free words (stdlib).
- `HybridEmbedder`: fuses several channels. It can use a weighted sum of z-scores, standardised per
  EU unit (`norm="query"`) or over the whole matrix (`"global"`), or reciprocal rank fusion
  (`fusion="rrf"`, k = 60). A `Channel` can read a unit's own text only (`field="body"`), without the
  headings and lead-ins. This addresses error 3 above: a short list item diluted by its context.
  After weighted fusion, scores are mapped back onto the first channel's scale (per row for `query`).
  So an EU unit that matches nothing still scores low, and thresholds stay close to the lexical ones.
- `StructuralPrior`: lets the structure vote. A national unit gains when other parts of its § match
  the EU unit (`section_weight`) or its odstavec does (`paragraph_weight`). It also gains when the EU
  unit's siblings match its § (`sibling_weight`): points of one NIS2 paragraph tend to land in one §.
  The result is renormalised, so the best unit of a § keeps its score.
- Presets: `make_hybrid("hybrid-lex" | "hybrid" | "hybrid-rrf")`. You can also select them on the
  command line:

```bash
openacquis eval nis2-cz --embedder hybrid-lex   # stdlib only, about 5 s
openacquis eval nis2-cz --embedder hybrid       # needs openacquis[embeddings]
openacquis eval nis2-cz --embedder bm25
openacquis eval nis2-cz --embedder hybrid-rrf
```

### How it was tuned

Weights, normalisation and prior weights were chosen with **two folds by article parity**, the split
used for the threshold above. For each fold, the configuration with the best provision-level MRR was
picked on the other fold's gold provisions. It was then scored on this fold, and the two test folds
were pooled. The grids:

- stdlib: lexical body weight {0, 0.25, 0.5}, BM25 {0, 0.25, 0.5, 1}, BM25 body {0, 0.25}, global or
  query z-scores;
- full: the stdlib channels, plus e5 {0, 0.5, 1}, e5-sym {0, 0.5, 1, 1.5} and e5 body {0, 0.25};
- RRF: weights for lexical, lexical body, BM25, e5 and e5-sym;
- prior: section {0, 0.25, 0.5, 1} × odstavec {0, 0.25, 0.5} × siblings {0, 0.25, 0.5, 1}, with
  siblings at paragraph or article level.

The tuning script lives outside the repository. It reuses `evaluate.retrieval` on cached score
matrices.

The two folds agreed on the main choices:

- per-query z-scores;
- lexical text weight 1, lexical body 0.25–0.5;
- for the full grid, e5-sym 1–1.5 and e5 body 0.25, with e5 on the full text weighted 0;
- for the stdlib prior, siblings 0.5–1.

The presets fix these rounded values. The preset rows further down therefore use settings informed by
both folds and are slightly optimistic. **The cross-validated rows are the honest estimate.**

### Results: EU provision → national provision (197 gold EU provisions)

| Method | Tuning | R@1 | R@5 | R@10 | MRR | § R@1 | § R@5 | § R@10 | § MRR |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lexical (v0.3 default) | none | 0.350 | 0.563 | 0.635 | 0.445 | 0.492 | 0.787 | 0.858 | 0.615 |
| lexical, body only | none | 0.376 | 0.584 | 0.640 | 0.457 | 0.503 | 0.761 | 0.848 | 0.611 |
| BM25 | none | 0.325 | 0.508 | 0.579 | 0.404 | 0.431 | 0.716 | 0.807 | 0.550 |
| BM25, body only | none | 0.391 | 0.558 | 0.619 | 0.464 | 0.477 | 0.746 | 0.807 | 0.584 |
| e5 | none | 0.264 | 0.442 | 0.543 | 0.341 | 0.396 | 0.736 | 0.822 | 0.541 |
| e5, body only | none | 0.365 | 0.553 | 0.609 | 0.437 | 0.477 | 0.756 | 0.832 | 0.593 |
| e5-sym | none | 0.294 | 0.482 | 0.584 | 0.377 | 0.452 | 0.766 | 0.832 | 0.593 |
| RRF lexical + e5-sym (`hybrid-rrf`) | none | 0.365 | 0.584 | 0.665 | 0.462 | 0.513 | 0.777 | 0.832 | 0.629 |
| z-fusion lexical + e5-sym, equal weights | none | 0.401 | 0.599 | 0.660 | 0.489 | 0.553 | 0.812 | 0.853 | 0.657 |
| lexical + prior | 2-fold CV | 0.350 | 0.574 | 0.655 | 0.449 | 0.528 | 0.802 | **0.873** | 0.643 |
| stdlib fusion | 2-fold CV | 0.416 | 0.589 | 0.675 | 0.495 | 0.533 | 0.787 | 0.858 | 0.642 |
| stdlib fusion + prior | 2-fold CV | 0.416 | 0.594 | 0.670 | 0.491 | 0.553 | 0.812 | 0.863 | 0.662 |
| RRF, tuned weights | 2-fold CV | 0.350 | 0.599 | 0.680 | 0.457 | 0.503 | 0.792 | 0.863 | 0.632 |
| full fusion (lexical + e5) | 2-fold CV | 0.411 | **0.635** | 0.690 | **0.505** | 0.543 | **0.822** | 0.868 | 0.661 |
| full fusion + prior | 2-fold CV | 0.401 | 0.624 | **0.695** | 0.497 | 0.548 | **0.822** | 0.868 | **0.663** |
| preset `hybrid-lex` (stdlib) | fixed, see above | 0.431 | 0.609 | 0.685 | 0.505 | 0.584 | 0.807 | 0.858 | 0.679 |
| preset `hybrid` (lexical + e5) | fixed, see above | 0.426 | 0.635 | 0.690 | 0.515 | 0.563 | 0.817 | 0.863 | 0.670 |

`hybrid-lex` = lexical 1 + lexical body 0.5 + BM25 0.5, per-query z-scores, prior (§ 0.25, siblings 1).
`hybrid` = lexical 1 + lexical body 0.25 + e5-sym 1 + e5 body 0.25, per-query z-scores, no prior (the
folds disagreed on the prior).

At the other levels, for the presets on the full set:

- **odstavec:** `hybrid-lex` R@1 0.508, R@5 0.721, MRR 0.596; `hybrid` 0.477, 0.721, 0.582. Lexical
  was 0.411, 0.655, 0.516.
- **article → §:** `hybrid` 0.697 / 0.879 / 0.909 / MRR 0.780; `hybrid-lex` 0.636 / 0.879 / 0.909 /
  0.739. Lexical was 0.636 / 0.909 / 0.939 / 0.750.

The article level has only 33 items, so one article is 3 points.

**What changed, honestly.**

- **Fusion helps, and most of the gain is stdlib.** Under cross-validation, provision-level MRR goes
  from 0.445 to 0.495 without any model (stdlib fusion), and to 0.505 with e5. R@1 goes from 0.350 to
  about 0.41.
- **R@5 is where e5 adds something.** R@5 rises from 0.563 to 0.635 with e5; the stdlib fusion gets
  0.589–0.609.
- **Equal-weight z-fusion of lexical and e5-sym, with no tuning at all, already gets most of the
  gain** (MRR 0.489).
- **The gains are small next to the sample.** One gold EU provision is 0.5 points. The differences
  between the tuned variants (about ±0.01) are within noise; the gain over lexical (+0.05 to +0.07
  MRR) is not.
- **RRF is worse than weighted z-fusion here.** RRF throws away how far ahead lexical's top hit is.
  Lexical is often decisively right (shared wording), so that margin is worth keeping.
- **The body-only channels carry much of the gain.**
  - Example 3 above, Art. 21(2)(i): its first hit moves from rank 7 to rank 1 in both presets.
  - Example 1, Art. 23(4)(a): stays at rank 1.
  - Example 4, Art. 30(2): moves from 7 to 5 (`hybrid-lex`) or 6 (`hybrid`).
  - Art. 6(6), the paraphrased definition from the take-aways: 7 → 2 with `hybrid`; lost from the top 10 with
    `hybrid-lex`.
  - Example 5, Art. 2(2)(a): still missed.
  - At R@5 against lexical: `hybrid` gains 15 gold EU provisions and loses 1; `hybrid-lex` gains 16
    and loses 7.
- **The structural prior is mostly a § -level tool.** It lifts § R@1 (0.533 → 0.553 for stdlib
  fusion, 0.492 → 0.528 for lexical) and § MRR, but does not improve provision-level MRR under
  cross-validation. Sanctions (Art. 36, example 2) are still not solved: `hybrid-lex` brings a first
  hit to rank 8, `hybrid` none. Following cross-references remains the fix to try.

### Predicted links and thresholds

Fused scores are mapped onto the lexical scale, but they are a little lower. The lexical default
threshold (0.20, which `align.default_threshold` uses for any unknown method, including `hybrid:…`)
is too strict for them. The 2-fold choices were:

- `hybrid-lex`: 0.174 and 0.157, giving pooled in-scope P 0.430, R 0.158, F1 **0.232** (lexical:
  0.190);
- `hybrid`: 0.142 and 0.127, giving P 0.410, R 0.155, F1 0.225.

At a threshold of 0.17, `hybrid-lex` gives:

- **Provision links:** P 0.329 (in scope 0.437), R 0.160, F1 in scope 0.234; 90 gold EU provisions
  get a correct link (lexical at 0.20: 73).
- **Section links:** in-scope F1 0.491 (lexical 0.414).
- **Gap candidates:** 50, of which 21 are listed in the table (lexical: 118, of which 66).
- **Extra-rule candidates:** 112, of which 35 are in the table.

The gap list is shorter and less often contradicted by the table, but 42 % of it still is. It
remains a reading list.

`hybrid-rrf` scores are about 1/61 and carry only ranks, not an absolute level. Since 0.4.0 the RRF
presets have their own threshold family (`"hybrid-rrf"` in `align.DEFAULT_THRESHOLDS`: threshold 0.03,
gap margin 0.001), which gives links: 421 predicted, in-scope precision 0.405. They give **no gap
list**: every EU unit's best candidate scores about the same, so a low score does not mean "nothing
similar". Measured against the table, RRF gap lists agree with it less often than a random list
(15–21 % not listed at every margin tried, against the 28.3 % base rate below). `align` and `eval`
therefore report no gap candidates for `hybrid-rrf` and say why (`gap_note` in the JSON). Its
extra-rule list is still produced (99 units at 80.8 %). Use RRF for ranking only.

### Cross-lingual readiness

Lexical and BM25 compare surface forms, so they only work when the directive and the act are in
the same language. The pipeline is built for that: `load_documents` fetches the directive in the
country's language. That is Czech for CZ, and will be **Slovak for Slovakia and German for Austria and
Germany** when those adapters exist. NIS2 provision IDs are identical across language versions, so
the gold set and rollups carry over.

The light stemmer is Czech. Slovak is close enough; for German use `LexicalEmbedder(stem=False)`, or
add a German stemmer, and re-tune the weights.

Only the dense channels are cross-lingual by design. Both multilingual-e5 and bge-m3 are trained
multilingual. To check this without a Slovak or German act, NIS2 was matched in other languages
against the Czech act, with the gold remapped by language code (all 197 gold provisions exist in
every version):

| NIS2 language vs CZ act | lexical MRR (R@5) | e5 MRR (R@5) | e5-sym MRR (R@5) | `hybrid` MRR (R@5) |
|---|---:|---:|---:|---:|
| cs (same language) | 0.445 (0.563) | 0.341 (0.442) | 0.377 (0.482) | **0.515 (0.635)** |
| sk | 0.410 (0.528) | 0.335 (0.457) | 0.373 (0.503) | **0.462 (0.604)** |
| de | 0.192 (0.264) | 0.332 (0.416) | **0.354 (0.437)** | 0.271 (0.376) |
| en | 0.214 (0.279) | 0.290 (0.371) | **0.333 (0.447)** | 0.293 (0.401) |

These are provision-level figures.

- **e5 barely moves across languages** (MRR 0.33–0.35 from DE, EN, SK or CS), so the dense path is
  language-independent as intended.
- **Lexical collapses across language families.** Lexical SK → CZ still works, because the languages
  share many stems. It is not a substitute for same-language matching.
- **Use a hybrid only within one language.** For a cross-language pair, `hybrid` is worse than e5-sym
  alone, because the lexical channel adds noise. Use a dense embedder on its own, or fetch the
  directive in the act's language (the default).

### Recommendation

- **CI and default without extras: `hybrid-lex`.** It is stdlib-only, deterministic, and runs in
  about 5 s. Cross-validated, it improves on lexical at every provision-level measure: MRR 0.445 →
  0.49–0.50, R@1 0.350 → 0.42–0.43. It also improves § R@1 and link F1. Adopted in 0.4.0: it is what
  `--embedder auto` (the default) picks without the embeddings extra and what the website uses, and
  `align.DEFAULT_THRESHOLDS` has `"hybrid": 0.17`.
- **Full install: `hybrid`** (lexical + multilingual-e5; about 5 s with a warm vector cache, a few minutes on CPU when cold). It has
  the best R@5 (0.635 vs 0.563) and MRR (0.505 cross-validated), and the best article-level MRR. Its
  default threshold is 0.14 (`"hybrid-dense"` in `align.DEFAULT_THRESHOLDS`); `--embedder auto` picks it
  when the extra is installed. For a cross-language pair use `e5-sym` alone.
- **Not recommended:** RRF (worse ranking, no gap list) and BM25 alone (below lexical). The prior is
  useful at § level and optional at provision level.
- **Still weak:**
  - top-1 at provision level is right only about 43 % of the time;
  - sanctions need cross-reference following;
  - all figures come from one directive, one country and one government table.

## v0.4 gap and extra-rule candidates

*Run on 2026-10-12 with OpenAcquis 0.4.0. Same gold set, documents and units as above.*

v0.3 reported every EU unit without a predicted link as a *gap candidate*, and every unit of the act
that matched nothing as an *extra-rule candidate*. Neither list was usable: with lexical matching at
0.20, 66 of the 118 gap candidates are provisions the official table lists. v0.4
(`openacquis.leftovers`) filters both lists in four steps. None needs a model, and all work in every
language with a keyword list (cs, sk, de, en):

1. **Scope.**
   - EU provisions that no member state transposes are set aside, not reported as gaps:
     - final provisions, found by article heading (transposition, amendments of other EU acts, repeal,
       entry into force, addressees, delegated acts, committee procedure, review, correlation table);
     - units addressed only to the Commission or ENISA, naming no member state, national authority
       or regulated entity. Joint bodies made up of national representatives (the Cooperation Group,
       the CSIRTs network, EU-CyCLONe) do not count: units about them are national duties too, and the
       table often maps them.
   - On the national side, boilerplate is set aside: definitions, entry into force, transitional and
     repeal clauses, enabling clauses, penalty lists (they implement Art. 36 through cross-references,
     see error 2 above), amendments of other acts, transposition notes, footnotes, and annexes
     published only as PDF.
2. **Score margin.** A gap candidate's best score must stay at least 0.01 below the link threshold.
   An EU unit that just misses the threshold usually has a match.
3. **Structure.**
   - A lead-in is not a gap when one of its points is linked, and a point is not a gap when its
     lead-in is linked.
   - An extra-rule candidate must sit in a § with no linked unit at all: a § that partly transposes
     an article is not an extra rule.
   - Only the **main** transposing act (the first act searched) can have extra-rule candidates.
4. **Grouping.** Both lists are also reported per EU article and per national §, the level a
   reviewer reads.

The LLM judge was not used: it has still not been run, and the keyless rule judge made links worse in
v0.3. Reproduce with `openacquis eval nis2-cz -e hybrid-lex` (or `-e lexical`). The report has a
step-by-step table (`leftover_ablation` in `openacquis.evaluate`, nine rows: three filter steps and six
gap margins scaled to the method's default margin). Each row also gives the article with the most gap
candidates and the precision outside it, a 95 % Wilson interval, the scope-filtered counts, and the
margin chosen out of sample (`cross_validated_gap_margin`).

### Results against the table

**Precision** is the share of candidates the table does *not* list (gap candidates) or does *not*
cite (extra-rule candidates), i.e. agreement with the table's silence. The **base rate** is what a
random list of the same units would get: 28.3 % of the 473 EU units are not covered by the table, and
49.9 % of the 629 units of Act 264/2025 Sb. are not cited.

| Embedder (threshold) | Step | Gap candidates | Gap precision | Extra-rule candidates | Extra-rule precision | §§ with extra rules |
|---|---|---:|---:|---:|---:|---:|
| lexical (0.20) | v0.3: every unlinked unit | 118 | 44.1 % | 195 | 74.9 % | 37 |
| | + scope and boilerplate | 107 | 40.2 % | 120 | 85.8 % | 25 |
| | + structure | 54 | 44.4 % | 47 | 83.0 % | 12 |
| | + gap margin 0.01 (**v0.4 default**) | 39 | 51.3 % | 47 | 83.0 % | 12 |
| `hybrid-lex` (0.17) | v0.3: every unlinked unit | 50 | 58.0 % | 112 | 68.8 % | 27 |
| | + scope and boilerplate | 44 | 54.5 % | 72 | 80.6 % | 18 |
| | + structure | 30 | 66.7 % | 33 | 81.8 % | 11 |
| | + gap margin 0.005 | 21 | 71.4 % | 33 | 81.8 % | 11 |
| | + gap margin 0.0075 | 19 | 73.7 % | 33 | 81.8 % | 11 |
| | + gap margin 0.01 (**v0.4 default**) | **16** | **81.2 %** | **33** | **81.8 %** | **11** |
| | + gap margin 0.015 | 12 | 75.0 % | 33 | 81.8 % | 11 |
| | + gap margin 0.02 | 7 | 57.1 % | 33 | 81.8 % | 11 |
| | + gap margin 0.03 | 3 | 66.7 % | 33 | 81.8 % | 11 |
| `hybrid` (0.14, with e5) | v0.4 default | 4 | 75.0 % | 16 | 93.8 % | 5 |

**Read these numbers with care.**

- **The samples are small and clustered.**
  - With `hybrid-lex`, 13 of 16 gap candidates are not in the table. The 95 % Wilson interval is
    57–93 %.
  - 12 of the 16 come from one place, Art. 32(2) and 32(7): the list of factors an authority weighs
    before enforcement, and two kinds of audit. The table does not map them. They are more likely
    transposed by the general administrative-offences law (Act 250/2016 Sb.), which the table does not
    cover, than missing.
  - **Outside Art. 32 the list is no better than chance:** only 1 of the other 4 candidates is not in
    the table (25 %, below the 28.3 % base rate). The 81 % rests on that one cluster.
  - Precision does not rise steadily with the margin (0.005: 71.4 %, 0.0075: 73.7 %, 0.01: 81.2 %,
    0.015: 75.0 %, 0.02: 57.1 %, 0.03: 66.7 %), so the default margin sits on a local peak.
- **The margin was chosen on this set.** Chosen out of sample over the grid {0, 0.005, 0.0075, 0.01,
  0.015, 0.02, 0.03} by the Wilson lower bound:
  - one half of the articles (by parity) scored on the other: the folds pick 0 and 0.0075, and pooled
    16 of 25 gap candidates are not in the table (64.0 %, interval 44.5–79.8 %); with lexical, 16 of
    34 (47.1 %);
  - leave one article out: 16 of 21 (76.2 %, interval 54.9–89.4 %); with lexical, 16 of 32 (50.0 %).

  Both figures still depend on the link threshold and the preset, which were tuned on all articles.
- **The scope filter costs a little precision.** Final provisions and units addressed only to the
  Commission or ENISA are mostly not in the table, so dropping them lowers the measured precision
  (lexical 44.1 % → 40.2 %). With lexical at 0.20 the filter sets aside 11 unlinked EU units, 2 of
  which the table does map (Art. 4(3) and Art. 41(1)); with `hybrid-lex`, 6, of which 1 (Art. 41(1)).
  Before the joint bodies were taken out of the filter it set aside 18 such units with lexical, 9 of
  them mapped by the table. They are dropped because a reviewer gains little from them.
- **Extra rules: precision is high, recall is not measured.**
  - 27 of the 33 `hybrid-lex` candidates are not cited in the table (interval 66–91 %). Of the 11 §§,
    7 are not cited at all.
  - Most are national machinery with no NIS2 counterpart: the state of cyber emergency (§ 37), the
    national coordination centre and its community (§§ 48–53, which implement Regulation 2021/887,
    not NIS2), and the public contract with the operator of the national CERT.
  - The 6 cited units are in §§ 12, 20 and 23. For example, the table pairs § 20 odst. 2 and § 23
    odst. 1 (countermeasures) with the supervisory and enforcement measures of Art. 32, whose wording
    they do not share.
  - "Not cited" does not mean "gold-plating". Most of these are procedure or organisation, which a
    directive leaves to member states.
- **What improved.**
  - Against v0.3 (lexical, 118 gap candidates at 44 %), `hybrid-lex` with the filters gives 16 at
    81 %. The gap list is 7 times shorter, and false alarms against the table fall from 66 to 3.
  - The extra-rule list goes from 195 units at 75 % to 33 units in 11 §§ at 82 %.
  - Both lists are now short enough to review in an hour. They are still **candidates**: the table
    describes a 2024 bill and covers one act.

## v0.4 four countries (no gold set)

*Run on 2026-10-12 with OpenAcquis 0.4.0, `hybrid-lex`, the directive fetched in each act's language.
These are the numbers on the website's alignment pages (`site/static-data/`).*

| | CZ | SK | AT | DE |
|---|---|---|---|---|
| Main act | 264/2025 Sb. (version of 2025-11-01) | 69/2018 Z. z. (version of 2026-04-30) | NISG 2026, Gesetzesnummer 20013065 (in force 2026-10-01) | BSIG, `bsig_2025` (NeuRIS version 2026-07-29) |
| NIS2 text | Czech | Slovak | German | German |
| Stemmer | Czech | Czech (close enough) | none | none |
| Units of the act | 629 | 762 | 545 | 603 |
| EU units with a link (of 473) | 423 | 454 | 463 | 448 |
| Unlinked EU units set aside (final provisions, Commission / ENISA only) | 6 | 7 | 8 | 10 |
| Linked EU units in those categories | 31 | 34 | 32 | 30 |
| Gap candidates (articles) | 16 (4) | 5 (3) | 1 (1) | 6 (4) |
| Extra-rule candidates (§§) | 33 (11) | 34 (7) | 13 (7) | 59 (12) |

- **No official table, so no precision.** Only Czechia has one. For SK, AT and DE these lists are
  unchecked; they need reviewers (`review.html`).
- **The thresholds come from Czech.** Scores are higher where the act copies the directive's wording.
  The median best score per EU unit is 0.22 (CZ), 0.29 (SK), 0.27 (DE) and 0.44 (AT).
  - Austria's NISG 2026 often reproduces NIS2 word for word. For example, § 39 Abs. 7 Z 1 scores 0.99
    against Art. 32(7)(a).
  - So nearly every unit clears the Czech-tuned threshold, and Austria has a single gap candidate,
    Art. 14(7) (the Cooperation Group's work programme). That may be right, but it is not measured.
- **Some extra-rule candidates look like real national additions:**
  - SK: § 27b, blocking;
  - DE: § 8, malware defence for the federal administration, and § 55, the voluntary IT security
    label;
  - AT: § 12 and § 14, the national coordination bodies.
- **Some gap candidates are EU-facing duties that the filters do not recognise:**
  - SK Art. 3(5), member states reporting lists of entities to the Commission;
  - duties of the joint bodies, which the scope filter no longer sets aside because the Czech table
    maps many such units: EU-CyCLONe's rules of procedure, Art. 16(4) (SK, DE), and Art. 16(3)(b)
    and (c) (DE); the Cooperation Group's work programme, Art. 14(7) (AT).
- **Searching more acts moves links away from the main act.** With `--measures`, Slovakia's 9 notified
  measures are searched together (85 s). Then 468 EU units get a link and there are no gap candidates.
  But the extra-rule candidates of Act 69/2018 grow from 34 to 49. 92 of the 468 links now go to other
  acts, most to the Electronic Communications Act 452/2021 (41), which leaves more of the main act
  unlinked. The default is therefore the main act alone.
- **Labels and IDs.**
  - Provision labels follow each country's citation style: `§ 24 ods. 3 písm. a)` (SK),
    `§ 32 Abs. 4 lit. a` (AT), `§ 28 Abs. 1 Nr. 3 Buchst. a` (DE).
  - EU provisions are labelled in the language version used.
  - NIS2 IDs differ only by language code, so a future Slovak, Austrian or German correlation table
    plugs into `GOLD_FILES` unchanged.

## v0.5 held-out generalisation: six more official tables

*Run on 2026-10-12 with OpenAcquis 0.5.0, `hybrid-lex` (the stdlib default), texts from the cache the gold sets
were built with.*

Every threshold, weight and filter above was chosen on **NIS2 × Czechia**. Version 0.5 adds six more official
correlation tables ([data/eval/README.md](../data/eval/README.md), [data/eval/README_sk.md](../data/eval/README_sk.md)).
They are used **only as held-out tests**: the matcher, the link threshold (0.17) and the gap margin (0.01) were
not changed after looking at them, and nothing below was tuned on them.

```bash
openacquis eval --list                     # the seven sets
openacquis eval cer-cz                     # = openacquis eval 32022L2557 cz
openacquis eval 32023L0970 sk -e hybrid-lex -o pay_sk.json
```

What is scored. The matcher searches the act(s) each table was checked against (the main act) in that version;
gold links into other acts are left out (the Slovak tables also list amendments to other acts: NIS2 × SK keeps
443 of 470 links, CER × SK 326 of 414, Pay × SK 109 of 232). Slovak *negative* records ("nothing transposes
this", `n.a.`) are never gold links. `--all-acts` searches and scores every act a table cites.

| Set | Act searched | Gold EU provisions / pairs | R@1 | R@5 | R@10 | MRR | § R@5 | Link P (in scope) | EU recall | Gap candidates (not in table) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NIS2 × CZ (**tuning set**, for reference) | 264/2025 | 197 / 568 | 0.431 | 0.609 | 0.685 | 0.505 | 0.807 | 0.329 (0.437) | 0.457 | 16 (13) |
| CER × CZ | 266/2025 | 94 / 229 | 0.468 | 0.638 | 0.734 | 0.539 | 0.787 | 0.420 (0.567) | 0.489 | 10 (7) |
| Accessibility Act × CZ | 424/2023 | 114 / 146 | 0.781 | 0.895 | 0.912 | 0.829 | 0.921 | 0.541 (0.787) | 0.781 | 2 (1) |
| Whistleblower × CZ | 171/2023 | 97 / 683 | 0.361 | 0.639 | 0.753 | 0.468 | 0.804 | 0.314 (0.396) | 0.361 | 22 (9) |
| NIS2 × SK | 69/2018 | 195 / 442 | 0.385 | 0.600 | 0.662 | 0.473 | 0.785 | 0.349 (0.482) | 0.405 | 7 (4) |
| CER × SK | 367/2024 | 122 / 321 | 0.656 | 0.836 | 0.861 | 0.738 | 0.918 | 0.506 (0.657) | 0.680 | 2 (2) |
| Pay Transparency × SK | 76/2026 | 92 / 109 | 0.793 | 0.902 | 0.913 | 0.837 | 0.957 | 0.625 (0.806) | 0.793 | 14 (10) |

*Re-run after a code change (2026-10-12, same day, still 0.5.0):* national units that only cite the
directive or announce its transposition ("Mit diesem Bundesgesetz wird die Richtlinie … umgesetzt", the Slovak
annex "Zoznam preberaných právne záväzných aktov", a list item that is mostly the directive's full title) are no
longer candidates of any EU provision, except that a transposition clause stays a candidate of the directive's
"Transposition" article, which the official tables pair it with. This changes the candidate pool, not a threshold
or weight. It was prompted by the static pages of pairs without a table (one such unit was the top link of 30 of
73 rows of Representative Actions × DE), not by these scores. The rows above are the re-run; Whistleblower × CZ,
NIS2 × SK and CER × SK also reflect revised gold files (683, 442 and 321 pairs). On the same gold files, with
and without the change: the Czech sets and Pay × SK are identical (1–2 units leave the pool: § 1 transposition
clauses, the Slovak transposition § and annex); NIS2 × SK R@1 0.379 → 0.385, R@5 0.595 → 0.600, § R@5
0.769 → 0.785, with 2 more gap candidates (5 → 7, both not in the table: rows of Annexes I and II whose only
link was the annex listing the directive); CER × SK R@1 0.648 → 0.656, link precision 0.497 → 0.506. `openacquis
eval` prints the excluded units next to the unit counts. All seven rows were re-run on the final gold files (the Pay × SK revision only moved
links into other acts, such as the Constitution, so its 109 scored pairs are unchanged); on the rebuilt page of
Representative Actions × DE the unit (VDuG § 2 Abs. 1 Nr. 2) is the top link of no row.

R@k, MRR: EU provision → national provision, as in the tables above (hit = gold provision, inside it or
containing it). § R@5: the right § in the top 5. Link P: precision of the best candidate per EU unit at the
threshold 0.17, over all predictions and (in brackets) over EU units the table covers. EU recall: share of gold
EU provisions with a correct predicted link. Gap candidates: how many, and how many of them the table does not
list (a listed one is a false alarm against the table).

**What it shows.**

- **No collapse outside the tuning set.** On every held-out set R@5 is at least the tuning set's 0.609 except
  NIS2 × SK (0.600), and § R@5 is 0.78–0.96. The pipeline was not overfitted to NIS2 × CZ in a way that shows up
  here.
- **Slovak works without Slovak tuning.** NIS2 × SK (same directive, other language and law) is a little below
  NIS2 × CZ; CER × SK and Pay × SK are well above it.
- **New, single-purpose acts are easy; amended or broad acts are hard.** The Accessibility Act, the Slovak CER
  and equal-pay acts were written for their directive and follow its wording (R@1 0.66–0.79). Whistleblower × CZ
  is hardest at provision level (R@1 0.361): its table pairs whole EU articles with ranges of Czech §§ (685 links
  from 49 rows), so per-provision pairs are noisy and the § level (0.804) is the fairer number.
- **Gap candidates stay a reading list.** Pooled over the six held-out sets, 33 of 57 gap candidates (58 %) are
  provisions the table does not list. That is a reading list for reviewers, not a finding; on
  Whistleblower × CZ 13 of 22 are listed in the table.
- **The thresholds travel.** The best in-sample link threshold per set ranges from 0.16 to 0.24
  (`best_threshold` in the JSON reports), around the default 0.17; we did not change it.

**Caveats.** These tables describe government **bills**; their numbering was mapped to the acts as published
by text comparison (renumbered and unresolved IDs are listed in each `_summary.json`). The Czech tables have no
compatibility column; the Slovak ones mark almost everything as fully compatible and never flag gold-plating, so
they cannot test the extra-rule candidates. Agreement with a table is not legal correctness.

## LLM judge (NIS2 × CZ)

*Run on 2026-10-12 with OpenAcquis 0.5.0 and prompt `judge-cs-2026-10-12.2`; scored on 2026-10-13. The judgments
are in [`data/judgments/32022L2555_cz.jsonl`](../data/judgments/32022L2555_cz.jsonl).*

The pair judge ([judge.md](judge.md)) was run once on NIS2 × Czechia. There is no API key, so it was not run
through the Anthropic API or the batch runner. The same prompts were answered by **Claude Opus 5.5 in Claude Code
subagents** inside the maintainer's Claude subscription
([how](judge.md#running-it-in-claude-code-no-api-key)). The pairs are the **top 3 machine candidates** of each of the
202 rows (directive paragraphs) of the website's alignment page `site/static-data/32022L2555_cz.json`
(`hybrid-lex`), which makes **606 pairs**. Each of 31 subagents judged 20 pairs with the exact Czech system prompt
and user messages of the API judge. `scripts/judge_session.py collect` then ran the usual evidence checks.
**All 96 related judgments have quotes found verbatim in both texts.** Labels: unrelated 510, partial 57, same 16,
option 9, different value 8, stricter 6.

Reproduce the scores (offline, stdlib):

```bash
python scripts/judge_session.py evaluate data/judgments/32022L2555_cz.jsonl --examples 23
```

**What is scored.** We only score pairs whose EU row the official table covers: 414 pairs in 138 rows. A candidate
counts as **confirmed** when the table links the same EU unit, or one above or below it, to the same national
provision, or one above or below it. "§ level" only asks for the same act and §. "Related" means any label except
`unrelated`, at any rank.

| Selection (414 pairs in scope) | Pairs | Precision | Precision, § level |
|---|---:|---:|---:|
| Machine top-1 candidate (no judge) | 138 | 0.449 | 0.558 |
| Judge: related (any label but `unrelated`, rank 1–3) | 94 | **0.755** | **0.883** |
| Judge: `same` or `partial` | 71 | **0.831** | **0.915** |

| Agreement with the table | |
|---|---|
| Table-confirmed candidates the judge keeps (labels them related) | 71 of 106 (67 %) |
| Candidates not in the table that the judge rejects (`unrelated`) | 285 of 308 (93 %) |
| Related, but not in the table | 23 |

**What it shows.** The judge filters well. Keeping only the pairs it calls related raises precision against the
table from 0.45 (machine top-1) to 0.76, or 0.83 for `same`/`partial`. At § level that is 0.88–0.92. It rejects
93 % of the candidates the table does not list. It also finds table links at rank 2 or 3, where the machine's
top-1 is wrong: 39 of the 94 related pairs are not the top-1 candidate.

**Caveats. Read these before quoting the numbers.**

- **Recall is bounded by the candidates.** The judge only saw the top 3 machine candidates per row. Of the 138
  covered rows, only 75 have a table-confirmed provision among them, and the judge keeps one for 51. The machine's
  top-1 is confirmed for 62. Higher precision comes with fewer rows that have any link: 58 of 138 rows get a
  related candidate. Whatever the matcher misses, the judge cannot find.
- **It drops a third of the table's links.** 35 table-confirmed candidates were labelled `unrelated`. Some are
  probably judge errors. Others are table links that the two texts alone do not show (the table pairs whole
  paragraphs with § ranges, or definitions with the provisions that use them). The prompt tells the judge to use
  only the two texts.
- **The 23 related pairs the table does not list could be real finds or errors.** Nobody has reviewed them. Five
  examples, with the judge's rationale as written in the JSONL:
  1. Art. 3(4) → § 34(1)(c), `partial` (0.80): "Both require current contact details including e-mail addresses
     and telephone numbers. The national item does not mention address or IP ranges, and it does not cover the
     other listed items or the duty to notify changes." This may be a real find: the table pairs Art. 3(4) with
     §§ 6, 9 and 11, not with § 34.
  2. Art. 26(5) → § 54(3), `option` (0.75): "The directive allows a requested Member State to take supervisory
     measures within the limits of a mutual assistance request; Czechia uses this, letting the Office inspect
     providers with a main establishment elsewhere only on, and within the scope of, such a request."
  3. Art. 27(3) → § 11(2), `stricter` (0.55): "Both require notification of changes to submitted data; the
     directive allows up to three months, the Czech act 14 days, though only for data not held as reference data
     in base registers." This is a gold-plating candidate the table, which has no compatibility column, cannot
     confirm.
  4. Art. 34(4) → § 59(4)(b), `different_value` (0.50): "Both set maximum fines, but the Czech letter sets CZK
     175 million or 1.4% of turnover, below the directive's EUR 10 million or 2% for essential entities. It
     appears to address a different (lower) tier of offences." Most likely the wrong tier of offences: a
     wrong pairing, not a difference in values.
  5. Art. 15(2) → § 42(4)(f), `partial` (0.50): "The directive says the CSIRT network consists of representatives
     of national CSIRTs and CERT-EU, with the Commission as observer and ENISA as secretariat. The Czech provision
     covers only the national side, sending representatives to the CSIRT network." This is a loose match. The same
     Czech letter is called `partial` for four different EU paragraphs (Art. 10(6), 14(3), 15(2) and 16(2)).
- **One run, one model, not the API.** It was a single run with no repeat and no inter-run agreement. The model was
  Opus 5.5 in Claude Code subagents, not the default Sonnet 5.5 through the batch API. Structured output was not
  enforced: the subagents wrote JSON files that `collect` validated. Subagents may differ in calibration. The
  confidences are uncalibrated: 19 of the 96 related judgments have confidence 0.5 or less.
- **The pairs differ from the API path's.** They come from the page data, where texts are clipped at 2,500
  characters (21 pairs have a clipped text, noted in their JSONL lines). Rows are directive paragraphs, not the
  473 EU units of `openacquis align`.
- **The table is not perfect.** It describes the 2024 bill, and its numbering was mapped to the act as published
  (see [`data/eval/README.md`](../data/eval/README.md)). It has no compatibility column, so it cannot check the
  `stricter` / `different_value` / `option` labels at all. "Not in the table" does not mean wrong. Agreement with
  the table is not legal correctness.

On the website, every judged candidate on the
[NIS2 × Czechia page](https://jerrywoof.github.io/OpenAcquis/alignment.html?id=32022L2555_cz) shows its label,
confidence, rationale and checked quotes, marked "AI judge (Claude), unreviewed suggestion". A "AI judge says
related" filter lists them, and the matrix counts `stricter` / `different value` labels per article.

### Judge results per pair

Numbers from `scripts/judge_session.py evaluate data/judgments/<pair>.jsonl` against the official correlation table.
All labels are unreviewed machine suggestions.

| Pair | Judged pairs | In scope | Top-1 P (§) | Judge related P (§) | Same/partial P (§) | Kept | Rejected | Verified evidence |
|---|---|---|---|---|---|---|---|---|
| NIS2 × CZ (`32022L2555_cz`) | 606 | 414 (138 EU units) | 0.449 (0.558) | 0.755 (0.883) | 0.831 (0.915) | 71 / 106 (67.0%) | 285 / 308 (92.5%) | 96 / 96 |
| CER × CZ (`32022L2557_cz`) | 309 | 186 (62 EU units) | 0.435 (0.532) | 0.628 (0.744) | 0.635 (0.757) | 49 / 53 (92.5%) | 104 / 133 (78.2%) | 82 / 82 |
| EAA × CZ (`32019L0882_cz`) | 396 | 246 (82 EU units) | 0.756 (0.805) | 0.853 (0.879) | 0.878 (0.898) | 99 / 109 (90.8%) | 120 / 137 (87.6%) | 129 / 129 |
| Whistleblower × CZ (`32019L1937_cz`) | 255 | 162 (54 EU units) | 0.333 (0.389) | 0.720 (0.840) | 0.682 (0.818) | 36 / 41 (87.8%) | 107 / 121 (88.4%) | 56 / 56 |
| NIS2 × SK (`32022L2555_sk`) | 606 | 396 (132 EU units) | 0.379 (0.530) | 0.726 (0.806) | 0.722 (0.791) | 90 / 116 (77.6%) | 246 / 280 (87.9%) | 131 / 133 |
| CER × SK (`32022L2557_sk`) | 309 | 195 (65 EU units) | 0.585 (0.708) | 0.887 (0.975) | 0.897 (0.971) | 71 / 91 (78.0%) | 95 / 104 (91.3%) | 89 / 89 |
| Pay Transparency × SK (`32023L0970_sk`) | 282 | 231 (77 EU units) | 0.584 (0.675) | 0.906 (0.988) | 0.892 (0.986) | 77 / 82 (93.9%) | 141 / 149 (94.6%) | 88 / 88 |

- NIS2 × CZ: run on 2026-10-12, described above.
- CER × CZ: run on 2026-10-13 with Claude Opus 5.5 in 16 Claude Code subagents; labels: unrelated 227, partial 73, same 5, different_value 2, option 2.
- EAA × CZ: run on 2026-10-14 with Claude Opus 5.5 in 20 Claude Code subagents; labels: unrelated 267, partial 80, same 31, different_value 13, stricter 3, option 2.
- Whistleblower × CZ: run on 2026-10-15 with Claude Opus 5.5 in 13 Claude Code subagents; labels: unrelated 199, partial 46, option 4, same 3, stricter 3.
- NIS2 × SK: run on 2026-10-15 with Claude Opus 5.5 in 31 Claude Code subagents; labels: unrelated 473, partial 112, same 12, different_value 6, stricter 2, option 1.
- CER × SK: run on 2026-10-15 with Claude Opus 5.5 in 16 Claude Code subagents; labels: unrelated 220, partial 64, same 13, different_value 5, option 4, stricter 3.
- Pay Transparency × SK: run on 2026-10-16 with Claude Opus 5.5 in 15 Claude Code subagents; labels: unrelated 194, partial 63, same 13, option 5, stricter 5, different_value 2.

### Unmeasured pairs

Pairs without an official correlation table, so there is no score; label counts only. All labels are unreviewed machine suggestions.

| Pair | Judged pairs | Run | Subagents | unrelated | partial | same | different_value | stricter | option | Verified evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| NIS2 × AT (`32022L2555_at`) | 606 | 2026-10-16 | 31 | 425 | 129 | 23 | 12 | 9 | 8 | 181 / 181 |
| NIS2 × DE (`32022L2555_de`) | 606 | 2026-10-16 | 31 | 484 | 82 | 13 | 10 | 6 | 11 | 122 / 122 |
| CER × AT (`32022L2557_at`) | 309 | 2026-10-16 | 16 | 238 | 57 | 8 | 1 | 4 | 1 | 71 / 71 |
| CER × DE (`32022L2557_de`) | 309 | 2026-10-16 | 16 | 237 | 58 | 8 | 3 | 1 | 2 | 72 / 72 |
| EAA × SK (`32019L0882_sk`) | 396 | 2026-10-17 | 20 | 295 | 77 | 14 | 3 | 4 | 3 | 101 / 101 |
| EAA × AT (`32019L0882_at`) | 396 | 2026-10-17 | 20 | 258 | 56 | 58 | 11 | 9 | 4 | 138 / 138 |
| EAA × DE (`32019L0882_de`) | 396 | 2026-10-17 | 20 | 253 | 93 | 35 | 8 | 5 | 2 | 140 / 143 |
| Whistleblower × SK (`32019L1937_sk`) | 255 | 2026-10-18 | 13 | 205 | 41 | 2 | 4 | 0 | 3 | 50 / 50 |
| Whistleblower × AT (`32019L1937_at`) | 255 | 2026-10-18 | 13 | 157 | 58 | 12 | 9 | 9 | 10 | 98 / 98 |
| Whistleblower × DE (`32019L1937_de`) | 255 | 2026-10-18 | 13 | 158 | 68 | 15 | 7 | 3 | 4 | 97 / 97 |
| Pillar Two × CZ (`32022L2523_cz`) | 744 | 2026-10-19 | 38 | 345 | 284 | 97 | 12 | 2 | 4 | 396 / 399 |
