"""互斥分类规则；规则配置参与版本指纹。"""

import hashlib
import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Classifier:
    builtin_bundle_prefixes: tuple[str, ...] = ()
    bundle_before_unity: bool = False

    def __post_init__(self) -> None:
        for prefix in self.builtin_bundle_prefixes:
            if (
                not prefix.startswith("assets/")
                or not prefix.endswith("/")
                or ".." in prefix.split("/")
                or "\\" in prefix
                or "\x00" in prefix
            ):
                raise ValueError("首包 Bundle 前缀必须为 assets/ 下的精确目录")

    @property
    def version(self) -> str:
        value = json.dumps(
            [sorted(self.builtin_bundle_prefixes), self.bundle_before_unity], separators=(",", ":")
        )
        return "apk-classifier-1:" + hashlib.sha256(value.encode()).hexdigest()

    def classify(self, path: str, is_directory: bool) -> tuple[str, str | None]:
        parts = path.split("/")
        abi = parts[1] if len(parts) >= 3 and parts[0] == "lib" else None
        if is_directory:
            return "other", abi
        if abi is not None and path.endswith(".so"):
            return "native_library", abi
        if re.fullmatch(r"classes(?:[0-9]+)?\.dex", path):
            return "dex", None
        unity = path.startswith("assets/bin/Data/")
        bundle = path.startswith(self.builtin_bundle_prefixes)
        if bundle and (self.bundle_before_unity or not unity):
            return "builtin_bundle", None
        if unity:
            return "unity_data", None
        if path.startswith("res/") or path == "resources.arsc":
            return "android_resource", None
        if path == "AndroidManifest.xml" or path.startswith("META-INF/"):
            return "manifest_and_metadata", None
        if path.startswith("assets/"):
            return "other_assets", None
        return "other", abi
