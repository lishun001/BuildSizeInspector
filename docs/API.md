# M2–M5 API 使用约定

API 前缀为 `/api/v1`，交互式合同见运行服务的 `/docs` 和 `/openapi.json`。CI 使用项目 Bearer Token，浏览器使用 HttpOnly 会话。不要把凭据放入命令参数、URL 或日志；令牌在程序内从凭据环境注入请求头。

## 上传闭环

自动下载使用同一清单和上传接口。Jenkins 上传报告角色后，调用 `POST /builds/{id}/finalize-from-url`，请求体为 `{"download_url":"https://apk.example.com/path/game.apk"}`。同项目 `builds:submit` 权限和完整的 Unity 报告是必要条件，APK 角色仍提供原文件的长度和 SHA-256。返回 202 后，Worker 下载、校验、封存并分析，详情接口可查看状态。相同链接的并发请求复用原任务；任务失败后可提交更新后的链接重试。同一清单已封存时，旧请求返回同一任务。

下载目标由 `APK_DOWNLOAD_HOSTS` 限定。链接通过请求体传输，待下载时加密保存，在封存成功或任务最终失败后清除。响应、日志和既有回执均不包含链接。构建详情中的 `apk_download_pending` 表示当前任务等待后台取得 APK。

1. `POST /projects/{key}/builds`：JSON body 为 submission Schema；`Idempotency-Key` 必须等于该 run_id。新建 201，相同身份与清单重试 200，清单/身份冲突 409。
2. `PUT /builds/{id}/artifacts/{role}`：流式二进制 body；Content-Type 为 application/octet-stream，Content-Length 和 X-Content-SHA256 与清单一致。成功 201，已验证相同角色 200。失败不登记未验证半文件，客户端整文件重传。
3. `POST /builds/{id}/finalize`，body 为 `{}`：全部声明产物已验证后封存，并在同一数据库事务写入初始分析任务。成功/重试 202，不等待 Celery 完成。重复 finalize 不创建新修订或新任务。
4. `GET /builds/{id}`：查询 upload_state、analysis_state、当前/请求中的修订、覆盖与任务。任务列表支持 limit/offset，默认 50、最大 200，并返回 jobs_next_offset。

202 表示已接受，完整 ZIP CRC/解压校验由 Worker 完成。分析失败会保留稳定 error_code；没有旧成功结果时 result 为 null。缺失或部分成功的 Unity/Git 层可能使项目期望覆盖为 PARTIAL，不能把它解释为 ZIP 指标缺失。

分步提交复用上述接口。Jenkins Shell 先初始化包含最终 APK 摘要的完整清单，并上传报告角色。此时 `GET /builds/{id}` 中 APK 为 `pending`，构建仍为 `UPLOADING`。Linux 的 `complete-apk` 校验下载的 APK 后补传 `apk` 角色，再调用 finalize。两个阶段之间传递的 CLI 回执含蒲公英 `buildKey`，无需新增服务端下载接口或改变现有 API 合同。

服务端 report_url 保留 Web 路由约定；M5 页面尚未实现，当前通过上述 API 查询结果。所有 URL 来自 PUBLIC_BASE_URL；CI 来源链接必须在 TRUSTED_LINK_HOSTS 精确允许列表中且不含凭据。

## 查询与比较

| 接口 | 行为 |
|---|---|
| `GET /projects/{key}/builds` | 按构建完成时间列出历史，大小在尚未成功分析时为 null |
| `GET /builds/{id}/entries` | 条目分页；q 为字面路径搜索，sort 仅 path/compressed_bytes；revision_id 可固定翻页修订 |
| `GET /builds/{id}/artifacts/{role}/download` | 鉴权后流式下载，不返回存储路径 |
| `POST /comparisons` | body 含 base_build_id、target_build_id；需 UUID Idempotency-Key，返回 202/冻结 comparison_id |
| `GET /comparisons/{id}` | 双方分析修订、可比性、选择原因和物理指标 |
| `GET /comparisons/{id}/changes` | 服务端分页，默认排除 UNCHANGED；可按状态、字面路径和增量排序 |
| `POST /builds/{id}/reanalyze` | 管理员、空对象 body、新 UUID Idempotency-Key；相同键复用修订 |

列表 limit 默认 50、最大 200，offset 非负。changes 的 sort 仅 delta/absolute_delta/old_bytes/new_bytes。构建详情的 metadata 是有计数的有限预览；完整元数据应下载 build_meta 产物。目录和重复内容不会在详情中一次返回全部路径。

上传和重分析需要项目管理员或相应提交权限；CI token 不能执行管理员重分析。viewer 可读取已授权项目并创建派生比较，不能上传或修改原始构建。所有浏览器写操作需要正确 Origin 和 X-CSRF-Token；手动比较必须同时具备两端项目权限，并限制同项目。

