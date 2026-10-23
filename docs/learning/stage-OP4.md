# OP-4：人工批准与修复安全边界

## 设计

`opspilot/action_safety.py` 只实现签名、限时、单次使用的本地 dry-run 批准。允许动作是 `restart_service` 和 `rollback_release`；目标仅限四个实验服务。执行环境必须精确为 `test`。`operator` 只能批准重启，`release_manager` 才能批准回滚；`agent`、production、未知服务、签名篡改、过期、重放和审计账本损坏都会拒绝。

运行 `python -m opspilot.action_cli approve` 会要求人工输入 `APPROVE <case-id>`。票据签名绑定事故、角色、目标、动作、环境和过期时间；默认 120 秒，允许 30–300 秒。密钥与票据保存在被 Git 忽略的 `var/`。

模拟执行命令必须带 `--dry-run`：

```powershell
python -m opspilot.action_cli approve --case-id lab-release-01 --action rollback_release --target orders --environment test --role release_manager --approved-by operator-1
python -m opspilot.action_cli dry-run --approval-file var/action-approvals/<approval-id>.json --environment test --dry-run
```

Dry-run 仅写本地审计。报告固定 `external_system_calls=0`、`external_writes=0`。回滚结果与健康检查都标为模拟；不访问 Docker、MCP、生产 API 或业务系统。

## 攻击测试

回归覆盖过期批准、票据重放、production 环境错配、签名篡改、agent 角色越权、错误角色与 production 票据签发。测试验证拒绝原因留在本地审计，且没有外部调用。live MCP 工具清单只含查询与调查工具；没有签发或执行动作工具。

## 限制

这是本机演示安全闸，不是企业授权系统。审批者角色由本地 CLI 用户声明，没有接入身份提供者；签名账本适用于顺序运行，不提供跨进程分布式锁。模拟验证不能证明真实系统回滚成功。任何真实业务操作仍须另外设计并做独立安全审查。
