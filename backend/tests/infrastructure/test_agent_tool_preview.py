"""Agent Tool 输入输出公开预览的脱敏与截断契约。"""

from literature_agent.infrastructure.agent.agent_tool_preview import build_tool_preview


def test_tool_preview_redacts_sensitive_keys_and_inline_credentials() -> None:
    preview = build_tool_preview(
        {
            "command": "curl -H 'Authorization: Bearer top-secret-token' https://example.com",
            "api_key": "should-never-render",
            "nested": {"password": "also-secret", "query": "路径规划"},
        },
        max_bytes=8_192,
        pretty=True,
    )

    assert "路径规划" in preview.text
    assert "[已脱敏]" in preview.text
    assert "top-secret-token" not in preview.text
    assert "should-never-render" not in preview.text
    assert "also-secret" not in preview.text
    assert preview.truncated is False


def test_tool_preview_is_utf8_bounded_and_marks_truncation() -> None:
    preview = build_tool_preview("路径" * 100, max_bytes=48)

    assert preview.truncated is True
    assert "预览已截断" in preview.text
    assert len(preview.text.encode("utf-8")) <= 96
