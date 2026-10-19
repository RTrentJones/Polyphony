"""The rate job's leak guard (tpm-plan-wave13 D4(f)): bundles only, no LLM_MAX_RPD."""

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
WF = Path(__file__).parents[2] / ".github/workflows/baseline.yml"


def test_the_rate_job_downloads_bundles_only_and_never_sets_max_rpd():
    wf = yaml.safe_load(WF.read_text())
    rate = wf["jobs"]["rate"]
    runs = "\n".join(s.get("run", "") for s in rate["steps"])
    ok = r"-p 'baseline-bundle-\*'|-n baseline-rate-state "
    gets = re.findall(r"gh run download .*", runs)
    assert gets and all(re.search(ok, g) for g in gets)
    leak = re.search(r"baseline-(key|state)-|LLM_MAX_RPD", str(rate) + str(wf["env"]))
    assert not leak and "LLM_MAX_RPD" in wf["jobs"]["generate"]["env"]
