"""平台固定 Playwright/arXiv MCP Catalog 的供应链合同。"""

from literature_agent.infrastructure.agent.mcp_catalog import PLATFORM_MCP_CATALOG


def test_platform_catalog_freezes_reviewed_versions_and_tool_projection() -> None:
    entries = {item.catalog_id: item for item in PLATFORM_MCP_CATALOG.entries}

    assert {key: item.version for key, item in entries.items()} == {
        "arxiv-search": "0.6.2",
        "playwright": "0.0.79",
    }
    assert {tool.name for tool in entries["arxiv-search"].tools} == {
        "search_papers",
        "get_abstract",
    }
    playwright_tools = {tool.name for tool in entries["playwright"].tools}
    assert {"browser_navigate", "browser_snapshot", "browser_click"} <= playwright_tools
    assert playwright_tools.isdisjoint(
        {
            "browser_evaluate",
            "browser_run_code_unsafe",
            "browser_file_upload",
            "browser_drop",
            "browser_close",
            "browser_network_request",
        }
    )


def test_platform_catalog_schema_hashes_match_reviewed_real_servers() -> None:
    contracts = {
        entry.catalog_id: {tool.name: tool.input_schema_hash for tool in entry.tools}
        for entry in PLATFORM_MCP_CATALOG.entries
    }

    assert contracts["arxiv-search"] == {
        "search_papers": "346607f2d86733412e07c700fb657137970aee7675401da5ec67868d8ffada9e",
        "get_abstract": "ef2980a2c7456b21f465516135f6cb6e64c79e51c337d34a2411d631b3d0d7d8",
    }
    assert contracts["playwright"]["browser_navigate"] == (
        "2165538e098634780eec628947d795a2619b4d2e3cef0e36d3084ac46abb94f7"
    )
    assert contracts["playwright"]["browser_click"] == (
        "7fed82f52ae7018670d967e4429b63501948c80de2df2e9279ce4c103694d0d3"
    )


def test_nonempty_platform_catalog_does_not_enable_default_profile() -> None:
    """Catalog 可用性与 Session 授权分离；缺少 Profile 时不自动启用。"""
    assert PLATFORM_MCP_CATALOG.resolve_profile(None) == ()
