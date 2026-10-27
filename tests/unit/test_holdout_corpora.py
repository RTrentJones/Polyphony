"""SPEC-94 S5: the frozen holdout corpora and the development/holdout guard.

These checks are mechanical (D19-4): names renamed, no original left, structure
and counts. They read the holdout files directly, never through the corpus
readers a run uses, and grade nothing.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from evals import splits
from evals.steps import pipeline
from evals.tools import build_gt, rename

ROOT = Path(__file__).resolve().parents[2]
CORPORA = ROOT / "evals" / "corpora"
DEVELOPMENT = ["cel", "dracula", "frankenstein", "wuthering"]
HOLDOUT = ["holdout-a", "holdout-b"]


def _read(book: str, name: str) -> str:
    return (CORPORA / book / name).read_text(encoding="utf-8")


def _json(book: str, name: str) -> dict:
    return json.loads(_read(book, name))


# --- the split ---------------------------------------------------------------


def test_every_corpus_declares_its_split():
    dirs = sorted(d.name for d in CORPORA.iterdir() if d.is_dir())
    assert dirs == sorted(DEVELOPMENT + HOLDOUT)
    assert splits.corpora("development") == DEVELOPMENT
    assert splits.corpora("holdout") == HOLDOUT


@pytest.mark.parametrize("book", HOLDOUT)
def test_a_run_reads_a_holdout_corpus_only_when_it_names_it(book, monkeypatch):
    monkeypatch.delenv(splits.ALLOW_ENV, raising=False)
    with pytest.raises(SystemExit, match="holdout corpus"):
        pipeline.load_corpus(book)
    with pytest.raises(SystemExit, match="holdout corpus"):
        pipeline.load_excerpts(book)
    other = next(b for b in HOLDOUT if b != book)
    monkeypatch.setenv(splits.ALLOW_ENV, other)  # naming the other one is not enough
    with pytest.raises(SystemExit):
        pipeline.load_excerpts(book)
    monkeypatch.setenv(splits.ALLOW_ENV, book)
    text, gt = pipeline.load_corpus(book)
    assert gt["book"] == book and text  # asked for by name: readable


def test_the_graders_corpus_readers_refuse_an_unrequested_holdout(
    monkeypatch, tmp_path
):
    """Nit: prompt_contract and plot read evals/corpora directly; they go
    through the same guard as the pipeline's readers."""
    import json

    from evals.graders import plot
    from evals.graders import prompt_contract as pc

    monkeypatch.delenv(splits.ALLOW_ENV, raising=False)
    with pytest.raises(SystemExit, match="holdout corpus"):
        pc.book_inputs("holdout-a")
    with pytest.raises(SystemExit, match="holdout corpus"):
        pc._corpus_file("holdout-b", "excerpts.txt")
    with pytest.raises(SystemExit, match="holdout corpus"):
        pc.corpus_inputs({"book": "holdout-a"}, {})
    with pytest.raises(SystemExit, match="holdout corpus"):
        plot.main(["draft", str(tmp_path), "--book", "holdout-a"])
    (tmp_path / "eval-report-holdout-b.json").write_text(
        json.dumps({"book": "holdout-b", "steps": {}})
    )
    with pytest.raises(SystemExit, match="holdout corpus"):
        plot.regrade(tmp_path)
    assert pc._corpus_file("", "excerpts.txt") == ""  # no book: nothing read


@pytest.mark.parametrize("book", DEVELOPMENT)
def test_development_corpora_need_no_request(book, monkeypatch):
    monkeypatch.delenv(splits.ALLOW_ENV, raising=False)
    assert splits.allowed(book) and pipeline.load_excerpts(book)


