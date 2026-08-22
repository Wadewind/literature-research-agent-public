"""固定 ``search-strategy.v1`` 模型输出的确定性 Schema 与校验。"""

import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError

from literature_agent.domain.arxiv import ArxivSearchQuery
from literature_agent.domain.exceptions import ReviewSearchStrategyInvalidError
from literature_agent.domain.review_evidence_matrix import AnalysisDimension

SEARCH_STRATEGY_MAX_BYTES = 64 * 1024
_DIMENSION_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class SearchStrategyValidationError(ReviewSearchStrategyInvalidError):
    """策略模型输出不满足固定 Schema。"""


class _DimensionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension_key: str
    name: str
    extraction_question: str


class _SearchStrategyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normalized_question: str
    arxiv_query: str
    dimensions: list[_DimensionPayload]


@dataclass(frozen=True, slots=True)
class ReviewSearchStrategy:
    normalized_question: str
    arxiv_query: str
    dimensions: tuple[AnalysisDimension, ...]

    def to_payload(self) -> dict:
        return {
            "normalized_question": self.normalized_question,
            "arxiv_query": self.arxiv_query,
            "dimensions": [
                {
                    "dimension_key": item.dimension_key,
                    "name": item.name,
                    "extraction_question": item.extraction_question,
                }
                for item in self.dimensions
            ],
        }


def parse_search_strategy(content: str) -> _SearchStrategyPayload:
    if len(content.encode()) > SEARCH_STRATEGY_MAX_BYTES:
        raise SearchStrategyValidationError("search_strategy_output_too_large")
    try:
        return _SearchStrategyPayload.model_validate_json(content)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise SearchStrategyValidationError("search_strategy_schema_invalid") from exc


def validate_search_strategy(payload: _SearchStrategyPayload) -> ReviewSearchStrategy:
    question = payload.normalized_question.strip()
    if not question or len(question) > 4_000:
        raise SearchStrategyValidationError("search_strategy_question_invalid")
    if not 3 <= len(payload.dimensions) <= 6:
        raise SearchStrategyValidationError("search_strategy_dimensions_count_invalid")
    dimensions = []
    keys: set[str] = set()
    for item in payload.dimensions:
        key = item.dimension_key.strip()
        name = item.name.strip()
        extraction_question = item.extraction_question.strip()
        if (
            not _DIMENSION_KEY.fullmatch(key)
            or key in keys
            or not name
            or len(name) > 200
            or not extraction_question
            or len(extraction_question) > 1_000
        ):
            raise SearchStrategyValidationError("search_strategy_dimension_invalid")
        keys.add(key)
        dimensions.append(AnalysisDimension(key, name, extraction_question))
    try:
        query = ArxivSearchQuery(payload.arxiv_query)
    except ValueError as exc:
        raise SearchStrategyValidationError("search_strategy_arxiv_query_invalid") from exc
    return ReviewSearchStrategy(question, query.expression, tuple(dimensions))


SEARCH_STRATEGY_JSON_SCHEMA = _SearchStrategyPayload.model_json_schema()
