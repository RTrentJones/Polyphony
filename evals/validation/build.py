"""W19 S7 (SPEC-94, D19-9): the blinded RN/RC validation set, built deterministically.

Each chapter in `chapters.json` is an intact chapter of a development corpus's
renamed excerpt, split into four scenes at paragraph boundaries, with a plan
(title, the change the chapter makes, one beat per scene). From it come the
intact version and damaged variants:

- narrator_drift: the RN scene replaced by another narrator's passage of about
  its length (the same book's other voice_sources narrator).
- narrator_drift_in (trimmed chapters): only paragraphs inside the RN scene's
  trimmed middle replaced, so RN reads the drift and RC cannot.
- duplicated_event: a paragraph of an earlier scene repeated verbatim in a
  later one, where RC reads it.
- duplicated_event_in (trimmed chapters): the repeat inside a trimmed middle.
- broken_transitions: the scenes reordered so no scene follows its original
  predecessor.
- planned_change_removed: the paragraphs where the plan's change happens cut.

Every damage is located against RC's trimming (pipeline.rc_content): `out` when
RC's input shows all of it, `in` when none, `partial` otherwise, with the
fraction shown. A cut is located in the intact chapter (what RC would have
read). The items carry no variant, damage or chapter label; the key does.

python -m evals.validation.build [--seed N] [--items F --key F] [--estimate]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path

from evals.steps.pipeline import (
    _RC_CHAPTER_WORDS,
    CORPORA,
    _chapter_body,
    _paragraphs,
    load_excerpts,
    narrator_samples,
)

CONFIG = Path(__file__).with_name("chapters.json")
DEFAULT_SEED = 19
ITEMS_SCHEMA = "polyphony-rnrc-validation-items/1"
KEY_SCHEMA = "polyphony-rnrc-validation-key/1"
ITEM_FIELDS = ("id", "book", "narrator", "plan", "scenes")
DAMAGES = (
    "narrator_drift",
    "duplicated_event",
    "broken_transitions",
    "planned_change_removed",
)
# A reorder that keeps no scene after its original predecessor.
REORDER = {3: [2, 1, 0], 4: [2, 0, 3, 1]}
_SEPARATOR = re.compile(r"[*\s]+")


def load_config(path: Path = CONFIG) -> list[dict]:
    return json.loads(path.read_text("utf-8"))["chapters"]


def _check_split(book: str) -> None:
    """Refuse a holdout corpus: validation reads development corpora only."""
    spec = CORPORA / book / "spec.json"
    split = json.loads(spec.read_text("utf-8")).get("split") if spec.exists() else None
    if split == "holdout":
        raise ValueError(f"{book} is a holdout corpus; S7 uses development corpora")


def chapter_paragraphs(book: str, heading: str) -> list[str]:
    """The chapter's paragraphs exactly as the pipeline splits its excerpt."""
    _check_split(book)
    return _paragraphs(_chapter_body(load_excerpts(book), heading))


def _is_separator(p: str) -> bool:
    return bool(_SEPARATOR.fullmatch(p))


def intact_scenes(ch: dict) -> list[list[tuple[int, str]]]:
    """[[(paragraph index, text), ...] per scene], separators dropped."""
    paras = chapter_paragraphs(ch["book"], ch["heading"])
    return [
        [(i, paras[i]) for i in range(a, b + 1) if not _is_separator(paras[i])]
        for a, b in ch["scenes"]
    ]


def donor_passage(donor: dict, start: int, target: int) -> list[str]:
    """Consecutive paragraphs of the donor chapter from `start`, as many as
    bring the passage nearest `target` words (at least one)."""
    paras = chapter_paragraphs(donor["book"], donor["heading"])
    out, words = [], 0
    for p in paras[start:]:
        if _is_separator(p):
            continue
        if donor.get("quoted"):  # the teller's frame quotes, as narrator_samples
            p = p.strip('"“”').strip()
        n = len(p.split())
        if out and abs(words + n - target) >= abs(words - target):
            break
        out.append(p)
        words += n
    return out


def scene_text(paras: list[str]) -> str:
    return "\n\n".join(paras)


