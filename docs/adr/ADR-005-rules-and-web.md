# ADR-005：证据规则、冻结配置与本地 Web 闭环

日期：2026-11-18，Asia/Shanghai。状态：本地闭环已实施并验证，实际结果见 IMPLEMENTATION_STATUS.md。

## 决策

M5 沿用 React/Vite、Ant Design、ECharts 和既有 API/队列。Sites 技能按项目的本地开发范围使用，不创建托管 Site、不发布内部数据；部署仍在 M6。前端依赖使用精确版本和 pnpm 锁文件，后端没有新增依赖。前端通过同源 /api，不向浏览器注入 CI token，不关闭 CSRF。

纯规则引擎实现规格的 12 类规则。全部 finding 固定 additive=false，保留 observed/correlated/hypothesis。APK 总预算/总增长包含容器开销，因此 metric=none，不伪称全是 ZIP 条目贡献；条目规则使用 zip_compressed_bytes，资源规则使用 unity_packed_bytes。增长阈值按整数 bytes 与整数基点进行 AND 比较，避免门禁浮点边界。无基线、跨配置组或不完整关键元数据跳过自动增长门禁，绝对预算独立评估。

只有已定义、两端已知的 Texture/Audio Importer 字段可用于相关证据；未知扩展继续在原始报告中保留，不能触发确定配置归因。源 GUID 新增仅称“新增入包”。native 增长缺少符号映射时只给出假设和控制变量验证建议，不猜 SDK/作者责任。

新增迁移 0005_rules_web：追加 rule_configs、findings，以及项目规则版本、比较的规则版本/快照。自动基线选择、规则快照、findings 与原分析结果在相同项目锁/fencing 发布事务中完成；手动比较在创建时冻结规则，不受排队期间配置更新影响。更新通过 If-Match/行锁控制，只有项目 Web 管理员可改配置/复核；所有读取继续检查项目范围，CI token 无管理权限。

每份比较最多存储 5000 项 finding；引擎继续统计完整候选数并明确 findings_truncated，不把截断当完整原因清单。证据定位至 immutable analysis_revision 或原始产物 SHA，详情解析已知字段/条目/资源并提供授权下载；重复条目路径最多预览 50 个。人工复核状态独立于原始 finding payload、乐观锁和审计，不改写原始置信。

趋势按真实兼容指纹隔离；缺失分析的数据点按声明配置核对后保留 null。为有界读取，每次至多检查最近 200 个候选，返回 candidate_window，不声称完整历史。大表服务端分页，URL 保存过滤状态。图表 tooltip 采用 ECharts richText，用户文件路径/元数据由 React 转义。

## 替代方案与边界

实时按新配置重算旧 URL 会无审计地改变结论，因此保留冻结配置；需要新规则结果时显式重分析或新建比较。直接相加 findings 会重复计数，因此物理增量始终只由既有条目差异与 overhead 给出。缺少源信息不自动生成 SDK/责任归属。

设置页首版只提供预算、增长阈值和固定基线，分类自定义与保留期执行属于 M6/后续范围，避免提供没有执行效果的表单。主包体积警告如实保留，前端性能和生产 TLS/代理/恢复验收不以本地功能测试代替。

## 官方资料

- [Vite 同源代理](https://vite.dev/config/server-options)
- [Ant Design Table](https://ant.design/components/table/)
- [Playwright 配置](https://playwright.dev/docs/test-configuration)
- [ECharts 柱状图](https://echarts.apache.org/handbook/en/how-to/chart-types/bar/basic-bar/)
