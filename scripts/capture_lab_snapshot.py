"""Freeze live, loopback telemetry before a fault-bearing service restarts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.telemetry import LabTelemetry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    telemetry = LabTelemetry()
    sources = {}
    for name in ("metrics", "logs", "traces", "deployments"):
        sources[name] = getattr(telemetry, name)(args.start, args.end, args.limit)
    missing = [name for name in ("metrics", "logs", "traces") if not sources[name].get("records")]
    if missing:
        print(json.dumps({"error": "missing telemetry sources", "sources": missing}), file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(sources, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "counts": {key: len(value.get("records", [])) for key, value in sources.items()}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