def test_the_runner_refuses_a_holdout_book_without_holdout(monkeypatch):
    from evals import run

    monkeypatch.delenv(splits.ALLOW_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["evals.run", "--book", "holdout-a"])
    with pytest.raises(SystemExit, match="holdout corpus"):
        run.main()
    monkeypatch.setattr(sys, "argv", ["evals.run", "--book", "dracula", "--holdout"])
    with pytest.raises(SystemExit, match="not a holdout corpus"):
        run.main()


def test_no_default_or_workflow_selects_a_holdout_corpus():
    """No default book, CI workflow, run sentinel or evals module names one: a
    holdout run is always an explicit `--holdout` (D19-4)."""
    paths = [  # evals.yml names them only behind holdout=yes (tested below)
        *(
            p
            for p in (ROOT / ".github" / "workflows").glob("*.yml")
            if p.name != "evals.yml"
        ),
        *(ROOT / "evals").rglob("*.py"),
        *(ROOT / "scripts").glob("*"),
        ROOT / "evals" / "history.csv",
        ROOT / "evals" / ".run-eval",
    ]
    for p in paths:
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            for book in HOLDOUT:
                assert not re.search(rf"\b{book}\b", text), f"{p} names {book}"
    for module in ("run.py", "offline.py"):
        src = (ROOT / "evals" / module).read_text(encoding="utf-8")
        default = re.search(r'"--book", default="([\w-]+)"', src).group(1)
        assert splits.split(default) == "development", module


def _evals_step(
    tmp_path,
    book: str,
    holdout: str,
    sentinel: str = "",
    ref_type: str = "tag",
    repeat: str = "",
) -> tuple:
    """Run evals.yml's book-selection step with `python` stubbed to echo, so
    the shell logic is tested as written. Returns (exit code, evals.run calls)."""
    import subprocess

    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "evals.yml").read_text())
    step = next(
        s
        for s in wf["jobs"]["eval"]["steps"]
        if s.get("name", "").startswith("Run evals")
    )
    # Review S4: every dispatch input reaches the shell through `env`, never
    # interpolated into the script.
    assert "${{" not in step["run"]
    assert step["env"]["REPEAT_INPUT"] == "${{ github.event.inputs.repeat }}"
    script = step["run"]
    (tmp_path / "evals").mkdir(exist_ok=True)
    (tmp_path / "evals" / ".run-eval").write_text(sentinel)
    env = {
        "PATH": "/usr/bin:/bin",
        "BOOK_INPUT": book,
        "HOLDOUT": holdout,
        "STEPS": "all",
        "BASE_LABEL": "ci",
        "GITHUB_REF_TYPE": ref_type,
        "GITHUB_REF_NAME": "v1" if ref_type == "tag" else "main",
        "REPEAT_INPUT": repeat,
    }
    stub = 'python() { [ "$2" = evals.run ] && echo "RUN $*"; return 0; }\n'
    out = subprocess.run(
        ["bash", "-c", stub + script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    runs = [ln for ln in out.stdout.splitlines() if ln.startswith("RUN ")]
    return out.returncode, runs, out.stdout


@pytest.mark.parametrize(
    "book, holdout, sentinel, books, flag",
    [
        ("", "no", "", ["dracula", "frankenstein", "cel"], False),  # push default
        ("", "no", "books: dracula", ["dracula"], False),  # the sentinel
        (
            "dracula frankenstein cel",
            "no",
            "",
            ["dracula", "frankenstein", "cel"],
            False,
        ),
        (
            "dracula frankenstein cel",
            "yes",
            "",
            HOLDOUT,
            True,
        ),  # the default means both
        ("holdout-b", "yes", "", ["holdout-b"], True),
    ],
)
def test_the_evals_workflow_runs_a_holdout_book_only_with_holdout_yes(
    tmp_path, book, holdout, sentinel, books, flag
):
    code, runs, _ = _evals_step(tmp_path, book, holdout, sentinel)
    assert code == 0
    assert [re.search(r"--book (\S+)", r).group(1) for r in runs] == books
    assert all(("--holdout" in r) == flag for r in runs)


@pytest.mark.parametrize(
    "book, holdout",
    [
        ("holdout-a", "no"),
        ("cel holdout-b", "no"),
        ("dracula", "yes"),
        ("holdout-a cel", "yes"),
    ],
)
def test_the_evals_workflow_refuses_a_mixed_or_unflagged_holdout_run(
    tmp_path, book, holdout
):
    code, runs, out = _evals_step(tmp_path, book, holdout)
    assert code == 1 and not runs and "::error title=Holdout run::" in out


@pytest.mark.parametrize("ref_type", ["branch", ""])
def test_the_evals_workflow_runs_the_holdout_only_from_a_tag(tmp_path, ref_type):
    """Review S4: holdout=yes from a branch (or an unknown ref) is refused —
    the holdout runs on a release's frozen code (D19-4)."""
    code, runs, out = _evals_step(tmp_path, "holdout-a", "yes", ref_type=ref_type)
    assert code == 1 and not runs
    assert "::error title=Holdout run::holdout=yes runs only from a tag" in out
    # A development run does not care which ref it was dispatched from.
    code, runs, _ = _evals_step(tmp_path, "cel", "no", ref_type=ref_type)
    assert code == 0 and len(runs) == 1


def test_the_repeat_input_reaches_the_runner_through_env(tmp_path):
    code, runs, _ = _evals_step(tmp_path, "cel", "no", repeat="3")
    assert code == 0 and "--repeat 3" in runs[0]
    code, runs, _ = _evals_step(tmp_path, "cel", "no", sentinel="repeat: 2")
    assert "--repeat 2" in runs[0]


# --- the judge input (SPEC-95 E0) ---------------------------------------------


def _wf_step(name: str) -> dict:
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "evals.yml").read_text())
    return next(s for s in wf["jobs"]["eval"]["steps"] if s.get("name") == name)


