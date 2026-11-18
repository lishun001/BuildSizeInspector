from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def root() -> Path:
    return ROOT


def _redact(text: str) -> str:
    import os
    import re
    from urllib.parse import quote

    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    values = []
    keys = {"DATABASE_URL", "BUILDSIZE_TEST_DATABASE_URL", "REDIS_URL", "BUILDSIZE_TEST_REDIS_URL"}
    keys.update(
        key
        for key in os.environ
        if any(
            word in key.upper()
            for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "PRIVATE_KEY", "CREDENTIAL")
        )
    )
    for key in keys:
        value = os.environ.get(key)
        if value and len(value) >= 8:
            values.append(value)
            if "URL" in key:
                try:
                    password = make_url(value).password
                except (ValueError, ArgumentError):
                    password = None
                if password:
                    values.extend([password, quote(password, safe="")])
    for value in sorted(set(values), key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    # 测试中直接签发、未经过环境变量的项目令牌同样不能进入失败报告。
    return re.sub(r"\bbsi_[A-Za-z0-9_-]{32,}\b", "[REDACTED]", text)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.failed and report.longrepr:
        report.longrepr = _redact(report.longreprtext)
    report.sections = [(title, _redact(content)) for title, content in report.sections]
    return report
