# M0–M6 开发与运维说明

当前已接通协议、离线分析、三步上传、Celery/outbox、修订发布、Unity 资源比较、CI CLI、多仓 Git 和蒲公英结果关联。12 类规则和真实 Web 页面也已接通；本地完整容器栈、保留/清理、恢复与性能样本已验证；外部生产/CI 验收仍有缺口。API 合同见 API.md，阶段边界见 IMPLEMENTATION_STATUS.md。

## 本地环境

已验证 Python 3.12.0、uv 0.11.15、Node 25.8.0、pnpm 11.1.3。当前没有使用 conda，后端虚拟环境位于 `backend/.venv`。锁文件分别为 `backend/uv.lock` 与 `frontend/pnpm-lock.yaml`。

```bash
make install
make test
make lint
make typecheck
make e2e
make demo
```

`make e2e` 会创建一次性 PostgreSQL/Redis 环境，验证 API、真实 prefork Worker、SIGKILL/队列丢失恢复、CLI 与 HTTP；需要可用的 Docker/浏览器，包含真实页面测试。`make demo` 在 `.buildsize/demo/` 写入明确标识的合成 ZIP 与对比 JSON。这些 ZIP 不可安装，也不代表 Unity 构建。

## 离线分析

```bash
uv run --project backend --frozen buildsize analyze /path/to/final.apk --output .buildsize/snapshot.json
uv run --project backend --frozen buildsize compare /path/to/old.apk /path/to/new.apk --output .buildsize/comparison.json
uv run --project backend --frozen buildsize validate build-meta examples/build-meta.json
```

结果使用整数 bytes；展示时除以 1,048,576 得 MiB。`delta_apk_bytes` 严格等于条目压缩增量合计加 `delta_overhead_bytes`。正向增长与负向缩减合计为净变化。目录视图不能相加。分类器版本不一致必须重算，不能混比。

`analyze` 的 Android 元数据默认 not_requested。`--apkanalyzer` 仅接受管理员配置的本地工具；工具缺失、沙箱缺失或硬资源限制失败时明确 unavailable。基础 ZIP 结果仍可用。真实 Android 身份核验尚未完成。

`--max-apk-bytes`、`--max-entries`、`--max-entry-bytes`、`--max-total-bytes` 和 `--timeout` 可收紧本次离线资源预算。默认值遵循规格。非法路径、重叠、CRC 错误、预算或期限超限都会以非零退出码结束。CLI stdout 是 JSON，诊断在 stderr；指定 `--output` 时使用临时文件加原子替换，失败保留原输出。输出不允许覆盖输入文件。

网络提交 CLI 的五个命令已经接通，配置/冻结/重试及流水线合并见 [CI 接入说明](../integrations/jenkins/README.md)。离线 compare 只处理本地文件。

## 数据库、队列与 API

先复制 `.env.example` 到 `.env`，在本机设置 DATABASE_URL、POSTGRES_PASSWORD、SESSION_SECRET、TOKEN_HASH_PEPPER，并把 Jenkins 来源 URL 的精确主机名填入 TRUSTED_LINK_HOSTS。后两个 secret 至少各 32 字符且互不相同，不要提交 `.env` 或把值放在 shell 命令参数里。数据库连接使用 PostgreSQL 16 / psycopg URL，密码中的特殊字符必须进行 URL 编码。Compose 用户/库名为 buildsize，开发 API 通过本机 5432 访问数据库。

```bash
make db
make migrate
uv run --project backend --frozen --env-file .env buildsize-admin create-user admin
uv run --project backend --frozen --env-file .env buildsize-admin create-project demo-game --name 演示项目
uv run --project backend --frozen --env-file .env buildsize-admin grant admin demo-game --role admin
make dev
```

在其他终端从同一个仓库根目录启动：

```bash
make worker
make scheduler
```

API、Worker、scheduler 必须使用相同 DATABASE_URL 和 BLOB_ROOT。模板默认 Blob 路径为 .buildsize/blobs，队列为本机 Redis 6379；完整容器化栈见 deploy/README.md，根 Compose 继续用于本机进程开发。P0 Worker 并发固定为 1。dispatcher 每 2 秒巡检，默认 30 秒重新投递尚未完成的任务；租约到期后可恢复 Worker 中止的任务。单次调度可运行 buildsize-dispatcher --once，投递失败退出 4，记录保留以便恢复。

密码由 getpass 交互输入。API 绑定 127.0.0.1:8000。`/api/v1/health/live` 仅表示进程可用；`/api/v1/health/ready` 会实际访问数据库并验证迁移版本，失败返回 503。

OpenAPI 已提供登录、项目、三步上传、历史/条目/鉴权下载、冻结比较及管理员重分析。浏览器所有写操作需要与 PUBLIC_BASE_URL 一致的 Origin；除登录外，还需从登录响应或 `/auth/me` 取得 csrf_token，通过 X-CSRF-Token 提交。具体输入/错误见 API.md。

