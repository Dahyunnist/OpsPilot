# OP-4：人工批准与修复安全边界

## 设计

`opspilot/action_safety.py` 只实现签名、限时、单次使用的本地 dry-run 批准。允许动作是 `restart_service` 和 `rollback_release`；目标仅限四个实验服务。执行环境必须精确为 `test`。`operator` 只能批准重启，`release_manager` 才能批准回滚；`agent`、production、未知服务、签名篡改、过期、重放和审计账本损坏都会拒绝。

运行 approve 会要求输入 `APPROVE <case-id>`。票据签名绑定事故、角色、目标、动作、环境、过期时间、调查 run ID、证据引用摘要和建议摘要。默认 120 秒，允许 30–300 秒。密钥与票据保存在 Git 忽略的 `var/`。签发与 dry-run 必须提供同一调查报告；run、证据或建议变化都会拒绝旧票据。

模拟执行命令必须带 `--dry-run`：

```powershell
python -m opspilot.action_cli approve --case-id lab-release-01 --action rollback_release --target orders --environment test --actor-id local-release-manager --identity-mode local-test --investigation-report reports/investigation.json
python -m opspilot.action_cli dry-run --approval-file var/action-approvals/<approval-id>.json --investigation-report reports/investigation.json --environment test --dry-run --actor-id local-release-manager --identity-mode local-test
```

Dry-run 只在本地模拟并写本地审计；不会对业务系统执行写入。报告固定 `external_system_calls=0`、`external_writes=0`。回滚结果与健康检查都标为模拟；不访问 Docker、MCP、生产 API 或业务系统。

签发与执行都要求 32-byte HMAC key；执行入口独立拒绝短密钥，避免调用方绕过签发器后使用弱密钥验票。

## 攻击测试

回归覆盖过期批准、票据重放、production 环境错配、签名篡改、agent 角色越权、错误角色与 production 票据签发。测试验证拒绝原因留在本地审计，且没有外部调用。live MCP 工具清单只含查询与调查工具；没有签发或执行动作工具。

签名批准链测试建议与批准的动作/目标一致，再核对批准 ID 与 `dry_run_completed` 审计记录。这里没有单独的人工复核状态或业务执行步骤。损坏的审计 JSONL（语法错误、非对象事件或缺少 `event_type`）会 fail closed：执行器拒绝 dry-run，不覆盖或追加损坏账本。上述保护由离线回归验证。

## 离线状态流复核

状态链为：调查报告提供 run ID、证据引用和建议；按 `ACTION_SCOPES` 检查角色；操作者输入精确确认语后签发绑定 case/action/target/role/environment/expiry、run ID、证据摘要、建议摘要的票据；执行器校验签名、时限、环境、权限、nonce 和当前报告摘要；通过后只写 `dry_run_completed` 的本地模拟记录。rollback 输出 `simulated_execution` 与 `rollback_verification.status=simulated_pass`，并固定 `real_environment_verified=false`、外部调用/写入为 0。

新增负例覆盖票据恰好到期、重放、生产环境声明/执行、production/agent 声明篡改、四种越权 role/action 组合。所有执行拒绝都写 `dry_run_rejected`，外部调用为 0；过期边界按 `expires_at <= now` 拒绝。AST 回归继续确认执行器不依赖网络、子进程、Docker 或业务客户端。

`simulated_pass` 只表示模拟检查通过；没有独立的人类复核状态，也没有真实回滚或复核证据。本地 resolver 只提供固定合成主体，未接身份提供者。本阶段仅做离线测试与文档审计。

审批决策只读取 `ActorResolver` 返回的 `Principal`，CLI 不接收角色或审批人自报字段。主体不一致、主体缺少动作所需角色、resolver 未配置，均 fail-closed。CLI 必须显式选择 `local-test` 和固定测试主体；生产身份 resolver 尚未实现。此本地 resolver 只是合成身份，不等于接入 IdP，也不证明用户身份。批准绑定的调查报告仍由本地调用者提供，run ID 也是报告内声明值；HMAC 不证明报告来源。有效票据仍只触发本地 dry-run。

定向命令 `python -m unittest tests.test_action_safety tests.test_action_cli tests.test_s3 -v`：本轮身份边界回归 29 项通过。覆盖人工确认拒绝/通过、精确到期边界、重复消费、主体错配、自报角色拒绝、缺 resolver、错环境、越权和模拟回滚。无模型调用、Docker 服务或真实修复调用。

## 2026-11-13 trace/manifest 对照与拒绝负例

通用运行 manifest 的顶层 `run_id`/`trace_id` 是本次运行的关联标识。execution `events` 必须与二者一致；`telemetry_evidence` 的 `run_id` 必须属于该运行，但其 `trace_id` 是实际服务 span 的分布式 trace，可与顶层关联 ID 不同。冻结 OP-1 host manifest 的 report/truth SHA-256 重新核对，48 条 span 均关联同一 `run_id`，且 span trace ID 与运行关联 trace ID 分开。新增 validator 负例拒绝 event trace/run 错配与跨 run span；不误拒绝不同的真实 span trace ID。

OP-4 安全负例仍用固定 synthetic report、Principal 与内存 HMAC key。过期/恰好到期、重放、主体错配、环境篡改、证据/建议/run 变化、越权和损坏账本均拒绝，拒绝审计的外部调用数为 0。未使用 Docker、模型或额外预算。当前宿主的 `bad_release` 验收被 Docker Desktop 命名管道拒绝：`open //./pipe/dockerDesktopLinuxEngine: Access is denied.`；健康基线未启动，未发 checkout 或遥测查询。停止命令也因相同权限失败，Compose 状态无法核实；遵照要求不再重试。

## 限制

这是本机演示安全闸，不是企业授权系统。没有接入身份提供者；签名账本适用于顺序运行，不提供跨进程分布式锁。模拟验证不能证明真实系统回滚成功。任何真实业务操作仍须另外设计并做独立安全审查。

## 2026-11-13 OP-4 负例复核

重跑 `\.\venv\Scripts\python.exe -m unittest tests.test_action_safety tests.test_action_cli tests.test_s3 -q`：32 项通过。覆盖过期/恰好到期、单次使用与重放拒绝、错误环境、越权角色、签名与调查绑定、dry-run 及模拟回滚检查。执行与拒绝审计均断言外部调用/写入为 0；真实环境回滚未执行。