重新分析排队时旧 current 继续可读；新修订全部成功后原子切换。失败保留旧结果。原 APK 已过期时重分析 410，已有索引仍可用于版本比较。没有基线返回 NO_BASELINE，delta_apk_bytes 为 null。跨兼容组比较仍可计算物理差异，但增长门禁 SKIPPED。

当前未接通规则 findings 和 Web 页面；未实现路由返回 404，不返回演示业务成功。CI 客户端与蒲公英关联见下文。

## 关键错误

错误均为 error.code/message/request_id/details；不回显凭据或完整非法请求内容。

| 状态 | 典型 code |
|---|---|
| 401 | UNAUTHENTICATED |
| 403 | CSRF_REJECTED |
| 404 | NOT_FOUND；不存在与无权限使用相同响应 |
| 409 | BUILD_IDENTITY_CONFLICT、ARTIFACTS_INCOMPLETE、UPLOAD_STATE_CONFLICT、ANALYSIS_NOT_AVAILABLE、DISTRIBUTION_CONFLICT |
| 410 | BLOB_UNAVAILABLE |
| 411 / 415 | CONTENT_LENGTH_REQUIRED / CONTENT_TYPE_INVALID |
| 413 | UPLOAD_TOO_LARGE、JSON_LIMIT_EXCEEDED |
| 422 | ARTIFACT_HASH_MISMATCH、ARTIFACT_LENGTH_MISMATCH、EXTERNAL_LINK_INVALID、协议/JSON 错误 |
| 429 | UPLOAD_QUOTA_EXCEEDED、LOGIN_RATE_LIMIT |
| 503 | SERVICE_UNAVAILABLE、STORAGE_IO_ERROR |

## M3 Unity 层

- GET /builds/{id}/unity-assets：GUID/类型聚合，scope=container 可看容器内聚合；source_guid 或 q 筛选，分页最多 200。
- GET /builds/{id}/unity-records：原始记录、原始 GUID/路径、容器/记录索引和准确 JSON Pointer；允许按 GUID/容器索引过滤。
- GET /builds/{id}/unity-containers：原始 short_path、overhead、映射状态及 mapper/evidence。
- GET /builds/{id}/asset-importers：成功/失败、已定义设置、各类哈希、unsupported_fields 与原始 Pointer；未知扩展完整保留于产物。
- GET /comparisons/{id}/changes?layer=unity_asset 或 asset_importer：独立差异；默认 APK 层行为保持不变。
- GET /comparisons/{id}/unity-changes?kind=asset 或 importer：相同的 Unity 专用分页入口。

构建查询均支持 revision_id 固定不可变修订。Unity packed 指标为 unity_packed_bytes，additive_to_apk=false；Importer 设置差异 metric=none，大小为 null。原始 JSON SHA 随查询返回，Pointer 以实际数组位置定位，不等于报告自带索引。

READY 表示项目期望的数据层成功，不表示 GUID/映射覆盖率为 100%。来源未知记录保留，覆盖率独立显示；无记录时比例为 null。报告缺失/截断不会推导资源新增或删除。Importer 部分失败时成功交集的观测设置差异可返回 partial，完整性缺口仍保留；没有可用基线则明确 unavailable/NO_BASELINE。

## M4 CI 与 Git/发布结果

`GET /projects/{key}/baseline-candidate` 需要 builds:read 和项目授权。查询参数包括 application_id/channel/variant/ci_instance_id/ci_job_full_name/build_completed_at，及参与兼容组的 branch/development_build/scripting_backend/architectures（数组以重复参数传入）。完成时间必须带时区。硬条件缺失、无历史或不合理时钟返回 NO_BASELINE；匹配时返回 CANDIDATE、base_build_id、analysis_revision、仓库 revision/dirty/kind。候选使用同一固定基线/最接近较早构建策略，尚未冻结最终比较，不得直接作为责任证据。

`GET /builds/{id}/git-changes` 返回上报的原始 Git 范围及文件变化，支持 repo_key/revision_id/limit/offset。每条含 base/head、range_status、状态、旧/新路径、blob ID、source_guid；不返回源码或作者。此接口是构建的原始采集证据，不能假定适用于任意手动基线。比较结果中的 git_evidence_status、git_matched_repositories、git_change_count 才是核对两端后的状态/计数。NO_BASELINE/missing/mismatched_range 的计数为 null，complete 的真实空差异为 0；dirty、非祖先或部分缺失为 partial。仅匹配仓库范围可用于当前比较，findings 不可把提交列表当定量根因。

`PUT /builds/{id}/distribution/pgyer` 需要 distribution:write 或 Web 管理员，浏览器同时验证 CSRF/Origin。仅允许 SEALED 构建。body 五个字段全部显式提供：

