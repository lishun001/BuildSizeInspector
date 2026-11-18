# 完整 Compose 栈与离线运维

此目录提供 web/api/worker/scheduler/postgres/redis，外加一次性 migrate。根目录 compose.yaml 继续供本机开发进程使用。没有自动修改现有 Jenkinsfile、游戏工程或真实发布流程。

## 启动

需要 Docker/Compose、构建网络及足够磁盘空间。复制 deploy/env.example 到仓库根 .env.deploy，在私有文件中填好配置；不能提交该文件、把密钥放在参数/日志中，或打印完整 compose config。APP_ENV=production 要求 PUBLIC_BASE_URL 为实际 HTTPS origin。DATABASE_URL 使用内置 postgres 主机、buildsize 用户/数据库，密码与 POSTGRES_PASSWORD 一致并按 URL 规则编码；SESSION_SECRET/TOKEN_HASH_PEPPER 至少各 32 字符且不同。TRUSTED_LINK_HOSTS 列出真实 Jenkins/安装页的精确主机。

```bash
make stack-build
make stack-up
```

默认唯一主机端口为 127.0.0.1:8080；API、数据库和 Redis 不发布主机端口。正式环境须由部署者配置真实 HTTPS 入口转发到该地址，保留 Host/Origin，允许最大 2 GiB 请求并匹配上传超时。当前没有配置公网 DNS、证书或真实域名。

只在本机试用时可将私有文件中的 APP_ENV 改为 development，PUBLIC_BASE_URL 改为 http://127.0.0.1:8080。该模式不代表生产 TLS 已验证。不要把开发模式开放到公网。

启动顺序为 PostgreSQL 健康 → migrate 成功 → API/Worker/scheduler → API ready → web。迁移失败或 secret 缺失会阻止启动，不能以健康检查 live 代替 ready。更新镜像后先备份，重新 stack-build/stack-up；现有卷不会被自动删除。

API/Worker 以 UID/GID 10001 运行，web 为 101；应用根文件系统只读、能力移除、禁止提权，Blob 使用独立共享持久卷。既有卷的所有权需管理员核对，不自动递归 chown 用户目录。P0 Worker 并发 1、CPU 上限 4、内存上限 2 GiB（不是已承诺的占用）；API/DB/Redis 有独立资源上限。

NGINX 禁用请求/响应缓冲，上传直接流到 API；SPA 路由回退、CSP/nosniff/禁止嵌框已启用。NGINX 覆盖客户端转发头，API 仅通过该私有网络信任代理；**不要为 API 另行发布端口**。多级 TLS 代理下，默认限流地址可能聚合到内层代理对端，真实客户端地址策略必须按部署拓扑验证，不得直接信任任意外部 X-Forwarded-For。

## 管理员引导

没有默认用户。进入当前完整栈创建账号，密码由终端交互输入：

```bash
docker compose --env-file .env.deploy -p buildsize -f deploy/compose.yaml exec api buildsize-admin create-user admin
docker compose --env-file .env.deploy -p buildsize -f deploy/compose.yaml exec api buildsize-admin create-project demo-game --name 游戏项目
docker compose --env-file .env.deploy -p buildsize -f deploy/compose.yaml exec api buildsize-admin grant admin demo-game --role admin
```

项目 token 按原管理 CLI 创建并立即放入 Jenkins 凭据管理器；不要归档其一次性 stdout。发布关联仍由原蒲公英流程提供安全最终结果。

## 清理：先计划、再复核应用

APK/JSON/派生索引默认最早期限为 30/180/365 天，可通过运行环境配置。所有 current 修订、固定基线、pin、活跃任务和上传保护原始文件；当前/请求中修订及任意比较引用保护派生索引。**达到天数不保证会删，受保护数据可长期保留**。用户已于 2026-11-18 确认：所有历史构建的 current 同时保护原始文件和派生索引，不仅保护项目最新版。

自动删除默认关闭。管理员可在本机 uv 环境使用同一私有环境文件生成计划：

```bash
uv run --project backend --frozen --env-file .env.deploy python -m buildsize.ops retention --project demo-game --output .buildsize/retention-plan.json
uv run --project backend --frozen --env-file .env.deploy python -m buildsize.ops retention --project demo-game --plan .buildsize/retention-plan.json
```

完整栈的 postgres 主机仅能从容器访问，直接使用 scheduler 容器同一模块即可，先在终端设置私有 umask，不开放数据库端口：

```bash
umask 077
docker compose --env-file .env.deploy -p buildsize -f deploy/compose.yaml exec -T scheduler python -m buildsize.ops retention --project demo-game > .buildsize/retention-plan.json
docker compose --env-file .env.deploy -p buildsize -f deploy/compose.yaml exec -T scheduler python -m buildsize.ops retention --project demo-game --plan /dev/stdin < .buildsize/retention-plan.json
```

