"""应用配置。"""

import os
from dataclasses import dataclass, field

_DEFAULT_APP_NAME = "Literature Review Agent"
_DEFAULT_DATABASE_URL = "postgresql+psycopg://agent:agent@127.0.0.1:5432/agent_service"
_DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"
_DEFAULT_DEV_ACTOR_ID = "dev-user"
_DEFAULT_STORAGE_ROOT = "data/storage"
_DEFAULT_MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024  # 50 MiB（2026-08-20 定稿）
_DEFAULT_PARSER_TIMEOUT_SECONDS = 300.0  # 2026-08-20 定稿
_DEFAULT_WORKER_LEASE_SECONDS = 600.0  # 2026-08-20 定稿
_DEFAULT_WORKER_HEARTBEAT_INTERVAL_SECONDS = 30.0  # 2026-08-20 定稿
_DEFAULT_WORKER_RECONCILE_INTERVAL_SECONDS = 30.0
_DEFAULT_MAX_RUN_ATTEMPTS = 3
_DEFAULT_OUTBOX_POLL_INTERVAL_SECONDS = 1.0
_DEFAULT_OUTBOX_MAX_ATTEMPTS = 10
_DEFAULT_OUTBOX_DISPATCH_BATCH_SIZE = 20
# Model Gateway 默认值（2026-08-20 定稿）：Embedding 默认智谱 embedding-3，
# Chat 默认 DeepSeek deepseek-v4-flash，均为 OpenAI 兼容端点；API Key 默认
# None，缺失时 Adapter 首次调用给出明确错误（本地开发使用 Fake）。
_DEFAULT_EMBEDDING_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
_DEFAULT_EMBEDDING_MODEL = "embedding-3"
_DEFAULT_EMBEDDING_DIMENSIONS = 1024
_DEFAULT_CHAT_BASE_URL = "https://api.deepseek.com"
_DEFAULT_CHAT_MODEL = "deepseek-v4-flash"
_DEFAULT_MODEL_TIMEOUT_SECONDS = 60.0
_DEFAULT_MODEL_MAX_RETRIES = 2
# Chunk 切分默认值（2026-08-20 定稿）：实验起点，切片 6 检索实验可校准
_DEFAULT_CHUNK_MAX_TOKENS = 512
_DEFAULT_CHUNK_OVERLAP_TOKENS = 64
# Embedding 执行默认值（切片 5，2026-08-21 定稿）：backend 默认 fake——
# 本地开发与测试默认不触网；批次大小控制单次 Embedding 调用的文本数
_DEFAULT_EMBEDDING_BACKEND = "fake"
_DEFAULT_EMBEDDING_BATCH_SIZE = 32
# Hybrid Retrieval 默认值（切片 6，2026-08-21 检索实验校准）：各路 Top-K、
# 每篇论文进入最终结果的上限、最终结果的总 Token 预算。
# 每篇上限 5 会在 fake 向量距离并列较多时截掉目标 chunk（评测 q08 实测），
# 校准为 8 后 8/8 题 Recall 达标；语料小（每篇 ≤10 chunks），该值待真实
# Provider 评测再评估。
_DEFAULT_RETRIEVAL_TOP_K = 20
_DEFAULT_RETRIEVAL_PER_PAPER_LIMIT = 8
_DEFAULT_RETRIEVAL_TOKEN_BUDGET = 3000


