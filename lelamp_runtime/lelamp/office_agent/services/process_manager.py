from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import IO, Sequence


class ProcessManager:
    """Owns consistent subprocess startup and bounded shutdown semantics."""

    def spawn(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path,
        stdout: int | IO[bytes] = subprocess.DEVNULL,
        stderr: int | IO[bytes] = subprocess.STDOUT,
    ) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            list(command),
            cwd=str(cwd),
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    @staticmethod
    def stop(process: subprocess.Popen[bytes] | None, *, timeout: float = 3.0) -> bool:
        if process is None or process.poll() is not None:
            return False
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
        return True

    @staticmethod
    def stop_group(pid: int, *, timeout: float = 4.0) -> None:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
