"""Chunk/RAG 共用的 token 计数边界。"""

import re
from collections.abc import Callable
from functools import lru_cache

import tiktoken

OFFLINE_TOKENIZER = "unicode-word.v1"
_OFFLINE_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


@lru_cache(maxsize=4)
def get_token_counter(tokenizer: str) -> Callable[[str], int]:
    """返回指定版本的计数器；只有真实 tokenizer 才加载 tiktoken 词表。"""
    if tokenizer == OFFLINE_TOKENIZER:
        return lambda text: len(_OFFLINE_TOKEN_PATTERN.findall(text))
    encoding = tiktoken.get_encoding(tokenizer)
    return lambda text: len(encoding.encode(text))


def count_tokens(tokenizer: str, text: str) -> int:
    """使用 Profile 固定的 tokenizer 确定性计数。"""
    return get_token_counter(tokenizer)(text)
