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
                  f"先记录 UTC 起点：Set-Location {ROOT}; $start=[DateTime]::UtcNow.ToString('o')。必须先于 run-scenario；启动脚本会写 deployment timestamp。",
                  f"启动场景：.\\lab\\run-scenario.ps1 -Scenario {fault}",
                  "端口：Gateway 18080；orders 18081；inventory 18082；payment 18083；Prometheus 19090；Jaeger 16686",
                  "触发：POST http://127.0.0.1:18080/checkout，order_id 使用新 GUID",
                  "请求和日志轮询结束后记录 UTC 终点：$end=[DateTime]::UtcNow.ToString('o'); $from/$to 必须复用这次运行的 $start/$end。",
                  "只读取证：GET 127.0.0.1:18081/logs?start=$from&end=$to；GET 127.0.0.1:18080/deployments?start=$from&end=$to；Prometheus 19090；Jaeger 16686",
                  "18081/logs 是 orders；18080/deployments 是 gateway。禁止用 18081/deployments。两端使用同一 UTC 半开区间 [start,end)；必须满足 start <= deployment timestamp < end。任意后续短窗返回空不能证明记录缺失。",
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
