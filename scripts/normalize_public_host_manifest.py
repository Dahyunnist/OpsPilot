"""Write public-schema v2 view of the committed OP-1 host run manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opspilot.run_manifest import normalize_host_manifest  # noqa: E402


def normalizer_code_sha256() -> str:
    digest = hashlib.sha256()
    for relative in ("opspilot/run_manifest.py", "scripts/normalize_public_host_manifest.py"):
        payload = (ROOT / relative).read_bytes()
        digest.update(relative.encode("utf-8") + b"\0" + len(payload).to_bytes(8, "big") + payload)
    return digest.hexdigest()


def main() -> int:
    source_path = ROOT / "reports" / "op1-host-matrix-run-manifest.json"
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes.decode("utf-8"))
    report_path = ROOT / source["report_path"]
    truth_path = ROOT / source["gold_source"]
    report_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()
    truth_sha256 = hashlib.sha256(truth_path.read_bytes()).hexdigest()
    normalized = normalize_host_manifest(
        source,
        source_manifest_sha256=hashlib.sha256(source_bytes).hexdigest(),
        report_sha256=report_sha256,
        truth_sha256=truth_sha256,
        normalizer_sha256=normalizer_code_sha256(),
    )
    output = ROOT / "reports" / "op1-host-matrix-public-manifest.json"
    output.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