生产配置须设置 APP_ENV=production 与 HTTPS origin，Cookie 自动设置 Secure；实际 TLS 代理与生产部署仍待 M6 验证。会话期限 8 小时，登录按真实连接 IP 每分钟限制 10 次，不信任任意 X-Forwarded-For。

项目 CI token 由本机管理员创建，仅创建时在 stdout 显示一次，应立即放入凭据管理器。撤销时使用创建返回的 token UUID。不要归档 token 输出。

```bash
uv run --project backend --frozen --env-file .env buildsize-admin create-token demo-game --scope builds:read --scope builds:submit --scope distribution:write
uv run --project backend --frozen --env-file .env buildsize-admin revoke-token TOKEN_UUID
```

## PostgreSQL 专项验证

设置专用测试连接环境变量 BUILDSIZE_TEST_DATABASE_URL 后运行 `make test-postgres`，验证 PostgreSQL 基础和 M2 事务。不要把连接值放到命令行参数或测试日志里。测试检查 PostgreSQL 主版本为 16，创建随机命名的隔离 Schema，执行迁移/唯一键竞态/降级/升级，最后删除该测试 Schema；不会修改默认应用 Schema。使用具备创建 Schema 权限的专用测试账户。

没有专用 PostgreSQL/Redis 连接时，普通 make test 会明确跳过对应的真实服务场景。SQLite 及离线 DDL 检查不能代替 PostgreSQL 行锁、Celery 或持久化验收。

Docker 可用时运行 make test-postgres-container。脚本以固定 digest 启动一次性 PostgreSQL 16 和 Redis 7，生成临时数据库凭据并仅通过环境传递，运行完整测试。PostgreSQL 使用本次专属临时卷以支持真实重启检查；最后删除自建容器和卷。报告为 .buildsize/postgres-test-results.xml；make e2e 的报告为 .buildsize/e2e-results.xml。真实 Worker 只传入 job UUID，测试仅删除本次随机 UUID 队列。最新实际结果见 IMPLEMENTATION_STATUS.md。

## 备份与恢复边界

完整栈使用 deploy/README.md 的离线备份入口：先暂停外部写入、停止 web/api/worker/scheduler，再同时保存 PostgreSQL 与已登记 Blob。恢复只允许受信任备份和空数据库/空 Blob 卷；不提供 force 覆盖。密钥配置单独加密保管，不能只备份数据库或把运行 secret 混入公开报告。

本轮实际完成了空卷恢复后的登录、历史/token、下载和重分析；演练仅针对隔离 Compose 栈，不能替代真实业务灾备签收。Redis 消息由数据库 outbox/lease 恢复。全部本地清理默认 dry-run，受保护引用和共享内容不会按单个构建删除；未知未登记正式文件保留核对，不能直接对 Blob 根目录定时 rm -rf。

## 故障定位

- `UNAUTHENTICATED`：检查登录、会话到期、令牌撤销/到期；不要打印令牌。
- `CSRF_REJECTED`：检查 Origin、PUBLIC_BASE_URL 和 X-CSRF-Token。
- `LOGIN_RATE_LIMIT`：等待下一个分钟窗口；多个进程共享数据库计数。
- `SERVICE_UNAVAILABLE`：检查 PostgreSQL 连接、迁移与 ready 返回值。
- `ZIP_INVALID_STRUCTURE` 等错误：保留原 APK，不尝试执行或无界解压；用合法样本验证后重试。
- `TOOL_RESOURCE_LIMIT_UNAVAILABLE`：当前平台不能强制工具资源上限，增强解析停止；基础 ZIP 分析仍可运行。

API 记录 request_id、UTC 时间、阶段、方法、状态和耗时；Worker 记录 build/revision/job、输入清单 SHA、解析器版本、阶段和错误码。默认入口关闭原始 URL 访问日志，不记录 Authorization、Cookie、请求体或连接凭据。测试报告在 .buildsize/ 下，实施状态和剩余验收见 IMPLEMENTATION_STATUS.md。


## M3 Unity 独立验证

UPM 接入见 integrations/unity/com.company.buildsize/README.md。显式调用应保留原 BuildOptions 并按位增加 DetailedBuildReport；知道本次 options.scenes 时传给导出重载。回调仅在环境启用时执行，缺目录/run_id 没有副作用。

```bash
make unity-test
make unity-validate
make test-postgres-container
```

前两条使用已安装的固定 6000.0.76f1，不自动装/升级 Editor，也不连接云项目。工具拒绝已有非托管目录、符号链接工程、运行中的目标 Editor 或锁；不会借用/关闭用户其他 Editor。包由 UPM API 安装，仅独立验证工程显式装载同一测试源以避免零测试假通过。第一次需要已有可用 Unity 许可与模板/测试依赖，Android 构建还需 SDK/NDK/JDK；缺失时保留错误与 NOT_VERIFIED，不自动接受新许可。

