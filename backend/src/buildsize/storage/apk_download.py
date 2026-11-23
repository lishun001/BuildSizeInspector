"""只从运维批准的外部 HTTPS 主机读取最终 APK。"""

import base64
import hashlib
import hmac
import http.client
import ipaddress
import re
import socket
import ssl
import time
from urllib.parse import SplitResult, urljoin, urlsplit

from cryptography.fernet import Fernet, InvalidToken

from buildsize.config import Config
from buildsize.domain.errors import BuildSizeError
from buildsize.storage.local import StagedUpload

NAT64_PREFIXES = tuple(
    ipaddress.ip_network(prefix) for prefix in ("64:ff9b::/96", "64:ff9b:1::/48")
)


def download_target(url: str, hosts: tuple[str, ...]) -> SplitResult:
    if not hosts:
        raise BuildSizeError("APK_DOWNLOAD_DISABLED", "服务器尚未配置允许下载 APK 的主机")
    try:
        if len(url) > 8192 or not re.fullmatch(r"[!-~]+", url) or "\\" in url:
            raise ValueError
        parsed = urlsplit(url)
        host = parsed.hostname
        if (
            parsed.scheme != "https"
            or not host
            or host not in hosts
            or "." not in host
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
            or parsed.netloc.lower() not in {host, f"{host}:443"}
            or "#" in url
            or not parsed.path.startswith("/")
            or parsed.path.startswith("//")
            or parsed.path.lower().rstrip("/") == "/apiv2/app/install"
        ):
            raise ValueError
    except ValueError:
        raise BuildSizeError(
            "APK_DOWNLOAD_URL_INVALID", "需要经服务器批准的 HTTPS APK 直链"
        ) from None
    return parsed


def _cipher(config: Config) -> Fernet:
    key = hmac.digest(config.session_secret.encode(), b"buildsize-apk-download-v1", hashlib.sha256)
    return Fernet(base64.urlsafe_b64encode(key))


def protect_url(config: Config, url: str) -> str:
    return _cipher(config).encrypt(url.encode()).decode()


def reveal_url(config: Config, ciphertext: str) -> str:
    try:
        return _cipher(config).decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise BuildSizeError("APK_DOWNLOAD_CREDENTIAL_INVALID", "请重新提交下载链接") from None


def _resolve(host: str) -> str:
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if not addresses:
        raise OSError
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if (
            not address.is_global
            or address.is_multicast
            or address.is_reserved
            or isinstance(address, ipaddress.IPv6Address)
            and (
                address.is_site_local
                or address.sixtofour is not None
                or address.teredo is not None
                or any(address in network for network in NAT64_PREFIXES)
            )
        ):
            raise BuildSizeError("APK_DOWNLOAD_ADDRESS_DENIED", "APK 下载主机解析到受限地址")
    return str(addresses[0][4][0])


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, timeout: float) -> None:
        super().__init__(host, 443, timeout=timeout)
        self.address = address
        self.tls = ssl.create_default_context()

    def connect(self) -> None:
        # 连接已校验的 IP，TLS 仍验证原域名。连接期间不再次解析域名。
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self.tls.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def download_apk(config: Config, url: str, output: StagedUpload, size: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout

    def remaining() -> float:
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise BuildSizeError("APK_DOWNLOAD_TIMEOUT", "下载 APK 超过时间限制")
        return min(20.0, seconds)

    try:
        for _ in range(4):
            target = download_target(url, config.apk_download_hosts)
            assert target.hostname is not None
            address = _resolve(target.hostname)
            conn = PinnedHTTPSConnection(target.hostname, address, remaining())
            try:
                path = target.path + ("?" + target.query if target.query else "")
                conn.request("GET", path, headers={"Accept-Encoding": "identity"})
                with conn.getresponse() as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.getheader("Location")
                        if (
                            not location
                            or not re.fullmatch(r"[!-~]+", location)
                            or "\\" in location
                        ):
                            raise BuildSizeError("APK_DOWNLOAD_RESPONSE_INVALID", "下载重定向无效")
                        url = urljoin(url, location)
                        continue
                    if response.status in {401, 403, 404, 410}:
                        raise BuildSizeError(
                            "APK_DOWNLOAD_LINK_UNAVAILABLE", "下载链接不可用，请确认权限或更新链接"
                        )
                    if response.status in {408, 429} or 500 <= response.status <= 599:
                        raise BuildSizeError("APK_DOWNLOAD_IO", "下载服务暂时不可用")
                    if (
                        response.status != 200
                        or response.getheader("Content-Encoding", "identity").lower() != "identity"
                        or response.getheader("Content-Type", "")
                        .lower()
                        .startswith(("text/", "application/json"))
                    ):
                        raise BuildSizeError("APK_DOWNLOAD_RESPONSE_INVALID", "链接未返回 APK 文件")
                    length = response.getheader("Content-Length")
                    if length is not None and (not length.isdigit() or int(length) != size):
                        raise BuildSizeError("ARTIFACT_LENGTH_MISMATCH", "下载 APK 大小与登记不符")
                    while True:
                        remaining()
                        chunk = response.read1(64 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                    remaining()
                    return
            finally:
                conn.close()
    except ssl.SSLError:
        raise BuildSizeError("APK_DOWNLOAD_TLS", "APK 下载服务器证书校验失败") from None
    except (OSError, http.client.HTTPException):
        raise BuildSizeError("APK_DOWNLOAD_IO", "无法读取 APK 下载响应") from None
    raise BuildSizeError("APK_DOWNLOAD_RESPONSE_INVALID", "APK 下载重定向次数超限")
