"""Registry of evaluation (gold) sets: official correlation tables turned into gold links.

One :class:`GoldSet` per directive x country, with where the table came from and which act
it was checked against. :mod:`openacquis.align` (``GOLD_FILES``), :mod:`openacquis.evaluate`,
:mod:`openacquis.dataset` (``OFFICIAL_TABLES``), the command line (``openacquis eval``) and
the website build all read this list, so a new set is added here once.

Czech sets: ``scripts/build_evalset.py`` (government bills on psp.cz, *rozdílové tabulky*).
Slovak sets: ``scripts/build_evalset_sk.py`` (NR SR, *tabuľky zhody*). See
``data/eval/README.md`` and ``data/eval/README_sk.md``.

Only NIS2 x Czechia was used to tune thresholds and filters (``openacquis.align``); every
other set is a held-out test.
"""

from __future__ import annotations

from dataclasses import dataclass

CZ_LICENCE = "Official work (úřední dílo), not protected by copyright (§ 3 of Czech Act No. 121/2000 Sb.)."
SK_LICENCE = ("Legislative draft and its explanatory material, not protected by copyright "
              "(§ 5 of Slovak Act No. 185/2015 Z. z.).")
CZ_PUBLISHER = "Government of the Czech Republic / Chamber of Deputies (psp.cz)"
SK_PUBLISHER = "Government of the Slovak Republic / National Council (nrsr.sk)"


@dataclass(frozen=True)
class GoldSet:
    """One official correlation table as an evaluation set."""

    name: str  # CLI name, e.g. "nis2-cz"
    celex: str
    country: str  # adapter code
    path: str  # JSONL, relative to the repository root
    title: str  # the table, as cited on the website and in the dataset
    url: str  # the table file
    history_url: str  # the bill's page
    acts: tuple[str, ...]  # main act(s) the set was checked against (evaluation searches these)
    version: str | None = None  # version of the acts the IDs follow
    publisher: str = CZ_PUBLISHER
    licence_note: str = CZ_LICENCE
    tuned: bool = False  # True only for the set thresholds were tuned on

    @property
    def key(self) -> tuple[str, str]:
        return (self.celex, self.country)


_SETS = (
    GoldSet("nis2-cz", "32022L2555", "cz", "data/eval/nis2_cz.jsonl",
            "Rozdílová tabulka, government bill of the Cybersecurity Act (sněmovní tisk 759/0, 2024)",
            "https://www.psp.cz/sqw/text/orig2.sqw?idd=244067", "https://www.psp.cz/sqw/historie.sqw?o=9&t=759",
            ("264/2025",), "2025-11-01", tuned=True),
    GoldSet("cer-cz", "32022L2557", "cz", "data/eval/32022L2557_cz.jsonl",
            "Rozdílová tabulka, government bill of the Critical Entities Act (sněmovní tisk 947)",
            "https://www.psp.cz/sqw/text/orig2.sqw?idd=253085", "https://www.psp.cz/sqw/historie.sqw?o=9&t=947",
            ("266/2025",), "2025-08-19"),
    GoldSet("eaa-cz", "32019L0882", "cz", "data/eval/32019L0882_cz.jsonl",
            "Rozdílová tabulka, government bill of the Accessibility Requirements Act (sněmovní tisk 539)",
            "https://www.psp.cz/sqw/text/orig2.sqw?idd=232281", "https://www.psp.cz/sqw/historie.sqw?o=9&t=539",
            ("424/2023",), "2025-07-01"),
    GoldSet("whistleblower-cz", "32019L1937", "cz", "data/eval/32019L1937_cz.jsonl",
            "Rozdílová tabulka, government bill of the Whistleblower Protection Act (sněmovní tisk 352)",
            "https://www.psp.cz/sqw/text/orig2.sqw?idd=221044", "https://www.psp.cz/sqw/historie.sqw?o=9&t=352",
            ("171/2023",), "2025-02-15"),
    GoldSet("nis2-sk", "32022L2555", "sk", "data/eval/nis2_sk.jsonl",
            "Tabuľka zhody, government bill amending the Cybersecurity Act (NR SR print 508; Act 366/2024 Z. z.)",
            "https://www.nrsr.sk/web/Dynamic/Download.aspx?DocID=553679",
            "https://www.nrsr.sk/web/Default.aspx?sid=zakony/cpt&ZakZborID=13&CisObdobia=9&ID=508",
            ("69/2018",), "2025-01-01", SK_PUBLISHER, SK_LICENCE),
    GoldSet("cer-sk", "32022L2557", "sk", "data/eval/cer_sk.jsonl",
            "Tabuľka zhody, government bill of the Critical Infrastructure Act (NR SR print 516; Act 367/2024 Z. z.)",
            "https://www.nrsr.sk/web/Dynamic/Download.aspx?DocID=553726",
            "https://www.nrsr.sk/web/Default.aspx?sid=zakony/cpt&ZakZborID=13&CisObdobia=9&ID=516",
            ("367/2024",), "2025-01-01", SK_PUBLISHER, SK_LICENCE),
    GoldSet("pay-sk", "32023L0970", "sk", "data/eval/pay_sk.jsonl",
            "Tabuľka zhody, government bill of the Equal Pay Act (NR SR print 1142; Act 76/2026 Z. z.)",
            "https://www.nrsr.sk/web/Dynamic/Download.aspx?DocID=583022",
            "https://www.nrsr.sk/web/Default.aspx?sid=zakony/cpt&ZakZborID=13&CisObdobia=9&ID=1142",
            ("76/2026",), "2026-06-07", SK_PUBLISHER, SK_LICENCE),
)

#: Name -> set, in the order above.
GOLD_SETS: dict[str, GoldSet] = {s.name: s for s in _SETS}
#: (CELEX, country) -> set.
BY_PAIR: dict[tuple[str, str], GoldSet] = {s.key: s for s in _SETS}


def find(name_or_celex: str, country: str | None = None) -> GoldSet:
    """``find("nis2-cz")``, ``find("32022L2557", "cz")`` or ``find("32022L2557_cz")``; ``LookupError`` if none."""
    key = name_or_celex.strip()
    if country is None and key.lower() in GOLD_SETS:
        return GOLD_SETS[key.lower()]
    if country is None and "_" in key:
        key, country = key.split("_", 1)
    if country is not None:
        s = BY_PAIR.get((key.upper(), country.strip().lower()))
        if s is not None:
            return s
    raise LookupError(f"no evaluation set for {name_or_celex}{' ' + country if country else ''}; "
                      f"available: {', '.join(f'{s.name} ({s.celex} {s.country})' for s in _SETS)}")


__all__ = ["BY_PAIR", "GOLD_SETS", "GoldSet", "find"]
