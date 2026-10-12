# OpenAcquis

**Open, AI-assisted map of how EU directives are written into national law: provision by provision, country by country.**
Spot gaps, gold-plating and national choices, with links to the source texts.

> Status: **early prototype (v0.1)**. Today the tool lists which national laws each member state has notified for a directive. The provision-level matching described below is the roadmap.

---

## Why

An EU directive sets goals; each member state writes its own law to reach them. The EU records *which* national laws a country says implement a directive, but nobody publishes *which section implements which article*, or where a country left something out or added extra rules ("gold-plating").

That knowledge is currently sold by law firms one directive at a time. OpenAcquis aims to make it an open public good.

Example: for the NIS2 cybersecurity directive, the EU database lists **304 national measures from 25 member states**. Czechia alone notified 83, from the new Cybersecurity Act 264/2025 Sb. to amendments of the Criminal Code. Which of them implements Article 23 (incident reporting)? Today you have to read them all.

## What it will look like

| NIS2 article | 🇨🇿 CZ | 🇸🇰 SK | 🇦🇹 AT | 🇩🇪 DE |
|---|---|---|---|---|
| Art. 23(4)(a): early warning within 24 h | ✅ same | ⚠️ stricter | 🔍 review | ❌ not found |

Every cell links to the exact text on both sides. Every AI suggestion is labelled as a suggestion until a human reviews it. *(Illustrative; not real results.)*

## How it works

```
EUR-Lex / CELLAR ──► which national acts implement directive X   (works today)
        │
        ▼
Directive text (24 languages) + national law texts (national portals)
        │
        ▼
Split into provisions ──► multilingual matching ──► LLM judge per pair
        │                                            (same / partial / different value /
        ▼                                             stricter / missing / option chosen)
Leftovers: unmatched EU articles = gap candidates; unmatched national rules = gold-plating candidates
        │
        ▼
Human review ──► open dataset (CC-BY) ──► website, API, change alerts
```

More detail: [docs/how-it-works.md](docs/how-it-works.md) · Data: [docs/data-sources.md](docs/data-sources.md) · Plan: [ROADMAP.md](ROADMAP.md)

## Quickstart

Requires Python 3.10+. No API keys; no dependencies.

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

  CZE Czechia        83 ████████████████████████████████████████
  HUN Hungary        39 ███████████████████████████████████████
  ...
  ESP Spain           0  (none notified)
```

CELEX numbers identify EU acts: `3` + year + `L` (directive) + number. NIS2 = `32022L2555`, Accessibility Act = `32019L0882`, Pay Transparency = `32023L0970`.

## Contributing

There is a lot to do, and much of it is welcoming to newcomers:

- **Country adapters**: fetch and split national law for your country (each country is a separate module).
- **Legal review**: law students and practitioners checking AI suggestions.
- **Evaluation data**: Czech government bills include official correlation tables (*rozdílové tabulky*) that pair Czech sections with EU articles; turning them into a test set is a great first project.

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Disclaimer

OpenAcquis is a research and transparency tool, **not legal advice**. Outputs are candidates with evidence, not findings that a state has breached EU law. Always check the official texts.

## License

Code: [MIT](LICENSE). Published datasets will be CC-BY-4.0. EUR-Lex data is reused under the European Commission's reuse policy (Decision 2011/833/EU); source: © European Union, https://eur-lex.europa.eu.
