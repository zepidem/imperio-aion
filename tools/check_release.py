# SPDX-License-Identifier: GPL-3.0-only
"""Report known source-preparation gaps; never publishes or edits state."""
import json
from pathlib import Path

def main():
    status=json.loads((Path(__file__).resolve().parents[1]/'metadata/release-status.json').read_text(encoding='utf-8'))
    print(json.dumps(status,ensure_ascii=False,indent=2))
    return 2 if status['remaining'] else 0

if __name__=='__main__':raise SystemExit(main())
