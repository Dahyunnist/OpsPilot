"""Print safe host-side reproduction steps for the six synthetic lab scenarios."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "lab"


def render() -> str:
    truth = json.loads((LAB / "scenarios/truth.json").read_text(encoding="utf-8"))
    lines = ["OpsPilot 合成故障宿主验收清单（只打印，不执行；不调用模型、不删除数据卷）", ""]
    for case in truth["cases"]:
        fault = case["fault"]
        case_id = case["case_id"]
        lines += [f"## {case_id} / {fault}", f"预期真值（仅评测端）：{', '.join(case['root_causes'])}",
                  f"启动：Set-Location {ROOT}; .\\lab\\run-scenario.ps1 -Scenario {fault}",
                  "时间窗：$start=[DateTime]::UtcNow.AddMinutes(-30).ToString('o'); $end=[DateTime]::UtcNow.AddMinutes(5).ToString('o')",
                  "端口：Gateway 18080；orders 18081；inventory 18082；payment 18083；Prometheus 19090；Jaeger 16686",
                  "触发：POST http://127.0.0.1:18080/checkout，order_id 使用新 GUID",
                  "只读取证：GET orders /logs（18081）；GET gateway /deployments（18080）；Prometheus 19090；Jaeger 16686",
                  "停止：.\\lab\\stop.ps1；检查：docker compose -f .\\lab\\compose.yaml ps --all", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="accepted for clarity; execution is never performed")
    parser.parse_args()
    print(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