def visibility(scenes: list[list[str]], budget: int = _RC_CHAPTER_WORDS) -> list:
    """Per scene, per paragraph, per word: does RC's input show it? Mirrors
    pipeline.rc_content/_trim_middle: over `budget` words in all, a scene over
    its share keeps its first share//2 and last share - share//2 words."""
    total = sum(len(p.split()) for s in scenes for p in s)
    share = budget // max(1, len(scenes))
    out = []
    for s in scenes:
        n = sum(len(p.split()) for p in s)
        head = max(1, share // 2)
        tail = max(1, share - head)
        cut = total > budget and n > share
        at, scene = 0, []
        for p in s:
            k = len(p.split())
            scene.append(
                [not cut or i < head or i >= n - tail for i in range(at, at + k)]
            )
            at += k
        out.append(scene)
    return out


def trimmed(scenes: list[list[str]], budget: int = _RC_CHAPTER_WORDS) -> bool:
    return any(not all(w) for s in visibility(scenes, budget) for w in s)


def _shown(scenes: list[list[str]], where: list[tuple[int, int]]) -> float:
    """The fraction of the words of paragraphs `where` [(scene, position)]
    that RC's input shows."""
    vis = visibility(scenes)
    words = [w for s, p in where for w in vis[s][p]]
    return sum(words) / len(words) if words else 0.0


def _location(fraction: float) -> str:
    return "out" if fraction == 1 else "in" if fraction == 0 else "partial"


def _insert_at(scenes: list[list[str]], scene: int, para: str, want: str) -> int:
    """The paragraph boundary of `scene` to insert `para` at: the first after
    its opening paragraph that leaves it fully shown (`out`), else the most
    central that leaves it fully hidden (`in`)."""
    n = len(scenes[scene])
    order = (
        list(range(1, n + 1)) + [0]
        if want == "out"
        else sorted(range(1, n), key=lambda b: abs(b - n / 2))
    )
    for b in order:
        trial = [list(s) for s in scenes]
        trial[scene].insert(b, para)
        if _location(_shown(trial, [(scene, b)])) == want:
            return b
    raise ValueError(f"no boundary in scene {scene} leaves the paragraph {want}")


def variants(ch: dict) -> list[dict]:
    """The intact chapter and each damaged variant: {variant, damage, scenes,
    location, shown, rn_scene, detail}."""
    base = intact_scenes(ch)
    texts = [[t for _, t in s] for s in base]
    pos = {i: (s, k) for s, sc in enumerate(base) for k, (i, _) in enumerate(sc)}
    rn = ch["rn_scene"]
    is_trimmed = trimmed(texts)
    out = [dict(variant="intact", damage=None, scenes=texts, location=None, shown=None)]

    def add(variant, damage, scenes, shown, rn_scene=rn, **detail):
        out.append(
            dict(
                variant=variant,
                damage=damage,
                scenes=scenes,
                location="out" if shown is None else _location(shown),
                shown=None if shown is None else round(shown, 4),
                rn_scene=rn_scene,
                detail=detail,
            )
        )

    # narrator drift: the whole RN scene.
    target = sum(len(p.split()) for p in texts[rn])
    passage = donor_passage(ch["donor"], ch["donor"]["start"], target)
    drift = [list(s) for s in texts]
    drift[rn] = passage
    add(
        "narrator_drift",
        "narrator_drift",
        drift,
        _shown(drift, [(rn, k) for k in range(len(passage))]),
        scene=rn,
        donor=ch["donor"]["narrator"],
        words=(target, sum(len(p.split()) for p in passage)),
    )
    if ch.get("drift_in"):
        a, b = ch["drift_in"]["paras"]
        ks = [pos[i][1] for i in range(a, b + 1) if i in pos]
        if any(pos[i][0] != rn for i in range(a, b + 1) if i in pos):
            raise ValueError(f"{ch['id']}: drift_in leaves the RN scene")
        target = sum(len(texts[rn][k].split()) for k in ks)
        passage = donor_passage(ch["donor"], ch["drift_in"]["donor_start"], target)
        drift = [list(s) for s in texts]
        drift[rn][ks[0] : ks[-1] + 1] = passage
        add(
            "narrator_drift_in",
            "narrator_drift",
            drift,
            _shown(drift, [(rn, ks[0] + k) for k in range(len(passage))]),
            scene=rn,
            donor=ch["donor"]["narrator"],
            words=(target, sum(len(p.split()) for p in passage)),
        )
    # a duplicated event, where RC reads it and (trimmed) where it cannot.
    src_scene, src_k = pos[ch["dup"]["source"]]
    para = texts[src_scene][src_k]
    for where, variant in (("out", "duplicated_event"), ("in", "duplicated_event_in")):
        if where not in ch["dup"]:
            continue
        to = ch["dup"][where]
        if to <= src_scene:
            raise ValueError(f"{ch['id']}: the repeat must come in a later scene")
        at = _insert_at(texts, to, para, where)
        dup = [list(s) for s in texts]
        dup[to].insert(at, para)
        add(
            variant,
            "duplicated_event",
            dup,
            _shown(dup, [(to, at)]),
            source_scene=src_scene,
            scene=to,
            source_shown=_shown(dup, [(src_scene, src_k)]),
        )
    # broken transitions: scene order shuffled; every seam is at a scene's
    # head or tail, which RC always shows.
    order = REORDER[len(texts)]
    add(
        "broken_transitions",
        "broken_transitions",
        [texts[i] for i in order],
        None,
        rn_scene=order.index(rn),
        order=order,
    )
    # the planned change cut, located where the intact chapter showed it.
    cut = {pos[i] for i in ch["change"] if i in pos}
    removed = [
        [t for k, t in enumerate(s) if (n, k) not in cut] for n, s in enumerate(texts)
    ]
    kept = [s for s in removed if s]
    add(
        "planned_change_removed",
        "planned_change_removed",
        kept,
        _shown(texts, sorted(cut)),
        rn_scene=rn - sum(1 for s in removed[:rn] if not s),
        paragraphs=sorted(ch["change"]),
        scenes_left=len(kept),
    )
    for v in out:
        v.setdefault("rn_scene", rn)
        v.setdefault("detail", {})
        v["rc_trimmed"] = trimmed(v["scenes"])
        if v["rc_trimmed"] != is_trimmed and v["variant"] != "planned_change_removed":
            raise ValueError(f"{ch['id']} {v['variant']}: changes whether RC trims")
    return out


def sample_overlap(ch: dict) -> list[int]:
    """Indices of the RN scene's paragraphs that hold one of the narrator's
    samples (none, by construction of chapters.json)."""
    samples, _ = narrator_samples(ch["book"], ch["narrator"])
    scene = intact_scenes(ch)[ch["rn_scene"]]
    return [i for i, t in scene for s in samples if s[:80] in t]


def _digest(items: list[dict]) -> str:
    blob = json.dumps(items, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


def build(seed: int = DEFAULT_SEED, chapters: list[dict] | None = None):
    """(items, key): every variant of every chapter, in a seeded order, ids
    drawn from the same seed. Items show only ITEM_FIELDS."""
    rows = []
    for ch in chapters if chapters is not None else load_config():
        for v in variants(ch):
            rows.append((ch, v))
    rng = random.Random(seed)
    rng.shuffle(rows)
    ids: set[str] = set()
    items, key = [], {}
    for ch, v in rows:
        while (i := f"v{rng.getrandbits(32):08x}") in ids:
            pass
        ids.add(i)
        items.append(
            {
                "id": i,
                "book": ch["book"],
                "narrator": ch["narrator"],
                "plan": ch["plan"],
                "scenes": [scene_text(s) for s in v["scenes"]],
            }
        )
        key[i] = {
            "chapter": ch["id"],
            "variant": v["variant"],
            "damage": v["damage"],
            "location": v["location"],
            "shown": v["shown"],
            "rc_trimmed": v["rc_trimmed"],
            "rn_scene": v["rn_scene"],
            "detail": v["detail"],
        }
    out_items = {"schema": ITEMS_SCHEMA, "n": len(items), "items": items}
    out_key = {
        "schema": KEY_SCHEMA,
        "seed": seed,
        "items_digest": _digest(items),
        "items": key,
    }
    return out_items, out_key


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--items", type=Path)
    ap.add_argument("--key", type=Path)
    ap.add_argument("--book", help="estimate for one book's items only")
    ap.add_argument("--estimate", action="store_true", help="print calls and cost")
    a = ap.parse_args(argv)
    items, key = build(a.seed)
    for path, doc in ((a.items, items), (a.key, key)):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    counts: dict[str, int] = {}
    for k in key["items"].values():
        counts[k["variant"]] = counts.get(k["variant"], 0) + 1
    print(json.dumps({"items": items["n"], "variants": counts}))
    if a.estimate:
        from evals.validation.step import estimate

        mine = [it for it in items["items"] if not a.book or it["book"] == a.book]
        print(json.dumps(estimate(mine, key), indent=1))


if __name__ == "__main__":
    main()
