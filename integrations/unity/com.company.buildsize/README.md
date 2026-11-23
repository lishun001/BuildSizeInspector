# BuildSize Inspector Unity 导出器

Editor-only UPM 包，不包含 Runtime 程序集或额外运行时依赖。已在 Unity 6000.0.76f1 编译、运行 8 项 EditMode 测试，并在独立 Android 验证工程实际构建两次。其他补丁版本仍须编译/运行验证。

通过 Unity Package Manager 的“从磁盘添加”选择本目录 package.json，或使用现有 Pipeline package add 命令。不要直接修改用户 manifest 或升级 Editor。本包的 .meta 文件随源码提供。

## 显式采集

在已有 Editor 构建入口保留所有原选项，按位或增加 DetailedBuildReport，拿到 BuildReport 后调用：

```csharp
options.options |= BuildOptions.DetailedBuildReport;
var report = BuildPipeline.BuildPlayer(options);
BuildSize.Editor.BuildSizeExporter.Export(report, reportDirectory, runId, options.scenes);
```

原三参数 `Export(report, reportDirectory, runId)` 同样可用；不能从 BuildReport 可靠恢复完整场景列表时会记录 warning，不猜测 EditorBuildSettings 场景。四参数版本只读取调用者本次实际 scenes，不修改场景或构建选项。最终游戏构建结果仍以 report.summary.result 为准，采集失败返回 false/告警。

runId 必须由 CI prepare 持久化后传入，不能在上传重试时重新生成。reportDirectory 必须是 Assets 外的独立目录，例如项目根下 .buildsize/该run_id。拒绝跨 run_id 覆盖、身份不明的旧报告目录和符号链接输出目录。

## 回调模式

仅在以下环境变量存在时自动采集：

```text
BUILDSIZE_EXPORT_MODE=callback
BUILDSIZE_RUN_ID=本次持久化的UUID
BUILDSIZE_REPORT_DIR=Assets外的本次独立目录
BUILDSIZE_IMPORTER_COLLECTION=true
```

默认不自动采集。显式模式应使用 BUILDSIZE_EXPORT_MODE=explicit，避免额外回调；若两种方式同时调用，只允许同 run_id 的最终显式结果替换该次本地报告。回调可观测到 build_result=Unknown，原样保留；完成时间尚不可用时为 null，由 CI 提供实际完成时间，不用上传时间冒充。

## 输出与完整性

- unity-report.json：容器和所有采集到的原始记录，保留空 GUID/路径、生成资源和报告内索引，不导出虚构 localFileId。
- asset-importers.json：只遍历报告涉及且有源路径/有效 GUID 的资源；纹理、音频及通用哈希。不会扫描全项目、强制加载所有纹理或 SaveAndReimport。
- unity-settings.json：实际可采的设置，中间文件，不是上传 role；CLI 合并元数据时必须保留 null 和 warnings。
- capture-complete.json：最后提交的采集回执，含本轮文件清单、长度、SHA-256 和耗时。CI 只能读取回执列出的本次文件，并逐一验哈希；没有完成回执就不读取旧文件兜底。

报告导出后，由 Jenkins Shell 读取采集回执并上报。签名完成的 APK 摘要和对应的下载直链由发布流水线提供，Worker 下载文件并校验后开始分析。[Jenkins 自动下载接入](../../jenkins/README.md)

每个 JSON 临时写完后原子替换，单文件限制 64 MiB，最多 10,000 容器/100,000 记录、50,000 Importer。达到记录上限写 BUILDSIZE_TRUNCATED warning；后端不会将截断报告当作完整资源比较。超出输出大小则采集失败，完成回执不生成。

单个 Importer 失败不影响其余资源。源文件/.meta SHA 是可选项，文件不可读、不存在或超过 64 MiB 时保持 null；dependency_hash 是 Unity 依赖导入哈希，不能当作源文件 SHA。Automatic 纹理格式保持原值，不推断实际平台编码。

实际测试的 4,116 条报告记录中有生成/未知记录，2,154 个请求的 Importer 有 3 个不可采；后端显示覆盖与 PARTIAL，成功交集的设置变化仍可查看。容器前缀映射仅在已验证版本和真实 APK 完整路径存在时为 exact，其余明确 candidate/unmapped/ambiguous。

## 开发验证

仓库根目录执行 make unity-test；make unity-validate 另构建两份 Android APK。脚本只使用 .buildsize/unity/BuildSizeValidation，拒绝正在运行的目标工程，不操作其他 Editor。包内测试源会显式装载到该独立工程的 Editor 测试程序集，避免本地包未被默认 testables 发现而“0 项通过”。

验证工程及 APK 是受控样本，不代表用户实际游戏工程、Jenkins 或蒲公英已验收。详见仓库 docs/IMPLEMENTATION_STATUS.md 与 docs/validation/。
