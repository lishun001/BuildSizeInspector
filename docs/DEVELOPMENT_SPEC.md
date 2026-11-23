# Unity APK 包体变化检测平台
## 开发规格与 Codex 执行计划

| 项目 | 内容 |
|---|---|
| 文档版本 | 1.0 |
| 编写日期 | 2026-11-17 |
| 项目代号 | BuildSize Inspector |
| 主要使用者 | Unity 项目主程、研发、构建维护者 |
| 首版对象 | Jenkins 产出的最终 Android APK，以及同次构建的 Unity 报告 |
| 已确定的接入方式 | Jenkins Shell 上报 Unity 报告、APK 摘要和 HTTPS 直链，由 Worker 下载并分析。见 ADR-008 |
| 推荐首个适配目标 | 用户现有 Unity 6.0 项目；优先在实际使用的 6000.0.x 编辑器验证 |
| 交付性质 | 待实现的开发规格，不是已经开发完成的平台 |
| 协议依据 | 本文、`../schemas/`、`../examples/` |
| 开发任务 | `TASKS.md` |
| 技术来源 | `REFERENCE_SOURCES.md`；R 编号对应官方资料 |

> 本文用“必须”定义首版验收要求，用“后续”定义不应提前扩张的范围。本文中的 API、CLI 和目录是本项目待实现的接口，不是第三方现成服务。示例构建、大小、提交、域名均为虚构数据。
>
> **核心原则：先把增量算对，再做资源定位，最后做有证据的原因分析。绝不为了看起来解释完整，把推测写成事实。**

---

## 1. 产品目标与边界

### 1.1 必须回答的问题

一次 Jenkins 构建完成后，平台应回答：

1. APK 相比基线增加或减少了多少字节，增长集中在哪些文件或分类？
2. Unity 报告中的哪些原始资源、容器、导入设置发生变化？
3. 哪些构建配置、依赖或代码修改与这些变化相关，证据来自哪里？
4. 本次变化是否触发预算或增量阈值，应优先检查什么？

最终用户路径为：

```text
Jenkins 构建记录
  -> 当前 APK / 构建报告
  -> 自动基线与总增量
  -> 文件增量排行榜
  -> Unity 资源及设置变化
  -> 证据与候选原因
  -> Jenkins / 蒲公英 / 代码平台链接
```

### 1.2 首版 P0 范围

| 模块 | 首版必须实现 |
|---|---|
| 构建接入 | Jenkins CLI；分步骤上传；大小与 SHA-256 校验；幂等；异步分析 |
| 包体分析 | ZIP 条目索引、内容哈希、压缩前后大小、目录/类别聚合、精确对账 |
| 版本管理 | 历史构建、趋势、自动基线、手动对比、配置不一致提示 |
| Unity 采集 | Editor-only 导出器；BuildReport、实际构建配置、入包资源的导入设置 |
| Unity 对比 | GUID 追踪、报告资源聚合、容器映射状态、生成资源/未知资源展示 |
| 原因分析 | 有限的确定性规则；证据链；置信等级；不允许伪造精确贡献 |
| 代码关联 | Jenkins 本地收集多仓版本；可选 Git 文件差异；不依赖服务端访问私有 Git |
| 蒲公英 | 保留原发布脚本；记录发布状态、版本标识和安全的安装页面链接 |
| Web | 登录、项目总览、构建详情、版本对比、规则设置 |
| 运维 | Docker Compose、数据库迁移、备份恢复说明、结构化日志 |
| 质量 | 单元、协议、集成、前端端到端测试；真实 Unity/Jenkins 验证记录 |

### 1.3 明确不纳入 P0

AAB 设备分发体积、IPA、OBB、Play Asset Delivery、APK 运行时内存、实际安装占用、热更新下载量、IL2CPP 符号级归因、自动修改资源、自动回滚、AI 自动分析、复杂组织权限、蒲公英历史 APK 批量拉取。

YooAsset 的版本化专用适配放在 P1。P0 必须能把 APK 内的 Bundle 当普通 ZIP 条目统计，但不得宣称已经解析 Bundle 内部资源。P0 为后续适配保留数据模型和接口边界。

**不要把“首版暂不支持”实现成假数据接口或永远成功的占位功能。未支持能力应明确显示 `unsupported`。**

### 1.4 完成定义

在不更换现有 Unity 打包方法、不迁移蒲公英、不要求平台读取私有仓库的条件下：

- 两次真实构建能够上传并生成对比。
- APK 总增量与文件级增量、额外开销严格对账。
- 至少一次真实 Unity 资源变更能定位到源路径或明确显示无法映射。
- 检测服务故障不改变游戏构建及现有蒲公英发布的原有结果。
- 页面主要数据来自真实 API；演示数据必须显著标记。

---

## 2. 冻结的技术决策

以下是本项目的选型，不是对这些产品“最新版本”的声明。初始化时锁定可用补丁版本、依赖锁文件及容器镜像，不使用浮动 `latest`。

| 领域 | 决策 |
|---|---|
| 服务端 | Python 3.12、FastAPI、Pydantic 2 |
| 数据库 | PostgreSQL 16、SQLAlchemy 2、Alembic |
| 异步执行 | Celery 5 + Redis 7；PostgreSQL 记录持久任务状态和 outbox |
| Web | React、TypeScript、Vite、Ant Design、ECharts；pnpm 锁文件 |
| 首版文件存储 | `LocalBlobStorage`，API/Worker 共享持久卷；默认本机 SSD |
| 后续对象存储 | 通过 `BlobStorage` 接口添加 S3-compatible 适配，不把厂商逻辑写入分析器 |
| APK 基础解析 | Python `zipfile`，不依赖 Unity 安装 |
| Android 增强解析 | `apkanalyzer` 可选适配，独立探测；不可用时标记元数据未核验 |
| Unity 采集 | C# Editor-only UPM 包；不得引入游戏运行时程序集 |
| CI 客户端 | Python CLI，与后端共享协议模型或同一 Schema 测试 |
| 测试 | pytest、Hypothesis；Vitest、Playwright |
| 部署 | 单机 Docker Compose；生产按需要扩展 Worker，不先做微服务/Kubernetes |
| UI | 中文、桌面优先、开发工具风格；暗色为默认，颜色不能是唯一状态提示 |

API 不负责耗时解包或归因。Worker 不通过网络拉取任意 URL，也不执行 APK 内容。

### 2.1 系统组成

