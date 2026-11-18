# 官方技术参考

核对日期：2026-11-17。引用用于确认外部工具/API 的基本语义；本文档中的业务架构、阈值、表结构和任务拆分是项目设计决策。真实适配仍需在项目实际版本上编译、运行和测试。

## R01 — Unity Player 构建回调

来源：`https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Build.IPostprocessBuildWithReport.OnPostprocessBuild.html`

用途：使用 `IPostprocessBuildWithReport` 接收 Player 构建报告；不能把它当独立 AssetBundle 构建的通用回调。早期失败/取消时不能依赖回调一定执行。

## R02 — Unity PackedAssetInfo

来源：`https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Build.Reporting.PackedAssetInfo.html`

用途：确认源 GUID、源路径、类型、packedSize 等公开字段。不能据此假定存在稳定的跨构建逐对象 ID。

## R03 — Unity PackedAssets

来源：`https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Build.Reporting.PackedAssets.html`

用途：确认容器、contents、shortPath 和 overhead。报告容器和 APK ZIP 条目的映射需要本项目额外验证。

## R04 — Unity BuildReport.GetFiles

来源：`https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Build.Reporting.BuildReport.GetFiles.html`

用途：读取构建输出文件信息。最终签名/后处理 APK 的大小仍由上传后的实测文件决定。

## R05 — Unity DetailedBuildReport

来源：`https://docs.unity3d.com/6000.0/Documentation/ScriptReference/BuildOptions.DetailedBuildReport.html`

用途：在发起构建时请求更多报告信息。不把该选项解释成所有 Android 包内容的完整资源归因。

## R06 — Python zipfile

来源：`https://docs.python.org/3/library/zipfile.html`

用途：读取 ZIP 条目、压缩大小、原始大小、CRC 和内容流。实现按选定 Python 版本支持的压缩方法验证，不假定当前在线文档的新增能力在旧解释器可用。

## R07 — Android APK Signature Scheme v2

来源：`https://source.android.com/docs/security/features/apksigning/v2`

用途：签名块并非普通 ZIP 文件条目。因此需要单独的 APK overhead 口径，但不能把所有 overhead 都称为签名。

## R08 — Jenkins Credentials Binding

来源：`https://www.jenkins.io/doc/pipeline/steps/credentials-binding/`

用途：安全注入凭据、避免 shell 回显和不安全插值。掩码不是防止任意泄密的绝对保证。

## R09 — Celery Tasks

来源：`https://docs.celeryq.dev/en/stable/userguide/tasks.html`

用途：确认任务重投递与幂等的必要性。数据库 outbox、lease 和 fencing 是本项目为可靠性增加的设计，不是单开 Celery 开关即可自动获得的保证。

## R10 — Jenkins Pipeline Basic Steps

来源：`https://www.jenkins.io/doc/pipeline/steps/workflow-basic-steps/`

用途：`catchError`、`timeout`、中断处理。新增分析阶段应区分普通失败与用户中止，避免改变原流水线语义。

## R11 — Unity TextureImporter.GetPlatformTextureSettings

来源：`https://docs.unity3d.com/6000.0/Documentation/ScriptReference/TextureImporter.GetPlatformTextureSettings.html`

用途：读取平台覆盖设置；原始/Automatic 配置与实际打包结果须区分。

## R12 — Unity AssetDatabase.GetAssetDependencyHash

来源：`https://docs.unity3d.com/6000.0/Documentation/ScriptReference/AssetDatabase.GetAssetDependencyHash.html`

用途：采集依赖相关哈希；不能将其当作源文件内容 SHA-256。

## R13 — 蒲公英上传与发布

来源：`https://www.pgyer.com/doc/view/api_upload`

用途：区分文件传输与最终发布完成；本平台只关联现有发布流程结果，不保存临时上传签名或账号 API Key。

## R14 — Android apkanalyzer

来源：`https://developer.android.com/tools/apkanalyzer`

用途：APK 属性、Manifest、文件和对比分析。不同命令的大小口径需明确，不能替代本项目的物理 ZIP 对账。

## R15 — YooAsset 构建与报告

来源：

`https://www.yooasset.com/docs/guide-editor/AssetBundleBuilder`

`https://www.yooasset.com/docs/guide-editor/AssetBundleReporter`

用途：确认构建报告和资源/资源包视图的存在。具体报告结构必须依据用户项目 YooAsset 版本和真实样本适配；首版不猜测字段完成“通用”解析。

## 版本与验证提醒

Unity 文档可能重定向到新的官方文档站；必须保留实际读取的 6000.0 版本语义。在线 API 说明不能代替目标 Editor 编译。

没有真实 Unity 工程、APK、Jenkins 环境和蒲公英结果样本时，只能完成通用实现及夹具测试，不能宣称已经通过用户生产环境验收。
