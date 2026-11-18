# A01–A32 验收证据矩阵

日期：2026-11-18。DONE 表示当前受控本地测试有实际断言；不是生产业务签收。最新命令与数量见 IMPLEMENTATION_STATUS.md；证据主要在 backend/tests/、前端 Playwright 与 .buildsize 报告。所有 ZIP/性能夹具显式区分于 M3 真实 Unity APK。

| ID | 状态 | 证据与边界 |
|---|---|---|
| A01 | DONE | test_apk.test_stored_independent_accounting_and_no_baseline；test_pipeline.test_comparison_exact_accounting_and_baseline_time |
| A02 | DONE | test_apk.test_add_remove_modify_and_reverse：新增/删除物理字节账 |
| A03 | DONE | 同一用例：修改内容/压缩大小，包含移除负贡献 |
| A04 | DONE | test_add_remove_modify_and_reverse：同长内容 SHA 改变 |
| A05 | DONE | test_compression_and_container_changes，PACKAGING_CHANGED |
| A06 | DONE | test_random_accounting、反向取反、正负/净变化断言 |
| A07 | DONE | test_invalid_paths、test_directory_views_are_not_additive |
| A08 | DONE | duplicates/CRC/encryption/overlap/limits/declared-inflation；断连临时文件与 lease 清理 |
| A09 | DONE | test_unity_report.test_guid_rename_multiple_objects_and_independent_metrics |
| A10 | DONE | 同用例及实际 M3 4,116 原始记录，GUID/类型聚合不丢行 |
| A11 | DONE | test_generated_and_path_fallback_preserve_records，未归属保留 |
| A12 | DONE | duplicate_container_names/full_path_mapping_never_uses_basename |
| A13 | DONE | 独立 APK/packed 对比、真实 M3 两指标；UI 分层与复制 bytes |
| A14 | DONE | upload_idempotency、真实 CLI 重复 submit、并发初始化/封存 |
| A15 | DONE | 同 logical key/run_id 不同清单 409；旧产物/封存不可改写 |
| A16 | DONE | test_submit_resumes_verified_roles、受控网络重试与整文件重传 |
| A17 | DONE | optional_report_degradation_and_identity_conflict；capture run/hash/复制期替换 |
| A18 | DONE | 缺失 Unity 报告为 PARTIAL，测试 API 和 Playwright 的独立空态 |
| A19 | DONE | 真实 prefork、重复消息、SIGKILL、lease/fencing 与旧结果保护 |
| A20 | DONE | 真实 Redis 丢队列恢复、outbox 重投；Compose 重启/恢复 |
| A21 | DONE | comparison_exact_accounting_and_baseline_time：乱序历史不改旧基线 |
| A22 | DONE | compatibility_and_manual_cross_group、实际 ABI 不一致排除 |
| A23 | DONE | Unity/压缩改变仍可比；字段差异与证据规则测试 |
| A24 | DONE | candidate_and_manual_git_range_mismatch，不复用旧 Git 范围 |
| A25 | DONE | NO_BASELINE/null；真实浏览器首版与未知增量空态 |
| A26 | NOT_VERIFIED | CLI 断网/重试已测，Jenkins advisory 示例有语法证据；没有实际原流水线故障注入 |
| A27 | NOT_VERIFIED | CLI SIGINT=130/SIGTERM=143 已测；实际 Jenkins 手动中止/timeout/插件尚未验收 |
| A28 | NOT_VERIFIED | 平台发布状态/白名单/错误/幂等已测；实际蒲公英传输成功后发布失败样本未接入 |
| A29 | DONE | 项目/下载/规则/复核/诊断授权；实际跨项目浏览器拒绝；CSRF/同源 |
| A30 | DONE | 中文/空格/NUL rename；Playwright 恶意样式路径不执行；生产 CSP 同源包 |
| A31 | DONE | reanalysis_new_revision_atomic_switch_and_expired_raw；原 APK 不可用时索引可读、重算 410，M6 过期引用下载 410 |
| A32 | DONE | missing_tools_do_not_break_basic_analysis、沙箱失败关闭、元数据 not_requested/unavailable；真实受限 SDK 核验仍 NOT_VERIFIED |

本地新增：共享 Blob/pin/current/固定基线保护、两阶段宽限与计划变化拒绝、旧无引用索引过期、临时软链接拒绝、备份恢复拒绝穿越/链接/非空卷、生产 CSP/伪造转发头限流。真实生产安全扫描、外部 TLS、用户工程/设备验收及指定硬件性能仍未完成。
