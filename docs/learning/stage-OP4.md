# OP-4：人工批准与修复安全边界

## 设计

`opspilot/action_safety.py` 只实现签名、限时、单次使用的本地 dry-run 批准。允许动作是 `restart_service` 和 `rollback_release`；目标仅限四个实验服务。执行环境必须精确为 `test`。`operator` 只能批准重启，`release_manager` 才能批准回滚；`agent`、production、未知服务、签名篡改、过期、重放和审计账本损坏都会拒绝。

运行 approve 会要求输入 `APPROVE <case-id>`。票据签名绑定事故、角色、目标、动作、环境、过期时间、调查 run ID、证据引用摘要和建议摘要。默认 120 秒，允许 30–300 秒。密钥与票据保存在 Git 忽略的 `var/`。签发与 dry-run 必须提供同一调查报告；run、证据或建议变化都会拒绝旧票据。

模拟执行命令必须带 `--dry-run`：

```powershell
python -m opspilot.action_cli approve --case-id lab-release-01 --action rollback_release --target orders --environment test --role release_manager --approved-by operator-1 --investigation-report reports/investigation.json
python -m opspilot.action_cli dry-run --approval-file var/action-approvals/<approval-id>.json --investigation-report reports/investigation.json --environment test --dry-run
```

Dry-run 只在本地模拟并写本地审计；不会对业务系统执行写入。报告固定 `external_system_calls=0`、`external_writes=0`。回滚结果与健康检查都标为模拟；不访问 Docker、MCP、生产 API 或业务系统。

签发与执行都要求 32-byte HMAC key；执行入口独立拒绝短密钥，避免调用方绕过签发器后使用弱密钥验票。

## 攻击测试

回归覆盖过期批准、票据重放、production 环境错配、签名篡改、agent 角色越权、错误角色与 production 票据签发。测试验证拒绝原因留在本地审计，且没有外部调用。live MCP 工具清单只含查询与调查工具；没有签发或执行动作工具。

签名批准链测试建议与批准的动作/目标一致，再核对批准 ID 与 `dry_run_completed` 审计记录。这里没有单独的人工复核状态或业务执行步骤。损坏的审计 JSONL 也会 fail closed：执行器拒绝 dry-run，不覆盖或追加损坏账本。上述保护由离线回归验证。

## 离线状态流复核

状态链为：调查报告提供 run ID、证据引用和建议；按 `ACTION_SCOPES` 检查角色；操作者输入精确确认语后签发绑定 case/action/target/role/environment/expiry、run ID、证据摘要、建议摘要的票据；执行器校验签名、时限、环境、权限、nonce 和当前报告摘要；通过后只写 `dry_run_completed` 的本地模拟记录。rollback 输出 `simulated_execution` 与 `rollback_verification.status=simulated_pass`，并固定 `real_environment_verified=false`、外部调用/写入为 0。

新增负例覆盖票据恰好到期、重放、生产环境声明/执行、production/agent 声明篡改、四种越权 role/action 组合。所有执行拒绝都写 `dry_run_rejected`，外部调用为 0；过期边界按 `expires_at <= now` 拒绝。AST 回归继续确认执行器不依赖网络、子进程、Docker 或业务客户端。

`simulated_pass` 只表示模拟检查通过；没有独立的人类复核状态，也没有真实回滚或复核证据。审批人角色仍由本地调用者声明，未接身份提供者。本阶段仅做离线测试与文档审计。

批准绑定的调查报告由本地调用者提供，run ID 也是报告内声明值。HMAC 保证票据内容未被改动，不证明报告或操作者身份真实；没有 IdP、签名报告来源验证或生产授权。改动 run、证据记录、查询摘要或建议摘要后，旧票据拒绝；有效票据仍只触发本地 dry-run。

定向命令 `python -m unittest tests.test_action_safety tests.test_action_cli tests.test_s3 -v`：`23 passed`。覆盖人工确认拒绝/通过、精确到期边界、重复消费、错环境、越权角色/动作和模拟回滚状态。无模型调用、Docker 服务或真实修复调用。

## 限制

这是本机演示安全闸，不是企业授权系统。审批者角色由本地 CLI 用户声明，没有接入身份提供者；签名账本适用于顺序运行，不提供跨进程分布式锁。模拟验证不能证明真实系统回滚成功。任何真实业务操作仍须另外设计并做独立安全审查。
