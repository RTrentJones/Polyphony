"""E4 (SPEC-95): the chapter steps, their analysis, and evals.yml's release
overlay. No LLM call: fake clients and judges."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from evals import run  # noqa: F401 — registers every step
from evals.chapters import analyze, compare, generate

ROOT = Path(__file__).resolve().parents[2]
OLD, NEW = "a" * 40, "b" * 40
OUTLINE = [{"title": "One", "characters": ["Ann"], "children": [{"title": "beat"}]}]


def _report(path: Path, book: str, sha: str, step: str, res: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"book": book, "app_sha": sha, "steps": {step: res}}))


def _chapters(outline=OUTLINE, rows=None) -> dict:
    return {
        "outline": outline,
        "outline_digest": generate.digest(outline),
        "chapters": rows or [],
    }


# --- generate ---------------------------------------------------------------


def test_prior_outline_reads_this_books_reports_only(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    assert generate.prior_outline("dracula") is None
    _report(
        tmp_path / "1/a/eval-report-dracula.json",
        "dracula",
        NEW,
        "chapters",
        _chapters(),
    )
    other = [{"title": "Other"}]
    _report(
        tmp_path / "1/a/eval-report-cel.json", "cel", NEW, "chapters", _chapters(other)
    )
    outline, source = generate.prior_outline("dracula")
    assert outline == OUTLINE and source.endswith("eval-report-dracula.json")
    _report(
        tmp_path / "2/a/eval-report-dracula.json",
        "dracula",
        OLD,
        "chapters",
        _chapters(other),
    )
    with pytest.raises(SystemExit, match="2 outlines"):
        generate.prior_outline("dracula")


def test_digest_ignores_key_order_and_chapter_count_is_bounded(monkeypatch):
    assert generate.digest([{"a": 1, "b": 2}]) == generate.digest([{"b": 2, "a": 1}])
    monkeypatch.delenv("EVAL_CHAPTERS", raising=False)
    assert generate.chapter_count() == generate.DEFAULT_CHAPTERS
    monkeypatch.setenv("EVAL_CHAPTERS", "13")
    with pytest.raises(SystemExit):
        generate.chapter_count()
    monkeypatch.setenv("EVAL_CHAPTERS", "0")  # the outline-only run
    assert generate.chapter_count() == 0
    monkeypatch.setenv("EVAL_CHAPTER_REPLICATES", "7")
    with pytest.raises(SystemExit):
        generate.replicate_count()


class _Client:
    """17 calls per chapter; `left` requests today on the generator's model."""

    def __init__(self, fail_at=None, left=500):
        self.puts, self.generated, self.fail_at = [], [], fail_at
        self.calls, self.left, self.deleted = 0, left, []
        self.live: dict = {}  # chapter id -> live scene ids, as the app has them

    async def usage(self):
        from evals.harness.config import generator

        return {
            "by_purpose_30d": [{"purpose": "llm", "calls": self.calls}],
            "models": {generator()["model"]: {"left": self.left}},
        }

    async def delete_scene(self, sid):
        self.deleted.append(sid)
        for ids in self.live.values():
            if sid in ids:
                ids.remove(sid)

    async def put_outline(self, book_id, content):
        self.puts.append(content)

    async def promote_all(self, book_id):
        return {
            "chapters": [{"chapter_id": f"c{i}", "position": i} for i in (2, 0, 1, 3)]
        }

    async def get_chapter(self, cid):
        return {
            "scenes": [{"id": i} for i in self.live.get(cid, [])],
            "title": f"T{cid}",
            "summary": "s",
            "pov_character_id": "id-ann",
            "planned_beats": [{"title": "b1", "character_ids": ["id-ann", "x"]}],
        }

    async def generate_planned(self, cid, body):
        from evals.harness.client import EvalClientError

        if cid == self.fail_at:
            raise EvalClientError("422")
        self.generated.append(cid)
        self.calls += 17
        self.left -= 17
        sid = f"s-{cid}-{len(self.generated)}"
        self.live.setdefault(cid, []).append(sid)
        return {"queued": 1, "deferred": 0, "total": 1, "scenes": [{"id": sid}]}


