# 截断重试收尾验收

- 日期：2026-10-23
- 真实模型调用：无；冻结六案例未重跑。
- 策略：仅对 `finish_reason=length` 的 `truncated_json` 重试一次。预算翻倍，最多 512 tokens。初次请求与重试分别经过调用闸和费用预留；闸拒绝时不发请求。
- Stub 验收：完整 JSON 成功；普通错误 JSON 不触发重试；第一次截断后成功；第二次截断后 rules fallback；并发 specialist 的每个请求最多两次；预算闸拒绝重试后只计已发请求并 fallback。
- 回归：`.venv\Scripts\python.exe -m pytest -q`，91 passed、14 subtests；`compileall` 与 `git diff --check` 通过。新增并发费用预留闸测试：两路争用时仅一条请求发出，另一条请求前拒绝，账本只写一条完成记录。
- Docker：Engine `29.1.3` 可用。本机只有 `python:3.11-slim`；`python:3.12-slim` 不存在。此前 Docker Hub 认证超时，本轮不再拉取。Python 3.11 服务镜像通过既有六场景烟测；Python 3.12 正式镜像仍未验证。
- 费用：未产生新增模型费用；账本累计峰值 `$0.43784833 / $0.50`。

复跑：在项目目录执行上述 pytest 与 compileall 命令。Docker 镜像复核只需执行 `docker info --format '{{.ServerVersion}}'`、`docker image ls --format '{{.Repository}}:{{.Tag}}' python`；不要用 `docker pull`。
