"""应用配置。"""

import logging
import math
import os
from dataclasses import dataclass, field

_DEFAULT_APP_NAME = "Literature Review Agent"
_DEFAULT_DATABASE_URL = "postgresql+psycopg://agent:agent@127.0.0.1:5432/agent_service"
_DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"
_DEFAULT_DEV_ACTOR_ID = "dev-user"
_DEFAULT_STORAGE_ROOT = "data/storage"
_DEFAULT_MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024  # 50 MiB（2026-08-20 定稿）
_DEFAULT_PARSER_TIMEOUT_SECONDS = 300.0  # 2026-08-20 定稿
_DEFAULT_JOB_TIMEOUT_SECONDS = 1800.0  # 2026-08-29：完整 Run 独立预算
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
# RAG 回答默认值（切片 8，2026-08-21 定稿）：chat backend 默认 fake——
# 本地开发与测试默认不触网（仿 AGENT_EMBEDDING_BACKEND）；输出 token
# 上限约束 ChatModel 结构化回答长度
_DEFAULT_CHAT_BACKEND = "fake"
_DEFAULT_CHAT_JSON_SCHEMA_SUPPORTED = True
_DEFAULT_CHAT_THINKING_MODE = "disabled"
_DEFAULT_CHAT_REASONING_EFFORT = "low"
_DEFAULT_ANSWER_MAX_OUTPUT_TOKENS = 4096
# Research Agent Provider 与 RAG/Review Chat 独立；默认 Fake 保持离线零费用。
_DEFAULT_RESEARCH_RUNTIME_BACKEND = "fake"
_DEFAULT_RESEARCH_MODEL_BASE_URL = "https://api.deepseek.com"
_DEFAULT_RESEARCH_MODEL = "deepseek-v4-flash"
_DEFAULT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS = 4096
_DEFAULT_RESEARCH_MODEL_THINKING_MODE = "disabled"
_DEFAULT_RESEARCH_MODEL_REASONING_EFFORT = "low"
_DEFAULT_RESEARCH_SANDBOX_DOMAIN = "127.0.0.1:8080"
_DEFAULT_RESEARCH_SANDBOX_PROTOCOL = "http"
_DEFAULT_RESEARCH_SANDBOX_IMAGE = (
    "agent-service/research-agent-sandbox@sha256:"
    "8ded4a3cfb5603efac3e297a09f79f4bdef798379728eeb96d563ae8f99f40d1"
)
# arXiv 默认关闭真实网络：只有显式选择 httpx 才能访问官方 API。
_DEFAULT_ARXIV_BACKEND = "fake"
_DEFAULT_WORKER_METRICS_PORT = 8001
_DEFAULT_LOG_LEVEL = logging.INFO
_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
_THINKING_MODES = frozenset({"disabled", "enabled"})
_REASONING_EFFORTS = frozenset({"low", "high", "max"})


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
    job_timeout_seconds: float = field(default=_DEFAULT_JOB_TIMEOUT_SECONDS)
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
    embedding_api_key: str | None = field(default=None, repr=False)
    embedding_model: str = field(default=_DEFAULT_EMBEDDING_MODEL)
    embedding_dimensions: int = field(default=_DEFAULT_EMBEDDING_DIMENSIONS)
    chat_base_url: str = field(default=_DEFAULT_CHAT_BASE_URL)
    chat_api_key: str | None = field(default=None, repr=False)
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
    chat_backend: str = field(default=_DEFAULT_CHAT_BACKEND)
    chat_json_schema_supported: bool = field(
        default=_DEFAULT_CHAT_JSON_SCHEMA_SUPPORTED
    )
    chat_thinking_mode: str = field(default=_DEFAULT_CHAT_THINKING_MODE)
    chat_reasoning_effort: str = field(default=_DEFAULT_CHAT_REASONING_EFFORT)
    answer_max_output_tokens: int = field(default=_DEFAULT_ANSWER_MAX_OUTPUT_TOKENS)
    research_runtime_backend: str = field(default=_DEFAULT_RESEARCH_RUNTIME_BACKEND)
    research_model_base_url: str = field(default=_DEFAULT_RESEARCH_MODEL_BASE_URL)
    research_model_api_key: str | None = field(default=None, repr=False)
    research_model: str = field(default=_DEFAULT_RESEARCH_MODEL)
    research_model_max_output_tokens: int = field(
        default=_DEFAULT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS
    )
    research_model_thinking_mode: str = field(
        default=_DEFAULT_RESEARCH_MODEL_THINKING_MODE
    )
    research_model_reasoning_effort: str = field(
        default=_DEFAULT_RESEARCH_MODEL_REASONING_EFFORT
    )
    research_sandbox_domain: str = field(default=_DEFAULT_RESEARCH_SANDBOX_DOMAIN)
    research_sandbox_protocol: str = field(default=_DEFAULT_RESEARCH_SANDBOX_PROTOCOL)
    research_sandbox_api_key: str | None = field(default=None, repr=False)
    research_sandbox_image: str = field(default=_DEFAULT_RESEARCH_SANDBOX_IMAGE)
    arxiv_backend: str = field(default=_DEFAULT_ARXIV_BACKEND)
    worker_metrics_port: int = field(default=_DEFAULT_WORKER_METRICS_PORT)
    log_level: int = field(default=_DEFAULT_LOG_LEVEL)

    def __post_init__(self) -> None:
        """校验完整 Job 与单次 Parser 的分层超时契约。"""
        if (
            not math.isfinite(self.parser_timeout_seconds)
            or self.parser_timeout_seconds <= 0
        ):
            raise ValueError("AGENT_PARSER_TIMEOUT_SECONDS 必须为正数")
        if (
            not math.isfinite(self.job_timeout_seconds)
            or self.job_timeout_seconds <= self.parser_timeout_seconds
        ):
            raise ValueError(
                "AGENT_JOB_TIMEOUT_SECONDS 必须大于 AGENT_PARSER_TIMEOUT_SECONDS"
            )
        thinking_settings = (
            ("AGENT_CHAT_THINKING_MODE", self.chat_thinking_mode, _THINKING_MODES),
            (
                "AGENT_CHAT_REASONING_EFFORT",
                self.chat_reasoning_effort,
                _REASONING_EFFORTS,
            ),
            (
                "AGENT_RESEARCH_MODEL_THINKING_MODE",
                self.research_model_thinking_mode,
                _THINKING_MODES,
            ),
            (
                "AGENT_RESEARCH_MODEL_REASONING_EFFORT",
                self.research_model_reasoning_effort,
                _REASONING_EFFORTS,
            ),
        )
        for name, value, allowed in thinking_settings:
            if value not in allowed:
                raise ValueError(f"{name} 必须为 {'、'.join(sorted(allowed))}")
        if not self.debug and (
            self.chat_thinking_mode == "enabled"
            or self.research_model_thinking_mode == "enabled"
        ):
            raise ValueError("启用模型 thinking 必须同时设置 AGENT_DEBUG=true")

    @classmethod
    def from_env(cls) -> "Settings":
        """根据环境变量创建设置对象。"""
        raw_log_level = os.getenv("AGENT_LOG_LEVEL", "INFO").strip().upper()
        debug = os.getenv("AGENT_DEBUG", "").lower() in {"1", "true", "yes"}
        try:
            log_level = _LOG_LEVELS[raw_log_level]
        except KeyError as exc:
            raise ValueError(
                "AGENT_LOG_LEVEL 必须为 DEBUG、INFO、WARNING、ERROR 或 CRITICAL"
            ) from exc
        raw_max_upload = os.getenv("AGENT_MAX_UPLOAD_SIZE_BYTES")
        max_upload_size = int(raw_max_upload) if raw_max_upload else _DEFAULT_MAX_UPLOAD_SIZE_BYTES
        raw_parser_timeout = os.getenv("AGENT_PARSER_TIMEOUT_SECONDS")
        try:
            parser_timeout = (
                float(raw_parser_timeout)
                if raw_parser_timeout
                else _DEFAULT_PARSER_TIMEOUT_SECONDS
            )
        except ValueError as exc:
            raise ValueError("AGENT_PARSER_TIMEOUT_SECONDS 必须为正数") from exc
        raw_job_timeout = os.getenv("AGENT_JOB_TIMEOUT_SECONDS")
        try:
            job_timeout = (
                float(raw_job_timeout)
                if raw_job_timeout
                else _DEFAULT_JOB_TIMEOUT_SECONDS
            )
        except ValueError as exc:
            raise ValueError(
                "AGENT_JOB_TIMEOUT_SECONDS 必须大于 AGENT_PARSER_TIMEOUT_SECONDS"
            ) from exc
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
        raw_answer_max_tokens = os.getenv("AGENT_ANSWER_MAX_OUTPUT_TOKENS")
        answer_max_output_tokens = (
            int(raw_answer_max_tokens)
            if raw_answer_max_tokens
            else _DEFAULT_ANSWER_MAX_OUTPUT_TOKENS
        )
        chat_thinking_mode = (
            os.getenv("AGENT_CHAT_THINKING_MODE", _DEFAULT_CHAT_THINKING_MODE)
            .strip()
            .lower()
        )
        chat_reasoning_effort = (
            os.getenv("AGENT_CHAT_REASONING_EFFORT", _DEFAULT_CHAT_REASONING_EFFORT)
            .strip()
            .lower()
        )
        raw_metrics_port = os.getenv("AGENT_WORKER_METRICS_PORT")
        try:
            worker_metrics_port = (
                int(raw_metrics_port) if raw_metrics_port else _DEFAULT_WORKER_METRICS_PORT
            )
        except ValueError as exc:
            raise ValueError("AGENT_WORKER_METRICS_PORT 必须为 0..65535 的整数") from exc
        if not 0 <= worker_metrics_port <= 65535:
            raise ValueError("AGENT_WORKER_METRICS_PORT 必须为 0..65535 的整数")
        research_runtime_backend = os.getenv(
            "AGENT_RESEARCH_RUNTIME_BACKEND", _DEFAULT_RESEARCH_RUNTIME_BACKEND
        )
        if research_runtime_backend not in {"fake", "deep_agents"}:
            raise ValueError(
                "AGENT_RESEARCH_RUNTIME_BACKEND 必须为 fake 或 deep_agents"
            )
        research_model_base_url = _DEFAULT_RESEARCH_MODEL_BASE_URL
        research_model_api_key: str | None = None
        research_model = _DEFAULT_RESEARCH_MODEL
        research_model_max_output_tokens = _DEFAULT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS
        research_model_thinking_mode = (
            os.getenv(
                "AGENT_RESEARCH_MODEL_THINKING_MODE",
                _DEFAULT_RESEARCH_MODEL_THINKING_MODE,
            )
            .strip()
            .lower()
        )
        research_model_reasoning_effort = (
            os.getenv(
                "AGENT_RESEARCH_MODEL_REASONING_EFFORT",
                _DEFAULT_RESEARCH_MODEL_REASONING_EFFORT,
            )
            .strip()
            .lower()
        )
        research_sandbox_domain = _DEFAULT_RESEARCH_SANDBOX_DOMAIN
        research_sandbox_protocol = _DEFAULT_RESEARCH_SANDBOX_PROTOCOL
        research_sandbox_api_key: str | None = None
        research_sandbox_image = _DEFAULT_RESEARCH_SANDBOX_IMAGE
        if research_runtime_backend == "deep_agents":
            research_model_api_key = os.getenv("AGENT_RESEARCH_MODEL_API_KEY") or None
            research_model_base_url = os.getenv(
                "AGENT_RESEARCH_MODEL_BASE_URL", _DEFAULT_RESEARCH_MODEL_BASE_URL
            )
            research_model = os.getenv(
                "AGENT_RESEARCH_MODEL", _DEFAULT_RESEARCH_MODEL
            )
            if research_model != _DEFAULT_RESEARCH_MODEL:
                raise ValueError(
                    "AGENT_RESEARCH_MODEL 当前只允许 deepseek-v4-flash"
                )
            raw_research_output = os.getenv(
                "AGENT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS"
            )
            try:
                research_model_max_output_tokens = (
                    int(raw_research_output)
                    if raw_research_output
                    else _DEFAULT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS
                )
            except ValueError as exc:
                raise ValueError(
                    "AGENT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS 必须为正整数"
                ) from exc
            if not 0 < research_model_max_output_tokens <= 4_096:
                raise ValueError(
                    "AGENT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS 必须在 1..4096 范围内"
                )
            research_sandbox_domain = os.getenv(
                "AGENT_RESEARCH_SANDBOX_DOMAIN", _DEFAULT_RESEARCH_SANDBOX_DOMAIN
            )
            research_sandbox_protocol = os.getenv(
                "AGENT_RESEARCH_SANDBOX_PROTOCOL", _DEFAULT_RESEARCH_SANDBOX_PROTOCOL
            )
            if research_sandbox_protocol not in {"http", "https"}:
                raise ValueError("AGENT_RESEARCH_SANDBOX_PROTOCOL 必须为 http 或 https")
            research_sandbox_api_key = (
                os.getenv("AGENT_RESEARCH_SANDBOX_API_KEY") or None
            )
            research_sandbox_image = os.getenv(
                "AGENT_RESEARCH_SANDBOX_IMAGE", _DEFAULT_RESEARCH_SANDBOX_IMAGE
            )
            if not research_sandbox_domain.strip() or not research_sandbox_image.strip():
                raise ValueError("Research Sandbox domain/image 不能为空")
        return cls(
            app_name=os.getenv("AGENT_APP_NAME", _DEFAULT_APP_NAME),
            debug=debug,
            database_url=os.getenv("AGENT_DATABASE_URL", _DEFAULT_DATABASE_URL),
            redis_url=os.getenv("AGENT_REDIS_URL", _DEFAULT_REDIS_URL),
            dev_actor_id=os.getenv("AGENT_DEV_ACTOR_ID", _DEFAULT_DEV_ACTOR_ID),
            storage_root=os.getenv("AGENT_STORAGE_ROOT", _DEFAULT_STORAGE_ROOT),
            max_upload_size_bytes=max_upload_size,
            parser_timeout_seconds=parser_timeout,
            job_timeout_seconds=job_timeout,
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
            chat_backend=os.getenv("AGENT_CHAT_BACKEND", _DEFAULT_CHAT_BACKEND),
            chat_json_schema_supported=os.getenv(
                "AGENT_CHAT_JSON_SCHEMA_SUPPORTED", "true"
            ).lower()
            in {"1", "true", "yes"},
            chat_thinking_mode=chat_thinking_mode,
            chat_reasoning_effort=chat_reasoning_effort,
            answer_max_output_tokens=answer_max_output_tokens,
            research_runtime_backend=research_runtime_backend,
            research_model_base_url=research_model_base_url,
            research_model_api_key=research_model_api_key,
            research_model=research_model,
            research_model_max_output_tokens=research_model_max_output_tokens,
            research_model_thinking_mode=research_model_thinking_mode,
            research_model_reasoning_effort=research_model_reasoning_effort,
            research_sandbox_domain=research_sandbox_domain,
            research_sandbox_protocol=research_sandbox_protocol,
            research_sandbox_api_key=research_sandbox_api_key,
            research_sandbox_image=research_sandbox_image,
            arxiv_backend=os.getenv("AGENT_ARXIV_BACKEND", _DEFAULT_ARXIV_BACKEND),
            worker_metrics_port=worker_metrics_port,
            log_level=log_level,
        )
