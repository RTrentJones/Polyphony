"""SPEC-98 P2: the judges' preference items carry nothing from the key.

python -m evals.calibration.prefs_guard items.json key.json

Refuses (exit 1, naming the item) when an item holds a key field, an arm or
ablation name, a writer, or a run id from the key, or when either file is not
the schema it claims. Prints counts only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from evals.calibration.answer import ITEMS_SCHEMA
from evals.calibration.prefs import KEY_SCHEMA

ITEM_FIELDS = {
    "id",
    "kind",
    "book",
    "context",
    "text_a",
    "text_b",
    "question",
    "allow_none",
}
WORDS = ("pov_fix", "no_excerpts", "no_voices", "flash-lite", "claude")


def leaks(items: dict, key: dict) -> list[str]:
    out = []
    if items.get("schema") != ITEMS_SCHEMA:
        out.append(f"items schema is {items.get('schema')!r}")
    if key.get("schema") != KEY_SCHEMA:
        out.append(f"key schema is {key.get('schema')!r}")
    runs = {
        v[side]["run"] for v in (key.get("key") or {}).values() for side in ("a", "b")
    }
    for it in items.get("items") or []:
        extra = set(it) - ITEM_FIELDS
        if extra:
            out.append(f"{it.get('id')}: unexpected fields {sorted(extra)}")
        meta = " ".join(str(it.get(f, "")) for f in ("id", "book", "question"))
        text = json.dumps(it).lower()
        for w in WORDS:
            if w in meta.lower():
                out.append(f"{it.get('id')}: names {w!r}")
        for r in runs:
            if r and r in text:
                out.append(f"{it.get('id')}: holds run id {r}")
    return out


def main(argv: list[str] | None = None) -> int:
    items_path, key_path = (argv or sys.argv[1:])[:2]
    items = json.loads(Path(items_path).read_text("utf-8"))
    key = json.loads(Path(key_path).read_text("utf-8"))
    found = leaks(items, key)
    for line in found:
        print(f"::error title=Preference items::{line}")
    print(f"{len(items.get('items') or [])} judge items, {len(found)} leaks")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