def _fake_pipeline(monkeypatch, client):
    async def book_with_cast(ctx, title):
        client.calls += 5  # the setup's own calls
        return {"id": "bk", "character_ids": {"Ann": "id-ann"}, "title": title}

    async def index_voice(ctx, book_id, names):
        return {"indexed": True}

    async def gen_outline(ctx, book):
        return {"portable": [{"title": "generated"}]}

    async def collect(ctx, sid):
        return {
            "id": sid,
            "status": "completed",
            "content": f"text of {sid} [[eval-x]]",
        }

    for name, fn in [
        ("_book_with_cast", book_with_cast),
        ("_index_voice", index_voice),
        ("_generate_outline", gen_outline),
        ("_collect_scene", collect),
    ]:
        monkeypatch.setattr(generate, name, fn)
    return SimpleNamespace(
        book="dracula", ground_truth={"gold_lines": {"x": 1}}, client=client
    )


async def test_chapters_generates_the_outline_once_and_writes_k_in_order(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    monkeypatch.setenv("EVAL_CHAPTERS", "3")
    client = _Client(fail_at="c1")
    res = await generate.step_chapters(_fake_pipeline(monkeypatch, client))
    assert res["outline_source"] == "generated" and res["outline"] == [
        {"title": "generated"}
    ]
    assert client.puts == [[{"title": "generated"}]]  # stored back in its portable form
    assert client.generated == ["c0", "c2"]  # positions 0..2, chapter c1 failed
    rows = res["chapters"]
    assert [r["position"] for r in rows] == [1, 2, 3]
    assert rows[1]["error"] == "422" and rows[1]["scenes"] == []
    assert rows[0]["pov"] == "Ann" and rows[0]["planned_beats"][0]["characters"] == [
        "Ann"
    ]
    assert res["score"] is None and res["voice_indexed"]
    assert res["requests"]["setup"] == 5
    assert res["requests"]["per_chapter"] == [17, 0, 17]  # the failed one spent none


async def test_outline_only_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    monkeypatch.setenv("EVAL_CHAPTERS", "0")
    client = _Client()
    res = await generate.step_chapters(_fake_pipeline(monkeypatch, client))
    assert res["outline"] == [{"title": "generated"}] and res["chapters"] == []
    assert client.generated == [] and client.puts == [[{"title": "generated"}]]


async def test_replicates_rewrite_the_same_plan_after_trashing(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    monkeypatch.setenv("EVAL_CHAPTERS", "2")
    monkeypatch.setenv("EVAL_CHAPTER_REPLICATES", "3")
    client = _Client()
    res = await generate.step_chapters(_fake_pipeline(monkeypatch, client))
    rows = res["chapters"]
    assert [(r["replicate"], r["position"]) for r in rows] == [
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
        (3, 1),
        (3, 2),
    ]
    assert client.generated == ["c0", "c1"] * 3
    # replicates 1 and 2 are trashed before the next; the last stays
    assert client.deleted == [s["id"] for r in rows[:4] for s in r["scenes"]]
    assert res["batch"] and res["stopped"] is None


async def test_the_run_stops_before_the_day_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    monkeypatch.setenv("EVAL_CHAPTERS", "4")
    monkeypatch.setenv("EVAL_CHAPTER_RESERVE", "40")
    client = _Client(left=80)
    res = await generate.step_chapters(_fake_pipeline(monkeypatch, client))
    assert len(res["chapters"]) == 3  # 80 -> 63 -> 46 -> 29 < 40
    assert "29 generator requests left" in res["stopped"]


async def test_chapters_reuses_a_prior_runs_outline(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    monkeypatch.setenv("EVAL_CHAPTERS", "1")
    _report(
        tmp_path / "9/a/eval-report-dracula.json",
        "dracula",
        NEW,
        "chapters",
        _chapters(),
    )
    client = _Client()
    ctx = _fake_pipeline(monkeypatch, client)

    async def never(ctx, book):
        raise AssertionError("the outline is reused, not generated")

    monkeypatch.setattr(generate, "_generate_outline", never)
    res = await generate.step_chapters(ctx)
    assert client.puts == [OUTLINE] and res["outline_digest"] == generate.digest(
        OUTLINE
    )
    assert res["outline_source"].endswith("eval-report-dracula.json")


# --- compare ----------------------------------------------------------------


def test_outcome_is_the_panels_order_rule():
    assert compare.outcome(1, 2, OLD, NEW) == OLD  # first both times
    assert compare.outcome(2, 1, OLD, NEW) == NEW
    assert compare.outcome(1, 1, OLD, NEW) == "tie"  # flipped with the order
    assert compare.outcome(None, 1, OLD, NEW) is None


def _row(pos, text):
    scenes = [{"content": text}] if text else []
    return {
        "position": pos,
        "title": "T",
        "summary": "S",
        "planned_beats": [{"title": "b"}],
        "scenes": scenes,
    }


def _two_releases(tmp_path, old_rows, new_rows, outline_new=OUTLINE):
    _report(
        tmp_path / "1/a/eval-report-dracula.json",
        "dracula",
        OLD,
        "chapters",
        _chapters(OUTLINE, old_rows),
    )
    _report(
        tmp_path / "2/a/eval-report-dracula.json",
        "dracula",
        NEW,
        "chapters",
        _chapters(outline_new, new_rows),
    )


def test_releases_needs_two_shas_and_one_outline(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    _report(
        tmp_path / "1/a/eval-report-dracula.json",
        "dracula",
        OLD,
        "chapters",
        _chapters(OUTLINE, [_row(1, "x")]),
    )
    with pytest.raises(SystemExit, match="got 1"):
        compare.releases("dracula")
    _two_releases(
        tmp_path, [_row(1, "x")], [_row(1, "y")], outline_new=[{"title": "other"}]
    )
    with pytest.raises(SystemExit, match="different outlines"):
        compare.releases("dracula")


class _Judge:
    """Prefers the chapter containing `favour`, or always passage 1 (position-driven)."""

    exhausted, fell_back = False, False

    def __init__(self, provider_id, favour=None, fail_after=None):
        self.provider_id, self.favour, self.prompts = provider_id, favour, []
        self.fail_after = fail_after

    async def ask_json(self, prompt):
        from app.llm.errors import SpendPausedError

        if self.fail_after is not None and len(self.prompts) >= self.fail_after:
            raise SpendPausedError("monthly ceiling")
        self.prompts.append(prompt)
        if self.favour is None:
            return {"preferred": 1}
        first = prompt.split("<chapter_1>")[1].split("</chapter_1>")[0]
        return {"preferred": 1 if self.favour in first else 2}

    async def score(self, rubric, content):
        return SimpleNamespace(score=0.7, explanation="ok")


async def test_compare_is_blind_both_orders_and_scores_forfeits(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    old = [_row(1, "plain old prose"), _row(2, "old two"), _row(3, ""), _row(4, "")]
    new = [
        _row(1, "vivid new prose [[eval-tag]]"),
        _row(2, "new two"),
        _row(3, "new three"),
        _row(4, ""),
    ]
    _two_releases(tmp_path, old, new)
    sonnet, free = _Judge("anthropic", favour="new"), _Judge("groq")
    monkeypatch.setattr(compare, "judges", lambda: {"sonnet": sonnet, "free": free})
    gt = {"synopsis": "A synopsis.", "cast": ["Ann"]}
    res = await compare.step_chapters_compare(
        SimpleNamespace(book="dracula", ground_truth=gt)
    )
    c = res["compare"]
    assert c["releases"] == [OLD, NEW] and c["stopped"] is None
    by = {p["position"]: p for p in c["pairs"]}
    assert by[1]["judges"]["sonnet"]["outcome"] == NEW
    assert by[1]["judges"]["free"]["outcome"] == "tie"  # always passage 1
    assert by[3]["forfeit"] == OLD and not by[3]["judges"]
    assert by[4]["forfeit"] == "both"
    assert len(sonnet.prompts) == len(free.prompts) == 4  # 2 pairs x 2 orders
    for p in sonnet.prompts:
        assert OLD not in p and NEW not in p and "[[eval" not in p
        assert "<canon>" in p and "PLANNED BEATS" in p
    assert by[1]["rc"]["sonnet"][NEW] == {"score": 0.7, "trimmed": False}
    assert "free" not in by[1]["rc"]  # the free chain's quota goes to preference


async def test_compare_pairs_replicates_of_one_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    old = [_row(1, "o-r1") | {"replicate": 1}, _row(1, "o-r2") | {"replicate": 2}]
    new = [_row(1, "n-r1") | {"replicate": 1}, _row(1, "n-r2") | {"replicate": 2}]
    _two_releases(tmp_path, old, new)
    sonnet = _Judge("anthropic", favour="n-")
    monkeypatch.setattr(compare, "judges", lambda: {"sonnet": sonnet, "free": None})
    gt = {"synopsis": "S", "cast": ["Ann"]}
    res = await compare.step_chapters_compare(
        SimpleNamespace(book="dracula", ground_truth=gt)
    )
    pairs = res["compare"]["pairs"]
    assert [(p["replicate"], p["position"]) for p in pairs] == [(1, 1), (2, 1)]
    # replicate r of one release is judged against replicate r of the other
    assert "o-r1" in sonnet.prompts[0] and "n-r1" in sonnet.prompts[0]
    assert "o-r2" in sonnet.prompts[2] and "n-r2" in sonnet.prompts[2]
    assert res["compare"]["batch"] == "None+None"  # no batch ids in these fixtures


async def test_compare_stops_on_a_spend_pause(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    _two_releases(
        tmp_path, [_row(1, "o1"), _row(2, "o2")], [_row(1, "n1"), _row(2, "n2")]
    )
    sonnet = _Judge("anthropic", favour="n", fail_after=3)
    monkeypatch.setattr(compare, "judges", lambda: {"sonnet": sonnet, "free": None})
    gt = {"synopsis": "S", "cast": ["Ann"]}
    res = await compare.step_chapters_compare(
        SimpleNamespace(book="dracula", ground_truth=gt)
    )
    assert "SpendPausedError" in res["compare"]["stopped"]
    assert len(sonnet.prompts) == 3 and res["compare"]["judges"]["free"] is None


# --- analyze ----------------------------------------------------------------


def _pair(book, pos, sonnet, free, forfeit=None, rc=None):
    js = {} if forfeit else {"sonnet": {"outcome": sonnet}, "free": {"outcome": free}}
    return {
        "book": book,
        "position": pos,
        "releases": [OLD, NEW],
        "forfeit": forfeit,
        "judges": js,
        "rc": rc or {},
    }


def test_win_rates_ties_forfeits_panel_and_kappa():
    pairs = [
        _pair("dracula", 1, NEW, NEW),
        _pair("dracula", 2, "tie", NEW),
        _pair("dracula", 3, OLD, "tie"),
        _pair("frankenstein", 1, NEW, None),  # free left an order unrated
        _pair("frankenstein", 2, None, None, forfeit=OLD),  # old wrote nothing
        _pair("frankenstein", 3, None, None, forfeit="both"),
    ]
    res = analyze.analyze(pairs, NEW[:7], OLD[:7])
    s = res["P(new>old) sonnet"]
    assert s["p"] == pytest.approx((1 + 0.5 + 0 + 1 + 1) / 5, abs=1e-4)
    assert (
        s["n"] == 5
        and s["forfeits"] == 1
        and s["both_empty"] == 1
        and s["unrated"] == 0
    )
    assert s["order_consistent"] == "3/4"
    f = res["P(new>old) free"]
    assert f["n"] == 4 and f["unrated"] == 1
    panel = res["P(new>old) panel"]  # agree -> that, else tie; forfeit applies
    assert panel["p"] == pytest.approx((1 + 0.5 + 0.5 + 1) / 4, abs=1e-4)
    assert res["fleiss_kappa sonnet~free"] is not None
    lo, hi = s["ci"]
    assert lo <= s["p"] <= hi


def test_rc_difference_and_release_checks():
    rc = {
        "sonnet": {
            NEW: {"score": 0.9, "trimmed": True},
            OLD: {"score": 0.4, "trimmed": False},
        }
    }
    pairs = [_pair("dracula", 1, NEW, NEW, rc=rc)]
    res = analyze.analyze(pairs, NEW, OLD)
    assert res["RC sonnet"]["new_minus_old"]["p"] == pytest.approx(0.5)
    assert res["RC sonnet"]["trimmed_chapters"] == 1
    with pytest.raises(SystemExit, match="names 0"):
        analyze.analyze(pairs, "c" * 7, OLD)


def _compare_report(tmp_path, n, pairs, rid, resumed_from=None, batch="b1"):
    c = {
        "book": "dracula",
        "batch": batch,
        "releases": [OLD, NEW],
        "report_id": rid,
        "resumed_from": resumed_from,
        "pairs": pairs,
    }
    _report(
        tmp_path / f"{n}/eval-report-dracula.json",
        "dracula",
        NEW,
        "chapters_compare",
        {"compare": c},
    )


def test_collect_reads_the_last_report_of_a_resume_chain(tmp_path):
    first = [_pair("dracula", 1, NEW, NEW), _pair("dracula", 2, None, None)]
    _compare_report(tmp_path, 1, first, "r1")
    second = [_pair("dracula", 1, NEW, NEW), _pair("dracula", 2, OLD, OLD)]
    _compare_report(tmp_path, 2, second, "r2", resumed_from="r1")
    pairs = analyze.collect([tmp_path])
    assert [p["judges"]["sonnet"]["outcome"] for p in pairs] == [NEW, OLD]


def test_collect_refuses_a_changed_outcome_or_two_unchained_reports(tmp_path):
    _compare_report(tmp_path, 1, [_pair("dracula", 1, NEW, NEW)], "r1")
    _compare_report(tmp_path, 2, [_pair("dracula", 1, OLD, NEW)], "r2", "r1")
    with pytest.raises(SystemExit, match="changed on resume"):
        analyze.collect([tmp_path])
    other = tmp_path / "other"
    _compare_report(other, 1, [_pair("dracula", 1, NEW, NEW)], "r1")
    _compare_report(other, 2, [_pair("dracula", 1, NEW, NEW)], "r9")
    with pytest.raises(SystemExit, match="not one resume chain"):
        analyze.collect([other])


def test_power_rises_with_the_effect():
    assert analyze.power(40, 0.95, 0.0, sims=60, resamples=200) > 0.9
    assert analyze.power(12, 0.5, 0.3, sims=60, resamples=200) < 0.2
    with pytest.raises(ValueError):
        analyze.power(10, 0.9, 0.5)
    # replicates of a chapter add power; chapters that differ take some back
    one = analyze.power(16, 0.7, 0.0, reps=1, sims=80, resamples=200, seed=2)
    three = analyze.power(16, 0.7, 0.0, reps=3, sims=80, resamples=200, seed=2)
    assert three > one
    with pytest.raises(ValueError):
        analyze.power(10, 0.9, 0.0, spread=0.2)


def test_replicates_and_batches_move_with_their_chapter(tmp_path):
    for n, batch in ((1, "b1"), (2, "b2")):  # two days of one book: two batches
        c = {
            "book": "dracula",
            "batch": batch,
            "releases": [OLD, NEW],
            "pairs": [
                _pair("dracula", 1, NEW, NEW) | {"replicate": 1},
                _pair("dracula", 1, OLD, OLD) | {"replicate": 2},
                _pair("dracula", 2, NEW, NEW) | {"replicate": 1},
            ],
        }
        _report(
            tmp_path / f"{n}/eval-report-dracula.json",
            "dracula",
            NEW,
            "chapters_compare",
            {"compare": c},
        )
    pairs = analyze.collect([tmp_path])
    assert len(pairs) == 6
    s = analyze.analyze(pairs, NEW, OLD)["P(new>old) sonnet"]
    assert s["n"] == 2 and s["pairs"] == 6  # clusters are outline chapters
    assert s["p"] == pytest.approx(4 / 6, abs=1e-4)


# --- evals.yml --------------------------------------------------------------


def _wf_step(name: str) -> dict:
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "evals.yml").read_text())
    return next(
        s for s in wf["jobs"]["eval"]["steps"] if s.get("name", "").startswith(name)
    )


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    for p, t in {
        "app/a.py": "release\n",
        "app/gone.py": "x\n",
        "alembic/env.py": "e\n",
        "alembic.ini": "i\n",
        "requirements.txt": "r\n",
        "evals/h.py": "h1\n",
    }.items():
        (r / p).parent.mkdir(parents=True, exist_ok=True)
        (r / p).write_text(t)
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "release")
    release = _git(r, "rev-parse", "HEAD")
    (r / "app/gone.py").unlink()
    (r / "app/a.py").write_text("later\n")
    (r / "app/new.py").write_text("n\n")
    (r / "evals/h.py").write_text("h2\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "harness")
    return r, release


_NO_FETCH = 'git() { [ "$1" = fetch ] && return 0; command git "$@"; }\n'


def _run(step: dict, cwd: Path, **env) -> subprocess.CompletedProcess:
    assert "${{" not in step["run"]  # inputs reach the shell through env only
    base = {"PATH": "/usr/bin:/bin", "HOME": str(cwd)}
    return subprocess.run(
        ["bash", "-c", _NO_FETCH + step["run"]],
        cwd=cwd,
        env=base | env,
        capture_output=True,
        text=True,
    )


def test_app_sha_replaces_the_app_tree_exactly(repo, tmp_path):
    r, release = repo
    step = _wf_step("App under test")
    assert step["env"]["APP_SHA"] == "${{ github.event.inputs.app_sha }}"
    genv, summary = tmp_path / "env", tmp_path / "summary"
    out = _run(
        step, r, APP_SHA=release, GITHUB_ENV=str(genv), GITHUB_STEP_SUMMARY=str(summary)
    )
    assert out.returncode == 0, out.stderr
    assert (r / "app/a.py").read_text() == "release\n" and (r / "app/gone.py").exists()
    assert not (r / "app/new.py").exists()  # the later ref's file is gone
    assert (r / "evals/h.py").read_text() == "h2\n"  # the harness stays
    assert genv.read_text() == f"GREENLIGHT_SHA={release}\n"
    assert release in summary.read_text()
    bad = _run(
        step,
        r,
        APP_SHA=release[:12],
        GITHUB_ENV=str(genv),
        GITHUB_STEP_SUMMARY=str(summary),
    )
    assert bad.returncode == 1 and "40-character" in bad.stdout


def test_from_runs_downloads_each_run_and_refuses_anything_else(tmp_path):
    step = _wf_step("Earlier runs' reports")
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text('#!/bin/bash\necho "gh $*" >> "$LOG"\n')
    gh.chmod(0o755)
    log, genv = tmp_path / "log", tmp_path / "env"
    env = {
        "PATH": f"{gh.parent}:/usr/bin:/bin",
        "LOG": str(log),
        "GITHUB_ENV": str(genv),
        "GITHUB_REPOSITORY": "o/r",
    }
    out = _run(step, tmp_path, FROM_RUNS="11,22", **env)
    assert out.returncode == 0, out.stderr
    calls = log.read_text().splitlines()
    assert calls == [
        f"gh run download {i} -R o/r -p eval-report-{i}* -D prior-reports/{i}"
        for i in (11, 22)
    ]
    assert genv.read_text() == f"EVAL_PRIOR_REPORTS={tmp_path}/prior-reports\n"
    assert _run(step, tmp_path, FROM_RUNS="11;rm", **env).returncode == 1


def test_holdout_corpora_must_be_holdout_v1s(repo):
    r, release = repo
    step = _wf_step("Holdout corpora are holdout-v1's")
    assert step["if"] == "${{ github.event.inputs.holdout == 'yes' }}"
    _git(r, "tag", "holdout-v1", release)  # the corpora are unchanged since: passes
    assert _run(step, r).returncode == 0
    (r / "evals/corpora/holdout-a").mkdir(parents=True)
    (r / "evals/corpora/holdout-a/x.txt").write_text("changed\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "corpus edit")
    out = _run(step, r)
    assert out.returncode == 1 and "holdout-v1" in out.stdout


def test_chapter_inputs_reach_the_step_through_env():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "evals.yml").read_text())
    inputs, env = wf[True]["workflow_dispatch"]["inputs"], wf["jobs"]["eval"]["env"]
    assert "0 = the outline only" in inputs["chapters"]["description"]
    assert inputs["replicates"]["default"] == "1"
    assert env["EVAL_CHAPTERS"] == "${{ github.event.inputs.chapters || '3' }}"
    assert (
        env["EVAL_CHAPTER_REPLICATES"] == "${{ github.event.inputs.replicates || '1' }}"
    )


# --- review fixes (principal, 2026-10-27) -----------------------------------


@pytest.mark.parametrize(
    "error, scenes, deferred, want",
    [
        ("generate-planned failed (429): quota", [], None, "quota"),
        (
            "poll timed out: /chapters/x — last state: [{'status': 'paused'}]",
            [],
            None,
            "quota",
        ),
        ("DailyRequestLimitError: limit", [], None, "quota"),
        ("ProviderUnavailableError: breaker open", [], None, "quota"),
        ("generate-planned failed (422): no planned beats", [], None, "product"),
        (None, [{"status": "failed", "content": ""}], None, "product"),
        (None, [{"status": "completed", "content": ""}], None, "product"),
        (None, [{"status": "completed", "content": "text"}], None, None),
        (None, [{"status": "completed", "content": "text"}], 2, "quota"),
    ],
)
def test_failure_class_separates_quota_from_the_product(error, scenes, deferred, want):
    assert generate.failure(error, scenes, deferred) == want


async def test_an_errored_chapter_records_its_failure_class(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    monkeypatch.setenv("EVAL_CHAPTERS", "2")
    client = _Client(fail_at="c1")  # raises EvalClientError("422")
    res = await generate.step_chapters(_fake_pipeline(monkeypatch, client))
    assert [r["failure"] for r in res["chapters"]] == [None, "product"]


async def test_a_paused_leftover_is_trashed_before_the_next_replicate(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    monkeypatch.setenv("EVAL_CHAPTERS", "1")
    monkeypatch.setenv("EVAL_CHAPTER_REPLICATES", "2")
    client = _Client(fail_at="c0")
    client.live["c0"] = ["paused-leftover"]  # an errored chapter's paused scene
    await generate.step_chapters(_fake_pipeline(monkeypatch, client))
    assert "paused-leftover" in client.deleted


def _fail_row(pos, text, failure, rep=1):
    return _row(pos, text) | {"failure": failure, "replicate": rep}


async def test_quota_excludes_both_sides_and_one_sided_is_not_attempted(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    old = [
        _fail_row(1, "o1", None),
        _fail_row(2, "", "quota"),  # old ran second and hit the day's cap
        _fail_row(3, "", "product"),  # old's product wrote nothing
        _fail_row(4, "o4", None),
    ]
    new = [
        _fail_row(1, "n1", None),
        _fail_row(2, "n2", None),
        _fail_row(3, "n3", None),
        # position 4: new's run stopped at its reserve: not attempted
    ]
    _two_releases(tmp_path, old, new)
    sonnet = _Judge("anthropic", favour="n")
    monkeypatch.setattr(compare, "judges", lambda: {"sonnet": sonnet, "free": None})
    gt = {"synopsis": "S", "cast": ["Ann"]}
    res = await compare.step_chapters_compare(
        SimpleNamespace(book="dracula", ground_truth=gt)
    )
    by = {p["position"]: p for p in res["compare"]["pairs"]}
    assert by[2]["excluded"] == "quota" and by[2]["quota"] == [OLD]
    assert by[2]["forfeit"] is None and not by[2]["judges"]
    assert by[3]["forfeit"] == OLD and by[3]["excluded"] is None
    assert by[4]["excluded"] == "not_attempted" and by[4]["not_attempted"] == [NEW]
    assert len(sonnet.prompts) == 2  # only position 1 was judged
    pairs = [p | {"book": "dracula", "releases": [OLD, NEW]} for p in by.values()]
    s = analyze.analyze(pairs, NEW, OLD)["P(new>old) sonnet"]
    assert s["pairs"] == 2 and s["p"] == 1.0  # the quota pair is no loss for old
    assert s["excluded"]["quota"] == {"pairs": 1, "new": 0, "old": 1}
    assert s["excluded"]["not_attempted"] == {"pairs": 1, "new": 1, "old": 0}


class _Refusing(_Judge):
    async def ask_json(self, prompt):
        from app.llm.errors import ProviderRefusalError

        if "refuse-me" in prompt:
            raise ProviderRefusalError("refused", spent=("", 10, 0, 0.0))
        return await super().ask_json(prompt)


async def test_a_refusal_leaves_the_pair_unrated_and_a_resume_judges_only_it(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path / "in"))
    old = [_fail_row(1, "o1", None), _fail_row(2, "o2 refuse-me", None)]
    new = [_fail_row(1, "n1", None), _fail_row(2, "n2", None)]
    _two_releases(tmp_path / "in", old, new)
    sonnet = _Refusing("anthropic", favour="n")
    monkeypatch.setattr(compare, "judges", lambda: {"sonnet": sonnet, "free": None})
    gt = {"synopsis": "S", "cast": ["Ann"]}
    ctx = SimpleNamespace(book="dracula", ground_truth=gt)
    res = await compare.step_chapters_compare(ctx)  # no crash
    c = res["compare"]
    by = {p["position"]: p for p in c["pairs"]}
    assert by[2]["judges"]["sonnet"]["outcome"] is None
    assert {o[3] for o in by[2]["judges"]["sonnet"]["orders"]} == {"refused"}
    assert c["counts"]["unrated_sonnet"] == 1
    # the resume: the prior compare report joins from_runs; only pair 2 is asked
    _report(
        tmp_path / "in/3/a/eval-report-dracula.json",
        "dracula",
        NEW,
        "chapters_compare",
        {"compare": c},
    )
    sonnet2 = _Judge("anthropic", favour="n")
    monkeypatch.setattr(compare, "judges", lambda: {"sonnet": sonnet2, "free": None})
    res2 = await compare.step_chapters_compare(ctx)
    c2 = res2["compare"]
    assert c2["resumed_from"] == c["report_id"]
    assert len(sonnet2.prompts) == 2 and all("o2" in p for p in sonnet2.prompts)
    by2 = {p["position"]: p for p in c2["pairs"]}
    assert by2[1] == by[1] | {"resumed": True}  # copied verbatim
    assert by2[2]["judges"]["sonnet"]["outcome"] == NEW


async def test_rc_reads_stripped_text_and_plans_must_match(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    _two_releases(
        tmp_path, [_fail_row(1, "o1 [[eval-x]]", None)], [_fail_row(1, "n1", None)]
    )
    seen = []

    class Rc(_Judge):
        async def score(self, rubric, content):
            seen.append(content)
            return await super().score(rubric, content)

    judge = Rc("anthropic", favour="n")
    monkeypatch.setattr(compare, "judges", lambda: {"sonnet": judge, "free": None})
    gt = {"synopsis": "S", "cast": ["Ann"]}
    await compare.step_chapters_compare(
        SimpleNamespace(book="dracula", ground_truth=gt)
    )
    assert seen and not any("[[eval" in c for c in seen)
    other = tmp_path / "plan"
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(other))
    _two_releases(
        other, [_fail_row(1, "o1", None)], [_fail_row(1, "n1", None) | {"title": "X"}]
    )
    with pytest.raises(SystemExit, match="plans differ"):
        await compare.step_chapters_compare(
            SimpleNamespace(book="dracula", ground_truth=gt)
        )


async def test_the_free_chains_provider_is_recorded_per_order(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    _two_releases(tmp_path, [_fail_row(1, "o1", None)], [_fail_row(1, "n1", None)])

    class FailsOver(_Judge):  # Groq's 413 on the first order, then OpenRouter
        async def ask_json(self, prompt):
            got = await super().ask_json(prompt)
            self.provider_id = "openrouter"
            return got

    free = FailsOver("groq")
    monkeypatch.setattr(compare, "judges", lambda: {"sonnet": None, "free": free})
    gt = {"synopsis": "S", "cast": ["Ann"]}
    res = await compare.step_chapters_compare(
        SimpleNamespace(book="dracula", ground_truth=gt)
    )
    orders = res["compare"]["pairs"][0]["judges"]["free"]["orders"]
    assert [o[4] for o in orders] == ["openrouter", "openrouter"]
    pairs = [
        p | {"book": "dracula", "releases": [OLD, NEW]} for p in res["compare"]["pairs"]
    ]
    assert analyze.analyze(pairs, NEW, OLD)["free_providers"] == ["openrouter"]


def test_decision_order_driven_disagreeing_and_null_with_its_detectable_rate(
    monkeypatch,
):
    flips = [_pair("dracula", k, "tie", NEW) for k in range(1, 4)]
    flips.append(_pair("dracula", 4, NEW, NEW))
    res = analyze.analyze(flips, NEW, OLD)
    assert res["decision"].startswith("undetermined: Sonnet is order-driven")
    assert res["sonnet_flips"] == [3, 4]
    # Sonnet favours new everywhere; the free chain favours old everywhere
    agree = [
        _pair("dracula", k, NEW, OLD)
        | {
            "judges": {
                "sonnet": {"outcome": NEW},
                "free": {"outcome": OLD, "orders": [[0, 0, 0, "ok", "openrouter"]]},
            }
        }
        for k in range(1, 9)
    ]
    res = analyze.analyze(agree, NEW, OLD)
    assert "free chain disagrees" in res["decision"] and "openrouter" in res["decision"]
    assert res["n"] == "8 pairs (Sonnet-rated plus forfeits) over 8 outline chapters"
    # a null: the smallest p on the grid with power >= 0.80, from power()
    asked = []

    def fake_power(n, p, ties, reps, **kw):
        asked.append((n, ties, reps, kw["sims"], kw["seed"], kw["spread"]))
        return 1.0 if p >= 0.7 else 0.0

    monkeypatch.setattr(analyze, "power", fake_power)
    null = [_pair("dracula", 1, NEW, NEW), _pair("dracula", 2, OLD, OLD)]
    res = analyze.analyze(null, NEW, OLD, mde_kw={})
    assert res["decision"].startswith("no difference detectable at n = 2")
    assert "p >= 0.7" in res["decision"]
    assert asked[0] == (2, 0.0, 1, 400, 0, 0.0)