现行自动下载流程及架构图见 [Jenkins 接入说明](../integrations/jenkins/README.md#现行流程jenkins-上报报告和直链buildsize-自动下载-apk)。报告验收后任务进入 Outbox，Worker 下载 APK、校验并封存后开始分析。下面的系统组成图保留原全量上传路线和共用的分析服务边界。

```text
[Unity Editor 插件]
   | unity-report.json + asset-importers.json + settings snapshot
   v
[Jenkins]
   | 最终签名 APK + build-meta.json + 可选 git-changes.json
   |
   +---------> [现有蒲公英上传流程]
   |                  |
   |                  +---- 发布结果关联 ----+
   v                                        |
[buildsize CLI] -> [API] <-------------------+
                    |
          +---------+----------+
          |                    |
     [PostgreSQL]         [BlobStorage]
     构建/任务/结果         APK/原始报告
          |
      [Outbox Dispatcher] -> [Redis / Celery] -> [Worker]
                                                    |
                                              分析结果事务提交
                                                    |
                                                  [Web]
```

Dispatcher 是同一代码仓内的轻量进程，不是新的业务微服务。Redis 不作为任务和分析结果的唯一事实来源。[R09]

### 2.2 建议代码仓结构

```text
buildsize-inspector/
├── AGENTS.md
├── README.md
├── compose.yaml
├── .env.example
├── Makefile
├── schemas/
├── examples/
├── docs/
│   ├── DEVELOPMENT_SPEC.md
│   ├── TASKS.md
│   ├── REFERENCE_SOURCES.md
│   ├── IMPLEMENTATION_STATUS.md
│   └── adr/
├── backend/
│   ├── pyproject.toml
│   ├── migrations/
│   ├── src/buildsize/
│   │   ├── api/
│   │   ├── auth/
│   │   ├── domain/
│   │   ├── storage/
│   │   ├── analyzers/
│   │   │   ├── apk_zip.py
│   │   │   ├── android_tools.py
│   │   │   ├── unity_report.py
│   │   │   ├── asset_importers.py
│   │   │   └── git_changes.py
│   │   ├── comparison/
│   │   ├── rules/
│   │   ├── jobs/
│   │   └── maintenance/
│   └── tests/
├── frontend/
│   ├── src/
│   └── tests/
├── cli/
├── integrations/
│   ├── jenkins/Jenkinsfile.example
│   └── unity/com.company.buildsize/
│       ├── package.json
│       ├── Editor/
│       └── Tests/Editor/
└── tests/fixtures/
```

分析器只接受本地只读输入和明确配置，输出规范化模型。HTTP、数据库、Celery 调用不得散落在分析算法中，便于脱离服务测试。

---

## 3. 不得破坏的数据不变量

### 3.1 大小口径

所有存储、计算和 API 使用整数 bytes；展示使用 MiB，`1 MiB = 1,048,576 bytes`。百分比在展示层格式化，不提前四舍五入。JSON 数值上限由 Schema 限制在安全整数范围内，数据库使用 `BIGINT`。

| 字段 | 定义 | 是否能代表用户下载量 |
|---|---|---|
| `apk_file_bytes` | 最终 APK 文件的实际长度 | 仅代表该 APK 文件本身 |
| `zip_payload_bytes` | 所有 ZIP 条目的 `compress_size` 之和 | 不能单独代表 APK 总长 |
| `zip_uncompressed_bytes` | 所有 ZIP 条目的 `file_size` 之和 | 不能代表安装占用或运行内存 |
| `apk_overhead_bytes` | `apk_file_bytes - zip_payload_bytes` | 不能自动等同于签名大小 |
| `unity_packed_bytes` | Unity 报告的资源序列化大小 | 不是 APK 压缩后贡献 |
| `unity_reported_total_bytes` | Unity summary 的原始值 | 仅作辅助，不能覆盖最终 APK 实测值 |

ZIP 字段以 Python 官方定义为准。[R06] APK v2 等签名可位于 ZIP 数据条目之外；不能只汇总文件列表就声称得到完整 APK 大小。[R07]

### 3.2 必须成立的恒等式

```text
APK = SUM(all ZIP compressed entry bytes) + APK overhead

delta_apk =
    SUM(new_entry_compressed_bytes - old_entry_compressed_bytes)
    + delta_overhead
```

新增条目旧值为 0；删除条目新值为 0。所有目录条目也必须参与总量核算：不能未经验证就假设其数据长度一定为 0。目录汇总是视图，不得和叶子条目再次相加。

出现负 overhead、条目重叠、结构非法或计数溢出时，视为无效输入或分析失败，不使用一个“未知资源”条目掩盖错误。

### 3.3 不能相加的两个层级

APK 文件层是物理压缩数据；Unity 层是报告归属的序列化数据。Unity 容器还可能经过内部压缩。两个层级并排展示，不制作资源到 APK 字节的一对一假账。

禁止：

```text
sharedassets0.assets 压缩增量 5 MiB
= 图片 A 的 packedSize 增量 3 MiB
+ 图片 B 的 packedSize 增量 2 MiB
```

除非未来实现了足够的精确映射和压缩归因证据。P0 不实现这种归因。

### 3.4 身份与幂等

一次提交对应一个项目、一个 CI 构建、一个产物变体：

```text
logical_build_key =
(project_id, ci.instance_id, ci.job_full_name, ci.build_number, variant)
```

`run_id` 是客户端准备阶段生成并持久化的 UUID，贯穿所有报告和上传重试。不得每重试一次就生成新 UUID。

同一个 logical key、同一份清单：返回原构建。相同 key 却换 APK 哈希或报告清单：返回 `409 BUILD_IDENTITY_CONFLICT`。**不要把 APK 哈希放入唯一构建键，从而让错误重传悄悄变成新构建。**

不同 logical key 允许产出相同 APK 内容；展示为两个构建，底层文件可去重。

### 3.5 原始数据与派生结果

原始产物在 sealed 后不可改写。解析器、规则和基线策略均记录版本。重新分析生成新的 `analysis_revision`；完成后原子切换当前结果，不覆盖正在被用户查看的版本。

缺失不是 0，未知不是空结果，没有基线不是零增长，失败不是通过。

---

## 4. 构建协议与产物清单

### 4.1 文件集合

| role | 文件名示例 | 要求 |
|---|---|---|
| `apk` | `game.apk` | 必填；最终签名/对齐/后处理完成后的文件 |
| `build_meta` | `build-meta.json` | 必填；匹配 Schema 和本次构建身份 |
| `unity_report` | `unity-report.json` | 正常接入必须采集；缺失允许降级 |
| `asset_importers` | `asset-importers.json` | 资源设置快照；缺失显示覆盖不完整 |
| `git_changes` | `git-changes.json` | 可选；包含自身对比的 base/head，不能冒充任意基线 |
| `packages_lock` | `packages-lock.json` | 可选；按 Unity 原格式存档，解析需版本检查 |

协议 JSON Schema 见 `../schemas/`；格式示例见 `../examples/`。Schema 是字段合同，不取代跨文件、唯一性和安全校验。

所有报告根字段必须包含 `schema_version`、`run_id`。Schema 版本不支持时，必填元数据拒绝提交；可选报告存档后标记 `unsupported_schema`，基础 APK 分析仍可继续。

### 4.2 元数据必须覆盖的信息

`build-meta.json` 记录项目、包名、版本、渠道、分支、CI 身份、实际构建时间、Unity 版本、构建配置、多仓提交和工具链版本。

必须区分：

- Jenkins `build_number`、Android `version_code`、产品 `version_name`。
- 构建参数中的请求值和可采集到的实际值。
- Unity 构建完成时间和平台上传时间。
- 游戏主仓库、美术仓库、配置仓库、子模块的独立 revision。
- `target_sdk_requested = "Auto"` 与 APK Manifest 最终解析出的 SDK 数值。

CI 传入参数不能替代实际 Unity/Gradle 配置。无法读取的值为 `null`，附 warning，不能擅自填默认值。

### 4.3 Schema 之外的语义验证

必须检查 artifact role 唯一且恰好包含一份 APK、一份元数据；文件名不得含路径；大小不能超过限制；所有 `run_id` 相同；metadata 中的项目、variant、CI 身份与初始化请求一致。

Unity 报告与元数据 Unity 版本不一致、容器索引重复、容器内记录索引重复、同一 importer GUID 重复，均标记数据错误。GUID 全零视为未知，不作为真实资源主键。

Unity 报告 `build_result=Unknown` 可以来自回调阶段，必须保留，不擅自改写。最终是否存在可分析 APK，由 Jenkins 产出和服务端校验决定。

### 4.4 扩展性

v1 必填语义保持稳定。未知字段默认不接受，避免拼写错误被静默忽略。后续通过 v2 或有明确 Schema 的扩展字段演进，不让任意 JSON 渗透到业务核心。

`asset_importers.settings` 是特例：允许版本化的导入器字段集合，但每个 importer 适配器必须自行验证值类型、限制深度和长度，并保留 `unsupported_fields`。

---

## 5. Unity Editor 采集器

### 5.1 集成方式

优先方案：现有构建入口拿到 `BuildPipeline.BuildPlayer(...)` 返回的 `BuildReport` 后，调用：

```csharp
BuildSizeExporter.Export(BuildReport report, string reportDirectory, string runId);
```

上述是本项目要实现的函数签名，不是 Unity 内置函数。

兼容方案：实现 `IPostprocessBuildWithReport`，通过环境变量启用采集。该回调针对 Player 构建，不替代独立 AssetBundle 构建的报告采集；早期失败或取消不能依赖它生成报告。[R01]

两种模式共享同一实现。必须防止同时启用导致覆盖：推荐显式调用模式通过环境变量关闭回调导出；否则只允许同 run_id 的最终导出原子替换该次构建的本地报告，并记录 capture_mode。

### 5.2 环境变量

```text
BUILDSIZE_RUN_ID
BUILDSIZE_REPORT_DIR
BUILDSIZE_EXPORT_MODE=explicit|callback
BUILDSIZE_IMPORTER_COLLECTION=true|false
```

没有 `BUILDSIZE_REPORT_DIR` 或 `RUN_ID` 时，不自动扫描和输出。实际构建设置先写入本地 `unity-settings.json`，由 CLI 合并为 build-meta；该中间文件不占一个上传 role。输出目录应在项目 Assets 外，例如：

```text
$WORKSPACE/.buildsize/<run_id>/
```

避免导出文件触发 Unity 重新导入。每次构建目录独立，禁止从上次构建目录兜底读取旧报告。

### 5.3 BuildReport 导出

使用公开 API：`packedAssets`、`PackedAssets.shortPath/overhead/contents`、`PackedAssetInfo.sourceAssetGUID/sourceAssetPath/type/packedSize` 和 `BuildReport.GetFiles()`。[R02][R03][R04]

输出容器索引和容器内记录索引，只用于本报告定位，**不是跨构建稳定的序列化对象 ID**。这些公开字段不足以保证逐对象跨版本匹配，P0 不虚构 `localFileId`。

保留空 GUID、空路径以及生成资源，不丢弃记录。无路径资源显示为“生成/内置/未归属”，而不是凭类型猜源文件。

在现有构建入口用按位或保留原选项：

```csharp
options.options |= BuildOptions.DetailedBuildReport;
```

`DetailedBuildReport` 提供额外报告信息；它不是“完整记录 APK 中所有内容”的保证。[R05] 该选项必须由构建发起方设置，不能指望 postprocess 回调补开。

### 5.4 Importer 与设置快照

仅采集已入包且有源路径的资源；后续 YooAsset 适配可补充 Bundle 资源集合。默认不扫描整个项目，不强制加载所有 Texture2D，不执行 `SaveAndReimport`。

首版采集：

| 对象 | 字段 |
|---|---|
| TextureImporter | 类型、MipMap、Read/Write、sRGB、Android override、最大尺寸、格式、压缩质量、Crunch |
| AudioImporter | 默认及 Android override 的压缩格式、质量、采样率设置、loadType |
| 通用资源 | GUID、路径、Importer 类型、依赖哈希、可选源文件 SHA-256、`.meta` SHA-256 |
| Player 配置 | backend、ABI、Development、stripping、engine stripping、压缩选项、IL2CPP code generation |
| 内容配置 | 场景 GUID、项目提供的首包策略摘要 |

Android 纹理覆盖设置通过对应平台 API 读取。[R11] `Automatic` 保留为 Automatic；不能根据经验伪造最终平台纹理格式。图集打包生成资源不能直接归属为某一源图片。

`AssetDatabase.GetAssetDependencyHash` 反映依赖导入相关内容，不是源文件 SHA-256；两种哈希分字段存储。[R12]

所有 Unity API 操作在编辑器支持的上下文中执行。采集器不得修改项目资源、场景、Importer、PlayerSettings 或构建选项中的非必要部分。

### 5.5 导出稳定性

临时文件写完后原子 rename；使用 UTF-8；数组按确定性规则排序。耗时、导出器版本、警告必须记录。单个 Importer 失败不影响其他资源，也不让游戏构建失败。

存在大型报告时设置记录数和输出上限；达到上限必须明确 `partial/truncated` 警告，不能悄悄截断后当完整报告提交。

真实 Unity 编译验证不可由普通 .NET 编译或模拟测试替代。没有 Unity 编辑器时，只能标记“未验证”，不得宣称插件已兼容某版本。

---

## 6. Jenkins 流水线与 CLI

### 6.1 工作流

```text
准备 run_id 与独立目录
 -> 拉取并记录各仓 revision
 -> 执行现有 Unity 构建
 -> 若为导出 Gradle：执行现有 Gradle、签名、后处理
 -> 确认最终 APK 路径，生成元数据
 -> 上传分析平台（不等待分析完成）
 -> 按原规则上传蒲公英
 -> 将蒲公英发布结果关联到同一构建
 -> 归档 receipt、报告和诊断日志
```

分析上传与蒲公英上传可独立执行，但 P0 不要求改写成 parallel。不得让新增阶段把原有 Unity 或蒲公英失败吞掉。

### 6.2 CLI 合同

实现以下命令；所有命令支持 `--help`，日志输出到 stderr，机器可读结果输出到 stdout 或 `--output` 文件。

```bash
buildsize prepare --project demo-game --variant android-release-arm64 --output .buildsize/context.json
buildsize collect --context .buildsize/context.json --apk BuildOutput/game.apk --output .buildsize/submission-dir
buildsize submit --directory .buildsize/submission-dir --receipt .buildsize/receipt.json
buildsize status --receipt .buildsize/receipt.json --output .buildsize/status.json
buildsize link-pgyer --receipt .buildsize/receipt.json --result .buildsize/pgyer-result.json
```

`prepare`：生成或复用该 logical key 的 run_id；输出报告目录和设置所需环境变量的信息。路径含空格应可用，不要求 `jq`。

`collect`：默认读取 context 中本 run_id 的报告目录；冻结 submission-dir 中的报告、时间戳和哈希，后续 submit 重试不得重新生成这些内容。明确使用传入 APK，禁止“取目录中最大 APK/最新 APK”。构建元数据字段从受控配置、Jenkins 环境、Unity 快照合并；冲突报错，不覆盖证据。支持多仓配置文件，不要求所有仓都是 submodule。

`submit`：校验、计算 SHA-256、初始化、补传未完成角色、finalize，收到 202 后结束。不得等待 Worker 完成才允许发包。

`link-pgyer`：只关联既有发布流程产出的安全结果，不接管蒲公英 API Key。receipt 不存在时明确记录关联失败，不能关联到“最近一次构建”。

### 6.3 凭据与失败行为

API 地址通过 `BUILDSIZE_API_URL`；令牌只从 `BUILDSIZE_API_TOKEN` 读取。Jenkins 使用 Credentials Binding 注入，shell 关闭回显，避免 Groovy 字符串插值泄密。[R08]

首版默认 `advisory`：

- 本次游戏构建失败：保留原失败，不提交旧 APK。
- 采集/上传失败：记录 warning、保留可重试材料，继续原蒲公英流程。
- 分析规则命中：页面标记警告；不主动使当前 Jenkins 构建失败。
- 用户手动终止任务：继续终止，不能被泛化 `catch(Exception)` 吞掉。

Jenkins 的 `timeout` 和手动终止都可能表现为中断异常，示例必须区分处理。[R10] 推荐 CLI 自带请求总期限；Jenkins 仅捕获普通上传失败，不笼统吞掉所有 `FlowInterruptedException`。

CLI 退出码建议：0 成功；2 本地数据/参数错误；3 权限或配置错误；4 网络/服务错误；5 哈希或协议冲突。示例 Jenkinsfile 用 `returnStatus` 处理常规 CLI 失败，而不是 `|| true` 掩盖所有问题。

### 6.4 网络与重试

可重试：连接断开、超时、HTTP 408/429/5xx。使用指数退避与 jitter，遵守合理的 Retry-After；默认最多 3 次。401/403/409/413/422 不盲目重试。

首版按文件恢复：已验证的角色无需重传，未完成的大文件从头重传；**不声称支持字节级断点续传**。对象存储分片续传属于 P1。

默认上传总期限 15 分钟，是可配置的系统超时，不是性能承诺。不要依赖 Jenkins 节点永久保留 workspace；按现有归档制度保存报告，必要时将最终 APK 交给已有产物归档系统。

### 6.5 多仓 Git 信息

本地收集主仓、美术、配置及各子模块 revision 和 dirty 状态；优先用 Git 命令实际读取，不仅依赖 `GIT_COMMIT`。记录工作树是否脏，但不上传源码、密钥、用户信息或整个环境变量表。

获取可用基线 revision 后，以 NUL 分隔输出解析 `git diff --name-status -z -M <base> <head>`，正确处理空格、中文和重命名。浅克隆缺少基线时，标记 `missing_base`；不得把空输出解释为没有修改。

每份 git-changes 必须携带各仓 base/head。仅在它们与所选比较完全匹配时使用；换成另一个手动基线后，旧差异不能复用。服务端 P0 不自动拉取私有 Git。

### 6.6 蒲公英边界

保存 `publish_state`、`build_key`、`build_version`、`install_page_url`、`published_at` 等允许字段，原始响应中的 token、签名、带 API Key 的下载地址不得保存或展示。

文件传输成功不等于蒲公英发布成功，必须使用现有发布流程的最终结果。[R13] 保留可跳转的安装页面即可；分析平台不依赖从蒲公英重新下载 APK。

即使分析暂不可用，也允许在本地保留关联 JSON，之后使用同一 receipt 重试。

---

## 7. 上传 API 与任务状态

### 7.1 基础约定

API 前缀 `/api/v1`。CI 使用项目作用域 Bearer Token；Web 使用登录会话。统一错误响应：

```json
{
  "error": {
    "code": "ARTIFACT_HASH_MISMATCH",
    "message": "上传文件与声明的 SHA-256 不一致",
    "request_id": "server-generated-id",
    "details": {"role": "apk"}
  }
}
```

所有列表分页；默认 50，最大 200。排序字段白名单。时间用 UTC ISO-8601；UI 可按浏览器时区展示并注明时区。

### 7.2 分步上传合同

**步骤一：初始化**

```text
POST /api/v1/projects/{project_key}/builds
Idempotency-Key: <run_id>
Body: submission.schema.json
```

新建返回 201；相同身份和清单重试返回 200；冲突返回 409。返回 build_id、上传角色状态、`upload_state`、`analysis_state`、report_url。所有 URL 必须由服务端可信配置生成。

服务端对清单进行规范化：artifact 按 role 排序，JSON 对象键排序、紧凑 UTF-8 编码，不转义中文；计算 `manifest_sha256`。本清单只有整数、字符串、对象和数组，禁止非有限数值。客户端不得用原始 JSON 空格差异判断清单是否相同。

**步骤二：逐文件上传**

```text
PUT /api/v1/builds/{build_id}/artifacts/{role}
Content-Type: application/octet-stream
Content-Length: <manifest 声明长度>
X-Content-SHA256: <manifest 声明 SHA-256>
```

按块写 `.part` 文件并计算实际大小和 SHA-256。验证成功后原子落盘并登记，返回 201；同角色相同已验证内容返回 200。失败不将 partial 文件登记为已验证产物。

长度或哈希不符返回 422；超限 413；sealed 后不允许换文件。身份、权限、配额、角色在读取大 body 前尽可能完成检查。

**步骤三：封存并排队**

```text
POST /api/v1/builds/{build_id}/finalize
Body: {}
```

清单中声明的全部文件都必须已验证，否则 409。APK 和 build_meta 必须有效；未声明的 unity_report 允许缺失。

同一数据库事务中 sealed 构建并写入 outbox。成功/重复请求都返回 202，含当前状态和任务标识。任务完成后重试 finalize 也不得重新排队。

如果可选 JSON 的 schema 不支持或内容有误，保存其原始产物、写明覆盖问题，再执行可完成的基础分析。关键身份信息冲突不属于可降级问题。

### 7.3 状态分离

```text
upload_state: UPLOADING -> SEALED
                        \-> EXPIRED（只针对未完成上传）

analysis_state: PENDING -> QUEUED -> RUNNING -> READY
                                            -> PARTIAL
                                            -> FAILED

quality_gate: NOT_EVALUATED | PASS | WARN | FAIL | SKIPPED
```

相同身份及清单的 EXPIRED 上传可经初始化重新进入 UPLOADING；sealed 构建不能回退。缺少基线用 `comparison_status=NO_BASELINE`，不挤入分析状态。

`READY`：核心 APK 分析成功，项目期望的 P0 数据层成功。
`PARTIAL`：APK 成功，但 Unity 报告、导入快照或已请求的增强解析缺失/失败。
`FAILED`：APK 无效、关键身份冲突或基础分析无法完成。

项目默认期望 Unity 报告与实际配置；Git 差异可选，不因首次无 Git 基线而让全部分析失败。各层另设 `complete/missing/unsupported/failed/not_requested`。

### 7.4 其他端点

| 方法与路径 | 用途 |
|---|---|
| `POST /auth/login`、`POST /auth/logout`、`GET /auth/me` | Web 会话 |
| `GET /projects` | 有权访问的项目 |
| `GET /projects/{key}/builds` | 历史构建与状态 |
| `GET /projects/{key}/trend` | 同兼容组大小趋势 |
| `GET /projects/{key}/baseline-candidate` | CI 查询候选基线及各仓 revision |
| `GET /builds/{id}` | 元数据、大小、覆盖率、外部链接 |
| `GET /builds/{id}/entries` | APK 条目分页查询 |
| `GET /builds/{id}/unity-assets` | Unity 报告资源分页查询 |
| `GET /builds/{id}/artifacts/{role}/download` | 授权下载 |
| `PUT /builds/{id}/distribution/pgyer` | 幂等关联蒲公英结果 |
| `POST /comparisons` | 创建比较，参数为 base_build_id、target_build_id |
| `GET /comparisons/{id}` | 摘要、兼容性、指标 |
| `GET /comparisons/{id}/changes` | 差异条目，支持按层级筛选 |
| `GET /comparisons/{id}/findings` | 原因和证据 |
| `POST /builds/{id}/reanalyze` | 管理员触发新分析修订 |
| `GET /projects/{key}/rules`、`PUT /projects/{key}/rules` | 规则配置，乐观锁 |
| `GET /health/live`、`GET /health/ready` | 健康检查 |

上表路径省略共同 `/api/v1` 前缀。详细 OpenAPI 在实现中由模型生成，并纳入合同测试。`baseline-candidate` 与实际分析时的最终基线可能不同，必须比较 revision 后再使用 Git 证据。

---

## 8. 分析流水线与可靠性

### 8.1 执行阶段

```text
验证输入清单
 -> 校验 APK ZIP 结构
 -> 流式计算每个条目内容哈希与指标
 -> 可选 Android Manifest/DEX 元数据核验
 -> 导入 Unity 报告
 -> 导入设置与 Git 数据
 -> 生成独立构建快照
 -> 选择并冻结基线
 -> 计算文件/资源/配置差异
 -> 执行规则
 -> 事务发布结果
```

所有阶段记录开始时间、耗时、输入哈希、解析器版本和 warning。不要为了显示进度伪造百分比；可以显示阶段名与真实处理条目数。

### 8.2 异步任务设计

任务唯一键包含 build_id、analysis_revision_id、解析器版本和输入清单摘要。finalize 只创建一次初始修订；管理员 reanalyze 使用独立 Idempotency-Key 创建新修订，同一管理请求重试复用该修订。重复消息、Worker 重启、finalize 重试不能产生多份当前结果。

Outbox dispatcher 至少一次投递；Worker 必须幂等，不能承诺消息“恰好一次”。采用带过期时间的任务 lease 与 attempt/fencing token，避免超时旧 Worker 覆盖新结果。[R09]

Worker 将数据写入自己的 analysis_revision/staging；仅在发布事务校验 lease token 后更新当前结果指针。失败清理该次中间结果，不删除旧的成功分析。

对于临时存储或网络故障最多自动重试 3 次；无效 ZIP、协议错误和身份冲突不自动重试。Redis 丢失后，可依据数据库未完成任务重新投递。

### 8.3 外部工具

`apkanalyzer` 用于可选的包名/版本/Manifest 和差异交叉检查；官方工具支持 APK 大小、文件和比较操作。[R14]

通过参数数组启动进程，禁止 `shell=True` 拼接上传文件名。固定 SDK/Java 版本，限制子进程时间、内存和输出长度。保存工具版本与错误摘要，不把工具 stderr 当可信 HTML。

工具不可用时，元数据标记 `declared` 或 `unverified`；不可伪装成 APK 实测值。解析出包名与声明不一致时阻止该构建参与自动基线，并在页面突出错误。

P0 的 ZIP 测试夹具不需要可安装；它们只能证明 ZIP 算法。真实 APK Manifest 验证必须使用真实或合法生成的 Android 测试包。

### 8.4 数据补录限制

v1 sealed 后不能替换报告。需要修复采集时重新执行 Jenkins 构建；重新分析只能重算已有原始数据，不能补出未采集的信息。后续可以增加经过审计的 supplemental artifact 协议，但不纳入首版。
---

## 9. APK 差异引擎

### 9.1 每个 ZIP 条目保存的字段

```text
entry_index, raw_path, normalized_path, is_directory
compressed_bytes, uncompressed_bytes, compression_method
crc32, content_sha256, category, detected_abi
```

`detected_abi` 首版来自 `lib/<abi>/` 路径，只表示观察到的目录 ABI，不代表已校验 ELF 架构。`content_sha256` 基于解压后的条目内容流；不能用 CRC32 判断内容相同。目录条目单独处理。对受限大条目必须按块读取，不一次读入内存。

路径规范化不得改大小写或随意做 Unicode 合并。拒绝绝对路径、盘符、`..`、反斜杠、NUL、符号链接条目、加密条目、重复原始路径及规范化后冲突。保留原路径用于审计。

### 9.2 互斥分类

按照优先级只归入一个物理类别：

| 类别 | 规则示例 |
|---|---|
| `native_library` | `lib/<abi>/*.so` |
| `dex` | 根目录匹配 `classes.dex` 或 `classes[0-9]+.dex` |
| `unity_data` | 已识别的 Unity Data 前缀 |
| `builtin_bundle` | 项目配置的首包 Bundle 精确前缀/清单 |
| `android_resource` | `res/` 和 `resources.arsc` |
| `manifest_and_metadata` | AndroidManifest、META-INF 等 |
| `other_assets` | 其他 `assets/` 条目 |
| `other` | 无法分类或特殊目录数据 |

若 Unity Data 与 Bundle 规则重叠，项目配置显式决定优先级并版本化。不同分类器版本比较时必须重算到同一版本，不能把分类规则调整算成内容变化。

### 9.3 匹配与差异状态

先按完整规范化路径匹配：

| 情况 | 结果 |
|---|---|
| 仅新版有 | `ADDED` |
| 仅旧版有 | `REMOVED` |
| 哈希变化 | `MODIFIED`，即使大小没变 |
| 内容哈希相同，但压缩大小/方法不同 | `PACKAGING_CHANGED` |
| 内容与主要属性均相同 | `UNCHANGED` |

另保存 `content_changed`、`compressed_size_changed`、`compression_method_changed`。APK 整体哈希变化而条目内容不变，只能说明容器/压缩表示/签名等发生变化，未深入解析时不能确定是哪一种。

可对新增和删除集合做一对一的哈希候选匹配，显示“可能移动/重命名”。多个同哈希候选不能强行配对。物理对账仍保留原始新增/删除，不因为 UI 重命名视图改变总量。

### 9.4 排行与聚合

默认按正向 `delta_compressed_bytes` 降序。用户可切换绝对变化量、旧大小、新大小。目录聚合从叶子计算；默认不把 `UNCHANGED` 全量返回前端。

至少展示：

```text
正向增长 = sum(max(delta_entry, 0)) + max(delta_overhead, 0)
负向缩减 = sum(min(delta_entry, 0)) + min(delta_overhead, 0)
净变化 = 正向增长 + 负向缩减
```

百分比 `old_bytes == 0` 时为 `null`，显示“新增/无旧值”。不能用 Infinity 或 100% 代替。

### 9.5 重复内容

同构建中，不同路径但非空内容 SHA-256 相同，可提示“存在相同内容的多个 ZIP 条目”。实际占用等于这些条目自身压缩字节之和。

这不是“可以安全删除”的结论。不同 ABI、资源装载路径或 SDK 可能要求各自副本。删除可节省大小仅能标为理论值，不能并入已证实优化收益。资源同时被多个 Bundle 引用也不自动证明资源字节重复存储。

---

## 10. Unity 资源对比与容器映射

### 10.1 跨版本身份

源 GUID 有效时，以 GUID 追踪原始资源，路径仅作为可变展示字段。一个 GUID 可以包含多个序列化对象类型和多个实例，不能只保留第一条。

P0 同时维护：

```text
原始记录：container_index + record_index（仅本报告内唯一）
容器内聚合：container identity + source GUID + asset_type
项目级聚合：source GUID + asset_type
```

没有 GUID 但有路径时，允许低置信的精确路径匹配，必须标记 `path_fallback`。GUID 和路径均缺失时，不跨构建强行逐对象匹配，只比较容器内的未知/生成资源汇总。

### 10.2 新增资源的语义

“新增入包资源”表示旧报告中没有、新报告中出现。不能直接写成“本次提交新建了文件”；它也可能是已有资源首次被引用、首包策略改变或场景引用变化。

GUID 相同但路径改变应显示重命名，不显示资源消失后新增。GUID 改变则先视为不同资源，即使文件名相同。

### 10.3 容器到 APK 的映射

映射状态：

```text
exact | candidate | unmapped | ambiguous
```

`exact` 仅用于有可验证路径规则或构建输出映射证据的关系。`PackedAssets.shortPath` 是报告容器路径信息，不保证直接等于 APK 内部路径。[R03]

P0 至少实现一个经真实 Unity 6 APK 验证的直接路径适配；其余明确显示 unmapped。若容器封装在内部归档中，不能因为文件名相近就假装存在独立 ZIP 条目。

禁止只用 basename 自动建立确定映射。多个候选必须显示 ambiguous。每条映射记录 mapper_version 和 evidence。

### 10.4 覆盖率与差异展示

分别展示：

- 有效源 GUID 的报告记录覆盖率。
- 能映射到 APK 条目/容器的报告容器数量比例。
- Importer 采集成功数量比例。
- 未归属 `unity_packed_bytes`。

这些是各自数据层的覆盖率，不是“解释了多少 APK 压缩增长”。生成资源未知时，仍保留其数量和大小，不删除后显示 100% 覆盖。

### 10.5 YooAsset P1 适配边界

YooAsset 官方构建报告可展示资源、资源包及关系；构建输出包含版本化的 `.report` 等文件。[R15] 但本项目不假定不同 YooAsset 版本 JSON 字段恒定。

实际开发时先获取项目使用的 YooAsset 版本与一份真实报告夹具，再实现对应 Adapter。输出统一模型：

```text
package_name, package_version, bundle_logical_id, physical_filename
bundle_hash, bundle_bytes, compression
source_assets[], dependencies[]
delivery_scope: builtin | remote | unknown
apk_entry_mapping
```

只统计确实内置进 APK 的 Bundle 对 APK 的贡献。远程 CDN 文件变化另列，不能计入 APK 增量。文件名含内容哈希时需要稳定逻辑身份，不把所有 Bundle 更新误判成完整删除重建。

P0 UI 不显示没有数据来源的 Bundle 内部“精确资源占用”。

---

## 11. 基线选择与可比性

### 11.1 自动基线兼容组

硬条件必须一致：

```text
project + platform + artifact_type + application_id
+ channel + variant + branch
+ development_build + scripting_backend + sorted(architectures)
```

关键值缺失时不自动混入已知组，显示“无法自动选择可比基线”。包名以核验值优先；未核验时只允许同可信 CI 来源的 declared 值参与，并明确提示。

Unity 版本、裁剪级别、压缩方式、SDK/NDK 版本、内置资源策略是**要检测的变化因素**，不能全部塞进硬兼容指纹，否则这些改变会导致永远选不到前版。

### 11.2 选择规则

在同兼容组、同分支、APK 基础分析成功的历史构建中，选择 `build_completed_at` 早于目标且最接近的一次。不得按上传完成时间或 Worker 完成时间选“上一版”。

同一时间用服务端构建 ID 确定性打破平局。客户端时间明显不合理时标记时钟问题并排除自动基线，默认容许偏差由配置控制。

项目可固定发布基线；固定基线仍检查兼容性。第一次没有基线显示 `NO_BASELINE`，仍提供当前包分析。

### 11.3 基线冻结

创建对比时保存 base_build_id、target_build_id、对应 analysis_revision、兼容组、策略版本和选择理由。晚到的历史构建不能悄悄改变已发布报告。

用户可以显式重新比较，生成新 comparison。不能让同一 URL 的结论无审计地切换到另一个基线。

### 11.4 手动比较

P0 只允许同项目内比较，双方权限必须验证。不同 ABI/Development/backend 等仍允许手动看差异，但展示显眼警告，自动增长门禁为 SKIPPED。

跨分支比较可以进行，但默认不作责任归因。Git diff 必须针对实际 base/head；资料不足时展示“缺少此基线的 Git 证据”。

---

## 12. 原因分析规则

### 12.1 三种置信等级

| 值 | 中文标签 | 可作出的结论 |
|---|---|---|
| `observed` | 已观测事实 | 新增某文件、某配置改变、某哈希不同 |
| `correlated` | 有证据关联 | 纹理报告增长与该资源导入设置同时改变 |
| `hypothesis` | 待验证假设 | IL2CPP 增长可能与代码/工具链/裁剪相关 |

“配置确实发生变化”是 observed；“配置导致全部增量”未必是 observed。同一规则允许观察事实与因果置信不同，解释文本必须明确。

`confirmed/rejected` 是人工复核状态，不自动把相关性升级成定量因果证据。

### 12.2 Finding 合同

见 `../schemas/finding.schema.json`。必须包含规则版本、对象、指标口径、证据定位、解释、建议检查、置信等级。

`evidence.locator` 使用 JSON Pointer、条目路径或明确的派生记录键。证据必须指向原始产物 SHA 或当前不可变 analysis_revision。

所有 finding 的 `additive=false`：一个条目可能同时命中 SDK 更新、ABI 和配置规则，不能把多个 finding 的大小再相加。唯一可相加的物理贡献账来自 APK 条目差异与 overhead。

### 12.3 P0 规则清单

| ID | 条件 | 输出限制 |
|---|---|---|
| `APK_ABSOLUTE_BUDGET` | APK 大于已配置预算 | 仅输出预算超标事实 |
| `APK_GROWTH_THRESHOLD` | 增量 bytes 与百分比同时满足阈值 | 仅可比、有基线时执行 |
| `NATIVE_LIBRARY_ADDED` | 新增 `.so` 条目 | 该条目压缩大小是观测贡献；SDK 身份需额外证据 |
| `ABI_SET_CHANGED` | 实际或声明 ABI 集变化 | 跨组比较提示，不伪装普通同配置增长 |
| `UNITY_SOURCE_ENTERED_BUILD` | 新 GUID 进入报告 | 称“新增入包”，不直接称“新增源文件” |
| `UNITY_TEXTURE_IMPORT_CHANGED` | 同 GUID 的 packed_bytes 增长且配置改变 | correlated，不宣称精确 APK 增量 |
| `UNITY_AUDIO_IMPORT_CHANGED` | 同类音频证据 | 同上 |
| `UNITY_VERSION_CHANGED` | Unity 版本不同 | 配置事实；相关 native 增长仅候选原因 |
| `PACKAGING_CHANGED` | 内容哈希相同但条目压缩大小/方法变化 | 压缩表示变化事实，具体配置原因需证据 |
| `DUPLICATE_ENTRY_CONTENT` | 多路径同非空内容哈希 | 重复内容事实，不保证可删除 |
| `REPORT_DATA_INCOMPLETE` | 报告缺失、错误、不支持或来源映射不足 | 说明受影响的结论范围 |
| `NATIVE_GROWTH_UNEXPLAINED` | native 增长但无精确映射 | hypothesis；列出需检查的配置，不猜开发者 |

首版默认仅启用 advisory 阈值建议：

```text
增量 >= 5 MiB AND 增幅 >= 3%
```

这是初始可修改参数，不是用户真实项目预算。绝对预算默认未配置。首版不启用统计趋势预测或 AI 模型，避免在数据不足时误报。

### 12.4 配置对比

记录旧值、新值、字段来源与缺失状态。包括 Unity、ABI、backend、Development、strip、压缩、工具链、Package lock 和首包策略摘要。

同源文件 SHA 不变而依赖哈希改变，只能说明依赖/导入相关内容变化，不能声称源文件被修改。原配置值缺失时不能断言“从默认值改为新值”。

### 12.5 Git 关联

优先用源 GUID、`.meta` 和准确的仓库相对路径建立候选关联。提交列表表示“构建之间的变化范围”，不是根因证明。

不得生成“某开发者导致包体膨胀”式责任判断。需要确证原因时，建议控制变量构建或 bisect；P0 只提供验证步骤，不自动触发构建农场。

---

## 13. 数据库模型

统一使用 UUID 主键、UTC 时间、`BIGINT` 字节字段。配置和原始元数据可用 JSONB，但可筛选指标不得全藏在 JSONB 中。

| 表 | 核心字段与约束 |
|---|---|
| `projects` | key 唯一、展示名、期望数据层、基线策略、预算与权限 |
| `users` / `project_memberships` | 用户、password hash、角色、项目授权 |
| `api_tokens` | token hash、prefix、项目、scope、有效期、撤销时间 |
| `builds` | logical key 唯一、run_id 唯一、元数据、两类状态、当前分析指针 |
| `artifact_blobs` | project_id + sha256 唯一、长度、存储键、保留时间 |
| `build_artifacts` | build_id + role 唯一、blob 引用、schema 状态 |
| `analysis_revisions` | build_id、parser set 版本、状态、输入摘要、指标 |
| `apk_entries` | revision_id、路径、压缩/原始大小、哈希、类型、类别 |
| `unity_containers` | revision_id + container_index 唯一、路径、overhead、映射状态 |
| `unity_asset_records` | container_id + record_index 唯一、GUID、路径、类型、packed_bytes |
| `asset_importers` | revision_id + GUID 唯一、导入器类型、设置、哈希 |
| `repository_snapshots` | build_id + repo_key 唯一、revision、dirty、kind |
| `comparisons` | 两端分析修订、策略版本、状态、差异统计、可比性 |
| `change_records` | comparison_id、层级、对象键、旧/新大小、变化类型 |
| `findings` | comparison/build、规则版本、置信、证据、复核状态 |
| `distribution_links` | build_id + provider 唯一、发布状态与安全链接 |
| `jobs` | 幂等键、attempt、lease owner/token/expiry、错误 |
| `outbox` | 事件、发送状态、重试次数 |
| `audit_events` | 操作人/令牌、动作、对象、时间、request_id |

长路径索引可使用固定长度 path hash + 原路径校验，避免超长文本索引问题。不能因为哈希索引就不验证原路径相等。

建议索引包括项目+完成时间、兼容组+完成时间、revision+path hash、revision+GUID、comparison+delta。大列表使用稳定游标或确定性分页。

删除采用有审计的生命周期流程。Blob 被多个构建引用时不能删除某个构建就直接删除共享文件。

---

## 14. Web 页面与交互

### 14.1 全局规范

顶部显示项目、环境、登录用户；所有大小旁有指标口径。数值可以复制原始 bytes，展示最多两位 MiB 小数。状态必须有文字，不能只靠红绿颜色。

表格服务端分页、搜索、排序，不能一次把十万条记录发给浏览器。URL 保存项目、两个版本和筛选条件，便于分享。

### 14.2 项目总览 `/projects/:key`

主要信息：最近 APK 大小、相对基线增量、分析覆盖状态、告警数、最近构建列表、历史趋势。

趋势按兼容组区分，不把 arm64 Release 与双 ABI Development 连接成一条折线。无基线显示破折号。失败或未分析构建有占位点/标记，不伪造大小。

### 14.3 构建详情 `/builds/:id`

展示 CI 来源、应用版本、最终 APK 哈希、Unity 版本、各仓 revision、构建配置、产物下载、蒲公英发布链接。

Tab 建议：概览、APK 文件、Unity 资源、构建配置、数据完整性。原始报告下载在权限检查后提供，不在公开页面直接暴露存储路径。

### 14.4 版本对比 `/comparisons/:id`

首屏：旧/新 APK 大小、净增量、增幅、基线选择理由、可比性警告。

下方 Tab：

```text
增长来源 | 文件差异 | Unity 资源 | 配置差异 | 原因与证据
```

“增长来源”用互斥类别和 overhead 瀑布/条形图；“Unity 资源”使用独立指标。切换指标时标题和轴标签一起变化。

点击增长项可查看两版值、对应资源、相关规则与证据。没有映射时展示实际缺口，不给出虚构跳转。

### 14.5 原因详情

卡片内容：观察到什么、可能原因、置信等级、证据、待验证事项。证据可定位到对应 JSON 字段或文件条目；复核操作写审计日志。

### 14.6 项目设置 `/projects/:key/settings`

管理员配置增量阈值、绝对预算、基线、分类规则及保留期。规则更新需版本号/If-Match，避免多人编辑互相覆盖。P0 可只提供少量必要表单，不做通用脚本规则编辑器。

### 14.7 必须设计的空态

没有构建、首版无基线、正在上传、排队、分析失败、部分分析、报告缺失、APK 过期无法重算、无权限、服务不可用、旧版本 Schema 不支持、元数据未核验。

每种状态提供具体解释，不用同一个“暂无数据”覆盖全部情况。

---

## 15. 权限、安全与资源限制

### 15.1 最小权限

P0 支持 admin、viewer 和项目作用域 CI token。CI token 只允许提交、查询自己项目的构建、关联蒲公英；不能管理用户或读取其他项目。

Web 使用 HttpOnly 会话 Cookie、同源部署、CSRF 防护、登录限流。初始管理员通过 CLI/环境引导建立，无默认密码。密码采用专门的密码哈希方案，例如 Argon2id；API token 仅保存安全哈希，创建时展示一次。

所有对象查询都做项目级授权；不能只检查是否登录。直接下载接口同样校验权限。默认服务不公开暴露到互联网。

### 15.2 文件处理

流式接收和哈希，不 `extractall`，不执行上传内容，不接受服务端任意 URL 拉取。原始文件名仅作展示，不用作磁盘路径。API/Worker 以非 root 运行；外部工具禁网并有资源限制。

默认可配置限制：

| 参数 | 初始值 |
|---|---|
| 单 APK | 2 GiB |
| 单 JSON 报告 | 64 MiB |
| 单构建全部文件 | 3 GiB |
| ZIP 条目数 | 100,000 |
| 累计解压字节 | 16 GiB |
| 单条目解压字节 | 4 GiB |
| 单分析任务总期限 | 20 分钟 |
| 同一 Worker 同时进行的大 APK 分析 | 1 |
| 单项目同时上传数量 | 2 |
| 未完成上传保留期 | 24 小时 |

限制是初始策略，不是压缩包天然保证。读取时累加真实解压字节和耗时；仅相信 ZIP 声明长度不足以防止恶意输入。高压缩比先标记风险，最终由实际解压预算限制，避免简单比例规则误杀正常高度可压缩数据。

解析检查 CRC、可支持的压缩方法、异常头和重叠条目。非法输入必须有明确错误码和清理测试。

### 15.3 敏感信息

禁止上传签名私钥、keystore、安装密码、API Key、Git 凭据、完整环境变量、私有源码全文。源码路径和提交信息也按内部敏感数据保护。

外部链接协议仅允许 http/https，配置可信域名；不得渲染未转义的提交文本、文件路径或工具输出。带 token/signature/password 的 URL 不得写日志或展示。

### 15.4 原始数据保留

默认建议：APK 30 天、JSON 报告 180 天、派生指标 365 天；均可配置，并在界面说明。这是本项目默认策略，不是蒲公英的保留规则。

固定基线、当前分析引用和管理员 pin 的构建有保护。Blob 清理基于全部引用及保护状态，使用标记后清理；任务临时文件与正式 Blob 分开。

即使原 APK 已过期，仍可使用已保存的完整条目索引比较；若某层索引也已清理，显示 unavailable，不能静默降为不完整结果。

---

## 16. 运维与可观测性

### 16.1 Compose 服务

```text
web       前端静态文件和 /api 同源代理
api       HTTP、认证、上传、查询
worker    Celery 分析任务
scheduler outbox 投递、lease 回收、清理；单实例
postgres  持久数据
redis     队列
```

API 和 Worker 挂载同一 Blob 持久卷；不得把大 APK 存在容器临时层。初期不强依赖 NAS，避免不确定的网络 IO 和锁语义；NAS/S3 迁移通过存储适配与运维验证执行。

### 16.2 环境配置

`.env.example` 必须覆盖：

```text
PUBLIC_BASE_URL
DATABASE_URL
REDIS_URL
BLOB_ROOT
SESSION_SECRET
TOKEN_HASH_PEPPER
UPLOAD_MAX_BYTES
ANALYSIS_TIMEOUT_SECONDS
WORKER_CONCURRENCY
APK_RETENTION_DAYS
REPORT_RETENTION_DAYS
METRICS_RETENTION_DAYS
ANDROID_SDK_ROOT
APK_ANALYZER_ENABLED
```

不包含真实秘密。生产启动检查缺失 secret 并拒绝不安全默认值。SDK/Java 可用性应在诊断页显示，安装方式和许可接受由部署者确认，不悄悄安装未锁定外部二进制。

### 16.3 日志和指标

日志字段：request_id、project_key、build_id、analysis_revision、job_id、stage、duration_ms、error_code。不输出 Authorization、Cookie、带签名 URL。

至少可统计上传耗时/失败率、队列等待、各阶段分析耗时、Worker 失败率、数据缺失率、存储量、过期任务数。首版可用日志和诊断 API，不强制引入完整监控集群。

### 16.4 备份与恢复

提供数据库备份、Blob 目录备份、密钥配置恢复说明。恢复演练要验证登录、历史查询、下载、已有结果展示、重分析。只备份 PostgreSQL 而丢失 Blob 不算完整恢复。

上传/分析清理任务必须有 dry-run 和审计记录；不允许一个定时 `rm -rf` 根据客户端路径执行清理。

### 16.5 性能验收方法

性能数字只能来自测量。初始测试场景：500 MiB APK、20,000 条目、50,000 条 Unity 记录，在 4 vCPU、8 GiB RAM、本地 SSD 的测试环境记录用时及峰值内存。

目标：单任务 Worker 峰值内存小于 1 GiB；常用分页查询目标 P95 小于 1 秒。它们是待验证目标，不是已经达到的 SLA。实际吞吐受解压后体积和存储影响，不写未经测试的“固定几秒完成”。

---

## 17. 验收测试矩阵

以下测试必须实际实现；更详细任务见 `TASKS.md`。

| 编号 | 场景 | 期望 |
|---|---|---|
| A01 | 两个完全相同 APK | 总增量为 0；没有伪新增 |
| A02 | 新增/删除/修改普通文件 | 每条和总量精确符合字节公式 |
| A03 | 同内容不同压缩方式 | content hash 不变，状态为 PACKAGING_CHANGED |
| A04 | 内容变化但长度相同 | MODIFIED，大小增量可为 0 |
| A05 | 仅 ZIP 注释/头/对齐等容器变化 | 体现在 overhead；不随意归因为签名 |
| A06 | 新增和删除同时出现 | 正增长、负缩减、净值正确 |
| A07 | 目录条目或特殊路径 | 不重算目录汇总；非法路径拒绝 |
| A08 | 重复路径、ZIP bomb、CRC 错误 | 有界失败、临时文件清理、不拖垮服务 |
| A09 | 两个构建 GUID 相同但资源改名 | 识别同资源，不显示双倍新增/删除 |
| A10 | 一个 GUID 多个报告对象 | 不丢行、不重复去重、正确聚合 |
| A11 | 无 GUID/无路径的生成资源 | 保留为未知，不能凭空映射 |
| A12 | 同名不同目录容器 | 不按 basename 错配 |
| A13 | BuildReport 和 APK 大小不等 | 正常并列显示，不强行补齐 |
| A14 | 同身份重复初始化/上传/finalize | 一个构建、一份当前任务结果 |
| A15 | 同身份不同哈希/清单 | 409，旧产物不被覆盖 |
| A16 | 上传中断后重试 | 已验证角色跳过，未完成文件重传 |
| A17 | 多文件 run_id 不一致/旧报告混入 | 拒绝或标记关键身份冲突 |
| A18 | Unity 报告缺失 | APK 分析成功，PARTIAL，不伪造资源 |
| A19 | Worker 重复消费/进程终止 | 可恢复，不出现两个当前修订 |
| A20 | Redis 消息丢失/重启 | 数据库任务可重新投递 |
| A21 | 多个构建乱序上传完成 | 基线按构建时间，不按上传顺序 |
| A22 | ABI/Development/backend 改变 | 不自动混为可比基线 |
| A23 | Unity/压缩/stripping 改变 | 仍能选前版并展示配置差异 |
| A24 | 手动更换基线但 Git diff 不匹配 | 不复用错误 Git 证据 |
| A25 | 只有第一个构建 | NO_BASELINE，而非“增长 0” |
| A26 | 分析服务不可用 | Jenkins 原发包流程不受新增检测影响 |
| A27 | 用户中止 Jenkins | 中止不会被错误处理吞掉 |
| A28 | 蒲公英传输成功但发布失败 | 显示失败/未发布，不显示可安装成功 |
| A29 | 未授权访问其他项目/下载文件 | 拒绝访问，不泄露是否存在 |
| A30 | 文件/日志包含中文、空格、脚本标签 | 正确处理，前端无脚本执行 |
| A31 | 原始 APK 过期 | 已保存指标可读；不支持的重算明确失败 |
| A32 | SDK 工具不可用 | 基础分析可用，元数据核验状态透明 |

增加 Hypothesis 随机 ZIP 对比：对所有合法输入验证 `delta_apk == sum(delta_entries) + delta_overhead`，并验证 `compare(A, B)` 的增量与 `compare(B, A)` 相反。

不能只对照自己同一套函数生成的 expected 值；固定夹具须人工或独立逻辑计算。真实 APK 与 apkanalyzer 的“压缩前/压缩后”口径不同，不应要求不同口径输出机械相等。

---

## 18. 交付物与实施顺序

完整首版交付：

| 交付物 | 验收方式 |
|---|---|
| 可运行源码 | 新环境按 README 能启动 |
| Compose 和迁移 | 空数据库迁移成功，重启不丢数据 |
| Unity Editor 插件 | 目标编辑器编译通过、真实构建导出 |
| Jenkins CLI/示例 | 两次真实流水线接入、失败隔离验证 |
| JSON Schema / OpenAPI | 示例和服务端协议测试通过 |
| 前端 | 完整真实数据流程及空态，非静态演示 |
| 测试与夹具 | 可重复运行，危险输入和竞态有覆盖 |
| 运维文档 | 配置、备份、恢复、清理、故障排查 |
| 实现状态表 | 已完成、未完成、未验证明确分开 |

实施里程碑为：

```text
M0 工程与合同
 -> M1 离线 APK 分析/比较
 -> M2 上传/任务/持久化闭环
 -> M3 Unity 导出与资源对比
 -> M4 Jenkins/蒲公英/Git 接入
 -> M5 Web 与规则闭环
 -> M6 加固、真实验证与发布
```

每个里程碑均有可执行测试与可运行结果，不允许先堆全部页面再实现核心数据。详见 `TASKS.md`。

---

## 19. 后续扩展，不得抢跑

| 阶段 | 内容 | 前置条件 |
|---|---|---|
| P1 | YooAsset 指定版本适配 | 真实构建报告样本与项目版本 |
| P1 | S3 直传/分片续传 | LocalBlobStorage 接口稳定、上传瓶颈已测量 |
| P1 | ELF/DEX 更细拆解 | 工具链版本固定、合法样本和映射证据 |
| P1 | 通知与显式严格门禁 | advisory 稳定，失败策略经项目确认 |
| P2 | AAB 设备规格体积 | 定义固定 device spec 与交付口径 |
| P2 | 受约束 AI 总结 | 原始证据和规则完整，输出可追溯 |
| P2 | 自动控制变量构建 | 构建成本、权限和安全边界明确 |

严格门禁必须由项目显式开启，分别规定上传失败、分析超时、PARTIAL、不可比、超预算的处理；不继承 advisory 的默认通过语义。

AI 不参与首版数值计算、基线选择和权限决策。后续 AI 只能读取已经归一化的证据，输出可验证的候选解释。

---

## 20. Codex 执行约束与完成报告

先阅读根目录 `AGENTS.md` 和 `TASKS.md`，检查现有仓库后实施；不得覆盖现有 Jenkinsfile 或游戏构建入口，不得擅自升级用户 Unity 版本。

遇到与本文冲突的真实 API 或版本差异，依据官方文档和编译结果修正，并写 ADR，不能靠猜字段继续。真实项目文件、凭据、Unity 安装缺失时，用夹具实现可验证部分，同时把外部验证列为未完成。

每次阶段完成报告必须写：

```text
实现了什么：
修改文件：
执行的测试及结果：
没有执行的测试及原因：
与规格的差异：
下一项任务：
```

“单元测试通过”不等于 Unity 编译通过；“Compose 文件写好”不等于已部署；“模拟 ZIP 测试通过”不等于真实 APK 验证完成。

**最终发布标准是可运行、可重试、可对账、可解释且不干扰发包，而不是截图上看起来功能齐全。**
