"""Run untrusted Python in a throwaway container (Architecture.md §6.2, §14).

Hardening per call: no network, read-only root filesystem with a small tmpfs workdir, all
capabilities dropped, no privilege escalation, unprivileged user, memory / CPU / PID limits, and a
hard timeout after which the container is killed. Output is capped. The container is removed
whatever happens.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from typing import Any

OUTPUT_CAP = 20_000


@dataclass(frozen=True)
class SandboxLimits:
    image: str = "foreman-sandbox:latest"
    memory: str = "512m"
    cpus: float = 1.0
    pids: int = 128
    max_timeout_s: int = 60
    workdir_size: str = "64m"


@dataclass(frozen=True)
class ExecResult:
    stdout: str
    stderr: str
    return_code: int | None
    timed_out: bool
    duration_ms: int
    truncated: bool = False


def container_kwargs(code: str, limits: SandboxLimits) -> dict[str, Any]:
    """The exact ``containers.run`` arguments — factored out so tests can assert every guard."""
    return {
        "image": limits.image,
        "command": ["python", "-I", "-c", code],
        "detach": True,
        "network_disabled": True,
        "network_mode": "none",
        "read_only": True,
        "tmpfs": {"/work": f"rw,size={limits.workdir_size}", "/tmp": "rw,size=32m"},
        "working_dir": "/work",
        "user": "65534:65534",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "mem_limit": limits.memory,
        "memswap_limit": limits.memory,
        "nano_cpus": int(limits.cpus * 1_000_000_000),
        "pids_limit": limits.pids,
        "environment": {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "HOME": "/work"},
        "stdin_open": False,
        "tty": False,
    }


def _cap(text: bytes | str) -> tuple[str, bool]:
    s = text.decode("utf-8", errors="replace") if isinstance(text, bytes) else text
    if len(s) > OUTPUT_CAP:
        return s[:OUTPUT_CAP] + f"\n…[truncated {len(s) - OUTPUT_CAP} chars]", True
    return s, False


def run_in_container(
    client: Any, code: str, *, timeout_s: int, limits: SandboxLimits
) -> ExecResult:
    """``client`` is a ``docker.DockerClient`` (or a test double with the same surface)."""
    timeout = max(1, min(int(timeout_s), limits.max_timeout_s))
    started = time.perf_counter()
    container = client.containers.run(**container_kwargs(code, limits))
    timed_out = False
    return_code: int | None = None
    try:
        try:
            outcome = container.wait(timeout=timeout)
            return_code = int(outcome.get("StatusCode", -1)) if isinstance(outcome, dict) else None
        except Exception:  # noqa: BLE001 — the docker SDK raises a generic ReadTimeout family here
            timed_out = True
            with contextlib.suppress(Exception):  # already gone
                container.kill()
        stdout, t1 = _cap(container.logs(stdout=True, stderr=False))
        stderr, t2 = _cap(container.logs(stdout=False, stderr=True))
    finally:
        with contextlib.suppress(Exception):  # best-effort cleanup
            container.remove(force=True)
    if timed_out:
        stderr = (stderr + f"\n[killed after {timeout}s timeout]").strip()
    return ExecResult(
        stdout=stdout,
        stderr=stderr,
        return_code=return_code,
        timed_out=timed_out,
        duration_ms=int((time.perf_counter() - started) * 1000),
        truncated=t1 or t2,
    )
