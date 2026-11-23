# ADR-007 蒲公英 APK 与 Shell 报告分步提交

## 决策

Jenkins 保留原有蒲公英分发流程。发布确认后，Mac 用 Shell 提交本次 Unity 报告、构建元数据和最终 APK 的长度与 SHA-256，并生成包含 `buildKey` 的本机回执。Linux 操作者从蒲公英下载指定版本，用同一回执调用 `complete-apk`。只有服务端确认报告已上传、APK 与原始摘要相符，才封存并排队分析。

原始构建清单仍要求 APK 和构建元数据，服务端上传协议、幂等键、权限、租约、数据库与分析器均沿用现有实现。分步提交回执带上原始清单，使 Linux 能在报告仍保留时刷新过期上传窗口，无需重新取得 Mac 上的 Git 工作区。补交先核查回执指向已存在的构建，错误 APK 保持待补交状态。

Mac 仍需要访问 BuildSize 来上传报告。蒲公英的 `buildKey` 是下载选择依据，APK 的逐字节一致性以 SHA-256 为准。若分发包字节改变，应调查来源，不能用错误包替换本次冻结清单。

2026-11-20 确认 Mac 不安装 BuildSize CLI，报告只通过 HTTPS 上报。Mac 用 Shell 调用仅依赖 Python 标准库和 Git 的 `unity-report-handoff.py` 生成同一格式的回执。服务端复用原有初始化和产物上传接口，Linux 继续用 `complete-apk`。这一接入保留 Git 提交与 dirty 状态。

## 替代方案与边界

- 原有直接上报 APK 的流程仍受支持，适用于 Jenkins 与 BuildSize 可直接传输大文件的环境。
- 已确认本阶段由操作者在 Linux 端提交下载文件。蒲公英公开安装接口文档限定移动端使用，确认支持 Linux 的官方或内部下载方案后可另行评估自动下载。本次没有新增服务端 URL 拉取接口或蒲公英下载凭据。[蒲公英安装 API](https://api.pgyer.com/doc/view/api_install)
- 已发布结果的展示仍通过独立的 `link-pgyer`。新版回执会校验待关联的发布结果 `buildKey` 与当初登记的一致。

本机可用 Shell、HTTP 和 Worker 验证数据流与重试。实际 Jenkins 任务、蒲公英下载安装包以及生产 TLS 仍需环境验收。
