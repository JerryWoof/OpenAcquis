# Evaluation: NIS2 × Czechia

*Run on 2026-10-12 with OpenAcquis 0.3.0.*

How well does automatic matching find the Czech provisions that transpose each NIS2 provision? We
compare it with the **official correlation table** (*rozdílová tabulka*) that the Czech government
attached to the bill of the Cybersecurity Act (sněmovní tisk 759/0, July 2024). The table lists which
bill provision implements which NIS2 provision. [`data/eval/README.md`](../data/eval/README.md) explains
how it became the gold set: 569 links (568 distinct pairs) between 197 NIS2 provisions in 33 articles
and 243 provisions of Act No. 264/2025 Sb.

Reproduce:

```bash
openacquis eval nis2-cz                      # lexical (stdlib), about 4 s once the texts are cached
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
