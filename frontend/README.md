# BuildSize Inspector Web

React/Vite、Ant Design 与 ECharts，使用真实同源 API，不内置演示构建、默认账号、token 或业务 mock。页面覆盖登录、项目同组趋势、构建详情、APK/Unity 分页、版本比较、配置差异、证据解析、管理员规则/复核/审计。所有大小为 MiB 展示，复制按钮复制整数 bytes。

## 本地运行

在仓库根目录按 OPERATIONS.md 完成数据库迁移与账号/项目授权，.env 设置 PUBLIC_BASE_URL=http://127.0.0.1:5173，分别运行：

```bash
make dev
make worker
make scheduler
make web
```

前三条在独立终端执行；web 监听 127.0.0.1:5173，将 /api 代理至本机 8000，保留 Origin。前端、API 的浏览器 origin 必须一致，不能通过关闭 CSRF 或开放 CORS 修复配置错误。CLI Bearer token 不注入页面；浏览器只使用 HttpOnly Cookie，CSRF 保留于会话内存。

```bash
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend typecheck
pnpm --dir frontend build
make browser-test
```

build 的输出在 frontend/dist。当前未发布到外部服务；NGINX 同源容器托管已实现并验证，见 deploy/README.md；外部 TLS 和正式生产部署仍未验收。构建工具会提示当前 UI/charts 主包大于 500 kB，未调高警告阈值来隐藏问题；性能与后续代码分包待实际数据测量。

## 浏览器测试

make browser-test 创建一次性 PostgreSQL/Redis，运行真实 Uvicorn、Vite、prefork Celery 与 dispatcher，再执行 Playwright 两条完整场景。登录后经浏览器共享 Cookie/CSRF 上传明确的合成 ZIP，等待真实异步分析，查看两层比较/原始证据、分页、手动版本选择、复核与规则；另外验证 viewer 只读、实际跨项目构建拒绝和首版无基线。包含恶意样式文件路径的转义检查与 390px 布局检查。无业务 mock，也不是实际 APK 安装或性能验收。

本机默认复用已安装 Google Chrome，不下载或改动用户 Editor。其他机器设置 BUILDSIZE_BROWSER_EXECUTABLE 为 Chrome/Chromium 的实际路径，或执行 pnpm --dir frontend exec playwright install chromium 后设置 BUILDSIZE_PLAYWRIGHT_INSTALLED=1。缺服务/浏览器时 pytest 明确跳过，不声称通过。Playwright 由隔离 Python fixture 启动；不要直接在真实生产环境指定测试账户运行。

报告 .buildsize/browser-results.xml、截图 .buildsize/web-desktop.png。失败截图/上下文在 frontend/test-results/，已忽略，可能包含测试账号输入，不归档公开。所有子进程、自建容器和临时卷由测试入口清理。make e2e 和完整容器测试也包含浏览器场景。

## 数据边界

- 趋势仅连接同兼容组，未分析项为 null，查询窗口最多最近 200 个候选；不会混合 ABI/Development/backend/分支。
- APK 分类与 overhead 相加对账；Unity packed 与 findings 不参与该物理账。
- 无基线、缺失、不支持、部分成功、失败和无权限有独立状态。元数据未经 Android 增强工具核验的事实保持可见。
- URL 保存构建、比较、tab、路径搜索、分页、排序和置信筛选。大表服务端分页，不把全部条目拉到浏览器。
- 规则更新使用 If-Match；旧比较保留冻结版本，复核仅修改独立状态并记审计，不提高因果置信。

六类 v1 Schema 仍由 scripts/generate-contracts.mjs 生成到 src/contracts，禁止手改。make typecheck 检查其一致性；业务响应类型在 src/types.ts。
