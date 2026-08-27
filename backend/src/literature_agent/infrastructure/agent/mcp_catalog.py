"""平台审核并固定版本/Schema 的 MCP Catalog 组合入口。"""

from literature_agent.domain.mcp_configuration import (
    McpCatalog,
    McpCatalogEntry,
    McpToolContract,
)


def _tools(values: dict[str, str]) -> tuple[McpToolContract, ...]:
    return tuple(
        McpToolContract(name=name, input_schema_hash=schema_hash)
        for name, schema_hash in values.items()
    )


_PLAYWRIGHT = McpCatalogEntry(
    catalog_id="playwright",
    version="0.0.79",
    display_name="Playwright Browser",
    parameters=(),
    tools=_tools(
        {
            "browser_console_messages": (
                "d503685c2c343d3f67d58e4e72ecd680e5b452d1e7905f3aa40dd0f5e6093853"
            ),
            "browser_handle_dialog": (
                "2d4cacf71e9fd7f902bebbc84f106614b891c2654de32b71b1f0d629a10d8b5c"
            ),
            "browser_find": "204f595563881a96f3f3830af62668b73678fd42dd04a57100fc55a3f39c8979",
            "browser_fill_form": "c991223a68247bd517c0fa1b61db444178ff6ad546e7214936d29b8de4c2f2ee",
            "browser_press_key": "57a7f701ebf865e5a7a759c15e8d04bca12cb756c2e3f4ae7280070bb6e310e2",
            "browser_type": "ed892740ebc14bb2a49467c08b8bb02717d50f1f3ef8ed06d51abf5ebb3249da",
            "browser_navigate": "2165538e098634780eec628947d795a2619b4d2e3cef0e36d3084ac46abb94f7",
            "browser_navigate_back": (
                "71fc596a20f27da3dec80050e5d1f553eba34aea24c2853b853488a811e6b892"
            ),
            "browser_network_requests": (
                "86d566ed7630b826e22f89987152935cd5963d5d31cf04e90c33f7918389646a"
            ),
            "browser_take_screenshot": (
                "8862e5da1b5030fc1a927c9ead00f2690872f24f282bc0d4cc3dbf017e041b34"
            ),
            "browser_snapshot": "36ee5bbb5798a52e26015635e1f6015b8f4b62f44119d53ad2516837667fcd61",
            "browser_click": "7fed82f52ae7018670d967e4429b63501948c80de2df2e9279ce4c103694d0d3",
            "browser_drag": "3dcca018f0b0e9341fee3c91b5420f82557a59a9d9763699e90970b2bd3ed1cb",
            "browser_hover": "3b0a5a3333d051dba9f6b023c341eef00f0e5264bef37bc0a11f83156937e803",
            "browser_select_option": (
                "03b097752bdb41817ec0a6a259062598e5c85c1a8c7106d0f2fde506321096bd"
            ),
            "browser_tabs": "268e0f3eca6295e0fef99d2ef85adfbd3d843c054087817fe96e02ccd8145711",
            "browser_wait_for": "004b7fd9ce085dd4bb13f67caa6d23a23078ef24342e31c552a377a010e36d5d",
        }
    ),
)

_ARXIV_SEARCH = McpCatalogEntry(
    catalog_id="arxiv-search",
    version="0.6.2",
    display_name="arXiv Search",
    parameters=(),
    tools=_tools(
        {
            "search_papers": "346607f2d86733412e07c700fb657137970aee7675401da5ec67868d8ffada9e",
            "get_abstract": "ef2980a2c7456b21f465516135f6cb6e64c79e51c337d34a2411d631b3d0d7d8",
        }
    ),
)

PLATFORM_MCP_CATALOG = McpCatalog((_ARXIV_SEARCH, _PLAYWRIGHT))
