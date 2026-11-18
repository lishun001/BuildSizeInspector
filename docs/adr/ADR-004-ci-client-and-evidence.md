# ADR-004：冻结 CI 客户端与可核对的 Git/发布证据

日期：2026-11-17。状态：已实施本地可验证部分；真实 Jenkins/蒲公英 NOT_VERIFIED。

## 背景

M4 需要在现有发包流程旁路上报，兼顾重复任务、断网重试、多仓和“候选基线与最终比较可能不同”的事实。不能让服务端读取私有 Git 或接管蒲公英 Key。M3 的 Unity Player 结束时间也不一定是最终签名 APK 的完成时间。

## 决策

- 增加共享合同的 Python client 子包和五个 CLI 命令，不添加 HTTP SDK 依赖。http.client 流式发送，TLS 默认验证；不跟重定向。连接/408/429/5xx 有限重试，总期限由 monotonic 时间控制，认证和协议错误不盲目重试。
- prepare 从 logical key 的规范摘要与固定 namespace 派生 UUIDv5，并原子持久化上下文。这是身份标识，不是凭据。相同逻辑任务跨本地路径仍具有同一 run_id；CI 实例/job/build/variant 改变需要新上下文目录。服务端唯一约束继续判定同身份不同内容冲突。
- collect 流式复制最终明确 APK；只采集同 run_id 完成回执列出的 Unity 文件，核对原始与复制后的 SHA/长度；最后发布冻结目录。submit 不重新生成元数据，只补未完成角色并封存；没有字节断点续传。
- 显式最终 APK/CI 完成时间覆盖 root 元数据的“完成”口径，原 Unity Player 时间保留于本地冻结 settings；两者不同记录 warning。其他实际 Unity 设置与受控声明冲突仍拒绝，不能借时间例外覆盖版本/ABI 等证据。
- 候选基线查询复用 Worker 的兼容组/时间/固定基线策略，限定已成功 APK 分析及同可信 CI 来源。候选不是冻结承诺。最终比较只使用其每仓 base/head 与实际两端一致的上报范围；dirty/非祖先/部分缺失保留 partial，不归因责任。
- 服务端持久化 repository_snapshots、git_ranges、git_file_changes、distribution_links，迁移为 0004_ci_links；新 parser set 包含 git-1:m4-1。原产物和旧修订不可改写；旧构建的源版本仍可从其不可变 build_meta 获取，新表不回填猜测值。
- 客户端 Git 仅本地只读、NUL 路径协议，不上传身份信息/源码/remote。Git 每命令 30 秒/64 MiB；仓库最多 200 个，key 最长 128 字符，差异最多 100,000、提交最多 10,000；.meta 单 blob 最多 8 KiB。超限拒绝或明确失败，不裁剪后宣称完整。限制是共享模型的资源/语义校验，不改变 v1 线格式。
- 蒲公英仅关联五个安全字段，限定独立 distribution:write scope 和项目授权；API 不拉取任何发布 URL。最终 published 必须有版本标识、安装页和时间，已成功结果只能同内容幂等。先传输成功但尚未发布不能显示安装成功。
- Jenkins 只提供可合并示例，原游戏/发布入口放在检测捕获范围外。新增普通错误只告警；FlowInterruptedException 重抛，CLI 130/143 保持 ABORTED。凭据、解析、归档等新增操作也在隔离范围内。每构建号/variant 独立目录，避免长期 workspace 复用旧 context。

## 替代方案与影响

随机 run_id 每次执行会制造重复任务；只在文件中随机生成则跨 workspace 恢复身份困难。UUIDv5 加持久化与服务端唯一约束覆盖两种情况；同 logical key 重新构建不同内容必须换新构建号。

服务端自动 fetch 能补历史，但会引入私有 Git 凭据和网络执行边界；本版保留 missing_base，由原检出流程决定是否补历史。保存原始蒲公英响应便于排错，但会扩大凭据暴露，因此只保存白名单结果。

真实 Jenkins sandbox/插件、实际发布最终结果适配仍需外部验收。本机使用真实 Git、CLI、HTTP、PostgreSQL 和原有异步 Worker 验证数据闭环，不宣称完成真实 CI 或蒲公英发布测试。

## 官方依据

- [Python 3.12 http.client](https://docs.python.org/3.12/library/http.client.html)：低层请求/连接/响应和流式发送。
- [Git diff](https://git-scm.com/docs/git-diff)：--name-status、-z 与 -M 的路径/重命名语义。
- [Jenkins Pipeline basic steps](https://www.jenkins.io/doc/pipeline/steps/workflow-basic-steps/)：错误与中断处理。
- [Jenkins Credentials Binding](https://www.jenkins.io/doc/pipeline/steps/credentials-binding/)：环境注入和 shell 字符串中的凭据处理。
