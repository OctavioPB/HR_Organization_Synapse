"""Download the public SNAP email-Eu-core networks used by the real-network test bed.

The datasets are NOT committed to the repository (SNAP states no redistribution
license, only a citation request), so they are fetched on demand into
data/external/snap/ (gitignored).  tests/validation/real_network_evaluator.py
skips its tests when the files are absent.

    python scripts/fetch_external_data.py          # download + verify
    python scripts/fetch_external_data.py --check  # verify only

Datasets (https://snap.stanford.edu/data/):
  email-Eu-core.txt.gz                     directed "u emailed v" edges, 1,005 people
  email-Eu-core-department-labels.txt.gz   NODEID DEPARTMENT (42 departments)
  email-Eu-core-temporal.txt.gz            SRC DST TS (seconds from 0), 986 people, 803 days
                                           — no department labels; node IDs differ from the static file

Please cite when publishing results:
  H. Yin, A. R. Benson, J. Leskovec, D. F. Gleich. Local Higher-order Graph Clustering. KDD 2017.
  J. Leskovec, J. Kleinberg, C. Faloutsos. Graph Evolution: Densification and Shrinking Diameters. ACM TKDD 2007.
  A. Paranjape, A. R. Benson, J. Leskovec. Motifs in Temporal Networks. WSDM 2017.
"""

from __future__ import annotations

import argparse
import gzip
import sys
import urllib.request
from pathlib import Path

BASE_URL = "https://snap.stanford.edu/data/"
DEST = Path(__file__).resolve().parents[1] / "data" / "external" / "snap"

# file → expected (line count, columns per line), from the SNAP dataset pages.
FILES: dict[str, tuple[int, int]] = {
    "email-Eu-core.txt.gz": (25_571, 2),
    "email-Eu-core-department-labels.txt.gz": (1_005, 2),
    "email-Eu-core-temporal.txt.gz": (332_334, 3),
}


def _verify(path: Path, expected_lines: int, columns: int) -> str | None:
    """Return an error message, or None if the file parses as expected."""
    try:
        with gzip.open(path, "rt", encoding="ascii") as fh:
            n = 0
            for line in fh:
                parts = line.split()
                if not parts:
                    continue
                if len(parts) != columns or not all(p.isdigit() for p in parts):
                    return f"{path.name}: unexpected line {line.strip()[:60]!r}"
                n += 1
    except (OSError, UnicodeDecodeError) as exc:
        return f"{path.name}: unreadable ({exc})"
    if n != expected_lines:
        return f"{path.name}: {n} lines, expected {expected_lines}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verify existing files only")
    args = parser.parse_args()

    DEST.mkdir(parents=True, exist_ok=True)
    failures = 0
    for name, (lines, cols) in FILES.items():
        path = DEST / name
        if not path.exists() and not args.check:
            print(f"downloading {name} …")
            tmp = path.with_suffix(path.suffix + ".part")
            req = urllib.request.Request(BASE_URL + name, headers={"User-Agent": "org-synapse-testbed/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
                out.write(resp.read())
            tmp.replace(path)
        if not path.exists():
            print(f"MISSING  {name}")
            failures += 1
            continue
        err = _verify(path, lines, cols)
        if err:
            print(f"INVALID  {err}")
            failures += 1
        else:
            print(f"ok       {name} ({lines:,} lines)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
