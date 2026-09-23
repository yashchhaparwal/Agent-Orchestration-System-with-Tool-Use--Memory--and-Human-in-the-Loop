"""Sandbox hardening, checked against a fake Docker client so the suite never needs Docker."""

from __future__ import annotations

from typing import Any

import pytest

from packages.shared.config import Settings
from packages.tools.mcp_servers.sandbox.runner import (
    OUTPUT_CAP,
    SandboxLimits,
    container_kwargs,
    run_in_container,
)
from packages.tools.mcp_servers.sandbox.server import build_server
from tests.unit.test_mcp_servers_inprocess import ToolFailedError, _call


class FakeContainer:
    def __init__(
        self, *, hang: bool = False, stdout: bytes = b"hi\n", stderr: bytes = b"", code: int = 0
    ) -> None:
        self.hang, self._out, self._err, self._code = hang, stdout, stderr, code
        self.killed = self.removed = False

    def wait(self, timeout: int) -> dict[str, int]:
        if self.hang:
            raise TimeoutError(f"read timed out after {timeout}s")
        return {"StatusCode": self._code}

    def kill(self) -> None:
        self.killed = True

    def logs(self, stdout: bool, stderr: bool) -> bytes:
        return self._out if stdout else self._err

    def remove(self, force: bool) -> None:
        self.removed = force


class FakeContainers:
    def __init__(
        self, container: FakeContainer | None = None, error: Exception | None = None
    ) -> None:
        self.container, self.error, self.kwargs = container or FakeContainer(), error, None

    def run(self, **kwargs: Any) -> FakeContainer:
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.container


class FakeClient:
    def __init__(
        self, container: FakeContainer | None = None, error: Exception | None = None
    ) -> None:
        self.containers = FakeContainers(container, error)


def test_every_hardening_guard_is_set() -> None:
    kw = container_kwargs("print(1)", SandboxLimits(memory="256m", cpus=0.5, pids=64))
    assert kw["network_disabled"] is True and kw["network_mode"] == "none"
    assert kw["read_only"] is True and "/work" in kw["tmpfs"] and kw["working_dir"] == "/work"
    assert kw["user"] == "65534:65534" and kw["cap_drop"] == ["ALL"]
    assert kw["security_opt"] == ["no-new-privileges"]
    assert kw["mem_limit"] == "256m" and kw["memswap_limit"] == "256m"  # no swap escape
    assert kw["nano_cpus"] == 500_000_000 and kw["pids_limit"] == 64
    assert kw["command"] == ["python", "-I", "-c", "print(1)"] and kw["detach"] is True
    assert kw["stdin_open"] is False and kw["tty"] is False


def test_run_collects_output_and_always_removes_container() -> None:
    client = FakeClient(FakeContainer(stdout=b"42\n", stderr=b"warn\n", code=0))
    r = run_in_container(client, "print(42)", timeout_s=10, limits=SandboxLimits())
    assert r.stdout == "42\n" and r.stderr == "warn\n" and r.return_code == 0
    assert r.timed_out is False and client.containers.container.removed is True


def test_timeout_kills_and_removes() -> None:
    c = FakeContainer(hang=True)
    r = run_in_container(FakeClient(c), "while True: pass", timeout_s=2, limits=SandboxLimits())
    assert r.timed_out is True and c.killed is True and c.removed is True
    assert "killed after 2s" in r.stderr and r.return_code is None


def test_timeout_is_clamped_to_the_limit() -> None:
    c = FakeContainer(hang=True)
    r = run_in_container(FakeClient(c), "x", timeout_s=9999, limits=SandboxLimits(max_timeout_s=5))
    assert "killed after 5s" in r.stderr


def test_output_is_capped() -> None:
    big = b"x" * (OUTPUT_CAP + 500)
    r = run_in_container(
        FakeClient(FakeContainer(stdout=big)), "x", timeout_s=5, limits=SandboxLimits()
    )
    assert r.truncated is True and len(r.stdout) < OUTPUT_CAP + 100 and "truncated 500" in r.stdout


async def test_server_reports_missing_image_and_empty_code(settings: Settings) -> None:
    server = build_server(
        settings,
        client=FakeClient(error=RuntimeError("404 Client Error: No such image: foreman-sandbox")),
    )
    with pytest.raises(ToolFailedError, match="make sandbox-image"):
        await _call(server, "run_python", code="print(1)")
    with pytest.raises(ToolFailedError, match="empty"):
        await _call(server, "run_python", code="   ")


async def test_server_returns_result_dict(settings: Settings) -> None:
    server = build_server(settings, client=FakeClient(FakeContainer(stdout=b"7\n")))
    out = await _call(server, "run_python", code="print(7)")
    assert (
        out["stdout"] == "7\n" and out["limits"]["network"] == "none" and out["timed_out"] is False
    )
