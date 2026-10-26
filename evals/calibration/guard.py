"""calibration.yml's leak guard: exit non-zero if items.json carries anything
outside the page's whitelist or any key-side id (export.leaks).
python -m evals.calibration.guard items.json key.json"""

import json
import sys
from pathlib import Path

from evals.calibration.export import leaks

if __name__ == "__main__":
    items, key = (json.loads(Path(p).read_text("utf-8")) for p in sys.argv[1:3])
    if why := leaks(items, key):
        sys.exit(f"items.json would unblind the rater: {len(why)} finding(s)")
    print("leak guard: clean")
