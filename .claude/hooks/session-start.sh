#!/bin/bash
# SessionStart hook: bootstrap a fresh clone so the pipeline, tests, and linter
# run out of the box in Claude Code on the web. Idempotent; local (non-web)
# sessions skip it entirely.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Python 3.12+ venv with all runtime + dev deps (ruff, pytest).
uv sync --extra dev

# The Yungching scraper drives headless Chromium via Playwright. Remote
# containers pre-install a version-agnostic binary at
# $PLAYWRIGHT_BROWSERS_PATH/chromium, which src/scrapers/site_yungching.py
# falls back to automatically — only download if neither that nor the
# revision Playwright expects is present.
uv run python - <<'EOF'
import os
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    expected = Path(p.chromium.executable_path)

browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
fallback = Path(browsers_path) / "chromium" if browsers_path else None

if expected.exists():
    print(f"chromium: OK ({expected})")
elif fallback and fallback.exists():
    print(f"chromium: OK via pre-installed fallback ({fallback})")
else:
    print("chromium: not found, installing via playwright")
    subprocess.run(["playwright", "install", "chromium"], check=True)
EOF

# Airtable credentials come from the environment (or .env locally). Live runs
# need them; dry runs don't. Surface early if they're missing.
if [ -z "${AIRTABLE_TOKEN:-}" ] && [ ! -f .env ]; then
  echo "WARNING: AIRTABLE_TOKEN not set and no .env file — live runs will fail (dry runs are fine)." >&2
fi

echo "session-start: environment ready"
