# 给 Codex 的启动提示词

将本开发包中的文件放入目标仓库。已有同名文件时先合并，不直接覆盖。然后把下面文本发送给 Codex。

---

请根据本仓库的开发文档实现 BuildSize Inspector。

先阅读 AGENTS.md、docs/DEVELOPMENT_SPEC.md、docs/TASKS.md，以及 schemas/ 和 examples/。先检查仓库现状和未提交改动，不得覆盖原有文件或修改真实项目的发布流程。

目标：保留现有 Jenkins 和蒲公英分发；Jenkins 构建后上传最终 APK、Unity BuildReport 和构建元数据。平台进行精确的 APK 体积对比、Unity 资源定位、构建配置比较和有证据的原因分析。

按 M0–M6 分阶段实施，先完成可测试的 APK 分析器与上传闭环，再接 Unity/Jenkins 和 Web。首版不做 AI、AAB、IPA、完整 YooAsset 适配或微服务扩张。

请直接开始实施，不要只重复设计方案。先给出本阶段要改动的文件和验收命令，再执行。若环境支持，完成测试并修复失败；没有 Unity、Jenkins 或 Android SDK 时，继续实现可验证的部分，将真实环境验证标记为 NOT_VERIFIED，不得假装已经验证。

必须遵守：
1. APK 字节增量能严格对账；Unity packedSize 不能当成 APK 压缩增量。
2. 同构建重复上传和任务重试必须幂等；原始数据不可被静默覆盖。
3. 基线按兼容条件和构建时间选择；配置变化要可见。
4. 结论绑定证据，区分已观测事实、相关线索和待验证假设。
5. 检测故障默认不能影响原有发包，也不能吞掉用户中止或原构建失败。
6. 所有页面使用真实接口；演示数据单独标识；权限和安全限制不能省略。

每个里程碑完成后更新 docs/IMPLEMENTATION_STATUS.md，说明已实现功能、实际执行的测试、未验证项和下一任务。需要改变规格时写 ADR，不要悄悄换技术栈或字段。
