# ADR-001：M0/M1 的工程边界与复现方式

日期：2026-11-17。状态：采用。

## 背景与决策

初始目录是开发规格包，没有应用源码、Git 元数据或既有游戏构建入口。本轮按照 TASKS 的依赖顺序落实 M0 基础与 M1 离线纵向流程。M2–M6 继续保留为待实施任务；不生成上传成功占位接口或静态业务页面。没有执行 Git 初始化、提交或改动真实发包流程。

服务端沿用 Python 3.12、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、PostgreSQL 16。后端用 uv 管理，`backend/uv.lock` 固定直接和传递依赖，所有复现命令使用 `--frozen`。本机实际解释器为 Python 3.12.0，记录在 `backend/.python-version`。测试使用 pytest/Hypothesis。前端目前只生成六类协议类型，以 pnpm 锁文件固定工具，React 页面在 M5 实现。

应用配置不允许把 SQLite 作为正常服务数据库。SQLite 仅供 `APP_ENV=test` 的隔离迁移/鉴权测试；另外提供真实 PostgreSQL 16 的独立 Schema 测试。Docker daemon 初始未启动，本轮通过已安装的 Docker Desktop CLI 启动后，已实际通过 PostgreSQL 迁移、并发唯一键、登录和项目授权验证。开发 Compose 暂时只包含 PostgreSQL，镜像固定为 `postgres:16.11-bookworm` 与已核验的 manifest digest；完整服务编排留到 M6。

采用带项目作用域的 HMAC token、服务端会话、Argon2id 密码与 CSRF origin/header 校验。无默认管理员或默认密码，CLI 引导创建用户/项目/授权。CI token 仅能查询授权项目；Web 项目角色为 admin/viewer。

## ZIP 实现限制

Python `zipfile` 用于读取目录与标准条目属性；压缩流独立按块解码并验证实际长度、CRC 和 SHA-256，防止声明长度伪装隐藏解压量。没有把压缩数据解到磁盘。API/数据库不参与算法计算。

P0 分析器支持 APK 使用的 Stored/Deflate、标准 ZIP64 和 data descriptor。其他压缩方法、多卷 ZIP、带扩展扇区的 ZIP64 结束记录明确拒绝。中央目录默认额外限额 64 MiB，避免仅限制条目数量却仍分配过多元数据。该限制可以由 `Limits` 调整。全部大小计算为整数 bytes；目录只是聚合视图，不参与二次求和。

默认分类器识别 `assets/bin/Data/`，不据此声称完成了 Unity 源资源或 Bundle 内部解析。项目自定义 Bundle 精确前缀及其相对 Unity Data 的优先级参与分类版本指纹；不同版本拒绝直接比较。可选的一对一改名候选尚未实现，物理新增/删除账保留完整。

APK 签名块不单独推断。文件长度减条目压缩字节得到 overhead，不能把整个 overhead 写成签名大小。没有基线时增量为 null/NO_BASELINE。

## Android 增强解析

只执行部署者配置的本地工具路径，通过 SDK 的 `source.properties` 记录工具版本。没有自动安装 SDK，也没有执行 `--version` 等未核实命令。字段读取使用官方 `manifest application-id/version-code/version-name/min-sdk/target-sdk` 子命令。

外部工具需禁网沙箱、CPU/地址空间/文件输出限制、父进程总期限与有界 stdout/stderr。没有沙箱或硬资源限制不可用时返回 unavailable，不退回无限制执行。本机 macOS 实测无法设置 `RLIMIT_AS`，因此本机增强解析不宣称已可运行；真实 SDK 及 Linux bubblewrap 运行仍为 NOT_VERIFIED。进程管道、输出限制与超时终止有独立子进程测试，这些测试不能证明 Android SDK 已联调。

## 影响与替代方案

当前可直接使用离线 CLI，但还不能完成 Jenkins 上传到 Web 展示的全流程。先验证物理字节账，可以在接入真实 APK 后独立交叉检查，避免上传、映射或页面错误掩盖计算错误。

另一种路径是先搭完整页面和任务占位接口；它无法验证真实增量，不采用。生产数据库选型保持 PostgreSQL，SQLite 仅是隔离测试工具。下一步实施 M2 的流式存储、三步上传、outbox 和修订发布。

## 核对来源

[Python 3.12 zipfile](https://docs.python.org/3.12/library/zipfile.html)、[FastAPI 安全](https://fastapi.tiangolo.com/tutorial/security/)、[SQLAlchemy 2 约束](https://docs.sqlalchemy.org/en/20/core/constraints.html)、[Android apkanalyzer](https://developer.android.com/tools/apkanalyzer)。实际字段和调用还通过本地类型检查、编译导入与测试核验。结果详见 IMPLEMENTATION_STATUS.md。
