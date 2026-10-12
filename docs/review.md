# Reviewing machine suggestions

OpenAcquis proposes three kinds of suggestions for each directive × country:

| Kind | The machine says | Example |
|---|---|---|
| `link` | this national provision implements this EU provision | NIS2 Art. 23(4)(a) → § 16 odst. 1 of Act 264/2025 Sb. |
| `gap` | nothing in the acts searched implements this EU provision | NIS2 Art. 32(7)(a) in Czechia |
| `extra_rule` | this provision of the main act implements nothing in the directive | § 27b (blocking) of Slovak Act 69/2018 Z. z. |

All three are **candidates** until a person checks them. Reviews are what turn them into data: a
reviewed link goes into the open dataset ([dataset.md](dataset.md)), and reviews are the only way to
check the countries that have no official correlation table (Austria and Germany so far).

On a *baseline* pair (registry status `baseline`: the act aligned was not notified for the directive, such as
Pay Transparency × AT and DE) the review page says so and asks whether the national provision **covers** the EU
provision, not whether it implements it. Such reviews enter the dataset with `relation = existing_law`.

There is no server and no account system. The website prepares a review record and you send it
through GitHub.

## How to review (website)

1. Open the review page for a country:
   [Czechia](https://jerrywoof.github.io/OpenAcquis/review.html?id=32022L2555_cz),
   [Slovakia](https://jerrywoof.github.io/OpenAcquis/review.html?id=32022L2555_sk),
   [Austria](https://jerrywoof.github.io/OpenAcquis/review.html?id=32022L2555_at),
   [Germany](https://jerrywoof.github.io/OpenAcquis/review.html?id=32022L2555_de).
2. Filter by kind (links, gap candidates, extra-rule candidates) and by article if you like. The page
   shows unreviewed suggestions one at a time, with the EU text, the suggested national text and the
   other candidates side by side. Every heading links to the official text (EUR-Lex, e-Sbírka,
   Slov-Lex, RIS, gesetze-im-internet.de).
3. Choose a verdict:
   - **Confirm**: the suggestion is right.
   - **Reject**: it is wrong, and you do not know the right answer.
   - **Correct**: it is wrong, and you give the right provision IDs (national IDs for a link or a gap,
     EU IDs for an extra rule). The field suggests the other candidates; any ID in the same act works,
     e.g. `cz:264/2025:par16.odst2` or `eu:32022L2555:cs:art21.par2.pt_a`.

   Add your GitHub user name (remembered in this browser) and, if useful, a note: the sentence or
   subparagraph you mean, another act that does it, why the machine is wrong. The user name must be
   the account that opens the issue or pull request: records filed in someone else's name are
   rejected.
4. Send it:
   - **One record:** "Open prefilled GitHub issue" opens a new issue with the record filled in. Tick
     the licence box and submit. A bot checks the record and comments within a minute or two. If the
     prefilled link would be longer than 7,500 characters (a very long note), the page offers no link
     and asks you to shorten the note or use the batch download.
   - **Several records:** "Add to batch and next" collects records in this browser. "Download JSONL"
     saves them as one file. Attach it to an issue, or add it to `data/reviews/` in a pull request.

What a verdict means: your reading of the two texts. "Confirm" says the pairing looks right, not that
the transposition is complete or correct. If a link is right but partial (the national provision
covers only part of the EU provision), confirm it and say so in the note.

## Record format

`data/reviews/<CELEX>_<cc>.jsonl`, one JSON object per line:

```json
{"schema": 1,
 "suggestion_id": "link:eu:32022L2555:cs:art23.par4.pt_a>cz:264/2025:par16.odst1",
 "directive": "32022L2555", "country": "cz", "kind": "link",
 "eu_id": "eu:32022L2555:cs:art23.par4.pt_a", "national_id": "cz:264/2025:par16.odst1",
 "machine": {"label": "candidate", "score": 0.58, "rank": 1, "method": "hybrid:…", "generator": "openacquis 0.4.0"},
 "verdict": "confirm", "corrected_ids": [],
 "reviewer": "octocat", "date": "2026-10-12", "note": ""}
```

| Field | Rule |
|---|---|
| `schema` | the integer `1` (not `true` or `1.0`) |
| `suggestion_id` | `<kind>:<eu_id or ->><national_id or ->`, exactly; it ties reviews of the same suggestion together |
| `directive`, `country` | CELEX number of a directive; lower-case alpha-2 code with an adapter |
| `kind` | `link` (both IDs), `gap` (`national_id` null), `extra_rule` (`eu_id` null) |
| `eu_id`, `national_id` | OpenAcquis provision IDs ([provisions.md](provisions.md)) of this directive and country; the act part of a national ID uses only `A-Za-z0-9_./-` (at most 40 characters), paths are dotted (`par12.odst2`) |
| `machine` | what was suggested: `label`, `score` (a finite number), `rank`, `method`, `generator` (all optional) |
| `verdict` | `confirm`, `reject` or `correct` |
| `corrected_ids` | required for `correct` (at most 20), empty otherwise; national IDs for `link` / `gap`, EU IDs for `extra_rule` |
| `reviewer` | GitHub user name; for an issue, the user who opened it |
| `date` | `YYYY-MM-DD` |
| `note` | free text, at most 2,000 characters |

No other fields are allowed, and no value may end in a newline. NaN and Infinity are rejected when
records are read, and JSON nested too deeply is reported as "JSON nested too deeply".
`openacquis.review.validate_record` checks all of this. Its error messages never repeat what the
submitter wrote: an unknown field name is shown only if it is a plain name (`[a-z_]`, else "unknown
fields (N)"), and the expected `suggestion_id` only when both IDs are well formed (else
"suggestion_id does not match kind, eu_id and national_id"). The command line does the same:

```bash
openacquis review validate batch.jsonl          # or an issue body saved as a file with ```json blocks
openacquis review add batch.jsonl --author LOGIN   # validate, check every reviewer is LOGIN, append (skips duplicates)
openacquis review stats                         # records and decisions so far
```

## From reviews to decisions

`openacquis.review.merge` folds all records of one suggestion into a decision. Each reviewer's
latest record counts once.

- **Every reviewer agrees:** the decision is `confirmed`, `rejected` or `corrected`. A correction
  counts only when the corrected IDs also agree.
- **Reviewers disagree:** the decision is `disputed`.
- Confirmed and corrected decisions become dataset rows. Rejected and disputed ones do not, but
  their records stay in `reviews.jsonl`.

The review page hides suggestions that already have a record ("Include reviewed" shows them, with
their status).

## The GitHub side

- **Issue form:** `.github/ISSUE_TEMPLATE/review.yml`. It has one JSON field, which the review page
  fills in, and a CC-BY-4.0 licence checkbox. Issues get the `review` label. In an issue body only the
  "Review record" section is read, and the licence box counts only under "Licence", so JSON pasted
  into "Anything else?" is ignored (also by `openacquis review add issue.md`). The checker relies on
  these two headings and the licence label; renaming them in the form breaks it.
- **Workflow:** `.github/workflows/reviews.yml` runs on issues labelled `review`.
  - Job `validate` (read-only token, checkout without stored credentials) runs
    `python -m openacquis.review check-issue FILE AUTHOR` (`openacquis.review.check_issue`). The issue
    body is passed as a file, never interpolated into a script. It validates every record, requires
    every `reviewer` to be the issue author (case-insensitive), and checks that the licence box is
    ticked. Output: JSON `{valid, records, errors}`; exit code 0 if valid, 1 if not, 2 on bad usage.
  - Job `comment` (only `issues: write`, no checkout) reads that JSON, comments the result as escaped
    code spans (at most 20 errors of 300 characters), updating its own comment when the issue is
    edited, and labels the issue `review-valid` or `review-invalid`.
- **The workflow never commits.** This is a deliberate choice. Writing to the repository from an
  issue would need write permissions on a job that processes untrusted text, and every record would
  land without a human glance.
- **Maintainers merge valid records instead.** Save the issue body as a file, run
  `openacquis review add issue.md --author <issue author>`, and open a pull request. For JSON-lines
  files submitted in a pull request, run `openacquis review validate FILE --author <PR author>`:
  nothing else checks that a PR's reviewers are its author. Then close the issue with a link to it.
  CI checks every file in `data/reviews/`: `tests/test_v04_review.py::test_bundled_reviews_validate`.

## Licence and credit

Reviews are published under CC-BY-4.0 as part of the OpenAcquis dataset, credited to the reviewer's
GitHub user name. They are not legal advice and are never presented as findings that a country
breached EU law.
