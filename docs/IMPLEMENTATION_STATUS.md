# 实施状态

更新日期：2026-11-23，Asia/Shanghai。

## Linux 一键部署

- `bash deploy/deploy.sh` 复用完整 Compose 栈。首次生成私有环境文件和随机密钥，启动时执行迁移并检查 API 就绪，重跑沿用配置和持久卷。[部署说明](../deploy/README.md#linux-一键部署)
- `bash -n deploy/deploy.sh`、`git diff --check`、`make contracts` 通过。使用 Docker Desktop 的 Linux 容器引擎，以独立 Compose 项目和临时配置运行脚本两次，验证配置权限为 `0600`、数据库迁移到 `0007_apk_download`、宿主机就绪接口返回 `ready`、冲突参数被拒绝、重跑保留配置和数据库记录。
- 目标 Linux 主机及 Mac Jenkins 到 HTTPS 网关的连接待实机验收。

## Unity 报告与 APK 后台下载

Jenkins Shell 用既有 Unity 插件采集本次报告并上传摘要，再通过 `finalize-from-url` 提交该 APK 的 HTTPS 直链。Worker 下载并校验后封存构建，网页展示包体与资源差异、证据、候选原因，以及后台下载状态。迁移为 `0007_apk_download`，新增配置 `APK_DOWNLOAD_HOSTS`。签名链接在等待期间加密保存，完成或终止后删除。原上传及手工补交入口保留。设计和边界见 ADR-008。

- `pytest backend/tests/test_apk_download.py backend/tests/test_ci_pipeline.py -q --tb=short` 共 **20 项通过**。测试使用控制的真实 HTTPS 响应，覆盖无 CLI 上报、回执和响应不含签名链接、错误哈希拒绝、更新链接重试、同一任务合并报告分析，以及项目和下载地址限制。
- `make test-postgres-container` 共 **188 项通过，无跳过，68.42 秒**，包括 PostgreSQL 迁移、Redis/Celery 和浏览器回归。
- `make lint`、`make typecheck`、`make contracts`、`make build`、接入 Shell 及 Jenkinsfile Groovy 语法检查通过。
- 真实 Jenkins 任务需要确认其发布环节能提供 Linux 可直接访问的 APK 文件链接。实际蒲公英直链和生产下载网络待联调。

## Mac 打包机免装 BuildSize CLI

按用户确认的接入方式，Mac Jenkins 在 Shell 中调用随仓的 `unity-report-handoff.py`，只依赖系统 Python 3 标准库和 Git，通过 HTTPS 上报报告及摘要。回执格式与既有 `complete-apk` 兼容。原 API 和数据库迁移无需变更。准备时记录 Git HEAD 和 dirty 状态，上报时冻结元数据与摘要，重复操作可复用同一构建。完整 Git 差异依然由原 CLI 路线生成。

- `make test-postgres-container` 共 **175 passed，无跳过，65.00 秒**。新增测试禁用第三方 Python 包加载，连接实际 HTTPS API，确认未信任证书时拒绝、持证书仅上传报告、状态保持待补交、重试复用同一构建，以及 Linux CLI 补交 APK 后能执行 Worker 分析。
- `make lint`、`make typecheck`、`make contracts`、`make build`、Shell 片段和 Jenkinsfile 的 Groovy 语法检查通过。真实 Mac Jenkins 任务、蒲公英文件与内网证书仍待用户环境验收。[Jenkins 配置](../integrations/jenkins/README.md)

## 蒲公英 APK 与 Shell 报告分步提交

按 ADR-007 实现。Mac Shell 仅上报 Unity 报告和构建元数据，同时冻结最终 APK 的长度和 SHA-256。操作者从蒲公英下载指定版本后，在 Linux 按同一回执补交。报告待齐及 APK 核对前不会封存或排队。服务端上传合同、数据库和分析任务沿用原实现。

- 新入口为 `submit-unity-reports.sh` 和 `buildsize complete-apk`，Jenkins 示例已接到原发布确认后的报告上报阶段。[接入说明](../integrations/jenkins/README.md)
- `make test-postgres-container` 共 **174 passed，无跳过，61.84 秒**。新增 Shell、真实 HTTP 和 Worker 流程覆盖只传报告、同长度错包拒绝、上传窗口续期及重复补交仅创建一个任务。报告为 `.buildsize/postgres-test-results.xml`。
- `make lint`、`make typecheck`、`make contracts`、`make build`、Shell 语法和 Jenkins 示例的 Groovy 语法检查通过。本机同时有 x64 和 arm64 Node，首次构建选到 x64 后缺少其原生模块，切回本机已安装依赖使用的 arm64 Node 后复测通过。
- 真实 Jenkins 发布后的 `buildKey`、蒲公英下载文件与生产入口仍待验收。验证使用本机夹具，不涉及真实蒲公英账号或安装 API。

## M6 当前交付

已完成可验证的本地完整栈、保留/清理、离线备份恢复、生产静态包安全检查和大样本测量。现在 M0–M6 本地核心均有可运行入口，但外部生产验收尚未完成，不能据此宣布真实发包平台已上线。当前目录仍没有 .git；没有初始化仓库、提交、修改真实 Jenkins/游戏工程或连接真实蒲公英发布。

### 实现了什么

- 独立 deploy/compose.yaml，保留根开发 Compose。web/api/worker/scheduler/postgres/redis 和一次性 migrate，持久 DB/Blob/队列卷、健康依赖、冻结镜像 digest、应用非 root/只读根/能力移除/资源约束。没有把 APK 留在容器临时层。
- 前端实际生产构建由 NGINX 同源托管，SPA、2 GiB 限额、超时、大文件不缓冲、CSP/nosniff/禁止嵌框；API/DB/Redis 不发布主机端口。入口覆盖伪造转发头，内部代理信任有明确边界，并以真实请求验证登录限流不能被外部 X-Forwarded-For 绕过。
- 新迁移 0006_lifecycle：pin、引用 expired_at 和 Blob gc_marked_at/deleted_at。默认最早期限 APK/报告/索引 30/180/365 天。保守保护所有 current 原始文件、固定基线、pin、活跃任务/上传；索引保护 current/requested 和所有比较引用。天数不保证删除，可能长期保留。
- 运维模块默认 dry-run，按服务器时间/引用生成 SHA 计划，应用前持项目锁重算，引用/pin/配置或数据卷身份变化拒绝旧计划。引用过期与共享 Blob 删除分开，标记后至少宽限 24 小时。过期引用不能借另一构建的相同内容下载，原始清单与身份保持不变。
- 无引用旧索引清理后明确 EXPIRED/unavailable，不比较成空结果。单项目预算 10,000 构建、100,000 Blob/修订，超限整体拒绝；查询只加载规划需要的字段。临时半文件独立检查全局上传租约、规范名称、inode/mtime/长度和期限，软链接及未知文件不删除。计划和应用均写审计；未登记正式文件保留核对。
- UI 支持管理员 pin 和只读实际保留说明，原始产物过期独立显示。项目诊断只允许管理员，返回任务状态/等待时间/登记字节等；不输出连接/secret，不把登记量当文件系统扫描。统一 Project→Build 发布锁顺序，并在手动比较持项目锁后刷新两端指针。
- 离线备份工具核对内置数据库连接、应用停止和目标为空，实际 pg_dump + 规范 Blob 流；恢复拒绝覆盖、穿越、链接和重复项，不 extractall。密钥另存，不混入备份/日志。空卷恢复后验证真实登录、旧 token、历史、下载和重分析。
- 新入口 stack-build/stack-up/stack-stop/stack-check/benchmark。自检仅使用随机 bsi-stack-test-* 项目、私有进程环境与临时卷；结束清理，没有启动用户常驻服务。完整部署说明、ADR-006、发布清单和 A01–A32 证据矩阵已补齐。

### 实际验证

| 命令/证据 | 实际结果 | 报告/边界 |
|---|---|---|
| `make test-postgres-container` | **173 passed，无跳过，65.19 秒** | PostgreSQL/Redis/prefork、迁移/降级/升级、保留/共享/pin/索引、备份恶意输入和原回归；.buildsize/postgres-test-results.xml |
| `make e2e` | **80 passed，无跳过，59.23 秒** | 原 CI/Unity/恢复/规则及两条真实浏览器场景；.buildsize/e2e-results.xml |
| `make test` | **166 passed，7 skipped，35.37 秒** | 未注入服务时真实服务/浏览器/锁检查明确跳过；.buildsize/test-results.xml |
| `make lint` / `make typecheck` | PASS | Ruff 98 个 Python 文件、新旧 UI 格式；mypy strict 69 个源码/工具文件，TS 与生成合同一致 |
| `make build` / `make stack-build` | PASS | 实际 TypeScript/Vite 与两个镜像；.buildsize/stack-build.log。JS 主包仍约 1.60 MB / gzip 520.5 kB，警告未隐藏 |
| `make benchmark`（含 stack-check 全流程） | PASS 本地检查 | 空库迁移、上传/Worker、重启、离线备份/空卷恢复、登录/token/下载/重分析、转发头限流；.buildsize/stack-validation.json |
| 生产静态包 Playwright | **1 passed，无跳过** | 实际 NGINX/CSP 下登录、pin、保留说明；.buildsize/stack-browser.xml、production-web.png。服务使用本机 HTTP development，不冒充外部 TLS 验收 |
| `make contracts` / 运维模块 `--help` | PASS | 原六份 Schema/示例及哈希保持；无凭据可查看帮助 |

本机最终性能样本（不是可安装 APK，也不是 Unity 实际导出）包含精确 **524,288,000 bytes、20,000 个 ZIP 条目、50,000 条 Unity 记录**：

| 指标 | 实测 |
|---|---:|
| 初始化及整文件上传 | 3.95 秒 |
| finalize HTTP 请求 | 0.25 秒 |
| 202 响应后到成功分析可见 | 7.24 秒 |
| 执行进程 ru_maxrss | 269,127,680 bytes |
| Worker cgroup 峰值（含父进程/页缓存） | 320,503,808 bytes |
| APK entries 分页 P95 / 50 次 | 8.10 ms |
| Unity assets 分页 P95 / 50 次 | 44.41 ms |

执行进程 RSS 与 cgroup 峰值不能相加。Worker 配额 4 CPU/2 GiB；Docker VM 实际为 arm64、14 CPU、8,318,976,000 bytes 总内存和共享虚拟磁盘，**不是指定独占 4 vCPU/8 GiB/本地 SSD 参考主机**。本次样本达到内存与分页目标，不能推断生产 SLA；真实硬件/负载仍 NOT_VERIFIED。原始数字、时间、镜像 ID 和环境保存在 docs/validation/performance-2026-11-18.json，栈检查摘要为 stack-validation-2026-11-18.json。

### 文件与决策

新增 deploy 的两份 Dockerfile、完整 Compose、NGINX、私有环境模板/说明和白名单 .dockerignore；新增 services/retention.py、ops.py、backup.py、0006 迁移、保留/备份测试，扩展存储适配器/配置/诊断/pin 与 UI。新增三个栈备份/自检/性能工具、生产静态包浏览器配置和用例，更新 Makefile/文档。后端与前端应用依赖未新增，继续使用既有锁文件。

2026-11-18 用户已确认：所有 current 修订同时保护原始文件与派生索引，允许长期保留。现有实现与该决策一致；本次仅同步 ADR、运维说明和发布清单，不改运行逻辑，自动清理保持关闭。本次文档校验 `make contracts` 通过，未重复运行全套业务测试。离线而非不停写快照、受信任空目标恢复、未知对象不自动清理等取舍见 ADR-006。真实发布入口没有修改。

### 发现的问题与处理

- 首轮 API 镜像的 .python-version 请求 3.12.0 与镜像 3.12.12 不同，改为显式使用镜像解释器并保持 frozen，不下载解释器或改动本机版本。
- 首轮 Compose tmpfs 的逗号在 YAML 行内数组中被解析为第二个挂载，实际启动失败；已正确引用字符串并重新完成全流程，未把镜像构建当部署成功。
- 新查询碰到 AnalysisRevision/Build 多外键 join 歧义、类型/格式及 PostgreSQL SUM 的 Decimal 序列化，已明确 join/类型/整数转换，不捕获后返回成功。
- 窄屏断言首次在 ResizeObserver 更新前读取尺寸而失败；等待实际无横向溢出条件，保留原严格阈值，完整回归重新通过。没有移除浏览器检查。
- 完整栈反复在独立随机项目上验证，恢复不允许 force/非空覆盖。中间失败只保存脱敏诊断；最终结果以成功报告为准。临时大样本由生成器清理，原 M3 实际证据未重采/覆盖。

### 剩余验收

A26/A27/A28 的实际 Jenkins/蒲公英任务仍 NOT_VERIFIED，用户实际工程、生产 APK/设备与受限 Android 工具也未补出证据。外部域名/TLS/多级代理、正式灾备目标、镜像漏洞扫描/组织安全审核、指定硬件与真实前端网络体验未验收；前端主包体积警告继续保留。

本地阶段已可按部署说明复现；生产发布前按 docs/RELEASE_CHECKLIST.md 逐项签收。保留策略、外部接入和未知正式孤儿文件不能用定时 rm -rf 绕过。没有把外部验证缺口标成 DONE。

### M6 工具调用简报

2026-11-18（Asia/Shanghai），本地 exec/apply_patch、冻结 uv/pnpm、Docker/Compose、PostgreSQL/Redis、真实 Chrome/Playwright。只读查询 Compose/NGINX/PostgreSQL 官方文档并核验基础镜像 manifest digest，链接见 ADR-006；没有上传内部源码/凭据或执行真实发布。自建容器/卷、迁移临时库与预览进程清理，结果与未验证边界保留在本文件和验证摘要。

## M5 历史交付

以下保留 M5 当次记录；当前能力、验证和未验证项以上方 M6 与逐项状态表为准。

已完成本地规则与 Web 闭环：12 类 P0 规则 → fencing 事务发布 findings → 冻结配置与证据查询 → 真实登录/项目/构建/比较页面 → 管理员复核、乐观锁和审计。没有创建业务 mock、默认账号或托管站点，也没有改动真实 Jenkins/蒲公英/Unity 发包入口。当前目录仍没有 Git 元数据，未初始化或提交。

### 实现了什么

- 纯规则引擎与 12 类观测/关联/假设规则，findings 固定 additive=false。APK 总预算/总增长包含容器开销，metric=none；条目压缩和 Unity packed 独立。增长同时检查整数 bytes 与整数基点，不用浮点判断阈值；无基线/跨组跳过增长门禁，绝对预算独立评估。
- 资源新增检查整个旧报告的 GUID，已有 GUID 仅增加类型不误称新源资源。Texture/Audio 只比较两端已知且已定义的 Importer 字段；未知扩展、null 和缺失不能伪装默认设置。native 未精确映射时仅给 hypothesis，不猜 SDK 或开发者责任。
- 新迁移 0005_rules_web：追加规则版本、比较配置快照、findings 与复核状态。自动基线与规则快照在同一项目锁/fencing 事务内选择并发布；手动比较在创建时冻结配置，不受排队期间阈值修改影响。旧报告不随规则更新改变。
- 规则与复核只允许 Web 项目管理员，保留 Origin/CSRF、If-Match、行锁和审计。并发同版本更新仅一个成功，另一个 409；CI token、viewer 和跨项目请求不能修改。原 finding payload/置信保持不可变，复核独立存储。
- findings 分页、过滤、置信等级和原始证据详情。证据解析到固定修订的 APK 条目、重复内容路径、GUID/类型聚合、已知导入设置、配置与覆盖；适用时提供原始产物 SHA 和授权下载。每比较最多存储 5000 项，完整候选计数与截断标记保留；重复路径预览最多 50 个。
- React/Vite + Ant Design/ECharts 真页面：登录、项目总览、同组趋势、构建来源/下载、APK 文件、Unity packed、版本选择、物理/资源/配置差异、证据抽屉、规则和审计分页。未知为破折号，首版/部分/失败/不支持/无权限有独立状态。重分析保留旧成功修订的事实明确提示，过期错误不伪装成功。
- 趋势按兼容组隔离，未分析点保留 null；候选窗口 200 明确返回。表格由服务器分页/搜索/排序，URL 保留筛选。用户文本由 React 转义，ECharts tooltip 使用 richText；数字可复制原始整数 bytes，展示 MiB。
- 新增 make web/build/browser-test、精确前端依赖锁文件与格式检查。开发浏览器通过 Vite 同源 /api 代理，Cookie/CSRF 保留，不注入 CI token、不关闭 CORS/CSRF。生产托管仍在 M6。

### 实际验证

| 命令/检查 | 实际结果 | 范围/报告 |
|---|---|---|
| `make test-postgres-container` | **164 passed，无跳过，60.69 秒** | PostgreSQL 16.11、Redis 7.4.6、真实 prefork、迁移/降级/升级、规则并发/审计/修订与原 M0–M4 回归；.buildsize/postgres-test-results.xml |
| `make e2e` | **71 passed，无跳过，55.70 秒** | 包含新真实浏览器闭环与原 CI/Unity/恢复；.buildsize/e2e-results.xml |
| `make browser-test` / 完整套件内 Playwright | **2 条浏览器场景通过** | Chromium（本机 Google Chrome）、真实 Vite/Uvicorn/数据库/队列，无业务 mock；.buildsize/browser-results.xml、browser-harness.xml |
| `make test` | **157 passed，7 skipped，31.25 秒** | 未注入专用服务时，真实服务/浏览器/行锁场景明确跳过；.buildsize/test-results.xml |
| `make lint` | PASS | Ruff 89 个 Python 文件；新 UI/测试的 Prettier 检查 |
| `make typecheck` | PASS | mypy strict 63 个源码/工具文件，TS 与六类生成合同一致 |
| `make build` | PASS（有主包体积警告） | TypeScript + Vite；2,088 个模块，JS 主包约 1.60 MB / gzip 520 kB；frontend/dist，未隐去大于 500 kB 警告 |
| `pnpm --dir frontend install --frozen-lockfile` | PASS | 精确版本锁文件可复现，无临时关闭 peer 或权限检查 |
| `make contracts` | PASS | 原六份 Schema/示例与四份 JSON 长度/哈希保持兼容；生成测试上下文不当成项目文档 |

Playwright 经真实浏览器 Cookie/CSRF 上传明确的合成 ZIP，等待 prefork 分析，验证 APK 路径中的脚本样式文本不会执行、25 条分页、两个数据层、手动版本选择、原始证据、管理员复核/规则、390px 布局、viewer 只读、实际跨项目构建和首版无基线。截图 .buildsize/web-desktop.png 来自该测试 API 的实际数据，不是静态 mock。合成 ZIP 不代表可安装 APK；完整套件仍验证 M3 明确指针下的真实 Unity APK/报告，没有重采或升级 Editor。

### 文件与决策

新增 rules/{config,engine}、services/rules.py、api/rules.py、0005 迁移与三个测试文件；扩展模型/分析事务/比较创建、历史摘要、项目角色、健康迁移版本。自动基线选择从事务外移入项目锁发布边界，保留原 fencing/幂等语义；旧内部测试调用同步调整。

前端新增真实应用入口、页面、样式、API/类型、共用状态/图表、Vite 和 Playwright，保留原生成合同。依赖已锁定：React 19.3.1、Vite 8.3.8、Ant Design 6.7.1、ECharts 6.1.0、Playwright 1.65.1。后端未增加依赖。更新 Makefile、环境示例、运维/API/README、前端说明与 ADR-005；忽略浏览器失败上下文，避免测试输入进入公开归档。

### 发现的问题与处理

- 先增加规则语义测试，首次因规则引擎尚不存在而失败；实现后通过。发现已有 GUID 新增对象类型不能叫“新源资源”，以及未知扩展不能触发导入归因，已补约束与回归。
- 静态检查发现新循环变量类型复用、迁移生成的未限定 Text、格式与移动选择逻辑后的未使用变量；全部修复，未放宽类型或删除断言。
- 首次浏览器定位因 Ant Design 两字按钮空格、中文 Drawer 标签失败；增加明确可访问名并按实际语言定位。版本下拉的退出动画会暂存旧选项，最终采用实际键盘选择并校验目标 URL，再创建真实异步比较，没有跳过交互。
- 增加 PostgreSQL 并发规则更新，确认同 If-Match 只有一方成功；旧比较的配置/结果与原始 finding payload 保持不变。
- 本轮专属迁移 SQLite 已删除；测试工具清理所有自建服务、随机队列和临时容器/卷。浏览器预览仅在隔离验证期存在，结束后关闭，不冒充常驻上线服务。

### 未验证项与下一步

生产同源托管/TLS/Compose、备份恢复、保留期/GC、规格性能场景及主包分包仍待 M6；构建警告不等于性能达标。设置页首版只提供预算、增长阈值和固定基线，不展示未执行的分类/保留期表单，边界见 ADR-005。浏览器实际验证为本机 Chrome，其他浏览器/真实设备、用户实际工程/生产 APK、真实 Jenkins/蒲公英和受限 Android 增强工具继续 NOT_VERIFIED。

下一步 M6：补齐完整应用容器编排和同源托管，再做可审计生命周期、备份恢复与安全/性能验收；外部 CI 验收缺口保持独立记录。

### M5 工具调用简报

2026-11-18（Asia/Shanghai），使用本地 exec/apply_patch、uv 独立环境、pnpm 冻结依赖、真实 PostgreSQL/Redis/Chrome 和 Playwright。按 Sites 技能的本地开发范围实施，没有创建/发布外部站点；原生预览只做本地展示。只读查询 Vite/Ant Design/ECharts/Playwright 官方资料并以当前版本编译和运行核验，链接见 ADR-005。中间失败按具体证据修复并重跑，没有用截图或 mock 替代测试，没有执行真实发布。

## M4 历史交付

以下保留 M4 当次记录；当前能力与验证以上方 M5 和逐项状态为准。

已接通 prepare → 原构建 → collect 冻结 → submit/202 → 分析/查询 → 蒲公英安全结果关联。当前目录仍没有 .git，没有擅自初始化、提交或改写真实 Jenkinsfile。没有调用蒲公英 API 或读取其 Key，也没有操作用户游戏工程。本轮实施 M4 可在本机验证的闭环，真实 Jenkins/蒲公英保持 NOT_VERIFIED。

### 实现了什么

- 五个 CI CLI 命令，API 地址和令牌仅从环境获取。prepare 原子创建上下文，按完整 logical key 稳定派生 run_id；每构建号/variant 使用独立目录。同次任务重试保留身份、时间戳、原始清单和 SHA，不生成新构建。
- collect 明确指定最终 APK，不扫描最新/最大文件；只接收同 run_id 完成回执列出的普通文件，核对长度/哈希，复制后再次校验。未知旧报告不回退读取，构建失败/取消不提交。完成时间区分 Unity Player 与最终 APK/CI，其他实际配置冲突拒绝覆盖。
- 流式 HTTP、TLS 校验、不跟重定向、有限重试、总期限和角色级恢复；202 即退出，不等待 Worker。凭据不进入参数、stdout、回执或配置。SIGINT=130/SIGTERM=143；失败明确非零，不伪装成功。
- 多仓实际 HEAD/dirty、NUL 路径、rename、blob ID 和小 .meta GUID；不上传源码、作者/email/remote 或整个环境。浅克隆为 missing_base，不自动 fetch；prepare 后 HEAD 改变拒收。
- 候选基线 API 与 Worker 共用兼容/时间/固定基线策略。新迁移 0004_ci_links 保存仓库快照、Git 范围/差异和发布关联；发布事务核对实际两端 base/head，手动更换基线后旧范围标记 mismatched_range，计数 null。dirty/部分缺失不能冒充完整责任证据。
- 蒲公英只关联五个白名单字段，单独 distribution:write scope、项目授权与浏览器 CSRF。传输/发布失败不显示安装成功；带凭据/签名 URL、未知 API 字段拒绝。已成功结果相同内容幂等，不同内容 409；缺回执不关联最近构建。
- Jenkins 合并示例把原游戏与发布步骤保留在捕获范围外；新增凭据绑定/读写/归档失败告警，FlowInterruptedException 重抛，子进程 130/143 保持 ABORTED。完整说明与外部故障注入清单已提供，没有创建占位发包脚本。

### 实际验证

| 命令/检查 | 实际结果 | 范围/报告 |
|---|---|---|
| `make test-postgres-container` | **156 passed，无跳过，42.90 秒** | PostgreSQL 16.11、Redis 7.4.6；完整迁移/降级/升级、原 prefork/重启恢复和新 CI/Git/发布关联；.buildsize/postgres-test-results.xml |
| `make e2e` | **67 passed，无跳过，38.43 秒** | 本机 CLI 子进程、HTTP、真实两 Git 仓、原异步/Unity 数据流；.buildsize/e2e-results.xml |
| `make test` | **151 passed，5 skipped，31.98 秒** | 未注入专用服务的 PostgreSQL/Redis 场景明确跳过；.buildsize/test-results.xml |
| `make lint` | PASS | Ruff 检查与格式检查，80 个 Python 文件 |
| `make typecheck` | PASS | mypy strict，58 个源码/工具文件；TS 编译与生成合同一致 |
| `make contracts`（由 make test 执行） | PASS | 六份 Schema/示例与四份实际 JSON 长度/哈希、文档引用检查 |
| 本机 Groovy 3.0.22 AST CONVERSION 解析 Jenkinsfile.example | PASS | 使用已安装 JDK/Gradle 库；只验证 Groovy 语法，未验证 Jenkins DSL、插件与 sandbox |

新增 26 项测试覆盖：同 logical key/context 复用、冻结重试与改写拒绝、HEAD 变更、错误 run_id、回执哈希及复制期间替换、仅采集回执列出文件、中文/空格/rename/GUID、真实浅克隆、两仓两次提交、202 不等待、相同构建/任务幂等、只补传未验证角色、候选和手动范围不符、Git/发布项目权限、蒲公英最终状态/凭据 URL/成功关联冲突、3 次网络重试与不可重试状态、重定向不转发令牌、总期限及进程中止。

新 CI 端到端 APK 为明确的合成 ZIP，仅验证数据流，不能冒充可安装 APK 或真实 Jenkins。完整套件仍读取 M3 明确指针的受控真实 Unity APK/报告，未重新构建或改变该证据。测试耗时不是性能 SLA。

### 文件与决策

新增 client/{context,files,git,submission,transport}、domain/{distribution,urls}、api/ci.py、jobs/git_layers.py 和 0004_ci_links 迁移。扩展 CLI、封存/比较/Worker、持久化模型和构建详情；新增三类 CI 测试并扩展 e2e 入口。依赖保持不变，沿用已冻结锁文件，无新增 HTTP/Git 库。原六份 Schema/示例线格式保持不变，Python 增加有界 Git 语义校验。

新增 integrations/jenkins 的完整示例、配置、安全结果和说明，新增 ADR-004；同步 README/API/OPERATIONS/TASKS。重试身份、最终 APK 时间和证据范围决策见 adr/ADR-004-ci-client-and-evidence.md。

### 发现的问题与处理

- 初始 Jenkins 示例在普通异常捕获中处理 130/143，会吞掉中止；已将中止分支保留为 ABORTED 并避免外层误捕获，原 FlowInterruptedException 重抛。
- 持久 workspace 若复用固定 context 路径，新构建会与旧身份冲突；示例改为每构建号/variant 独立目录，不通过覆盖旧上下文绕过校验。
- 既有缺少 Git 时的 unsupported 断言随实现更新为 missing，新增实际范围不符用例验证不能误用旧证据，没有删除测试或放宽权限。
- 失败断言曾展开本地测试直接签发的临时令牌；测试库已清理。全局 pytest 脱敏补充平台令牌格式，stdout/XML 失败回归继续验证失败退出码，不隐藏失败。
- 初次静态检查发现新增 Query 写法和格式问题，已修复并完整重跑。本轮临时迁移 SQLite 文件已清理；一次性数据库/Redis 容器和卷由验证脚本清理。

### 未验证项与下一步

M4-03 的 Jenkins DSL/插件/sandbox、中止与失败隔离需实际控制器验证；M4-05 两次真实任务、真实蒲公英最终结果适配仍 NOT_VERIFIED。未修改真实流水线来绕过环境缺口。客户端实际 Windows 节点、生产网络/TLS 故障、生产 APK/设备、Android 增强工具、浏览器、性能与完整恢复仍未完成。前端目前仅合同类型工程，没有 build/页面入口，因此没有伪造前端构建或 Playwright 结果。

下一步 M5-01：先做 12 类 P0 规则、版本化配置与不可相加的证据 findings，再接真实 API 页面及 Playwright。继续区分 APK 压缩与 Unity packed 指标、基线缺失与零增长、observed/correlated/hypothesis。

### M4 工具调用简报

2026-11-17，使用本地 exec_command/apply_patch 与既有 uv 环境，按冻结依赖执行 Git/CLI/HTTP/数据库/类型验证；使用 web 查询 Git diff、Python 3.12 http.client、Jenkins Pipeline/Credentials Binding 官方资料，链接见 ADR-004。外部资料只读，无业务数据上传、无真实发布/部署。HTTP 429/503 重试均为本地故障注入，不是实际服务调用失败。没有新建或使用子代理。

## M3 历史交付

以下保留 M3 当次记录；当前能力和验证以上方 M4 与逐项状态为准。

已实现 Editor-only UPM 导出包、Unity/Importer 领域解析、持久化、分页 API 和独立 packed 指标比较。已在 Unity 6000.0.76f1 的独立受控工程实际编译、测试、构建 Android APK，并通过后台上报/分析。没有操作另外运行的用户游戏 Editor，也没有升级 Unity 或改动真实发包入口。

### 实现了什么

- 导出包支持显式/环境回调；保留 Unknown、原始 GUID/路径/索引、生成资源和 Nullable 数据。只读取报告涉及的 Importer，不扫全项目、不重导入、不载入所有 Texture2D。没有引入 Runtime JSON 依赖。
- 实际配置从 BuildReport/PlayerSettings 读取；未知压缩 null+warning。原三参数 Export 保留；知道本次 options.scenes 时可用四参数重载导出实际场景 GUID，不猜 EditorBuildSettings。
- JSON UTF-8、安全整数、有界写入、原子替换与最后提交的 capture-complete 回执；跨 run_id 拒绝覆盖。单资源失败不改变游戏构建结果；上限/截断明确标识并使资源层不能作为完整比较。
- 原始记录逐行保留，JSON Pointer 使用实际数组下标。GUID+类型跨版本匹配；同 GUID 改名不变成新增/删除；多对象/多容器全部聚合；无 GUID 的路径低置信回退，完全未知按容器/类型汇总，重复容器名不强配。
- 新迁移 0003_unity_layers 持久化原始容器/记录、两类聚合、Importer 和独立 Unity 差异。重分析/手动比较复用原有修订与 fencing 事务。APK 压缩字节公式保持不变。
- 新资源/原始记录/容器/Importer 分页接口；比较支持 unity_asset/asset_importer 层，包含 Pointer、产物 SHA、mapper/evidence 和覆盖状态。已知设置只返回有定义的键；未知扩展保留在原始产物。
- Importer 部分失败时，仅成功交集给出观测设置差异并标记 partial；缺失/截断层不推导完整资源增删，不把空结果冒充无变化。

### 实际验证

| 命令/证据 | 实际结果 | 边界/记录 |
|---|---|---|
| `uv run --project backend --frozen python tools/validate_unity.py --build`（即 make unity-validate） | **8 项 EditMode passed + 两次实际 Android 构建成功** | 6000.0.76f1/ARMv7 Mono；.buildsize/unity/validation.json、editmode.xml、build-result.json、current-evidence.txt |
| `make test-postgres-container` | **130 passed，无跳过，28.76 秒** | 含真实 Unity APK/报告的 API/Worker/数据库场景；.buildsize/postgres-test-results.xml |
| `make e2e` | **41 passed，无跳过，25.75 秒** | 新 Unity 层、实际 APK、原 prefork/队列/数据库重启、CLI/HTTP；.buildsize/e2e-results.xml |
| `make test` | 125 passed，5 skipped | 未提供专用 PostgreSQL/Redis 时对应服务场景明确跳过，真实 Unity 证据在本机存在 |
| `make lint` / `make typecheck` | PASS | Ruff 66 个 Python 文件；mypy strict 48 个源码/工具文件；TS 与生成合同一致 |
| `make contracts` | PASS | 原六份 Schema/示例及真实 JSON 长度/哈希保持兼容 |

实测记录见 docs/validation/unity-6000.0.76f1.json。最终受控样本 A/B：

| 指标 | A | B | 净变化 |
|---|---:|---:|---:|
| 最终 APK bytes | 31,168,396 | 31,221,074 | **52,678** |
| 受控同 GUID Texture2D packed bytes | 4,228 | 65,668 | **61,440** |
| 原始报告记录数 | 4,116 | 4,116 | 保留全部 |
| 容器直接映射 | 41/42 | 41/42 | globalgamemanagers.assets 保持 unmapped |
| Importer 成功/请求 | 2,151/2,154 | 2,151/2,154 | 3 个不可采，保持 PARTIAL |

容器映射比例不是 APK 增长解释率。APK 包含实际 AndroidManifest/DEX/native 数据，绝非此前的合成 ZIP；没有 BuildSize 程序集进入 player。回调原始 build_result=Unknown，最终显式结果为 Succeeded；没有擅自改写回调文件事实。资源源文件与 Importer 的受控变化在验证脚本内，导出器自身前后 Importer 序列化值相同。

CI/Git 元数据在上报测试中明确使用协议夹具，只有 APK/Unity/Importer/实际设置来自真实构建；这不代表真实 Jenkins/Git 联调。测试耗时不是规格性能 SLA。

### 修改文件

新增 integrations/unity/com.company.buildsize 的 package/Editor/Tests/.meta/README，及仅供独立验证的 C# 引导/构建脚本。新增 tools/validate_unity.py、make unity-test/unity-validate 和实际证据摘要。

新增 domain/unity.py、analyzers/unity_report.py、comparison/unity.py、jobs/unity_layers.py、api/unity.py 与 0003 迁移；扩展 Worker/模型/覆盖/分层查询，解析器不依赖 HTTP/Celery/数据库。新增纯解析与资源管线测试，扩展真实服务验证入口。更新 README/API/OPERATIONS/TASKS/ADR-003。

### 发现的问题与处理

- 实际 Editor 编译确认 GetCompressionType 不是公开 API，改为明确标志或 null+warning。
- Unity 测试命令曾退出 0 但只发现 0 项，未计为通过。独立工程显式加载同一测试源，工具要求至少 8 项全通过，编译失败/零测试均非成功。
- macOS 临时目录含符号链接，输出保护正确拒绝；测试目录迁至验证工程 Assets 外的普通路径，没有放宽保护。
- Enum.ToString 混入已废弃的零值别名，实测反射后改用声明名称/非零/非废弃过滤，实际 build_options 仅 DetailedBuildReport，没有把 Mono 误报为 Il2CPP。
- 三个实际 Importer 失败会使总体覆盖不足，但不能挡住其余成功采集资源的设置观测，已保留 partial 交集比较。

### 没有执行的测试与下一步

用户实际游戏工程、生产 APK/设备安装、IL2CPP 与其他 Unity 补丁版本、Jenkins/蒲公英、浏览器和性能/恢复演练仍 NOT_VERIFIED。受控实际构建不替代用户生产验收；M3-06 保留这一缺口。Android 增强工具的本机硬资源限制问题未绕过，M1-05 仍未完成真实受限工具联调。

下一步 M4：prepare/collect/submit/status/link-pgyer、实际多仓 Git 范围、CI baseline-candidate 与 Jenkins advisory/中止隔离。必须以 capture-complete 校验后冻结本次文件，不读取上次目录；不重写真实发包脚本。

### M3 工具调用简报

2026-11-17，使用 unity-cli / unity-package-management 技能及本地命令。CLI 1.0.1 已核对为当前版本；未升级 Editor。自建工程不关联云项目，UPM 通过公开 Client.AddAndRemove 安装本地无运行时依赖包；没有手改用户 manifest。已安装 Android SDK/NDK/JDK 被实际使用，没有自动下载/升级 Editor 模块。公开 API 以 Unity 6000.0 官方资料及实际编译验证，细节见 ADR-003。阶段重跑均对应编译、测试或采集修复；保留 .meta、真实结果与未验证边界，没有删除失败测试/放宽断言。

## M2 历史交付

以下保留 M2 当次记录；当前能力与验证以上方 M4 记录和逐项状态表为准。

已接通：初始化清单 → 流式上传/校验 → 封存/outbox → Redis/Celery → 持久分析修订 → 自动/手动冻结比较 → 分页查询/授权下载。没有修改真实 Unity/Jenkins/蒲公英流程，也没有新增虚构页面。M3–M6 仍待实施，真实 APK/SDK/Unity/CI 验收保持 NOT_VERIFIED。

### 实现了什么

- LocalBlobStorage 按项目/内容寻址，临时写入、实际长度/SHA-256、fsync、独占原子提交与项目内去重；失败/断连清理临时文件，正式 Blob 不覆盖。
- 三步上传返回 201/200/202/409；清单身份、run_id、并发上传 lease、上传期限、JSON 限额、可选报告降级与项目权限均执行真实校验。跨项目访问/下载拒绝。
- finalize 只做元数据和有界 ZIP 目录预检；完整 CRC/解压在 Worker 执行，错误明确 FAILED，202 不代表分析通过。Unity/Importer/Git 领域解析在 M3/M4 前明确 unsupported，项目默认通常 PARTIAL。
- PostgreSQL 持久 job/outbox；Celery 仅携带 job UUID。重复消息忽略，失效 lease 可重领，fencing 拒绝旧 Worker，临时 I/O 最多三次消费尝试。
- 重分析使用独立幂等键，新修订完成后原子切换 current；失败保留旧结果，较旧请求不能覆盖较新请求。完整条目索引在原 APK 不可用后仍可读。
- 兼容基线按实际构建时间选择并冻结双方修订。Unity/压缩改变仍可选前版；ABI/Development/backend 缺失或变化不混组。声明身份限定可信 CI 来源，目录 ABI 与声明不符或明显未来时间排除自动选择。跨组手动比较跳过增长门禁。
- 列表分页、字面搜索、排序白名单；详情不一次返回全部目录/重复路径。CI 链接精确域名允许列表与凭据参数拒绝。浏览器写操作执行 CSRF。

### 修改文件

新增 storage、services、jobs、API routes/dependencies/contracts/limits、结构化日志及 `0002_upload_pipeline` 迁移；扩展 config、db models 和 auth 的安全表示。M1 只补 ZIP 目录预检入口与共用有界 JSON 解析，原物理算法/Schema/示例保持兼容。

新增 storage/pipeline/async/redaction 测试；扩展一次性 PostgreSQL/Redis 验证脚本、Makefile、Compose、环境模板。更新 README、OPERATIONS、TASKS，新增 API.md 与 ADR-002。Ruff 明确 first-party 包名，防止不同调用入口导致导入排序反复变化。

### 实际执行命令与结果

| 命令 | 实际结果 | 范围/报告 |
|---|---|---|
| `make install` | PASS | uv/pnpm 冻结锁文件；本次增加 Celery 5.7.0/Redis 客户端依赖 |
| `make test-postgres-container` | **116 passed，无跳过，21.70 秒** | PostgreSQL 16.11、Redis 7.4.6；`.buildsize/postgres-test-results.xml` |
| `make e2e` | **35 passed，无跳过，17.22 秒** | 真实 prefork、API、SIGKILL/队列丢失恢复、数据库重启、CLI/HTTP；`.buildsize/e2e-results.xml` |
| `make test` | 111 passed，5 skipped | 未注入专用服务时，真实服务/行锁/重启场景明确跳过；不是完整集成验收。`.buildsize/test-results.xml` |
| `make lint` | PASS | Ruff 检查与格式检查，57 个 Python 文件 |
| `make typecheck` | PASS | mypy strict，42 个源码/工具文件；TS 编译与 Schema 生成一致性 |
| `make contracts` | PASS | 六份 Schema/示例、四份 JSON 哈希和原文档结构检查 |
| `make demo` | PASS | 离线 synthetic ZIP，明确 demo=true，净增量 27 bytes |
| Worker/dispatcher `--help` | PASS | 无配置时可查看帮助，不会启动服务或展示秘密 |

所有耗时均为本机功能测试测量，不是 500 MiB 场景吞吐/内存 SLA。测试使用随机项目/Schema/队列/凭据；临时容器和专属卷均清理，未留下常驻平台 API/Worker/数据库。Docker Desktop 保持已运行状态。原始 JSON 协议示例的哈希校验仍通过。

### 发现并修复的问题

- prefork 子进程无法识别工厂内部注册的 Celery 任务；迁至可导入的 tasks 模块后，真实 prefork 两次构建通过。
- PostgreSQL 重启时 Docker 自动 HostPort 发生变化；测试改为创建时固定本轮随机端口，使用专属临时卷，实际重启后验证了原构建、完整索引与 Blob 下载。
- 该失败测试的库堆栈曾回显本轮一次性数据库密码；相关容器已销毁，密码失效，含该值的失败报告已删除。新增统一 pytest 输出/JUnit 脱敏和短堆栈，回归测试确认真实失败仍退出 1 且凭据不回显；会话对象也禁止 repr 暴露 token。
- 修正上传 close 异常仍释放 lease、整任务期限检查、有限详情预览及 broker 失败不能返回成功。

SIGKILL 和随机 Redis 队列删除是真实故障注入；为加速恢复验证，测试将数据库 lease 时间设为过去，没有等待完整 20 分钟。数据库容器重启是实际执行；这不代表已完成 M6 的完整备份恢复演练。

### 未验证事项与规格差异

真实 APK 安装/Manifest、Android SDK、Unity 导出与映射、Jenkins/蒲公英、浏览器页面和规格性能场景仍未验证。当前没有真实游戏工程/APK/流水线；对应实现阶段尚未完成，不以 ZIP 或后台测试替代。完整应用容器编排、引用/保留期清理和备份恢复属于 M6。

技术栈保持冻结选型。API 只提供 M2 已实现能力，网络提交 CLI、构建前 CI baseline-candidate 参数与 Git 匹配在 M4 实施。磁盘提交与数据库登记之间可能留下无引用的完整 Blob，API 被硬杀可能留下独立 .tmp；没有正式半文件被登记，安全回收留到 M6。细节见 ADR-002。

### 下一项任务

M3：Editor-only UPM 导出器、实际设置/Importer 快照、GUID/类型聚合与资源对比。先用现有合同实现可验证解析，再在实际游戏工程/真实 APK 上验证导出和容器映射；没有映射证据时保持 unmapped。

### M2 工具调用简报

2026-11-17（Asia/Shanghai），exec_command 用于完整读取现有实现/相关规格、修改、冻结安装与本地验证；web 核对 Celery/SQLAlchemy 官方说明，Starlette 页面 502 后以安装源码签名和实际流式调用降级核验。Docker 使用固定 PostgreSQL/Redis digest、仅本机端口和专属随机资源；测试凭据只从环境传递，不放入命令参数。重跑包含 prefork 注册、重启端口与测试脱敏修复，未删除失败测试或放宽断言。无生产部署或真实发布。

## M0/M1 历史交付

以下保留首次交付快照，当前能力以上方 M2 记录与逐项状态表为准；自动测试报告随最近一次运行更新。

本轮落实 M0 工程基础与 M1 离线纵向流程：本地 APK/ZIP 输入 → 安全索引与 SHA-256 → 压缩字节、分类/目录聚合与 overhead → 两版本精确对账 → 原子 JSON 输出。最小 API 已具备真实健康检查、登录/退出和项目授权。

这不是完整 P0 发布。上传、异步分析、自动基线、Unity 资源解析、Jenkins/蒲公英、规则和 Web 页面尚未实现。原始 Schema/示例保持不变，未覆盖真实发包入口。当前目录没有 Git 元数据，本轮没有执行 Git 初始化或提交。

### 实现了什么

- 六类 Pydantic 协议模型，严格字段/整数/UUID 校验、必填角色与大小约束、清单规范化哈希、跨文件身份/Unity 版本一致性、重复容器/记录/Importer 身份拒绝；nullable 字段不转为 0。
- 从原始 Schema 生成六类 TypeScript 类型，检查生成一致性与必填/nullable 语义。
- PostgreSQL 16 / SQLAlchemy / Alembic 基础迁移，构建 logical key 与 run_id 唯一约束、Blob/角色/任务幂等键及物理指标对账约束。
- Argon2id 密码、本机管理 CLI、项目作用域 HMAC CI token、服务端 HttpOnly 会话、CSRF、数据库登录限流和安全日志。跨项目请求返回与不存在相同的 404。
- ZIP 目录预检、Stored/Deflate 流式解码、实际解压预算、CRC、SHA-256、路径/重复/重叠/加密/符号链接拒绝、期限与整数上限。支持标准 ZIP64 和流式 data descriptor。
- 物理增删改、同长内容变化、压缩表示变化、互斥分类、独立目录视图、重复非空内容提示、正向/负向/净变化及 NO_BASELINE/null 语义。
- 可选 Android 工具探测与受限进程封装；工具缺失/沙箱或硬资源限制不可用会明确降级。真实 SDK 核验仍未完成。
- 离线 analyze/compare/validate/demo 命令、冻结依赖、开发数据库 Compose、运维说明、临时 PostgreSQL 验证入口。

### 修改文件

- `backend/pyproject.toml`、`backend/uv.lock`、`backend/.python-version`：Python 工程和依赖。
- `backend/src/buildsize/domain/`：协议、错误及物理快照；`analyzers/`、`comparison/`：APK 分析/分类/比较及可选工具适配。
- `backend/src/buildsize/api/`、`auth/`、`db/`、`config.py`、`admin.py`：服务、权限、模型与管理入口；`cli.py`、`__main__.py`：离线入口。
- `backend/alembic.ini`、`backend/migrations/`：明确的升级/降级迁移。
- `backend/tests/`：协议、ZIP、性质、权限、CLI、子进程、真实 HTTP 和 PostgreSQL 测试；`tests/fixtures/README.md`：夹具边界。
- `frontend/package.json`、`frontend/pnpm-lock.yaml`、`frontend/src/contracts/`、生成脚本和类型测试：协议类型工程。
- `Makefile`、`.env.example`、`.gitignore`、`compose.yaml`、`tools/test_postgres_container.py`：复现与隔离验证。
- `tools/validate_contracts.py`：仅增加依赖/生成目录排除，防止把 node_modules/.venv 的 Markdown 当作项目文档校验；原有合同检查保留。
- `README.md`、`docs/TASKS.md`、`docs/VALIDATION_REPORT.md`：补充当前阶段指引，保留原任务定义与历史记录；新增 ADR-001、OPERATIONS 和本状态文件。

### 实际执行命令与结果

| 命令/检查 | 实际结果 | 证据/边界 |
|---|---|---|
| `make install` | PASS | uv 和 pnpm 均按冻结锁文件安装成功 |
| `make test` | 72 passed, 1 skipped | 无显式 PostgreSQL 测试连接时，唯一跳过项明确标为 NOT_VERIFIED；报告 `.buildsize/test-results.xml` |
| `make test-postgres-container` | **73 passed，无跳过，6.85 秒** | 完整测试套件；真实 PostgreSQL 16.11；报告 `.buildsize/postgres-test-results.xml`。用时是本机功能测试耗时，不是性能 SLA |
| `make lint` | PASS | Ruff 检查及格式检查，36 个 Python 文件 |
| `make typecheck` | PASS | mypy strict，26 个源码/工具文件；TypeScript 严格编译；生成类型与六份 Schema 一致 |
| `make e2e` | 11 passed | CLI 子进程、真实 API 鉴权和 Uvicorn 真实 HTTP；不包含尚未实现的浏览器页面 |
| `make demo` | PASS | 独立合成 ZIP；demo=true；净增量 27 bytes；`.buildsize/demo/comparison.json` |
| `make contracts` | PASS | 六份 Schema/六份示例、四份真实示例 JSON 哈希、原规格/任务/引用检查 |
| `docker compose --env-file .env.example config --quiet`（仅通过进程环境提供临时密码） | PASS | 仅验证开发数据库 Compose 配置，不代表完整平台部署 |
| `docker manifest inspect postgres:16.11-bookworm` | PASS | 核验镜像存在；Compose 和测试脚本均固定已实际拉取的 manifest digest |
| 本机受限工具烟测 | 明确 unavailable | macOS 不能设置 RLIMIT_AS，返回 TOOL_RESOURCE_LIMIT_UNAVAILABLE；没有降级为无限制执行 |

PostgreSQL 专项实际验证了：空 Schema 升级、JSONB 数据、同 logical key 并发插入仅成功一次、ready 检查、真实数据库上的登录/会话/CSRF/CI token/项目授权、降级与再次升级。测试容器使用内存数据目录与随机本机端口，临时凭据不出现在命令参数或日志中；容器及数据均已清理。Docker Desktop 是本轮通过既有 CLI 启动的，当前未启动常驻项目数据库或 API 服务。

ZIP 验证包含独立计算的 Stored 头/目录字节账、100 组 Hypothesis 随机比较与反向取反，以及恰好 65,535/65,536 条目边界。测试先复现 65,535 条目普通 ZIP 被误判的问题，再修复并通过。危险输入均在隔离临时目录中运行。

### 没有执行的测试及原因

| 项目 | 状态 | 原因 |
|---|---|---|
| 真实 APK 安装、Manifest 与签名/工具交叉检查 | NOT_VERIFIED | 本目录没有真实 APK，ZIP 夹具不代表可安装产物 |
| 真实 apkanalyzer / Linux bubblewrap 执行 | NOT_VERIFIED | 没有配置固定 SDK 工具；macOS 硬资源限制不可用，保持拒绝执行 |
| Unity Editor 导出、两次真实资源变化与直接容器映射 | NOT_VERIFIED | 本机有 6000.0.59f2/6000.0.76f1 等编辑器，但目录没有实际游戏工程；M3 导出器尚未实现 |
| Jenkins 中止、分析故障隔离、蒲公英发布结果 | NOT_VERIFIED | 没有实际流水线/发布样本；M4 集成尚未实现 |
| 前端构建、Vitest/Playwright 浏览器业务流程 | NOT_VERIFIED | 当前只有协议类型工程，M5 页面尚未实现，没有假 build 脚本 |
| 500 MiB APK / 20,000 条目 / 50,000 Unity 记录性能 | NOT_VERIFIED | 没有执行规格性能场景，不发布吞吐/内存 SLA |
| 完整 Compose 部署与备份恢复演练 | NOT_VERIFIED | M6 未实施；当前只验证开发数据库配置与一次性 PostgreSQL 测试 |

### 与规格的差异

技术栈保持规格选型。本轮按任务表“完成当前可验证的最小纵向流程”交付 M0/M1，后续业务能力保留为 TODO。SQLite 严格限于测试，真实 PostgreSQL 验证已补齐。额外的中央目录预算、明确支持的 ZIP 变体和受限工具平台边界见 `adr/ADR-001-initial-implementation.md`。

### 下一项任务

M2-01 LocalBlobStorage：实现流式哈希、临时文件清理、原子提交和项目内去重；随后实现三步上传、outbox/lease/fencing、不可变修订发布与兼容基线。应复用现有协议和离线引擎，保持同身份重试/冲突与项目授权，不提前堆叠页面。

## 逐项任务状态

| 任务 ID | 状态 | 本轮依据或剩余事项 |
|---|---|---|
| M0-01 | DONE | 工程、锁文件、环境模板、ADR、真实依赖安装和镜像核对 |
| M0-02 | DONE | 六类模型、TS 类型、合同/语义/身份测试 |
| M0-03 | DONE | PostgreSQL 16 实际迁移/降级/升级、唯一键竞态与指标约束测试 |
| M0-04 | DONE | 会话/项目 token/角色、CSRF、限流、跨项目拒绝、真实 PostgreSQL 权限验证 |
| M1-01 | DONE | 流式解码、预检、路径/CRC/加密/重叠/预算异常测试 |
| M1-02 | DONE | SHA-256、物理快照和精确对账 |
| M1-03 | DONE | 增删改、压缩变化、分类和目录独立视图 |
| M1-04 | DONE | 独立固定字节账、Hypothesis、ZIP64 边界 |
| M1-05 | NOT_VERIFIED | 适配器/参数数组/资源封装及故障测试已实现，真实 SDK/沙箱联调待完成 |
| M2-01 | DONE | 流式临时写、实际长度/哈希、独占原子提交、项目内去重与断连清理 |
| M2-02 | DONE | 三步 API、严格 OpenAPI 合同、幂等/冲突/身份、并发封存与权限验证 |
| M2-03 | DONE | 真实 prefork、SIGKILL、Redis 队列丢失恢复、lease/fencing 与三次尝试限制 |
| M2-04 | DONE | 完整条目索引、分页、鉴权下载、修订切换及真实 PostgreSQL 重启持久化 |
| M2-05 | DONE | 按构建时间选基线、固定基线、缺失/ABI/时钟排除、跨组手动比较和修订冻结 |
| M3-01 | DONE | Editor-only UPM/.meta；实际 Editor 编译，8 项 EditMode，APK 未包含 BuildSize 程序集 |
| M3-02 | DONE | 两次真实 Android 构建、显式/回调、同 run_id 完整回执、实际场景重载、未知值保留 |
| M3-03 | DONE | Texture/Audio/通用快照；2,151/2,154 实际采集，失败不吞其他资源且 PARTIAL 透明 |
| M3-04 | DONE | 原始记录、GUID/类型/容器聚合、低置信路径、生成资源与 Importer 差异，持久化/API |
| M3-05 | DONE | 6000.0.76f1 Android/Mono 直接路径证据：41/42；其余 unmapped，未知版本 candidate |
| M3-06 | NOT_VERIFIED | 独立受控工程两次真实 APK 已通过；用户实际游戏工程/生产流水线尚未验收 |
| M4-01 | DONE | 五命令、冻结/同身份重试、真实本机 HTTP、角色恢复、期限/中止/脱敏测试 |
| M4-02 | DONE | 真实两仓、中文/空格/rename/GUID/blob、浅克隆、dirty、实际比较范围校验 |
| M4-03 | NOT_VERIFIED | 完整合并示例/说明和 Groovy 语法通过；实际 Jenkins 故障/中止隔离待外部验收 |
| M4-04 | DONE | 白名单/状态/项目授权/独立 scope、凭据 URL 拒绝、成功幂等与冲突、本机 HTTP 关联 |
| M4-05 | NOT_VERIFIED | 真实 Jenkins/发布验证 |
| M5-01 | DONE | 12 类纯规则、冻结设置、非相加合同、严格字段/未知语义与真实持久化 |
| M5-02 | DONE | 真实登录/项目/同组趋势/构建/分页/授权下载；声明与缺失明确 |
| M5-03 | DONE | 手动版本选择、物理/Unity/配置分层、证据解析、基线/置信与缺口 |
| M5-04 | DONE | 首版预算/阈值/固定基线、管理员 If-Match/并发锁、独立复核/审计；分类/保留期见 ADR-005 |
| M5-05 | DONE | 两条真实浏览器场景，含上传→prefork 分析→比较→证据→权限、分页/XSS 与窄屏 |
| M6-01 | DONE | 完整本地栈/迁移/同源生产静态包/共享卷/重启持久化；外部 TLS 另列未验证 |
| M6-02 | DONE | 保守软期限、共享/保护、两阶段/临时清理、dry-run/审计；离线空卷恢复及功能复验 |
| M6-03 | NOT_VERIFIED | 本地矩阵与生产静态包/转发头安全检查通过；A26–A28 真实外部 CI/发布未验收 |
| M6-04 | NOT_VERIFIED | 指定尺寸/记录数实测达标；参考独占硬件/真实负载/前端网络未验证，原始数据已提供 |
| M6-05 | DONE | 私有环境模板、启动/恢复/清理/故障指南、状态/验收矩阵与正式发布清单 |

## 工具调用简报

日期为 2026-11-17（Asia/Shanghai）。使用 exec_command 检查目录、Git/conda/运行时、依赖安装、修改及验证；使用 web 读取 Python 3.12 zipfile、FastAPI 安全、SQLAlchemy 2 约束与 Android apkanalyzer 官方文档，链接保存在 ADR-001。读取范围为完整项目规格、全部六份 Schema/示例、任务表及原验证工具。

主要参数为冻结锁文件、mypy strict、pytest 临时目录/100 组性质样本、固定 PostgreSQL 镜像 digest、随机隔离 Schema/端口及一次性数据目录。没有 429 或网络退避。初始 Docker 未启动，随后启动已有 Docker Desktop 并补齐真实验证；ZIP64 边界、首次工程配置和新文件格式检查失败均已修复并重跑。没有把中间失败或缺失工具写成成功，也没有执行生产部署或真实发布。