def _judge_step(tmp_path, judge: str, claude: str, base_url: str = "") -> tuple:
    """Run evals.yml's judge-chain step as written: (exit code, stdout,
    {name: value} it wrote to GITHUB_ENV)."""
    import subprocess

    step = _wf_step("Judge chain")
    # every input reaches the shell through `env`, never interpolated
    assert "${{" not in step["run"]
    assert step["env"]["JUDGE"] == "${{ github.event.inputs.judge || 'chain' }}"
    env_file = tmp_path / "github_env"
    env_file.write_text("")
    out = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "JUDGE": judge,
            "CLAUDE": claude,
            "BASE_URL": base_url,
            "FREE_CHAIN": "groq,openrouter",
            "GITHUB_ENV": str(env_file),
        },
        capture_output=True,
        text=True,
    )
    written = dict(
        ln.split("=", 1) for ln in env_file.read_text().splitlines() if "=" in ln
    )
    return out.returncode, out.stdout, written


@pytest.mark.parametrize(
    "judge, claude, chain",
    [
        ("chain", "no", "groq,openrouter"),  # the default: the free chain
        ("chain", "yes", "groq,openrouter,anthropic"),  # Claude last (SPEC-90 D14)
        ("claude", "yes", "anthropic,groq,openrouter"),  # Claude first (E0)
    ],
)
def test_the_judge_input_orders_the_chain(tmp_path, judge, claude, chain):
    code, _, env = _judge_step(tmp_path, judge, claude)
    assert code == 0
    assert env == {"EVAL_JUDGE_PROVIDER": chain, "EVAL_JUDGE_MODE": judge}


def test_judge_claude_without_claude_yes_or_a_booted_app_is_refused(tmp_path):
    code, out, env = _judge_step(tmp_path, "claude", "no")
    assert code == 1 and not env
    assert "::error title=Judge::judge=claude needs claude=yes" in out
    code, out, env = _judge_step(tmp_path, "claude", "yes", "https://x.example")
    assert code == 1 and not env and "::error title=Judge::" in out
    code, out, env = _judge_step(tmp_path, "sonnet", "yes")
    assert code == 1 and not env and "::error title=Judge::" in out


