"""Parse Profile 与确定性 profile 哈希。

``parser_profile_hash`` 唯一标识一次解析的"输入配置"：相同
Paper Version + 相同 profile 哈希视为同一解析需求，可复用已有结果。
"""

import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ParseProfile:
    """一次解析的配置画像。

    属性:
        parser_name: Parser 名称，例如 ``fake``、``docling``、``pypdf``。
        parser_version: Parser 实现版本。
        config: 解析配置（OCR 策略、管线参数等），参与哈希计算。
    """

    parser_name: str
    parser_version: str
    config: dict = field(default_factory=dict)

    @property
    def profile_hash(self) -> str:
        """返回该 Profile 的确定性哈希。"""
        return compute_profile_hash(self.parser_name, self.parser_version, self.config)


def compute_profile_hash(parser_name: str, parser_version: str, config: dict) -> str:
    """计算 Parse Profile 的确定性 SHA-256 哈希。

    使用键排序的规范化 JSON，保证相同语义的配置得到相同哈希。

    参数:
        parser_name: Parser 名称。
        parser_version: Parser 版本。
        config: 解析配置。

    返回:
        64 位十六进制哈希字符串。
    """
    canonical = json.dumps(
        {"name": parser_name, "version": parser_version, "config": config},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
