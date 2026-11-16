# OP-1/2 slow_sql 隔离复现卡

从仓库根目录运行 `lab/run-isolated-slow-sql.ps1`。脚本检查固定端口和本地基础镜像，再用 `--network=none` 从本地 orders 镜像覆盖当前服务代码；不拉取镜像或安装依赖。

脚本创建唯一 Compose project，直接发送合成 checkout 请求，记录健康基线、`slow_sql` 故障窗口和恢复请求。它在恢复重启 orders 前读取内存日志，并按故障时间窗查询 Jaeger；用 `order_id`、`trace_id` 关联 `postgres.query`。恢复请求必须成功且快于故障请求。

Jaeger 查询使用 `service=orders` 和 UTC 窗口对应的微秒时间戳，并按日志 trace ID 补查。slow SQL orders 在响应前执行 5 秒有界的 SDK `force_flush`，确保已结束的 span 离开批处理队列；flush 失败会进入 orders 日志。报告保留 Jaeger 原始响应、Collector/Jaeger 容器状态与近端日志。离线校验器覆盖真实响应结构、半开时间窗、订单与 trace 关联；空遥测即判失败。测试夹具不能证明现场已导出 span。

报告分别记录慢请求、恢复、遥测完整性及总状态。HTTP 200 且耗时超过 1.2 秒、恢复 HTTP 200 且更快，只能证明注入和恢复；缺少关联的 `postgres.query` 时总状态为 `telemetry_evidence_missing`，命令仍以失败退出。历史失败报告已有 orders 日志与 trace ID，却没有 Jaeger span；旧格式未保留 Collector 原始诊断，不能判定首个丢失点。新报告需检查 Collector/Jaeger 日志确认导出链路。

结束时只停止该 project 的容器，并核实容器已清空；不删除卷。输出路径会转为绝对路径，已有文件会拒绝覆盖。根因真值不进入诊断接口或报告。

离线回归：`tests/test_lab_checkout_e2e.py`、`tests/test_op1_slow_sql_isolated.py`。这些测试不启动 Compose。
