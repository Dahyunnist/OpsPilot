# OP-1 核心六类故障离线审计

本审计只检查脚本、服务代码、真值映射和离线测试；没有启动 Docker，也没有读取运行报告、回放文件或费用账本。它不证明本机运行时已通过。

## 核心故障映射

| 故障 | 注入与预期信号 | 确定性、恢复与边界 |
| --- | --- | --- |
| `db_pool_exhaustion` | `lab/service/app.py` 的 `start_pool_pressure` 打开 20 个 PostgreSQL 连接并持有 8 秒；订单请求等待最多 2 秒。预期订单 DB 错误和连接占用日志。 | 连接数与持有时间固定，但连接建立必须在 2 秒内完成；慢主机可能无法建立压力。服务重启会释放连接。 |
| `dependency_timeout` | payment `charge` 延迟 2 秒；orders 请求超时为 0.4 秒。预期 payment 延迟 trace 和 orders 超时日志。 | 固定延迟、固定超时；启动/调度抖动仍会影响观测。恢复通过重建无故障服务。 |
| `bad_release` | `lab/run-scenario.ps1` 将 orders 设为 v2，并指向不存在的 `inventory-v2`；启动注入前会跑健康基线。预期 v2 部署记录和库存依赖 DNS 错误。 | 主机名固定；Compose DNS 结果依赖网络实际启动。矩阵给该日志最多 10 秒收敛。 |
| `slow_sql` | orders 创建带 `opspilot.order_id` 属性的 `postgres.query` span，并执行 `SELECT pg_sleep(1.5)`。预期 PostgreSQL 慢查询 trace 和 orders 延迟 trace。 | SQL 延迟固定；要求 Jaeger 收到并返回 trace。日志在服务进程内存，重启后丢失，必须在重启前抓取。 |
| `retry_storm` | orders 最多尝试 4 次；payment 每个新进程先返回 3 次 503。预期至少 3 条 payment 失败和 3 条 orders 重试日志。 | 计数固定；每个场景重建 payment 进程会重置计数。重建无故障服务可恢复。 |
| `double_root` | 同时启用 `slow_sql` 和 payment 2 秒延迟；orders payment 超时为 0.4 秒。预期慢 SQL trace、payment 延迟 trace、orders 超时日志。 | 这是两个基础机制的复合场景，不是第七种独立注入，也不是随机种子变体。 |

## 隔离与证据窗口

- 六个订单故障请求使用随机 GUID 作为 `order_id`，不再把 fault 名写进可观测订单号。矩阵仍在本机报告里记录场景和 case ID；该报告不传给服务或调查工具。
- `truth.json` 仅由本机矩阵/评分脚本读取，不挂载给 Compose 服务。调查阶段取得遥测后，评分器才用 case ID 读取真值。
- `/logs` 和 `/deployments` 要求 UTC 起止时间，按 `[start,end)` 过滤。日志来自进程内最多 5000 条内存队列；部署来自 JSONL。Jaeger 查询最多取 1000 条 orders traces，矩阵再按 span 起始时间和订单号过滤。
- 每个场景前记请求窗口起点，采集后记终点；slow SQL 等 trace 给最多 10 次、每次 1 秒的收敛等待。所有核心场景都检查预期信号；窗口、服务名、订单号不匹配的证据不会计入。
- Host matrix 用独立 Compose project，先检查 Docker、端口和本地镜像，再执行健康基线、故障场景、无故障恢复。清理停止本次项目并检查容器；保留数据卷。
- 矩阵通过 `-NoBuild` 使用本地镜像，避免在线构建依赖。独立的手动场景脚本默认仍可构建；`bad_release` 固定不构建。

## 运行边界

离线测试只能证明源代码契约、真值映射、窗口筛选和脚本结构。它不能证明 Docker Engine 可用、20 个数据库连接能及时建立、容器启动、OTLP/Jaeger 收敛或六种故障在当前主机全数通过。既有单场景记录不等价于六类矩阵通过。不得将随机订单号或重复运行计为新机制。

手动单场景脚本与矩阵隔离强度不同：`run-op1-case.ps1` 依赖清理包装器；慢 SQL 和 retry 专用脚本使用默认 Compose 项目。需要并行运行时，应使用 host matrix 的独立项目。
