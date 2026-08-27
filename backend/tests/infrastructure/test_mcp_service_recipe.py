"""Sandbox 镜像内固定 MCP 启动 recipe 的离线状态机测试。"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType

import pytest


def _load_recipe() -> ModuleType:
    path = Path(__file__).parents[3] / "sandbox" / "research-agent" / "start-mcp-service"
    loader = importlib.machinery.SourceFileLoader("research_agent_mcp_recipe", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _install_fake_processes(
    monkeypatch: pytest.MonkeyPatch,
    recipe: ModuleType,
    runtime_dir: Path,
) -> tuple[dict[str, bool], list[str | None]]:
    running = {"open": False}
    starts: list[str | None] = []
    monkeypatch.setattr(recipe, "_RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(recipe, "_service", lambda _name, _host: (8931, [], {}))
    monkeypatch.setattr(recipe, "_port_is_open", lambda _port: running["open"])

    def read_pid(path: Path) -> int | None:
        return int(path.read_text(encoding="ascii")) if path.exists() else None

    def stop_process(_pid: int, _port: int) -> None:
        running["open"] = False

    def start_process(**kwargs: object) -> None:
        external_authority = kwargs["external_authority"]
        assert external_authority is None or isinstance(external_authority, str)
        starts.append(external_authority)
        running["open"] = True
        pid_path = kwargs["pid_path"]
        authority_path = kwargs["authority_path"]
        assert isinstance(pid_path, Path)
        assert isinstance(authority_path, Path)
        pid_path.write_text("101", encoding="ascii")
        authority_path.write_text(str(kwargs["state_value"]), encoding="ascii")

    monkeypatch.setattr(recipe, "_read_pid", read_pid)
    monkeypatch.setattr(recipe, "_stop_process", stop_process)
    monkeypatch.setattr(recipe, "_start_process", start_process)
    return running, starts


def test_recipe_allows_bootstrap_then_exact_once_and_rejects_changed_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recipe = _load_recipe()
    _, starts = _install_fake_processes(monkeypatch, recipe, tmp_path / "mcp")

    assert (
        recipe._ensure_service(
            "playwright",
            requested_authority=recipe._BOOTSTRAP_AUTHORITY,
            external_authority=None,
        )
        == 0
    )
    assert (
        recipe._ensure_service(
            "playwright",
            requested_authority="proxy.invalid:8931",
            external_authority="proxy.invalid:8931",
        )
        == 0
    )
    assert (
        recipe._ensure_service(
            "playwright",
            requested_authority="proxy.invalid:8931",
            external_authority="proxy.invalid:8931",
        )
        == 0
    )
    with pytest.raises(RuntimeError, match="authority 已变化"):
        recipe._ensure_service(
            "playwright",
            requested_authority="changed.invalid:8931",
            external_authority="changed.invalid:8931",
        )

    assert starts == [None, "proxy.invalid:8931"]


def test_concurrent_exact_configuration_converges_to_one_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recipe = _load_recipe()
    running, starts = _install_fake_processes(monkeypatch, recipe, tmp_path / "mcp")
    recipe._ensure_service(
        "playwright",
        requested_authority=recipe._BOOTSTRAP_AUTHORITY,
        external_authority=None,
    )

    def configure() -> int:
        return recipe._ensure_service(
            "playwright",
            requested_authority="proxy.invalid:8931",
            external_authority="proxy.invalid:8931",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(lambda _: configure(), range(4))) == [0, 0, 0, 0]

    assert running["open"] is True
    assert starts == [None, "proxy.invalid:8931"]
    assert (tmp_path / "mcp" / "playwright.authority").read_text(encoding="ascii") == (
        "proxy.invalid:8931"
    )
