from typing import Literal, Self

from pydantic import model_validator

from buildsize.domain.contracts import Contract, Text, Timestamp, Uri
from buildsize.domain.urls import validate_public_url


class PgyerResult(Contract):
    publish_state: Literal["publishing", "published", "failed", "not_published"]
    build_key: Text | None
    build_version: str | None
    install_page_url: Uri | None
    published_at: Timestamp | None

    @model_validator(mode="after")
    def publication(self) -> Self:
        if self.publish_state == "published":
            if not self.build_key or not self.install_page_url or self.published_at is None:
                raise ValueError("发布成功需要最终 build_key、安装页及发布时间")
        elif self.install_page_url is not None or self.published_at is not None:
            raise ValueError("未发布成功不能关联可安装成功链接或发布时间")
        if self.install_page_url:
            validate_public_url(self.install_page_url)
        return self
