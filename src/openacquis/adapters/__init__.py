"""National text adapters: fetch consolidated national law and split it into provisions.

Each adapter returns a :class:`openacquis.provisions.Document`. Adapters are
stdlib-only, take an injectable fetcher for offline tests, and cache politely.
"""

from .cz import ESbirkaClient, fetch_act, parse_fragments, parse_reference

__all__ = ["ESbirkaClient", "fetch_act", "parse_fragments", "parse_reference"]
