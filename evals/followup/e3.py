"""SPEC-95 E3: W12-9's bundles re-rated with Claude Sonnet, on P⁵'s frozen code.

`evals/baseline/` is byte-identical to P⁵ (`f57f026`); this module asserts the
blob ids and then calls the frozen `panel.load`, `panel.rate`, `panel.write`,
`panel.fleiss` and `score.win_rate` / `score.rated` unchanged. Only the client
each member gets differs, swapped in at run time:

- Sonnet (`anthropic:claude-sonnet-5-5`) is live, through main's app.llm (the
  Anthropic provider, priced, under the run's MONTHLY_COST_CEILING_USD). A
  reply Claude refuses or truncates is handed back empty, so the frozen code
  re-asks it and, after 3, leaves it unrated (SPEC-84's failure rule).
- W12-9's members (Groq, Nemotron) are replay-only: their answers come from
  W12-9's final rate-state, under the frozen cache key `member|prompt`. A
  prompt not byte-identical to W12-9's misses the cache, and the miss fails
  the run (CacheMiss) instead of calling the free member.
- The retest pass asks Sonnet every prompt again under a separate cache (so
  pass 1's answers are never replayed into it), recorded as its own sheet.

    python -m evals.followup.e3 rate <bundles dir> --out <ratings> [--retest]
    python -m evals.followup.e3 score <ratings> <key.json files...> [--sonnet-fallback]

`score` reads the keys by script and prints aggregates only.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import random
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
# P⁵'s blobs (SPEC-88; the gate's p-freeze row): git hash-object of each file.
FROZEN = {
    "evals/baseline/panel.py": "94433f1c7a394b4bd1098124ab4431d9ba1d905b",
    "evals/baseline/score.py": "186ae2652b876c6f9f73f187deea447f60cbaede",
    "evals/baseline/blind.py": "a7015ff528c13013e8ba06d2682fd965cea04252",
}
SONNET = "anthropic:claude-sonnet-5-5"
GROQ = "groq:openai/gpt-oss-120b"
NEMOTRON = "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free"
REPLAYED = (GROQ, NEMOTRON)
SHEETS = {
    SONNET: "sheet-sonnet.csv",
    f"{SONNET}#retest": "sheet-sonnet-retest.csv",
    GROQ: "sheet-groq.csv",
    NEMOTRON: "sheet-nemotron.csv",
}
TYPES = (("A", "B"), ("A", "C"), ("B", "C"))
AB_FLIP_LIMIT = 9  # pre-registered: more AB flips than this = order-driven
OVERALL = "overall (arm-sorted; not comparable to W12-9's 0.411)"
# Pre-registered replay integrity: the replayed members reproduce W12-9.
W12_9_AB_CONSISTENT = {GROQ: "0/18", NEMOTRON: "8/18"}


class CacheMiss(RuntimeError):
    """A replayed member was asked a prompt W12-9 never asked it."""


def blob_id(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def check_frozen(root: Path = ROOT) -> dict:
    got = {p: blob_id(root / p) for p in FROZEN}
    bad = [p for p, b in got.items() if b != FROZEN[p]]
    if bad:
        raise SystemExit(f"not P⁵'s frozen code: {', '.join(bad)}")
    return got


class _ReplayOnly:
    def __init__(self, provider):
        self.provider = provider

    async def generate(self, *a, **kw):
        raise CacheMiss(
            f"{self.provider.id}: a prompt is not in W12-9's rate-state "
            "(not byte-identical to W12-9's); nothing is asked of the free member"
        )


class _Metered:
    """The live client, metered; an unusable reply comes back empty (re-asked)."""

    def __init__(self, client, usage: dict):
        self._client, self.usage = client, usage
        self.provider = client.provider

    async def generate(self, messages, **kw):
        from types import SimpleNamespace

        from app.llm.errors import UnusableReplyError

        u = self.usage
        try:
            r = await self._client.generate(messages, **kw)
        except UnusableReplyError as e:
            _, tin, tout, cost = e.spent or ("", 0, 0, 0.0)
            u["unusable"] += 1
            r = SimpleNamespace(text="", tokens_in=tin, tokens_out=tout, cost_usd=cost)
        u["calls"] += 1
        u["tokens_in"] += r.tokens_in or 0
        u["tokens_out"] += r.tokens_out or 0
        u["cost_usd"] = round(u["cost_usd"] + (r.cost_usd or 0.0), 6)
        return r


def _new_usage() -> dict:
    return {"calls": 0, "unusable": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}


async def rate(bundle_root: Path, out: Path, retest: bool) -> dict:
    from app.llm.client import LLMClient
    from evals.baseline import panel
    from evals.harness.cache import Cache

    blobs = check_frozen()
    bundles = [panel.load(p) for p in sorted(bundle_root.rglob("bundle.json"))]
    if not bundles:
        raise SystemExit("no bundle to rate")
    cache_dir = os.getenv("EVAL_CACHE_DIR", ".eval-cache")
    usage = {"pass1": _new_usage(), "retest": _new_usage()}
    which = {"pass": "pass1"}
    replay_ids = {m.split(":", 1)[0] for m in REPLAYED}

    def client(provider):  # swapped in for the frozen module's LLMClient
        if provider.id in replay_ids:
            return _ReplayOnly(provider)
        return _Metered(LLMClient(provider), usage[which["pass"]])

    # The replays first: they prove the prompts byte-identical to W12-9's (or
    # fail on CacheMiss) before any Sonnet call is paid for.
    runs = [
        (GROQ, "panel", None),
        (NEMOTRON, "panel", None),
        (SONNET, "panel", "pass1"),
    ]
    if retest:
        runs.append((SONNET, "panel-retest", "retest"))
    got = {}
    with _patched(panel, "LLMClient", client):
        for member, ns, meter in runs:
            which["pass"] = meter or "pass1"
            key = f"{member}#retest" if ns == "panel-retest" else member
            got[key] = await panel.rate(member, bundles, Cache(cache_dir, ns))
    out.mkdir(parents=True, exist_ok=True)
    total = sum(len(b["pairs"]) + len(b["items"]) for b in bundles)
    unrated = {}
    for key, g in got.items():
        panel.write(out / SHEETS[key], g["prefer"], g["errors"])
        unrated[key] = total - len(g["prefer"]) - len(g["errors"])
    # Descriptive: Sonnet + Nemotron under SPEC-84's panel rule.
    px, py = got[SONNET]["prefer"], got[NEMOTRON]["prefer"]
    agreed = {p: px[p] if px[p] == py[p] else "tie" for p in px if p in py}
    ex, ey = got[SONNET]["errors"], got[NEMOTRON]["errors"]
    panel.write(
        out / "sheet-panel-sonnet-nemotron.csv",
        agreed,
        {i: (n + ey[i]) / 2 for i, n in ex.items() if i in ey},
    )
    record = {
        "frozen_blobs": blobs,
        "members": {"live": SONNET, "replayed": list(REPLAYED), "retest": retest},
        "unrated": unrated,
        "usage_usd": usage,
        "sonnet_cost_usd": round(sum(u["cost_usd"] for u in usage.values()), 4),
    }
    (out / "e3.json").write_text(json.dumps(record, indent=1))
    return record


@contextlib.contextmanager
def _patched(module, name: str, value):
    old = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, old)


# --- score ------------------------------------------------------------------


def _arms(judgments: list[tuple]) -> dict:
    """{(slot, frozenset arms): winner arm | None (tie)} from unblinded rows."""
    return {(slot, frozenset((a, b))): won for slot, a, b, won in judgments}


def _kappa(x: dict, y: dict, pair: tuple) -> float | None:
    from evals.baseline.panel import fleiss

    table = []
    for k in x:
        if k in y and k[1] == frozenset(pair):
            got = [x[k], y[k]]
            table.append([sum(g == c for g in got) for c in (*pair, None)])
    return round(fleiss(table), 4) if table else None


def _kappas(x: dict, y: dict) -> dict:
    out = {"".join(t): _kappa(x, y, t) for t in TYPES}
    from evals.baseline.panel import fleiss

    # overall: categories = the arm sorted first / second in each pair, or a
    # tie; not W12-9's bundle-order categories, so not comparable to its 0.411
    table = []
    for k in x:
        if k in y:
            first, second = sorted(k[1])
            table.append(
                [sum(g == c for g in (x[k], y[k])) for c in (first, second, None)]
            )
    out[OVERALL] = round(fleiss(table), 4) if table else None
    return out


def _consistent(judgments: list[tuple]) -> dict:
    """Pairs whose two orders agreed (a member cannot answer a tie)."""
    out = {}
    for t in TYPES:
        rows = [won for _s, a, b, won in judgments if {a, b} == set(t)]
        out["".join(t)] = f"{sum(w is not None for w in rows)}/{len(rows)}"
    return out


def _slot_diff(cont: dict, x: str, y: str, seed: int = 0) -> dict:
    """Mean over slots of continuity(x) - continuity(y), slot bootstrap 95%."""
    slots = sorted({s for s, _ in cont if (s, x) in cont and (s, y) in cont})
    if not slots:
        return {"diff": None, "ci": None, "slots": 0}
    d = {s: cont[(s, x)] - cont[(s, y)] for s in slots}
    rng = random.Random(seed)
    boot = sorted(
        mean(d[s] for s in rng.choices(slots, k=len(slots))) for _ in range(2000)
    )
    return {
        "diff": round(mean(d.values()), 4),
        "ci": [round(boot[50], 4), round(boot[1949], 4)],
        "slots": len(slots),
    }


def _continuity(cont: dict) -> dict:
    """Mean errors per scene for each arm, with the items it rests on; an arm
    with none rated (the fallback leaves unrated items out) is None."""
    out = {}
    for arm in "ABC":
        got = [n for (_s, a), n in cont.items() if a == arm]
        out[arm] = {"mean": round(mean(got), 4) if got else None, "items": len(got)}
    return out


def score(ratings: Path, key: dict, fallback: bool = False) -> dict:
    """`fallback` (pre-registered, only after a Sonnet prompt stayed unrated
    after 3 tries on two rate runs): each pair Sonnet left unrated scores ½,
    flagged and counted; an unrated continuity item is left out."""
    from evals.baseline import score as frozen

    check_frozen()
    meta = json.loads((ratings / "e3.json").read_text())
    unrated = meta["unrated"].get(SONNET)
    if unrated and not fallback:
        raise SystemExit(
            f"Sonnet left {unrated} prompts unrated: resume the rate dispatch "
            "(after two rate runs, the pre-registered fallback: --sonnet-fallback)"
        )
    sheets = {}
    for member, name in SHEETS.items():
        if (ratings / name).exists():
            sheets[member] = frozen.rated(ratings / name, key)
    j, cont = sheets[SONNET]
    slots = {v[0] for v in key["items"].values()}
    # A flip: an AB pair whose two orders both parsed and named different arms.
    flips = sum(1 for _s, a, b, won in j if {a, b} == {"A", "B"} and won is None)
    have = {(sl, frozenset((a, b))) for sl, a, b, _w in j}
    filled = [
        (sl, x, y, None)
        for sl in sorted(slots)
        for x, y in TYPES
        if (sl, frozenset((x, y))) not in have
    ]
    if filled and not fallback:
        raise SystemExit("Sonnet's ratings do not cover every slot's pairs")
    j = j + filled

    def rate_at(judgments, x, y, level):
        with _patched(frozen, "LEVEL", level):
            return frozen.win_rate(judgments, x, y)

    primary = rate_at(j, "B", "A", 0.95)
    lo, hi = primary["ci"]
    replay = {
        m: _consistent(sheets[m][0])["AB"] if m in sheets else None
        for m in W12_9_AB_CONSISTENT
    }
    intact = replay == W12_9_AB_CONSISTENT and not any(
        meta["unrated"].get(m) for m in W12_9_AB_CONSISTENT
    )
    if not intact:
        decision = "void: the replayed members do not reproduce W12-9"
    elif flips > AB_FLIP_LIMIT:
        decision = "undetermined: Sonnet is order-driven on A vs B"
    elif lo > 0.5:
        decision = "Sonnet prefers B to A on W12-9's 18 slots"
    elif hi < 0.5:
        decision = "Sonnet prefers A to B on W12-9's 18 slots"
    else:
        decision = "Sonnet does not separate B from A at n = 18 (not equivalence)"
    out = {
        "label": "free (gemini-3.5-flash-lite, not prod's prose model), Claude Sonnet "
        "judge, Sonnet at default sampling, not human; development corpora; C is the "
        "paid-tier ensemble; exploratory: W12-9's recorded decision (simplify "
        "toward A) stands",
        "replay integrity (AB order consistency, W12-9: groq 0/18, nemotron 8/18)": {
            "got": replay,
            "intact": intact,
        },
        "sonnet fallback (unrated pairs scored ½)": (
            {"pairs": len(filled), "prompts": unrated} if fallback else None
        ),
        "P(B>A) sonnet 95%": primary,
        "P(B>A) sonnet 97.5% (beside W12-9's)": rate_at(j, "B", "A", 0.975),
        "AB order flips (sonnet)": f"{flips}/{len(slots)}",
        "decision": f"{decision} (exploratory; W12-9's decision unchanged)",
        "P(C>A) sonnet, descriptive": rate_at(j, "C", "A", 0.95),
        "P(C>B) sonnet, descriptive": rate_at(j, "C", "B", 0.95),
        "order consistency": {m: _consistent(s[0]) for m, s in sheets.items()},
        "continuity per scene (sonnet)": _continuity(cont),
        "continuity A-B (sonnet)": _slot_diff(cont, "A", "B"),
    }
    pj = frozen.rated(ratings / "sheet-panel-sonnet-nemotron.csv", key)[0]
    sk = _arms(j)
    nk = _arms(sheets[NEMOTRON][0]) if NEMOTRON in sheets else {}
    kappa_panel = _kappas(sk, nk)
    out["panel sonnet+nemotron, descriptive"] = {
        "P(B>A) 95%": rate_at(pj, "B", "A", 0.95),
        "fleiss_kappa": kappa_panel,
        "reading": (
            "no decision (panel κ < 0.2)"
            if (k := kappa_panel[OVERALL]) is not None and k < 0.2
            else "κ ≥ 0.2"
        ),
    }
    out["fleiss_kappa"] = {
        "sonnet~groq": _kappas(sk, _arms(sheets[GROQ][0])) if GROQ in sheets else None,
        "sonnet~nemotron": kappa_panel,
    }
    retest = sheets.get(f"{SONNET}#retest")
    if retest:
        rk = _arms(retest[0])
        same = [k for k in sk if k in rk]
        out["retest (sonnet pass 1 vs 2), descriptive"] = {
            "same pair outcome": f"{sum(sk[k] == rk[k] for k in same)}/{len(same)}",
            "fleiss_kappa": _kappas(sk, rk),
            "P(B>A) 95%": rate_at(retest[0], "B", "A", 0.95),
            "unrated": meta["unrated"].get(f"{SONNET}#retest"),
        }
    out["sonnet_cost_usd"] = meta.get("sonnet_cost_usd")
    return out


def main(argv: list[str]) -> int:
    if argv[:1] == ["rate"]:
        out = Path(argv[argv.index("--out") + 1])
        rec = asyncio.run(rate(Path(argv[1]), out, "--retest" in argv))
        print(
            json.dumps({k: rec[k] for k in ("unrated", "usage_usd", "sonnet_cost_usd")})
        )
        return 0
    if argv[:1] == ["score"]:
        fallback = "--sonnet-fallback" in argv
        ratings, *files = [Path(a) for a in argv[1:] if a != "--sonnet-fallback"]
        keys = [json.loads(p.read_text()) for p in files]
        key = {"items": {i: v for k in keys for i, v in k["items"].items()}}
        out = score(ratings, key, fallback)
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return 0
    raise SystemExit(__doc__)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
