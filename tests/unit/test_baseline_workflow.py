"""W14-4, the rate job's leak guard: bundles only, no LLM_MAX_RPD; N3's GH_TOKEN."""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
WF = yaml.safe_load(Path(".github/workflows/baseline.yml").read_text())


@pytest.mark.parametrize("kind, code", [("key", 1), ("state", 1), ("bundle", 0)])
def test_the_rate_job_gets_bundles_only_and_its_guard_fails(tmp_path, kind, code):
    rate, ok = WF["jobs"]["rate"], r"-p 'baseline-bundle-\*'|-n baseline-rate-state "
    runs = "".join(s.get("run", "") for s in rate["steps"])
    gets = re.findall(r"gh run download .*", runs)
    assert gets and all(re.search(ok, g) for g in gets) and "GH_TOKEN" not in WF["env"]
    assert not re.search(r"baseline-(key|state)-|LLM_MAX_RPD", f"{rate}{WF['env']}")
    (tmp_path / f"bundles/1/baseline-{kind}-cel").mkdir(parents=True)
    guard = next(s["run"] for s in rate["steps"] if s.get("name") == "Leak guard")
    run = subprocess.run(["bash", "-eo", "pipefail", "-c", guard], cwd=tmp_path)
    assert run.returncode == code