def test_the_judge_chain_is_set_only_by_the_judge_step():
    """The job env no longer computes the chain, and the judge step runs before
    anything that grades; its input is a choice defaulting to the free chain."""
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "evals.yml").read_text())
    job = wf["jobs"]["eval"]
    assert "EVAL_JUDGE_PROVIDER" not in job["env"]
    names = [s.get("name") for s in job["steps"]]
    assert names.index("Judge chain") < names.index("Run evals + ingest to Tracer")
    judge = wf[True]["workflow_dispatch"]["inputs"]["judge"]
    assert judge["options"] == ["chain", "claude"] and judge["default"] == "chain"


def _calibration_items_step(tmp_path, steps: str, run_id: str, artifact: dict):
    """Run the calibration-items step with `gh` stubbed to write `artifact`."""
    import subprocess

    step = _wf_step("Calibration items (steps=calibration_answers)")
    assert "${{" not in step["run"]
    tmp_path.mkdir(parents=True, exist_ok=True)
    env_file = tmp_path / "github_env"
    env_file.write_text("")
    files = " ".join(f"{name}" for name in artifact)
    stub = (
        'gh() { echo "GH $*"; d="${@: -1}"; mkdir -p "$d"; '
        f'for f in {files}; do echo "{{}}" > "$d/$f"; done; }}\n'
    )
    out = subprocess.run(
        ["bash", "-e", "-c", stub + step["run"]],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "STEPS": steps,
            "CAL_RUN": run_id,
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_ENV": str(env_file),
        },
        capture_output=True,
        text=True,
    )
    return out.returncode, out.stdout, env_file.read_text()


def test_the_calibration_items_step_downloads_items_only(tmp_path):
    code, out, env = _calibration_items_step(tmp_path, "prose", "", {})
    assert code == 0 and "GH" not in out and not env  # not asked for
    code, out, env = _calibration_items_step(
        tmp_path, "calibration_answers", "", {"items.json": 1}
    )
    assert code == 1 and "::error title=Calibration::" in out and not env
    code, out, env = _calibration_items_step(
        tmp_path, "calibration_answers", "123", {"items.json": 1}
    )
    want = f"GH run download 123 -R o/r -n calibration-items -D {tmp_path}/"
    assert code == 0 and want + "calibration-items\n" in out  # the items only
    assert env.strip().endswith("calibration-items/items.json")
    code, out, env = _calibration_items_step(
        tmp_path / "k", "calibration_answers", "123", {"items.json": 1, "key.json": 1}
    )
    assert code == 1 and "holds a key" in out


# --- the renaming --------------------------------------------------------------


def _boundary(word: str) -> str:
    # '_' is a word character to `\b`, so an italicised `_Name_` hides from it;
    # a phrase may wrap across a line ('woman in\nwhite').
    words = r"\s+".join(re.escape(w) for w in word.split(" "))
    return rf"(?<![^\W_]){words}(?![^\W_])"


RENAMED = ["dracula", "frankenstein", "wuthering", *HOLDOUT]


def _originals(book: str) -> list[str]:
    spec = _json(book, "aliases.json")
    maps = [spec["aliases"], *spec.get("section_aliases", {}).values()]
    maps.append(spec.get("phrase_aliases", {}))  # an identifying phrase, too
    return sorted({k for m in maps for k in m})


def _corpus_texts(book: str) -> dict[str, str]:
    """Everything of a corpus that can reach a model: the excerpt, and
    spec.json / ground_truth.json (synopsis, cast, beats, lines) less the
    provenance fields and maintainer comments, which never leave the harness."""
    credit = {"_comment", "book", "author", "year", "source"}
    dump = lambda d: json.dumps(  # noqa: E731
        {k: v for k, v in d.items() if k not in credit}, ensure_ascii=False
    )
    texts = {"excerpts": _read(book, "excerpts.txt")}
    for name in ("spec.json", "ground_truth.json"):
        if (CORPORA / book / name).exists():
            texts[name] = dump(_json(book, name))
    synopsis = CORPORA / book / "synopsis.txt"
    if synopsis.exists():
        texts["synopsis.txt"] = synopsis.read_text(encoding="utf-8")
    return texts


