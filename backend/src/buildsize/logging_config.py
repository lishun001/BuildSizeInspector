"""只为项目日志配置默认输出，不记录请求体、查询参数或连接凭据。"""

import logging


def configure_logging() -> None:
    logger = logging.getLogger("buildsize")
    logger.setLevel(logging.INFO)
    if not logger.hasHandlers():
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
