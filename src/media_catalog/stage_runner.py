from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Generic, Protocol, TypeVar


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class StagePolicy:
    timeout_seconds: float
    retries: int = 1


@dataclass(frozen=True, slots=True)
class StageResult(Generic[T]):
    ok: bool
    value: T | None
    attempts: int
    error_type: str | None
    error_message: str | None


class StageRunner:
    def run(
        self,
        name: str,
        operation: Callable[[float], T],
        policy: StagePolicy,
    ) -> StageResult[T]:
        if not name.strip():
            raise ValueError("stage name must not be blank")
        if policy.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if policy.retries < 0:
            raise ValueError("retries must not be negative")

        attempts = 0
        for attempts in range(1, policy.retries + 2):
            try:
                value = operation(policy.timeout_seconds)
            except Exception as error:
                if attempts <= policy.retries:
                    continue
                return StageResult(
                    ok=False,
                    value=None,
                    attempts=attempts,
                    error_type=type(error).__name__,
                    error_message=_short_error(error),
                )
            return StageResult(
                ok=True,
                value=value,
                attempts=attempts,
                error_type=None,
                error_message=None,
            )
        raise AssertionError("unreachable")


def _short_error(error: Exception) -> str:
    first_line = str(error).splitlines()[0] if str(error) else ""
    return first_line[:240]


class HeartbeatStore(Protocol):
    def heartbeat(
        self, run_id: str, worker_pid: int | None = None
    ) -> None: ...


class HeartbeatThread:
    def __init__(
        self,
        store: HeartbeatStore,
        run_id: str,
        *,
        worker_pid: int | None = None,
        interval_seconds: float = 5.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._store = store
        self._run_id = run_id
        self._worker_pid = os.getpid() if worker_pid is None else worker_pid
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: Exception | None = None

    def __enter__(self) -> HeartbeatThread:
        if self._thread is not None:
            raise RuntimeError("heartbeat thread cannot be started twice")
        self._thread = threading.Thread(
            target=self._run,
            name=f"media-catalog-heartbeat-{self._run_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self._interval_seconds * 2))
            if self._thread.is_alive():
                raise RuntimeError("heartbeat thread did not stop")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._store.heartbeat(self._run_id, self._worker_pid)
                self.last_error = None
            except Exception as error:
                self.last_error = error
            self._stop.wait(self._interval_seconds)
