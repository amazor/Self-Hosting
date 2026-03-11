#!/usr/bin/env python3
"""Media VM bootstrap — see stack_config.py for steps.

Usage:
  cd docker_compose/media && sudo python3 bootstrap.py
  cd docker_compose/media && sudo python3 bootstrap.py --force -v
"""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.homelab_bootstrap import BootstrapRunner
from docker_compose.media import stack_config

if __name__ == "__main__":
    raise SystemExit(BootstrapRunner(stack_config).run())
