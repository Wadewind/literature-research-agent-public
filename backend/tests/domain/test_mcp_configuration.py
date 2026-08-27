import pytest

from literature_agent.domain.mcp_configuration import (
    McpCatalog,
    McpCatalogEntry,
    McpParameterSpec,
    McpProfileSelection,
    McpToolContract,
    create_mcp_profile,
    update_mcp_profile,
)


def _catalog() -> McpCatalog:
    return McpCatalog(
        (
            McpCatalogEntry(
                catalog_id="fixture-search",
                version="1.0.0",
                display_name="Fixture Search",
                parameters=(McpParameterSpec("corpus", required=True, max_length=20),),
                tools=(
                    McpToolContract.from_schema(
                        name="search",
                        input_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    ),
                ),
            ),
        )
    )


def test_catalog_resolves_versioned_selection_to_prefixed_sdk_neutral_ref() -> None:
    selection = McpProfileSelection(
        catalog_id="fixture-search",
        version="1.0.0",
        parameters=(("corpus", "papers"),),
    )
    profile = create_mcp_profile(
        owner_id="owner-a", session_id="session-a", selections=(selection,)
    )

    resolved = _catalog().resolve_profile(profile)[0]

    assert resolved.profile_id == profile.profile_id
    assert resolved.profile_revision == 1
    assert resolved.catalog_id == "fixture-search"
    assert resolved.version == "1.0.0"
    assert resolved.config_hash == selection.config_hash
    assert resolved.tools[0].name == "fixture-search_search"
    assert len(resolved.tools[0].input_schema_hash) == 64


@pytest.mark.parametrize(
    "parameters",
    [
        (("endpoint", "http://internal"),),
        (("token", "secret"),),
        (("corpus", "papers"), ("corpus", "other")),
    ],
)
def test_profile_selection_rejects_connection_secret_and_duplicates(
    parameters: tuple[tuple[str, str], ...],
) -> None:
    with pytest.raises(ValueError):
        McpProfileSelection("fixture-search", "1.0.0", parameters)


def test_catalog_fails_closed_on_unknown_version_or_parameter() -> None:
    with pytest.raises(ValueError, match="Catalog"):
        _catalog().validate_selection(
            McpProfileSelection("fixture-search", "2.0.0", (("corpus", "papers"),))
        )
    with pytest.raises(ValueError, match="参数"):
        _catalog().validate_selection(
            McpProfileSelection("fixture-search", "1.0.0", (("other", "papers"),))
        )


def test_profile_update_uses_revision_and_canonical_config_hash() -> None:
    selection = McpProfileSelection("fixture-search", "1.0.0", (("corpus", "papers"),))
    profile = create_mcp_profile(
        owner_id="owner-a", session_id="session-a", selections=(selection,)
    )

    updated = update_mcp_profile(profile, selections=())

    assert profile.revision == 1
    assert updated.revision == 2
    assert updated.config_hash != profile.config_hash


def test_profile_rejects_multiple_versions_of_same_catalog() -> None:
    with pytest.raises(ValueError, match="Catalog ID"):
        create_mcp_profile(
            owner_id="owner-a",
            session_id="session-a",
            selections=(
                McpProfileSelection("fixture-search", "1.0.0"),
                McpProfileSelection("fixture-search", "2.0.0"),
            ),
        )


def test_catalog_rejects_prefixed_tool_name_beyond_database_limit() -> None:
    with pytest.raises(ValueError, match="prefixed Tool"):
        McpCatalogEntry(
            catalog_id="catalog-name",
            version="1.0.0",
            display_name="Too Long",
            parameters=(),
            tools=(
                McpToolContract.from_schema(
                    name="t" * 95,
                    input_schema={"type": "object"},
                ),
            ),
        )