def _leaks(originals: list[str], text: str) -> list[str]:
    """Whole names case-insensitively (all-caps and `_italic_` included), and
    any capitalised variant of a one-word original (a plural or adjective:
    'Lintons', 'Transylvanian')."""
    found = [src for src in originals if re.search(_boundary(src), text, re.I)]
    for src in (o for o in originals if " " not in o):
        found += re.findall(rf"(?<![A-Za-z]){re.escape(src)}[a-z]+", text)
    return found


@pytest.mark.parametrize("book", RENAMED)
def test_a_renamed_corpus_leaks_no_original_name(book):
    """(F) SPEC-94: all five renamed corpora, development and holdout."""
    for where, text in _corpus_texts(book).items():
        assert not _leaks(_originals(book), text), where


def test_holdout_a_renames_its_title_phrase():
    """The novel's identifying phrase is renamed like a name, wherever it wraps."""
    assert "woman in white" in _originals("holdout-a")
    text = _read("holdout-a", "excerpts.txt")
    assert not re.search(r"(?i)woman\s+in\s+white|in\s+white,\s+policeman", text)
    assert len(re.findall(r"(?i)woman\s+in\s+blue", text)) == 6


def test_the_synthetic_corpus_carries_no_renamed_original():
    """cel has no source to rename; none of the other corpora's originals
    appears in it either."""
    originals = sorted({o for b in RENAMED for o in _originals(b)})
    for where, text in _corpus_texts("cel").items():
        assert not _leaks(originals, text), where


@pytest.mark.parametrize("book", HOLDOUT)
def test_a_holdout_rename_is_one_to_one(book):
    aliases = _json(book, "aliases.json")["aliases"]
    originals = {w for k in aliases for w in k.split()}
    for src, rep in aliases.items():
        same = [set(k.split()) for k, v in aliases.items() if v == rep]
        assert all(k <= set(src.split()) or set(src.split()) <= k for k in same)
        assert not set(rep.split()) & originals, rep


# --- the structure -------------------------------------------------------------


@pytest.mark.parametrize("book", HOLDOUT)
def test_a_holdout_corpus_has_the_development_shape(book):
    spec = _json(book, "spec.json")
    gt = _json(book, "ground_truth.json")
    aliases = _json(book, "aliases.json")
    text = _read(book, "excerpts.txt")
    assert spec["split"] == "holdout"
    # The excerpt's headings are exactly the declared sections, in order.
    heads = re.findall(r"(?m)^CHAPTER [IVXLC]+$", text)
    assert heads == aliases["sections"] == [s["heading"] for s in aliases["spans"]]
    # A few chapters, sized like the development corpora (56k-160k chars).
    assert 4 <= len(heads) <= 7 and 100_000 <= len(text) <= 160_000
    # Every narrator's chapters exist, and each has a full train/test split.
    for who, src in spec["voice_sources"].items():
        assert set(src["chapters"]) <= set(heads), who
        lines = gt["gold_lines"][who]
        assert len(lines["train"]) == 8 and len(lines["test"]) == 3, who
    # Every cast member is named in the excerpt (the surname at least).
    for name in spec["cast"]:
        assert name.split()[-1] in text, name
    # Each injection's anchor sits once inside the graded continuity window.
    window = text[:12000]
    for inj in spec["continuity_injections"]:
        assert window.count(inj["locate"]) == 1, inj["id"]
    assert [b["kind"] for b in spec["canonical_beats"]][0] == "inciting"
    # ground_truth.json is exactly what build_gt makes from these files.
    assert build_gt.build(book) == gt


