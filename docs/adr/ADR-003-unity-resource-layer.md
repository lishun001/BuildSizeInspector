# ADR-003：M3 Unity 资源层与导出验证

日期：2026-11-17。状态：采用。

## 数据模型与兼容性

保留 v1 Schema/示例，不把 JSON 记录索引当成跨构建对象 ID。新增 0003_unity_layers 迁移，持久化原始容器、原始记录、GUID/类型聚合、容器内聚合、Importer 及独立 Unity 差异表。原始 JSON Pointer 取实际数组位置，不假设 container_index/record_index 等于数组下标。

有效 GUID 按 GUID+asset_type 聚合，保留全部实例和路径；无 GUID 有路径使用 path_fallback；两者都无则按唯一容器完整路径+类型比较未知资源汇总。容器名重复时未知组不可跨版本匹配。完整原始记录仍可检索。聚合 key 是派生记录键，不是 localFileId。

APK 物理差异保持原算法与整数 bytes 恒等式。Unity packed bytes、容器 overhead 和 Unity summary 总量独立保存；不能与 APK 压缩字节相加。真实样本的纹理 packed 增量 61,440 bytes 与 APK 净增量不同。

报告/Importer 合同通过后由 Worker 解释领域语义，覆盖状态按实际解析更新。截断或资源预算错误使该层失败/整体 PARTIAL，基础 APK 分析仍可完成。设置快照部分失败时只对成功交集输出观测变化，并明确 partial；不能把未采到的资源当成删除或把空结果当没有变化。

## 导出器

UPM 包仅包含 Editor 程序集，用内部有界 JSON writer 保留 null/安全整数/UTF-8，不引入 Runtime JSON 包。显式 API 与环境启用的回调共用实现，缺启用信息无副作用。失败不抛出到游戏构建入口，完整性回执最后提交。CI 后续 collect 必须依据该回执/哈希冻结本次报告，不读取未列出的旧文件。

默认 10,000 容器、100,000 记录、50,000 Importer、每 JSON 64 MiB。源文件/.meta SHA 可选且单文件最多哈希 64 MiB。保留 unknown、Automatic 和失败 warning；没有读取所有环境变量或签名凭据。

编译验证发现 GetCompressionType 并非 6000.0.76f1 公开 API：压缩仅从明确 BuildReport 标志读取，其余 null+warning。实际 BuildOptions 有废弃零值别名，Enum.ToString 会把它们混入字符串；改为读取已声明且非零/非废弃的名称，未把 Mono 错写成 Il2CPP。

BuildReport 不足以可靠恢复 options.scenes；保留三参数签名并提供接收本次实际 scenes 的显式重载。缺失时警告，不借用 EditorBuildSettings。回调阶段 Unknown 和尚不可用的完成时间原样保留，由最终构建/CI 补证据。

## 容器映射

真实 Unity 6000.0.76f1、Android/Mono 构建验证了完整 short_path 到 assets/bin/Data/ 前缀的直接关系，包括 GUID 命名的容器。不提取 basename、不猜内部归档。仅已验证版本、唯一候选完整路径为 exact；未知版本 candidate，多候选/重复名 ambiguous，无路径关系 unmapped。每条关系保存 mapper_version、APK SHA 和验证引用。

42 个样本容器中 41 个直接命中，globalgamemanagers.assets 保持 unmapped。这个比例是容器映射覆盖率，不能称为 APK 压缩增长解释率。未验证用户项目或内部归档布局不会升级为精确对象归因。

## 验证边界

实际安装 Unity 6000.0.76f1，无升级、无云关联。独立 URP 验证工程由 CLI 创建，UPM 通过 Client.AddAndRemove 安装本地包；未手改用户 manifest，也未操作另外运行的游戏 Editor。

Unity CLI 首轮编译失败后按实际 API 修复；一次“成功”命令实际发现 0 测试，未记为通过。显式装载同一测试源后，最终 8 项 EditMode 实际通过。macOS 临时路径为符号链接的测试用例已改为工程内 Assets 外的真实路径。

已实际构建至少两次、同 GUID 纹理从 32×32 变为 128×128、验证显式与回调、同 run_id 最终替换及跨 run_id 拒绝、Importer 导出前后不变、没有 BuildSize 程序集进入 APK。完整 API 流程使用真实 APK/报告，CI/Git 字段明确标记为协议测试夹具；不宣称已完成真实 Jenkins/Git/用户工程验收。

[PackedAssetInfo 6000.0](https://docs.unity.com/en-us/engine/6000.0/script-reference/unityeditor/build/reporting/packedassetinfo)、[BuildReport.GetFiles 6000.0](https://docs.unity.com/en-us/engine/6000.0/script-reference/unityeditor/build/reporting/buildreport/getfiles)、[AudioImporter 6000.0](https://docs.unity.com/en-us/engine/6000.0/script-reference/unityeditor/audioimporter)、[TextureImporter 平台设置](https://docs.unity.com/en-us/engine/6000.0/script-reference/unityeditor/textureimporter/getplatformtexturesettings)。依据官方资料和实际 Editor 编译/构建，两者不能互相替代。
