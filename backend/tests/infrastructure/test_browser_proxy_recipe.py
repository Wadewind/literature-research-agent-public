"""Sandbox 内固定 websockify recipe 的离线状态机测试。"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType

import pytest


def _load_recipe() -> ModuleType:
    path = Path(__file__).parents[3] / "sandbox" / "research-agent" / "start-browser-proxy"
    loader = importlib.machinery.SourceFileLoader(
        "research_agent_browser_proxy_recipe", str(path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_recipe_has_fixed_websockify_command_without_static_web() -> None:
    recipe = _load_recipe()

    command = recipe._command()

    assert command[0].endswith("/websockify")
    assert command[-2:] == ["0.0.0.0:6080", "127.0.0.1:5901"]
    assert "--web" not in command
    assert "--timeout" in command
    assert "--idle-timeout" in command
    assert "--log-file" in command


def test_recipe_is_idempotent_and_concurrent_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recipe = _load_recipe()
    runtime_dir = tmp_path / "browser"
    starts: list[list[str]] = []
    running = {"open": False}
    monkeypatch.setattr(recipe, "_RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(recipe, "_port_is_open", lambda _port: running["open"])

    def read_pid(path: Path) -> int | None:
        return int(path.read_text(encoding="ascii")) if path.exists() else None

    def start_process(command: list[str], pid_path: Path) -> None:
        starts.append(command)
        running["open"] = True
        pid_path.write_text("101", encoding="ascii")

    monkeypatch.setattr(recipe, "_read_pid", read_pid)
    monkeypatch.setattr(recipe, "_start_process", start_process)

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(lambda _: recipe.ensure_browser_proxy(), range(4))) == [
            0,
            0,
            0,
            0,
        ]

    assert len(starts) == 1


def test_recipe_rejects_unknown_process_on_fixed_port(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recipe = _load_recipe()
    monkeypatch.setattr(recipe, "_RUNTIME_DIR", tmp_path / "browser")
    monkeypatch.setattr(recipe, "_port_is_open", lambda _port: True)
    monkeypatch.setattr(recipe, "_read_pid", lambda _path: None)

    with pytest.raises(RuntimeError, match="未知进程"):
        recipe.ensure_browser_proxy()
