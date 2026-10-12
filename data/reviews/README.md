# Human reviews

One JSON-lines file per directive and country (`32022L2555_cz.jsonl`, ...), one review record per line, appended
with `openacquis review add <file>` (which validates first). Records come from the website's
[review page](https://jerrywoof.github.io/OpenAcquis/review.html), either as a GitHub issue (checked by
`.github/workflows/reviews.yml`) or as a downloaded batch in a pull request.

Format, verdicts and how records are merged into the dataset: [docs/review.md](../../docs/review.md).
Reviews are published under CC-BY-4.0, credited to the reviewer's GitHub user name.

No reviews have been submitted yet.