临时文件计划也可用同样的容器和 stdin 方式传入。应用计划前先实际审查其过期对象和保护列表。

应用会在项目锁中按原 as_of/配置重新计算全部引用、pin 和保护，并绑定规范存储根/卷身份；变化后拒绝旧计划，不能把旧计划套到另一份数据卷。引用只做过期标记；共享内容仍被其他引用或保护构建使用时保留。无活引用 Blob 先标记，至少 24 小时后生成新计划才可删除。保留墓碑、哈希、清单、已存指标；过期引用下载/重分析返回 410。原始文件不会被改写为新内容。

current/比较引用一直存在时，派生索引也不会按天数强删。无引用旧修订清理后明确 EXPIRED/unavailable，不以空条目比较。单项目清理最多 10,000 构建、100,000 Blob/修订，超限整体拒绝而非截断后误称完整。

临时半文件是独立全局操作：python -m buildsize.ops temps --output 私有计划，再用 temps --plan 同一文件。仅管理 .tmp 中规范普通 .part 文件，文件超过一天且超过上传期限、没有任何活跃上传租约才可清理；inode/mtime/长度和租约必须仍一致。软链接、未知文件名不删除。所有计划/应用都有审计。未登记为数据库 Blob 的正式文件不会被自动删除；应先核对来源，不直接对卷执行 find -delete/rm -rf。

UI 构建详情支持管理员 pin；不能恢复已过期产物。项目设置显示实际保留配置。管理员诊断为 GET /api/v1/projects/{key}/diagnostics，包含任务状态、排队年龄、已登记未删除 Blob 字节和过期引用数；它不是文件系统占用扫描。运维 CLI python -m buildsize.ops diagnostics 提供本机同类摘要。

## 备份与恢复

先暂停外部写入、停止应用，但保留 PostgreSQL/Redis：

```bash
make stack-stop
uv run --project backend --frozen python tools/stack_backup.py backup --project buildsize --env-file .env.deploy --directory .buildsize/backups/release-001
```

目录必须不存在。工具核对 API 连接与内置 PostgreSQL 一致，确认应用已停，再 pg_dump 和流式 Blob 打包；包含数据库、规范 Blob、清单和 SHA。备份目录权限 0700，不包含 .tmp、运行密钥或未登记垃圾。数据库与 Blob 必须一起恢复；SESSION_SECRET/TOKEN_HASH_PEPPER 等原私有配置另外加密保管，否则原 token/会话不可恢复。

恢复只支持**受信任的本工具备份**，校验哈希不等于验证来源。使用独立 Compose 项目和空数据库、空 Blob 卷；不得直接指向已有生产数据：

```bash
uv run --project backend --frozen python tools/stack_backup.py restore --project buildsize-recovery --env-file .env.deploy --directory .buildsize/backups/release-001
docker compose --env-file .env.deploy -p buildsize-recovery -f deploy/compose.yaml up -d --wait
```

现有目标不为空即失败，不提供 force 覆盖。恢复时逐文件校验规范键、数据库登记、长度和 SHA，拒绝目录穿越、链接、重复项，不 extractall。任何失败都保留失败状态，不把半恢复卷当成功；重新使用新的空卷检查。Redis 队列由数据库 outbox/lease 恢复，不把缓存快照当任务事实。

恢复后的检查必须包含 ready、实际登录、历史比较、授权下载、旧 token 和一次重分析。本机构建/恢复演练已覆盖这些步骤；真实业务数据、外部 TLS 和灾备目标仍需部署验收。

## 可复现验证

```bash
make stack-build
make stack-check
make benchmark
```

后二者只操作随机 bsi-stack-test-* 项目、私有随机凭据和临时卷，结束即清理，不启动常驻用户服务。没有浏览器时 stack-check 将生产浏览器项标为 NOT_VERIFIED，其他可验证步骤继续；本机实际 Chrome 已通过生产静态包/CSP/pin/保留说明。若执行失败，.buildsize/stack-failure.log 保留脱敏诊断。

benchmark 产生精确 500 MiB/20,000 ZIP 条目与 50,000 条 Unity JSON，经过真实上传、prefork Worker 和两种分页各 50 次测量。报告 .buildsize/performance.json，明确为合成夹具；不代表真实 Unity APK、独占 SSD 或生产 SLA。CPU、内存配额、VM 规格、执行进程 RSS/cgroup 峰值分别记录；它们不能相加。临时大夹具会清理，报告保留。

验收矩阵见 docs/validation/ACCEPTANCE_MATRIX.md，正式接入前核对所有 NOT_VERIFIED 项。
