# AGENTS.md — BuildSize Inspector 开发约定

这是 Unity APK 包体变化检测平台的代码代理约定。对已有仓库使用时，先遵守原有更高层约定，再合并本文件内容；不得直接覆盖用户已有 AGENTS.md。

## 阅读顺序

`docs/DEVELOPMENT_SPEC.md` → `schemas/` 与 `examples/` → `docs/TASKS.md`。

目标是 Jenkins 上传最终 APK 和 Unity 报告，平台给出准确的版本增量与有证据的解释。蒲公英继续负责现有分发。首版不做 AI、AAB、IPA 和完整 YooAsset 适配。

## 实施纪律

按 M0–M6 实施可运行的纵向流程。先算法和合同测试，再 API、Unity/CI、页面。必须检查现有仓库、未提交改动和运行环境，不得删除用户文件、覆盖 Jenkinsfile、升级用户 Unity 或改动真实发布流程。

不确定的实际 API 必须查官方文档或编译核验。无 Unity/Jenkins/Android SDK 时完成其余可验证工作，并把对应外部验证标成 NOT_VERIFIED。不得伪造编译、测试、部署或性能结果。

## 数据底线

- 所有存储和计算使用整数 bytes；展示 MiB。
- APK 实测大小 = ZIP 条目压缩大小合计 + 容器额外开销。
- Unity packedSize 不是 APK 压缩贡献；两层不能加在同一个增量图里。
- 缺失用 null/状态，不用 0；无基线不是零增长。
- 不存在可靠对象 ID 时不要虚构 localFileId；按 GUID/类型/容器聚合并保留原始记录。
- findings 不可相加；原因必须携带证据和 observed/correlated/hypothesis 等级。
- 同一次构建重试不新增构建；同身份不同内容返回冲突。
- sealed 原始产物不可改写；重新分析创建新修订。

## 工程底线

API、分析器、存储、规则、前端分层。解析器不要依赖 HTTP 或 Celery，业务模型不要绑死对象存储厂商。

采用流式文件处理和有界解压；不 extractall、不 shell 拼接命令、不执行上传内容、不从任意 URL 拉文件。所有访问包含项目授权。

凭据不进入代码、参数日志、README 示例和提交历史。Jenkins 新增分析故障默认只告警；不能吞掉游戏构建失败或用户中止。

必须提交可复现测试、锁文件、迁移和运维说明。不能为了通过测试删除约束、把错误捕获后返回成功、把 mock UI 当成真实功能。

## 决策与进度

规格冲突或技术变更写 `docs/adr/`，说明影响和替代方案。每个阶段更新 `docs/IMPLEMENTATION_STATUS.md`，列出真实测试命令、结果与未验证项。

提交前检查：协议示例、单位/幂等/权限/异常测试、类型检查、前端构建。具体命令以已实现的项目入口为准。
