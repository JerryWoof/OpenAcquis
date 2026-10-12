# Contributing to OpenAcquis

Thanks for your interest! The project needs programmers, lawyers and people who know their country's legal databases.

## Ways to help
- **Country adapter**: write a module that downloads consolidated national laws for one member state and splits them into provisions. Open an issue titled `adapter: <country>` first so work isn't duplicated.
- **Legal review**: check AI-suggested matches. No coding needed.
- **Evaluation data**: convert official correlation tables (for example Czech *rozdílové tabulky*) into the test format.
- **Docs and translations**.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest -m "not live"   # offline tests
pytest                 # also hits the real EU endpoint
```

Guidelines:
- Keep the core dependency-free where practical; adapters may add optional extras.
- Every output that comes from AI must keep its evidence (source IDs and text spans) and a confidence label.
- Be polite to public servers: cache, rate-limit and set a descriptive User-Agent.

## Principles
- **Suggestions, not verdicts.** We never state that a country breached EU law.
- **Traceable.** Every claim links to the official source.
- **Open.** Code MIT, data CC-BY-4.0.
