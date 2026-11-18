# ZIP 测试夹具

当前夹具由 `backend/tests/test_apk.py` 在 pytest 的隔离临时目录生成，不随仓库附带伪装为真实产物的 APK。

固定 Stored 夹具独立计算头、路径和结束记录字节数；性质测试随机生成两组条目，检查实测文件长度差、物理对账和比较反对称性。包含真实携带数据的目录条目、中文/空格路径、同长改内容、压缩表示变化、CRC 错误、路径冲突、重叠、ZIP bomb、伪装解压长度、data descriptor 和 ZIP64 边界。

`make demo` 生成的 ZIP 在 `.buildsize/demo/`，输出带 demo 标记，不可安装。真实 APK、Unity 报告和 Jenkins 样本尚未提供，因此没有真实 Unity/Android/CI 验收夹具。

M2 的 test_pipeline 按实际字节生成清单/元数据，通过真实 API 上传；test_async_pipeline 使用 PostgreSQL、Redis 与 Celery，并验证 Worker 中止/队列丢失恢复。数据库重启测试只操作验证脚本创建的随机容器与专属卷。它们同样使用合成 ZIP，不把测试包称为可安装 APK。
