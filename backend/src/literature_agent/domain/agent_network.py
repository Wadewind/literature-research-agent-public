"""Research Agent 固定公网 egress 档案与声明来源目标地址检查。"""

import hashlib
import ipaddress
import json
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True, slots=True)
class SandboxEgressProfile:
    """不泄漏 Provider SDK 类型的版本化 Sandbox 网络契约。"""

    profile_id: str
    version: str
    default_action: str
    denied_non_loopback_cidrs: tuple[str, ...]
    allow_namespace_loopback: bool
    ipv6_disabled: bool
    profile_hash: str


_PUBLIC_EGRESS_DENIED_CIDRS = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "::/128",
    "100::/64",
    "2001:2::/48",
    "2001:db8::/32",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
)


def _canonical_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def create_research_public_egress_profile() -> SandboxEgressProfile:
    """返回平台唯一的 public-egress v1；loopback 仅供同 namespace 服务。"""
    payload: dict[str, object] = {
        "profile_id": "research-public-egress",
        "version": "v1",
        "default_action": "allow",
        "denied_non_loopback_cidrs": list(_PUBLIC_EGRESS_DENIED_CIDRS),
        "allow_namespace_loopback": True,
        "ipv6_disabled": True,
    }
    return SandboxEgressProfile(
        profile_id=str(payload["profile_id"]),
        version=str(payload["version"]),
        default_action=str(payload["default_action"]),
        denied_non_loopback_cidrs=_PUBLIC_EGRESS_DENIED_CIDRS,
        allow_namespace_loopback=True,
        ipv6_disabled=True,
        profile_hash=_canonical_hash(payload),
    )


RESEARCH_PUBLIC_EGRESS_PROFILE = create_research_public_egress_profile()


class FormalSourceValidationError(ValueError):
    """声明来源目标 URL 的稳定、安全错误。"""

    def __init__(self, code: str, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


@dataclass(frozen=True, slots=True)
class FormalPublicSource:
    url: str
    hostname: str
    port: int
    source_hash: str


def normalize_formal_public_source(value: str) -> FormalPublicSource:
    """规范化有界 HTTP(S) 声明目标；DNS 地址需随后独立检查。"""
    if not value.strip() or len(value) > 2048:
        raise FormalSourceValidationError("source_url_invalid", "来源 URL 非法")
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise FormalSourceValidationError("source_url_invalid", "来源 URL 非法") from exc
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or bool(parts.fragment)
    ):
        raise FormalSourceValidationError("source_url_invalid", "来源 URL 非法")
    try:
        host = hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise FormalSourceValidationError("source_url_invalid", "来源 URL 非法") from exc
    if host == "localhost" or host.endswith(".localhost"):
        raise FormalSourceValidationError(
            "source_target_forbidden", "来源目标不是普通公网地址"
        )
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise FormalSourceValidationError(
            "source_target_forbidden", "来源目标不是普通公网地址"
        )
    scheme = parts.scheme.lower()
    resolved_port = port or (443 if scheme == "https" else 80)
    authority_host = f"[{host}]" if literal is not None and literal.version == 6 else host
    authority = authority_host if port is None else f"{authority_host}:{port}"
    canonical = urlunsplit((scheme, authority, parts.path or "/", parts.query, ""))
    # Manifest 不保存可能携带 token/signature 的 query；hash 仍绑定完整规范化声明值。
    normalized = urlunsplit((scheme, authority, parts.path or "/", "", ""))
    return FormalPublicSource(
        url=normalized,
        hostname=host,
        port=resolved_port,
        source_hash=hashlib.sha256(canonical.encode()).hexdigest(),
    )


def validate_formal_public_source_addresses(
    source: FormalPublicSource, addresses: tuple[str, ...]
) -> None:
    """DNS 结果必须非空且全部是公网地址，防止混合回答与 rebinding 前置绕过。"""
    del source
    if not addresses:
        raise FormalSourceValidationError(
            "source_resolution_failed", "来源地址解析失败"
        )
    if any(is_forbidden_formal_source_address(value) for value in addresses):
        raise FormalSourceValidationError(
            "source_target_forbidden", "来源目标不是普通公网地址"
        )


def is_forbidden_formal_source_address(value: str) -> bool:
    """声明 URL/source 不得指向 loopback、私网、保留或非单播公网地址。"""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return True
    return not address.is_global
