"""MCP Catalog、Session Profile 与逐 Turn 冻结引用的 SDK-neutral 契约。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_PARAMETER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_PARAMETER_PARTS = frozenset(
    {
        "auth",
        "command",
        "cookie",
        "endpoint",
        "env",
        "image",
        "key",
        "network",
        "password",
        "secret",
        "token",
        "transport",
        "url",
    }
)
_MAX_SELECTIONS = 8


def canonical_json_hash(value: Any) -> str:
    """对有限 JSON 生成稳定 SHA-256。"""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class McpParameterSpec:
    """Catalog 允许用户填写的非敏感字符串参数。"""

    name: str
    required: bool = False
    max_length: int = 255

    def __post_init__(self) -> None:
        _validate_parameter_name(self.name)
        if not 1 <= self.max_length <= 1_000:
            raise ValueError("MCP 参数最大长度必须在 1..1000")


@dataclass(frozen=True, slots=True)
class McpToolContract:
    """Catalog 声明的原始 MCP Tool 名与输入 Schema 哈希。"""

    name: str
    input_schema_hash: str

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 100:
            raise ValueError("MCP Tool 名称非法")
        if not _SHA256.fullmatch(self.input_schema_hash):
            raise ValueError("MCP Tool Schema hash 非法")

    @classmethod
    def from_schema(cls, *, name: str, input_schema: dict[str, Any]) -> McpToolContract:
        return cls(name=name, input_schema_hash=canonical_json_hash(input_schema))


@dataclass(frozen=True, slots=True)
class McpCatalogEntry:
    """平台审核并固定版本的公开 Catalog 描述，不含连接细节。"""

    catalog_id: str
    version: str
    display_name: str
    parameters: tuple[McpParameterSpec, ...]
    tools: tuple[McpToolContract, ...]

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.catalog_id):
            raise ValueError("MCP Catalog ID 非法")
        if not self.version or len(self.version) > 50:
            raise ValueError("MCP Catalog 版本非法")
        if not self.display_name.strip() or len(self.display_name) > 100:
            raise ValueError("MCP Catalog 展示名称非法")
        if len({item.name for item in self.parameters}) != len(self.parameters):
            raise ValueError("MCP Catalog 参数名不得重复")
        if not self.tools or len({item.name for item in self.tools}) != len(self.tools):
            raise ValueError("MCP Catalog Tool 必须存在且名称不得重复")
        if any(len(f"{self.catalog_id}_{item.name}") > 100 for item in self.tools):
            raise ValueError("MCP Catalog prefixed Tool 名称超过平台上限")


@dataclass(frozen=True, slots=True)
class McpProfileSelection:
    """Session 选择的精确 Catalog 版本与安全参数。"""

    catalog_id: str
    version: str
    parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.catalog_id):
            raise ValueError("MCP Catalog ID 非法")
        if not self.version or len(self.version) > 50:
            raise ValueError("MCP Catalog 版本非法")
        names: list[str] = []
        for name, value in self.parameters:
            _validate_parameter_name(name)
            if not isinstance(value, str) or len(value) > 1_000:
                raise ValueError("MCP 参数必须是有界字符串")
            names.append(name)
        if len(set(names)) != len(names):
            raise ValueError("MCP 参数名不得重复")

    @property
    def config_hash(self) -> str:
        return canonical_json_hash(
            {
                "catalog_id": self.catalog_id,
                "version": self.version,
                "parameters": dict(sorted(self.parameters)),
            }
        )


@dataclass(frozen=True, slots=True)
class McpPolicyToolRef:
    """逐 Turn 冻结的 prefixed Tool 名与 Schema 哈希。"""

    name: str
    input_schema_hash: str

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 100:
            raise ValueError("MCP Policy Tool 名称非法")
        if not _SHA256.fullmatch(self.input_schema_hash):
            raise ValueError("MCP Policy Tool Schema hash 非法")


@dataclass(frozen=True, slots=True)
class McpPolicyRef:
    """逐 Turn 冻结的 Catalog 引用；不包含 endpoint、Secret 或 SDK 类型。"""

    profile_id: str
    profile_revision: int
    catalog_id: str
    version: str
    config_hash: str
    tools: tuple[McpPolicyToolRef, ...]

    def __post_init__(self) -> None:
        if not self.profile_id or len(self.profile_id) > 36 or self.profile_revision < 1:
            raise ValueError("MCP Policy Profile revision 非法")
        if not _IDENTIFIER.fullmatch(self.catalog_id) or not self.version:
            raise ValueError("MCP Policy Catalog 引用非法")
        if not _SHA256.fullmatch(self.config_hash):
            raise ValueError("MCP Policy config hash 非法")
        prefix = f"{self.catalog_id}_"
        if not self.tools or any(not tool.name.startswith(prefix) for tool in self.tools):
            raise ValueError("MCP Policy Tool 命名空间非法")
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("MCP Policy Tool 不得重复")


@dataclass(frozen=True, slots=True)
class McpProfile:
    """owner/Session 范围内、带 CAS revision 的 MCP 选择。"""

    profile_id: str
    owner_id: str
    session_id: str
    revision: int
    selections: tuple[McpProfileSelection, ...]
    config_hash: str
    created_at: datetime
    updated_at: datetime


class McpCatalog:
    """平台静态 Catalog；运行连接配置由 infrastructure 私有 Registry 持有。"""

    def __init__(self, entries: tuple[McpCatalogEntry, ...] = ()) -> None:
        keys = [(entry.catalog_id, entry.version) for entry in entries]
        if len(keys) != len(set(keys)):
            raise ValueError("MCP Catalog ID/version 不得重复")
        self._entries = entries
        self._by_key = dict(zip(keys, entries, strict=True))

    @property
    def entries(self) -> tuple[McpCatalogEntry, ...]:
        return self._entries

    def validate_selection(self, selection: McpProfileSelection) -> None:
        """验证选择仍对应平台注册版本与安全参数声明。"""
        self._validate_selection(selection)

    def _validate_selection(self, selection: McpProfileSelection) -> McpCatalogEntry:
        entry = self._by_key.get((selection.catalog_id, selection.version))
        if entry is None:
            raise ValueError("MCP Catalog 条目或版本不可用")
        supplied = dict(selection.parameters)
        specs = {item.name: item for item in entry.parameters}
        if not set(supplied).issubset(specs):
            raise ValueError("MCP Profile 包含 Catalog 未声明参数")
        missing = [
            item.name for item in entry.parameters if item.required and not supplied.get(item.name)
        ]
        if missing:
            raise ValueError("MCP Profile 缺少 Catalog 必填参数")
        for name, value in supplied.items():
            if len(value) > specs[name].max_length:
                raise ValueError("MCP Profile 参数超过 Catalog 长度限制")
        return entry

    def _resolve_selection(
        self, profile: McpProfile, selection: McpProfileSelection
    ) -> McpPolicyRef:
        entry = self._validate_selection(selection)
        return McpPolicyRef(
            profile_id=profile.profile_id,
            profile_revision=profile.revision,
            catalog_id=entry.catalog_id,
            version=entry.version,
            config_hash=selection.config_hash,
            tools=tuple(
                McpPolicyToolRef(
                    name=f"{entry.catalog_id}_{tool.name}",
                    input_schema_hash=tool.input_schema_hash,
                )
                for tool in entry.tools
            ),
        )

    def resolve_profile(self, profile: McpProfile | None) -> tuple[McpPolicyRef, ...]:
        if profile is None:
            return ()
        selections = sorted(profile.selections, key=lambda item: (item.catalog_id, item.version))
        return tuple(self._resolve_selection(profile, item) for item in selections)


def create_mcp_profile(
    *, owner_id: str, session_id: str, selections: tuple[McpProfileSelection, ...]
) -> McpProfile:
    """创建 revision=1 的 Session MCP Profile。"""
    _validate_profile(owner_id, session_id, selections)
    now = datetime.now(UTC)
    return McpProfile(
        profile_id=str(uuid4()),
        owner_id=owner_id,
        session_id=session_id,
        revision=1,
        selections=selections,
        config_hash=_profile_hash(selections),
        created_at=now,
        updated_at=now,
    )


def update_mcp_profile(
    profile: McpProfile, *, selections: tuple[McpProfileSelection, ...]
) -> McpProfile:
    """生成下一 revision；持久化层以旧 revision 做条件更新。"""
    _validate_profile(profile.owner_id, profile.session_id, selections)
    return replace(
        profile,
        revision=profile.revision + 1,
        selections=selections,
        config_hash=_profile_hash(selections),
        updated_at=datetime.now(UTC),
    )


def _profile_hash(selections: tuple[McpProfileSelection, ...]) -> str:
    return canonical_json_hash(
        [
            {
                "catalog_id": item.catalog_id,
                "version": item.version,
                "parameters": dict(sorted(item.parameters)),
            }
            for item in sorted(selections, key=lambda value: (value.catalog_id, value.version))
        ]
    )


def _validate_profile(
    owner_id: str, session_id: str, selections: tuple[McpProfileSelection, ...]
) -> None:
    if not owner_id or not session_id:
        raise ValueError("MCP Profile owner/session 不能为空")
    if len(selections) > _MAX_SELECTIONS:
        raise ValueError("MCP Profile 条目超过上限")
    keys = [item.catalog_id for item in selections]
    if len(keys) != len(set(keys)):
        raise ValueError("MCP Profile 的 Catalog ID 不得重复")


def _validate_parameter_name(name: str) -> None:
    if not _PARAMETER_NAME.fullmatch(name):
        raise ValueError("MCP 参数名非法")
    parts = frozenset(name.split("_"))
    if parts & _FORBIDDEN_PARAMETER_PARTS:
        raise ValueError("MCP Profile 不允许连接、网络或 Secret 参数")
