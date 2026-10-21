"""W13-4, SPEC-84's panel amendment: BASELINE_PANEL's two members rate the bundles.
python -m evals.baseline.panel <bundles dir> --out <ratings dir>"""

import asyncio
import csv
import json
import os
import re
import sys
from pathlib import Path

from app.llm.client import LLMClient
from app.llm.errors import QuotaExhaustedError
from app.llm.json_utils import extract_json_object
from app.llm.providers import PROVIDERS
from evals.baseline import blind
from evals.harness.cache import Cache
from evals.steps.pipeline import load_corpus

ASK = {
    "preferred": 'Which passage is better? Answer with JSON {"preferred": 1} or {"preferred": 2}.',
    "errors": 'Count the continuity errors against the canon sheet. Answer with JSON {"errors": <int>}.',
}
JSON = dict(
    temperature=0.0, purpose="eval_panel", response_format={"type": "json_object"}
) | {"max_tokens": 640}


def fleiss(table: list[list[int]]) -> float:
    """Fleiss' kappa over subjects x category counts; 1.0 if all in one category."""
    n, m = sum(table[0]), len(table)
    pe = sum((sum(r[j] for r in table) / (m * n)) ** 2 for j in range(len(table[0])))
    agree = sum((sum(c * c for c in row) - n) / (n * (n - 1)) for row in table) / m
    return 1.0 if pe == 1 else (agree - pe) / (1 - pe)


def load(path: Path) -> dict:
    b = json.loads(path.read_text())
    marked = any(map(blind.MARKERS.search, b["items"].values()))
    if marked or set(b) != {"items", "pairs", "edit"}:
        raise SystemExit(f"{path}: not a blinded bundle (arm marker or extra key)")
    gt = load_corpus(path.parent.name.removeprefix("baseline-bundle-"))[1]
    canon = f"{gt['synopsis']}\nCast: {', '.join(gt['cast'])}"
    return b | {"canon": f"<canon>\n{canon}\n</canon>"}


async def rate(member: str, bundles: list[dict], cache: Cache) -> dict:
    """{(a, b): winner id | "tie"} and {item: errors}; a quota stop ends it cleanly."""
    provider, model = member.split(":", 1)
    llm, out = LLMClient(PROVIDERS[provider]), {"prefer": {}, "errors": {}}

    async def ask(canon: str, what: str, *texts: str) -> int:
        fenced = [f"<passage_{k}>\n{t}\n</passage_{k}>" for k, t in enumerate(texts, 1)]
        prompt = "\n\n".join([canon, *fenced, ASK[what]])
        msg = [{"role": "user", "content": prompt}]

        async def call() -> str:
            return (await llm.generate(msg, model=model, **JSON)).text

        answer = await cache.memo("panel", f"{member}|{prompt}", call)
        return int(extract_json_object(answer)[what])

    try:
        for b in bundles:
            it, canon = b["items"], b["canon"]
            for a, c in b["pairs"]:  # both orders; inconsistent = a tie (1/2)
                one = await ask(canon, "preferred", it[a], it[c]) == 1
                two = await ask(canon, "preferred", it[c], it[a]) == 2
                out["prefer"][(a, c)] = (a if one else c) if one == two else "tie"
            for i, text in it.items():
                out["errors"][i] = await ask(canon, "errors", text)
    except QuotaExhaustedError as e:
        print(f"panel member {member} stopped: {e}")
    return out


def write(path: Path, prefer: dict, errors: dict) -> None:
    rows = [(blind.PREFER, a, c, w, "") for (a, c), w in prefer.items()]
    rows += [(blind.CONTINUITY, i, "", n, "") for i, n in errors.items()]
    with path.open("w", newline="") as f:
        csv.writer(f).writerows([("task", "item", "other", "answer", "minutes"), *rows])


async def main(root: str, out: Path) -> None:
    members = os.environ["BASELINE_PANEL"].split(",")
    bundles = [load(p) for p in sorted(Path(root).rglob("bundle.json"))]
    if not bundles:
        raise SystemExit("no bundle to rate")
    cache = Cache(os.getenv("EVAL_CACHE_DIR", ".eval-cache"), "panel")
    got = [await rate(m, bundles, cache) for m in members]
    (px, ex), (py, ey) = [(g["prefer"], g["errors"]) for g in got]  # two, counted
    out.mkdir(parents=True, exist_ok=True)
    for m, g in zip(members, got):
        write(out / ("sheet-%s.csv" % re.sub(r"\W", "_", m)), g["prefer"], g["errors"])
    agreed = {p: px[p] if px[p] == py[p] else "tie" for p in px if p in py}
    errors = {i: (n + ey[i]) / 2 for i, n in ex.items() if i in ey}
    write(out / "sheet-panel.csv", agreed, errors)
    table = [[(px[p] == v) + (py[p] == v) for v in (*p, "tie")] for p in agreed]
    kappa = {"members": members, "fleiss_kappa": fleiss(table) if table else None}
    left = sum(len(b["pairs"]) + len(b["items"]) for b in bundles) - len(agreed)
    (out / "kappa.json").write_text(json.dumps(kappa | {"unrated": left - len(errors)}))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], Path(sys.argv[sys.argv.index("--out") + 1])))
