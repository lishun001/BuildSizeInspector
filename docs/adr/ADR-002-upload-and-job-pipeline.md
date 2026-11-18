# ADR-002：M2 上传、任务恢复与修订发布

日期：2026-11-17。状态：采用。

## 决策

沿用 PostgreSQL 16、LocalBlobStorage、Celery 5 和 Redis 7。新增 `0002_upload_pipeline` 迁移，兼容升级已有 M0 数据；不是重新创建数据库。所有测试凭据只通过进程环境传递，API 不接收任意下载 URL。

上传清单按原协议规范化并计算 SHA-256，数据库保留原 logical key 唯一约束与 run_id 唯一约束。相同清单重试复用原构建；同身份不同清单冲突。项目行锁用于初始化和上传配额串行化，构建行锁保证 finalize 只封存一次、只创建一个初始修订/outbox。

文件流写入 Blob 根目录下的随机 `.part` 文件，校验实际长度和 SHA-256 后用独占硬链接原子提交到 `project_uuid/hash`，正式对象不被替换。同项目相同内容复用 Blob。已有内容仍须核验长度/哈希，不能覆盖损坏对象。磁盘与数据库不能同一事务提交：完整文件落盘后若数据库登记失败，可能保留一个无引用的完整 Blob；不能因此删除可能已被其他构建引用的文件。M6 将实现带审计的引用清理。普通异常/断连删除 `.part`；API 硬杀后的临时文件保留在独立临时目录，由后续生命周期任务处理，不能当作已验证产物。

项目最多同时进行两个文件上传，每个角色有独占 lease，单次上传默认 15 分钟。未封存构建 24 小时过期；相同身份与清单重新初始化可恢复，sealed 状态不回退。上传过程中不持有长期数据库行锁。

## 封存与异步验证边界

finalize 验证所有声明角色已上传、必填元数据和跨文件身份，并做有界 ZIP 中央目录检查；完整解压、CRC、内容哈希在 Worker 执行。这样 API 不承担耗时解包。中央目录合法但 CRC 错误的产物会封存后分析 FAILED；202 只表示排队，不表示包体分析成功。

可选报告缺失、无效或不支持时保存原始数据并记录覆盖状态；run_id 冲突始终拒绝。Unity/Importer/Git 的领域分析留到 M3/M4，合同通过不自动等于该领域解析完成，因此 M2 默认项目成功分析通常为 PARTIAL。页面可用的 APK 物理索引仍完整持久化。项目期望层通过已建模型保存，公开设置界面在 M5 提供。

JSON API 请求体默认限制为 1 MiB，逐文件上传不经过 JSON 缓冲；上传 JSON 报告仍保持 64 MiB 限制。CI 身份各字段受现有数据库列长度约束，四字段 UTF-8 总量额外限制为 1500 bytes，防止多字节长名称使 PostgreSQL 唯一索引溢出，超限明确 422。

CI 外部链接须为 HTTP(S)、精确匹配 `TRUSTED_LINK_HOSTS`，并拒绝 userinfo 及含 token/signature/password 等凭据参数的链接；不自动信任同域子域名。该配置必须由部署者设置，不从提交内容推导可信域名。

## 任务可靠性

finalize 在同一事务内写 sealed、analysis revision、job 和 outbox。dispatcher 在提交到 broker 后记录投递；如果消息丢失，超过投递间隔的未完成数据库任务可以再次投递。broker 失败保留记录并退避，单次调度退出非零，不声称成功。

Worker 使用递增 attempt/fencing token 和有到期时间的 lease。只有有效 lease、仍被构建请求的修订可成为 current；旧 Worker 或被较新请求替代的任务不覆盖结果。条目、比较、汇总及 current 指针在一个事务发布；失败回滚本轮中间行并保留旧成功结果。临时 I/O 最多三次消费尝试，无效输入不自动重试。任务总期限还覆盖报告校验、比较和发布，Celery 硬期限作为进程级补充。

Celery 任务在可导入模块中注册，消息仅携带 job UUID。实际 prefork 测试曾发现工厂内部注册在子进程中不可见，已改为 `jobs/tasks.py` 模块级注册，并通过真实 prefork 两次构建验证。故障注入使用单独 solo Worker，实际 SIGKILL 后让 lease 过期，再删除本测试随机命名的 Redis 队列，验证只依赖数据库即可恢复。

## 基线与查询

硬兼容条件按规格保留；Unity 版本、压缩和裁剪设置仍作为可见差异，不进入硬指纹。当前 APK 身份尚未由 SDK 核验，自动选择额外限定同 CI 实例与任务，并显式标记 declared。缺失硬字段、明显未来/异常时间或 native 目录 ABI 与声明不符时排除自动基线。目录 ABI 不代表已核验 ELF。

基线按早于目标的 build_completed_at 选择，同时间以服务端构建 UUID 排序。固定基线同样检查兼容性。对比保存双方修订、策略和选择原因，晚到历史构建不修改旧对比；手动跨组允许查看，但增长门禁 SKIPPED，Git 证据在 M4 前明确 unsupported。

持久化完整条目索引以支持原 APK 过期后的比较。列表采用服务端分页，详情只返回目录/重复组数量及有计数的有限元数据预览，完整原始元数据通过鉴权下载。前端页面、规则与 CI 客户端不纳入 M2。后续 M4 将接入构建前的 CI 基线候选查询参数与 Git 范围核对。

## 核验依据

[Celery 5 Tasks](https://docs.celeryq.dev/en/stable/userguide/tasks.html)、[SQLAlchemy 2 ORM DML](https://docs.sqlalchemy.org/en/20/orm/queryguide/dml.html)。Starlette 文档访问曾返回 502；流式 Request API 使用本机安装源码签名、断连测试和真实 HTTP/Celery 流程核验，没有以猜测代替实际调用。

实际命令和结果见 `IMPLEMENTATION_STATUS.md`。ZIP 夹具验证不代表真实 Android/Unity/Jenkins 验收。