unity-test 只运行 8 项 EditMode；unity-validate 另构建 A/B 两份实际 APK，并在 .buildsize/unity/current-evidence.txt 指定本次完整输出。后端测试只读该明确指针或 BUILDSIZE_UNITY_EVIDENCE 指定目录，不扫描“最新/最大 APK”。原 APK/JSON 位于受控目录并被 .gitignore 排除，提交的摘要位于 docs/validation/。

新迁移为 0003_unity_layers；make migrate 升级后重启 API/Worker/scheduler。旧修订仍可读，重新分析会生成新修订后原子切换，原始产物不改写。正常解析不要求映射/GUID 100%，覆盖率分别展示；截断/设置错误为 PARTIAL。若原 APK/JSON 已不可用，不承诺重新采集，保留现有索引或明确不可用。

## M4 迁移与重试材料

当前迁移 head 为 0004_ci_links。升级前备份，运行 make migrate，再重启 API/Worker/scheduler；ready 检查该版本。新增 repository_snapshots/git_ranges/git_file_changes/distribution_links。旧不可变元数据不回填猜测值；重新分析已上报 Git 会创建新修订，缺失的 Git 不能凭空补采。

升级不需要新依赖或锁文件变更。CI token 如需发布关联，单独创建含 distribution:write 的项目令牌，再按原凭据轮换流程替换；不要放宽成全局 token。TRUSTED_LINK_HOSTS 同时列出实际 Jenkins 与安装页主机。

保留每次构建独立的 context、整份冻结 submission 及 receipt；submit 断网后直接对同目录重试。receipt 和安全发布结果可用于稍后关联，API/项目/run_id 不一致必须停止核对。Jenkins 示例仅归档 JSON，APK 按已有制度归档；恢复提交时须同时取回原 APK。完整接入与失败注入清单见 integrations/jenkins/README.md，真实 Jenkins/蒲公英仍 NOT_VERIFIED。

## M5 Web 与规则

当前迁移 head 为 0005_rules_web；先备份，再 make migrate 并重启 API/Worker/scheduler。旧比较不回填猜测的历史规则，旧结果仍可读；要生成规则结果需显式重分析或新比较。新规则配置/复核有版本检查、项目管理员权限和审计。

本地 Web 使用 .env 的 PUBLIC_BASE_URL=http://127.0.0.1:5173，分别启动 make dev（API 8000）、make worker、make scheduler、make web。Vite 只代理 /api 并保留 Origin，浏览器与 API 共享 origin。纯 API 调试可以继续使用实际 API origin；不要为接页面关闭 CSRF、开放任意 CORS 或把 CI token 注入构建环境。

make build 实际执行 TypeScript 与 Vite 生产构建，输出 frontend/dist；这不是生产部署。应用同源代理/TLS/完整容器编排留到 M6。当前依赖主包有大于 500 kB 的真实构建警告，保留以便后续性能/分包处理，没有提高警告阈值。

make browser-test 运行独立 PostgreSQL/Redis/API/Vite/prefork 和两条 Playwright 场景；make e2e 和完整容器测试同样包含它。默认复用本机 Chrome，其他环境参考 frontend/README.md 配置浏览器。没有浏览器/专用服务时标记 NOT_VERIFIED，不用 mock UI 通过验收。报告 .buildsize/browser-results.xml、browser-harness.xml 和截图 web-desktop.png；失败上下文包含测试输入，不应对外归档。验证脚本关闭所有自建进程、随机队列、容器和临时卷。

## M6 完整栈与生命周期

完整启动、环境、HTTPS 边界、备份/空目标恢复、pin/清理与故障说明见 [部署与运维](../deploy/README.md)。当前迁移 head 为 0006_lifecycle；make migrate 后重启本机应用进程，或完整栈通过一次性 migrate 服务升级。新增 pin、引用过期标记和 Blob GC 墓碑，原始清单不改写。

新入口 make stack-build/stack-up/stack-stop/stack-check/benchmark；stack-check 和 benchmark 只管理随机隔离测试栈，结束清理，不启动真实常驻服务。原本机开发命令继续使用 .env；完整栈使用独立私有 .env.deploy，不要覆盖已有文件。诊断的 registered_blob_bytes 是已登记内容统计，不代表文件系统中未知/临时文件的实际总量。

性能、验收矩阵与最新命令见 IMPLEMENTATION_STATUS.md、docs/validation/ACCEPTANCE_MATRIX.md。用户已确认所有 current 修订同时保护原始文件与派生索引，允许长期保留；天数仍是最早清理期限，自动清理保持关闭。生产 TLS/DNS、真实 Jenkins/蒲公英、用户工程与正式安全扫描仍需外部验收。
