"""SPEC-98 D98-3: run reports committed to the eval-results branch."""

import json

import pytest

from evals import results


def _artifact(root, reports, done=True, ceiling=None):
    root.mkdir(parents=True, exist_ok=True)
    for name, body in reports.items():
        (root / name).write_text(json.dumps(body))
    if done:
        (root / "eval-done.json").write_text("{}")
    if ceiling is not None:
        (root / "eval-ceiling.json").write_text(json.dumps(ceiling))
    (root / "app.log").write_text("not copied")
    return root


def test_a_run_is_published_as_one_bundle_under_tier_and_experiment(tmp_path):
    src = _artifact(
        tmp_path / "a",
        {
            "eval-report-dracula.json": {"book": "dracula", "steps": {"chapters": {}}},
            "eval-report-cel.json": {"book": "cel", "steps": {}},
        },
        ceiling={"usd": 2.0},
    )
    dest = tmp_path / "results"
    out = results.publish(
        src, dest, tier="experiment", experiment="w22 arm/A", run="123", sha="abc"
    )
    assert out == dest / "experiment" / "w22-arm-A" / "123.json"
    body = json.loads(out.read_text())
    assert body["schema"] == results.SCHEMA
    assert (body["run"], body["attempt"], body["tier"]) == ("123", 1, "experiment")
    assert (body["experiment"], body["sha"]) == ("w22 arm/A", "abc")
    assert body["done"] is True and body["ceiling"] == {"usd": 2.0}
    assert sorted(body["reports"]) == [
        "eval-report-cel.json",
        "eval-report-dracula.json",
    ]
    assert not list(dest.rglob("app.log"))


def test_attempts_get_their_own_file_and_load_reads_them_in_order(tmp_path):
    dest = tmp_path / "results"
    report = {"eval-report-dracula.json": {"book": "dracula", "steps": {}}}
    for run, attempt in (("20", 1), ("9", 2), ("9", 1)):
        results.publish(
            _artifact(tmp_path / f"{run}-{attempt}", report, done=attempt == 1),
            dest,
            tier="gate",
            experiment="",
            run=run,
            attempt=attempt,
        )
    assert (dest / "gate" / results.UNTAGGED / "9-2.json").exists()
    got = results.load(dest, "gate", None)
    assert [(b["run"], b["attempt"], b["done"]) for b in got] == [
        ("9", 1, True),
        ("9", 2, False),
        ("20", 1, True),
    ]
    assert results.load(dest, "gate", "other") == []


def test_nothing_is_published_without_a_report_or_for_calibration_answers(
    tmp_path, capsys
):
    dest = tmp_path / "results"
    empty = _artifact(tmp_path / "empty", {})
    (empty / "eval-report-bad.json").write_text("not json")
    assert results.publish(empty, dest, tier="gate", experiment="x", run="1") is None
    private = _artifact(
        tmp_path / "private",
        {
            "eval-report-a.json": {"steps": {"chapters": {}}},
            "eval-report-b.json": {"steps": {"calibration_answers": {"answers": 1}}},
        },
    )
    argv = [str(private), str(dest), "--tier", "calibration", "--run", "2"]
    assert results.main(argv) == 0
    assert capsys.readouterr().out == ""
    assert not dest.exists()


def test_an_unknown_tier_or_a_bad_run_id_is_refused(tmp_path, capsys):
    src = _artifact(tmp_path / "a", {"eval-report-a.json": {"steps": {}}})
    with pytest.raises(ValueError):
        results.publish(src, tmp_path / "r", tier="gate", experiment="", run="../1")
    argv = [str(src), str(tmp_path / "r"), "--tier", "nope", "--run", "1"]
    assert results.main(argv) == 1
    assert "unknown tier" in capsys.readouterr().err
    assert not (tmp_path / "r").exists()


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("", "untagged"),
        (None, "untagged"),
        ("..", "untagged"),
        ("W21 day 3", "W21-day-3"),
    ],
)
def test_an_experiment_tag_is_one_safe_path_segment(text, want):
    assert results.slug(text) == want


def test_only_the_publish_job_can_write_and_it_holds_no_secret():
    """evals.yml: the eval job (which holds the provider keys) stays read-only;
    the publish job has the write permission and never names a secret."""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    wf = yaml.safe_load((root / ".github" / "workflows" / "evals.yml").read_text())
    jobs = wf["jobs"]
    assert "write" not in jobs["eval"]["permissions"].values()
    publish = jobs["publish"]
    assert publish["permissions"] == {"contents": "write"}
    assert publish["needs"] == "eval"
    assert "workflow_dispatch" in publish["if"]
    assert "secrets." not in json.dumps(publish)