@dataclass(frozen=True, slots=True)
class Settings:
    """不可变的应用配置。

    配置值从 ``AGENT_`` 前缀的环境变量读取，或使用本地开发的合理默认值。
    本类不引入额外依赖，因此可以在 lifespan 启动时构造，
    而不必依赖框架专属的配置库。
    """

    app_name: str = field(default=_DEFAULT_APP_NAME)
    debug: bool = field(default=False)
    database_url: str = field(default=_DEFAULT_DATABASE_URL)
    redis_url: str = field(default=_DEFAULT_REDIS_URL)
    dev_actor_id: str = field(default=_DEFAULT_DEV_ACTOR_ID)
    storage_root: str = field(default=_DEFAULT_STORAGE_ROOT)
    max_upload_size_bytes: int = field(default=_DEFAULT_MAX_UPLOAD_SIZE_BYTES)
    parser_timeout_seconds: float = field(default=_DEFAULT_PARSER_TIMEOUT_SECONDS)
    parser_backend: str = field(default="docling")
    worker_lease_seconds: float = field(default=_DEFAULT_WORKER_LEASE_SECONDS)
    worker_heartbeat_interval_seconds: float = field(
        default=_DEFAULT_WORKER_HEARTBEAT_INTERVAL_SECONDS
    )
    worker_reconcile_interval_seconds: float = field(
        default=_DEFAULT_WORKER_RECONCILE_INTERVAL_SECONDS
    )
    max_run_attempts: int = field(default=_DEFAULT_MAX_RUN_ATTEMPTS)
    outbox_poll_interval_seconds: float = field(default=_DEFAULT_OUTBOX_POLL_INTERVAL_SECONDS)
    outbox_max_attempts: int = field(default=_DEFAULT_OUTBOX_MAX_ATTEMPTS)
    outbox_dispatch_batch_size: int = field(default=_DEFAULT_OUTBOX_DISPATCH_BATCH_SIZE)
    embedding_base_url: str = field(default=_DEFAULT_EMBEDDING_BASE_URL)
    embedding_api_key: str | None = field(default=None)
    embedding_model: str = field(default=_DEFAULT_EMBEDDING_MODEL)
    embedding_dimensions: int = field(default=_DEFAULT_EMBEDDING_DIMENSIONS)
    chat_base_url: str = field(default=_DEFAULT_CHAT_BASE_URL)
    chat_api_key: str | None = field(default=None)
    chat_model: str = field(default=_DEFAULT_CHAT_MODEL)
    model_timeout_seconds: float = field(default=_DEFAULT_MODEL_TIMEOUT_SECONDS)
    model_max_retries: int = field(default=_DEFAULT_MODEL_MAX_RETRIES)
    chunk_max_tokens: int = field(default=_DEFAULT_CHUNK_MAX_TOKENS)
    chunk_overlap_tokens: int = field(default=_DEFAULT_CHUNK_OVERLAP_TOKENS)
    embedding_backend: str = field(default=_DEFAULT_EMBEDDING_BACKEND)
    embedding_batch_size: int = field(default=_DEFAULT_EMBEDDING_BATCH_SIZE)
    retrieval_top_k: int = field(default=_DEFAULT_RETRIEVAL_TOP_K)
    retrieval_per_paper_limit: int = field(default=_DEFAULT_RETRIEVAL_PER_PAPER_LIMIT)
    retrieval_token_budget: int = field(default=_DEFAULT_RETRIEVAL_TOKEN_BUDGET)

    @classmethod
    def from_env(cls) -> "Settings":
        """根据环境变量创建设置对象。"""
        raw_max_upload = os.getenv("AGENT_MAX_UPLOAD_SIZE_BYTES")
        max_upload_size = int(raw_max_upload) if raw_max_upload else _DEFAULT_MAX_UPLOAD_SIZE_BYTES
        raw_parser_timeout = os.getenv("AGENT_PARSER_TIMEOUT_SECONDS")
        parser_timeout = (
            float(raw_parser_timeout)
            if raw_parser_timeout
            else _DEFAULT_PARSER_TIMEOUT_SECONDS
        )
        raw_poll_interval = os.getenv("AGENT_OUTBOX_POLL_INTERVAL_SECONDS")
        poll_interval = (
            float(raw_poll_interval)
            if raw_poll_interval
            else _DEFAULT_OUTBOX_POLL_INTERVAL_SECONDS
        )
        raw_max_attempts = os.getenv("AGENT_OUTBOX_MAX_ATTEMPTS")
        max_attempts = int(raw_max_attempts) if raw_max_attempts else _DEFAULT_OUTBOX_MAX_ATTEMPTS
        raw_batch_size = os.getenv("AGENT_OUTBOX_DISPATCH_BATCH_SIZE")
        batch_size = (
            int(raw_batch_size) if raw_batch_size else _DEFAULT_OUTBOX_DISPATCH_BATCH_SIZE
        )
        raw_lease = os.getenv("AGENT_WORKER_LEASE_SECONDS")
        worker_lease = float(raw_lease) if raw_lease else _DEFAULT_WORKER_LEASE_SECONDS
        raw_heartbeat = os.getenv("AGENT_WORKER_HEARTBEAT_INTERVAL_SECONDS")
        heartbeat_interval = (
            float(raw_heartbeat)
            if raw_heartbeat
            else _DEFAULT_WORKER_HEARTBEAT_INTERVAL_SECONDS
        )
        raw_reconcile = os.getenv("AGENT_WORKER_RECONCILE_INTERVAL_SECONDS")
        reconcile_interval = (
            float(raw_reconcile)
            if raw_reconcile
            else _DEFAULT_WORKER_RECONCILE_INTERVAL_SECONDS
        )
        raw_run_attempts = os.getenv("AGENT_MAX_RUN_ATTEMPTS")
        max_run_attempts = (
            int(raw_run_attempts) if raw_run_attempts else _DEFAULT_MAX_RUN_ATTEMPTS
        )
        raw_dimensions = os.getenv("AGENT_EMBEDDING_DIMENSIONS")
        embedding_dimensions = (
            int(raw_dimensions) if raw_dimensions else _DEFAULT_EMBEDDING_DIMENSIONS
        )
        raw_model_timeout = os.getenv("AGENT_MODEL_TIMEOUT_SECONDS")
        model_timeout = (
            float(raw_model_timeout) if raw_model_timeout else _DEFAULT_MODEL_TIMEOUT_SECONDS
        )
        raw_model_retries = os.getenv("AGENT_MODEL_MAX_RETRIES")
        model_max_retries = (
            int(raw_model_retries) if raw_model_retries else _DEFAULT_MODEL_MAX_RETRIES
        )
        raw_chunk_max = os.getenv("AGENT_CHUNK_MAX_TOKENS")
        chunk_max_tokens = (
            int(raw_chunk_max) if raw_chunk_max else _DEFAULT_CHUNK_MAX_TOKENS
        )
        raw_chunk_overlap = os.getenv("AGENT_CHUNK_OVERLAP_TOKENS")
        chunk_overlap_tokens = (
            int(raw_chunk_overlap) if raw_chunk_overlap else _DEFAULT_CHUNK_OVERLAP_TOKENS
        )
        raw_embedding_batch = os.getenv("AGENT_EMBEDDING_BATCH_SIZE")
        embedding_batch_size = (
            int(raw_embedding_batch) if raw_embedding_batch else _DEFAULT_EMBEDDING_BATCH_SIZE
        )
        raw_retrieval_top_k = os.getenv("AGENT_RETRIEVAL_TOP_K")
        retrieval_top_k = (
            int(raw_retrieval_top_k) if raw_retrieval_top_k else _DEFAULT_RETRIEVAL_TOP_K
        )
        raw_per_paper_limit = os.getenv("AGENT_RETRIEVAL_PER_PAPER_LIMIT")
        retrieval_per_paper_limit = (
            int(raw_per_paper_limit)
            if raw_per_paper_limit
            else _DEFAULT_RETRIEVAL_PER_PAPER_LIMIT
        )
        raw_token_budget = os.getenv("AGENT_RETRIEVAL_TOKEN_BUDGET")
        retrieval_token_budget = (
            int(raw_token_budget) if raw_token_budget else _DEFAULT_RETRIEVAL_TOKEN_BUDGET
        )
        return cls(
            app_name=os.getenv("AGENT_APP_NAME", _DEFAULT_APP_NAME),
            debug=os.getenv("AGENT_DEBUG", "").lower() in {"1", "true", "yes"},
            database_url=os.getenv("AGENT_DATABASE_URL", _DEFAULT_DATABASE_URL),
            redis_url=os.getenv("AGENT_REDIS_URL", _DEFAULT_REDIS_URL),
            dev_actor_id=os.getenv("AGENT_DEV_ACTOR_ID", _DEFAULT_DEV_ACTOR_ID),
            storage_root=os.getenv("AGENT_STORAGE_ROOT", _DEFAULT_STORAGE_ROOT),
            max_upload_size_bytes=max_upload_size,
            parser_timeout_seconds=parser_timeout,
            parser_backend=os.getenv("AGENT_PARSER_BACKEND", "docling"),
            worker_lease_seconds=worker_lease,
            worker_heartbeat_interval_seconds=heartbeat_interval,
            worker_reconcile_interval_seconds=reconcile_interval,
            max_run_attempts=max_run_attempts,
            outbox_poll_interval_seconds=poll_interval,
            outbox_max_attempts=max_attempts,
            outbox_dispatch_batch_size=batch_size,
            embedding_base_url=os.getenv("AGENT_EMBEDDING_BASE_URL", _DEFAULT_EMBEDDING_BASE_URL),
            embedding_api_key=os.getenv("AGENT_EMBEDDING_API_KEY") or None,
            embedding_model=os.getenv("AGENT_EMBEDDING_MODEL", _DEFAULT_EMBEDDING_MODEL),
            embedding_dimensions=embedding_dimensions,
            chat_base_url=os.getenv("AGENT_CHAT_BASE_URL", _DEFAULT_CHAT_BASE_URL),
            chat_api_key=os.getenv("AGENT_CHAT_API_KEY") or None,
            chat_model=os.getenv("AGENT_CHAT_MODEL", _DEFAULT_CHAT_MODEL),
            model_timeout_seconds=model_timeout,
            model_max_retries=model_max_retries,
            chunk_max_tokens=chunk_max_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
            embedding_backend=os.getenv(
                "AGENT_EMBEDDING_BACKEND", _DEFAULT_EMBEDDING_BACKEND
            ),
            embedding_batch_size=embedding_batch_size,
            retrieval_top_k=retrieval_top_k,
            retrieval_per_paper_limit=retrieval_per_paper_limit,
            retrieval_token_budget=retrieval_token_budget,
        )
