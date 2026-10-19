"""SPEC-77: the manner sweep (G-S0, G-D) on fixed inputs."""

import json
from pathlib import Path

import pytest

from evals.measure import manner_sweep as ms
from evals.steps.pipeline import load_corpus

pytestmark = pytest.mark.unit

W10 = json.loads((Path(__file__).parents[1] / "fixtures/w10_archives.json").read_text())


def test_the_sweep_releases_p2_in_w10s_later_scenes_only():
    """(F) G-S0 on W10: p2 released in scenes 2 and 3 only (scene 1's own plan)."""
    scenes, cast = [], ms.cast_of(load_corpus("cel")[1]["characters"])
    for s in W10["scenes"]["scenes"]:
        blocks = [{"owners": b["movement"]} for b in s["blocks"]]
        scenes.append({"brief": s["brief"], "beats": s["beat_lines"], "chapter": 1})
        scenes[-1] |= {"canon": "", "blocks": blocks}
    store = {k: v for k, v in W10["store"].items() if k != "about"}
    plan = [(n, ln) for n, ch in enumerate(W10["plan"], 1) for ln in ch]
    got = ms.releases(scenes, store, W10["source"]["text"], cast, plan)
    assert [(r[0][:12], r[1], r[3], r[5]) for r in got] == [
        ("Desk Facing ", "Zara Okafor", 2, ["restates", None]),
        ("Initial Here", "Zara Okafor", 2, ["restates", None]),
    ]


def test_the_dracula_mode_lists_every_releasable_speech_beat(capsys):
    """(F) G-D: one row per speech-beat line the product indexes as movement."""
    ms.main(["--book", "dracula"])
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "dracula: 177 speech paragraphs; 3 speech-beat lines"
    assert [ln.split(" [")[0] for ln in out[1:]] == ["p9", "p192", "p271"]