```json
{"publish_state":"failed","build_key":null,"build_version":null,"install_page_url":null,"published_at":null}
```

publish_state 支持 publishing/published/failed/not_published。published 必须有 build_key、可信安装页及带时区 published_at；其他状态的安装页/发布时间必须 null。未知字段及带凭据/签名 URL 拒绝。普通状态可更新，同内容返回 200；已 published 的不同结果返回 409，不改写原关联。构建详情的 distribution.pgyer 返回这五个安全字段，原始发布响应不入库。CI 客户端缺少 receipt 会直接失败，不查找最近构建，也不请求蒲公英 API。

## M5 规则、证据与同组趋势

- `GET /projects/{key}/rules` 返回 `{version, settings}` 与 ETag。settings 只包含 absolute_budget_bytes（null 为未配置）、growth_bytes、growth_basis_points（100 基点 = 1%）、fixed_baseline_id（null 为自动）。
- `PUT /projects/{key}/rules` 只允许 Web 项目管理员，带 Origin、CSRF 和准确的 `If-Match: "版本号"`；缺失/过期版本返回 409 VERSION_CONFLICT，不自动覆盖。配置追加新版本，固定基线必须为本项目已有成功分析的构建。增长按 bytes 和基点同时满足，全部只给 advisory，不改变 Jenkins 发包状态。
- `GET /comparisons/{id}/findings` 支持 limit/offset、confidence、review_state、rule_id、字面对象 q。返回 finding（原 v1 合同）、id、review_version，另返回冻结 rules_version/rules_snapshot 与 truncated。最多存储 5000 项；总计数和截断状态保留，不把截断当完整结果。
- `GET /findings/{id}` 返回同一 finding 和 evidence：按 SHA/分析修订解析原始条目、重复内容路径预览（最多 50）、已知 Importer 字段、Unity GUID/类型聚合、覆盖和配置字段。来源包含 build_id/revision_id 与适用的授权 download_url，不返回磁盘路径。unsupported/过期资料可明确 unavailable；不从 URL 拉取数据。
- `PUT /findings/{id}/review` 接受 `{review_state}`，仅 Web 项目管理员，需 CSRF/Origin 和 If-Match 为当前 review_version；成功加一，冲突 409。复核独立存储并记录审计，不改写原始 payload 或 observed/correlated/hypothesis。
- `GET /projects/{key}/audit` 仅 Web 管理员，稳定分页返回 actor/action/object_id/时间，不暴露会话、token 或配置原文。
- `GET /projects/{key}/trend` 可指定 reference_build_id 和 limit（默认 30，最大 100）。只返回其硬兼容组，数据点的未分析大小为 null；没有可比配置返回 NO_COMPATIBLE_DATA/INCOMPLETE_PROFILE，不能绘成零值。候选窗口 200，响应明确 candidate_window。

构建列表新增 build_number、compatibility_key、comparison_id、delta_apk_bytes、finding_count；无基线或旧修订的未知指标为 null。项目详情返回当前授权 role 和配置环境。原三步上传、鉴权下载和客户端幂等语义保持不变。

自动比较在同一项目锁和 fencing 发布事务中冻结基线/规则并写 findings；手动比较在创建时冻结规则。已发布报告不随配置改变；需要新的规则结果应新建比较或重分析。quality_gate 为 PASS/WARN/SKIPPED，absolute_budget_gate 独立，APK 总量类规则 metric=none（因为总量含容器开销）。两类门禁都不使 CI 失败。

## M6 保留保护与诊断

BuildState 增加 pinned；产物状态增加 expired_at。过期状态不修改封存清单、长度、SHA 或原始内容身份；已过期引用的下载/重分析返回 410 BLOB_UNAVAILABLE，即便同一 Blob 仍因别的构建被保留。已存可用索引继续用于比较；无引用旧索引清理后为 EXPIRED，指定该修订查询明确 unavailable，不返回伪造空结果。

PUT /builds/{id}/pin 接受 `{pinned: true|false}`，仅 Web 项目管理员、Origin/CSRF，保留审计；相同状态幂等。pin 保护仍存在的内容，不恢复已过期产物。项目详情额外返回实际 retention_days 和 raw_protection，前端项目设置只读展示全局运行配置。

GET /projects/{key}/diagnostics 仅管理员，返回项目任务状态计数、最早排队时间、已登记未删除 Blob 字节与过期引用数，不能据此推断未知磁盘文件占用；不输出连接地址、令牌或 secret。

清理不接受公共 HTTP 路径或远程 URL，使用本地运维模块。保留期限/两阶段 GC/计划复核/审计及离线恢复见 deploy/README.md。生产 NGINX 限制和同源策略不替代 API 的项目权限与文件预算；API 不可另行公开端口来绕过受信任代理边界。
