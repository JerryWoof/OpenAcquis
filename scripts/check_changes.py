"""Check the watched EU and national sources for changes (change alerts).

    python scripts/check_changes.py [check] [--out-dir DIR] [--dry-run] [--sources cellar,cz,sk,at,de]
    python scripts/check_changes.py feed [--out site/feed.xml]

``check`` compares each source's cheap change signal with ``data/alerts/state.json``, prints a JSON summary,
rewrites the state file (unless only timestamps changed) and, with ``--out-dir``, writes ``changes.json``,
``summary.md``, one ``issues/NNN/`` folder per change and ``health.md`` for the alerts workflow
(.github/workflows/alerts.yml). Watch list: ``data/directives.json`` if it exists, else NIS2 and its main
act in CZ, SK, AT and DE. ``feed`` renders the Atom feed from the state file (run at site build time).
See docs/alerts.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from openacquis.alerts import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], root=ROOT))