# --- the build tool ------------------------------------------------------------


def test_apply_aliases_renames_italics_and_keeps_case():
    out = rename.apply_aliases(
        "_Earnshaw_ met EARNSHAW and Earnshaw’s dog.", {"Earnshaw": "Ashdown"}
    )
    assert out == "_Ashdown_ met ASHDOWN and Ashdown’s dog."


def test_extract_spans_heads_each_span_and_drops_the_source_heading():
    body = "I\n\nOne starts here. Walter.\n\nII\n\nTwo starts. Walter.\n\nIII\n\nx"
    spans = [
        {"heading": "CHAPTER I", "start": "Two starts.", "end": "\n\nIII\n\n"},
        {"heading": "CHAPTER II", "start": "One starts", "end": "\n\nII\n\n"},
    ]
    out = rename.extract_spans(body, spans, {"Walter": "Edwin"}, {})
    assert (
        out
        == "CHAPTER I\n\nTwo starts. Edwin.\n\n\nCHAPTER II\n\nOne starts here. Edwin."
    )
    with pytest.raises(SystemExit, match="not unique"):
        rename.extract_spans(body, [{**spans[0], "start": "Walter"}], {}, {})
    with pytest.raises(SystemExit, match="distinct HEADING"):
        rename.extract_spans(body, [spans[0], spans[0]], {}, {})


# --- claude_generate: judging apart from writing (SPEC-95) ---------------------


def _claude_step(tmp_path, generate: str) -> tuple:
    """Run evals.yml's claude=yes step as written, `gh` and the spend module
    stubbed: (exit code, stdout, {name: value} it wrote to GITHUB_ENV)."""
    import subprocess

    step = _wf_step("Claude for the eval users (claude=yes)")
    assert "${{" not in step["run"]
    want = "${{ github.event.inputs.claude_generate || 'yes' }}"
    assert step["env"]["GENERATE"] == want
    env_file, summary = tmp_path / "github_env", tmp_path / "summary"
    env_file.write_text("")
    stub = "gh() { :; }\npython3() { echo 4.50; }\n"
    out = subprocess.run(
        ["bash", "-e", "-c", stub + step["run"]],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "KEY": "sk-test",
            "BUDGET": "100",
            "CAP": "",
            "GENERATE": generate,
            "GITHUB_REPOSITORY": "o/r",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_ENV": str(env_file),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        capture_output=True,
        text=True,
    )
    written = dict(
        ln.split("=", 1) for ln in env_file.read_text().splitlines() if "=" in ln
    )
    return out.returncode, out.stdout, written


def test_claude_generate_yes_keeps_claude_yes_meaning(tmp_path):
    """The default: Claude writes (waves 17 and 18 were drawn so)."""
    code, _, env = _claude_step(tmp_path, "yes")
    assert code == 0
    assert env == {
        "ANTHROPIC_API_KEY": "sk-test",
        "MONTHLY_COST_CEILING_USD": "4.50",
        "ANTHROPIC_USERS": "@polyphony.eval",
        "EVAL_MODEL_LABEL": "claude-opus-5-5",
    }


def test_claude_generate_no_wires_the_judge_but_not_the_writer(tmp_path):
    code, _, env = _claude_step(tmp_path, "no")
    assert code == 0
    assert env == {"ANTHROPIC_API_KEY": "sk-test", "MONTHLY_COST_CEILING_USD": "4.50"}
    code, out, env = _claude_step(tmp_path, "maybe")
    assert code == 1 and not env and "::error::claude_generate" in out


def test_claude_generate_is_a_choice_defaulting_to_yes():
    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "evals.yml").read_text())
    inp = wf[True]["workflow_dispatch"]["inputs"]["claude_generate"]
    assert inp["options"] == ["yes", "no"] and inp["default"] == "yes"
