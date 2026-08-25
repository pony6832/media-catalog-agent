from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .database import CatalogDatabase
from .run_state import AnalysisRun, RunStateStore
from .workspace import MediaWorkspace, WorkspacePathError


class WorkerProcess(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def communicate(self) -> tuple[object, object]: ...


@dataclass(frozen=True, slots=True)
class SupervisorSnapshot:
    status: str
    worker_alive: bool
    run: AnalysisRun | None
    exit_code: int | None = None
    error_text: str = ""


def _spawn_process(arguments: list[str]) -> WorkerProcess:
    return subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _spawn_catalog_process(arguments: list[str]) -> WorkerProcess:
    return subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
    )


def _sanitize_process_message(value: str) -> str:
    cleaned = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    cleaned = re.sub(
        r"(?i)(GEMINI_API_KEY|GOOGLE_API_KEY)\s*=\s*\S+",
        r"\1=[REDACTED]",
        cleaned,
    )
    return cleaned[-240:] or "建立媒體清冊失敗"


def _heartbeat_is_fresh(run: AnalysisRun) -> bool:
    if not run.last_heartbeat:
        return False
    try:
        heartbeat = datetime.fromisoformat(run.last_heartbeat)
    except ValueError:
        return False
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
    return 0 <= age <= 15


class WorkerSupervisor:
    def __init__(
        self,
        *,
        python_executable: Path | str = sys.executable,
        process_factory: Callable[[list[str]], WorkerProcess] = _spawn_process,
        catalog_process_factory: Callable[
            [list[str]], WorkerProcess
        ] = _spawn_catalog_process,
        store_factory: Callable[[MediaWorkspace], RunStateStore] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        heartbeat_is_fresh: Callable[[AnalysisRun], bool] = _heartbeat_is_fresh,
        startup_grace_seconds: float = 30,
    ) -> None:
        self.python_executable = str(python_executable)
        self.process_factory = process_factory
        self.catalog_process_factory = catalog_process_factory
        self.store_factory = store_factory or (
            lambda workspace: RunStateStore(
                workspace.database_path, excel_path=workspace.excel_path
            )
        )
        self.monotonic = monotonic
        self.heartbeat_is_fresh = heartbeat_is_fresh
        self.startup_grace_seconds = max(0, startup_grace_seconds)
        self.workspace: MediaWorkspace | None = None
        self.store: RunStateStore | None = None
        self.run_id: str | None = None
        self.process: WorkerProcess | None = None
        self.catalog_process: WorkerProcess | None = None
        self.catalog_workspace: MediaWorkspace | None = None
        self.catalog_skill_root: Path | None = None
        self.arguments: list[str] | None = None
        self.launch_count = 0
        self._restart_used = False
        self._stale_since: float | None = None
        self._launched_at: float | None = None
        self._safe_stop_requested = False
        self._catalog_stop_requested = False
        self._catalog_terminal_status: str | None = None
        self._catalog_exit_code: int | None = None
        self._catalog_error_text = ""

    @property
    def is_busy(self) -> bool:
        processes = (self.catalog_process, self.process)
        return any(
            process is not None and process.poll() is None
            for process in processes
        )

    def start_catalog(self, root: Path, skill_root: Path) -> int:
        if self.is_busy:
            return self._active_process_id()
        workspace = MediaWorkspace.from_root(root)
        self.workspace = None
        self.store = None
        self.run_id = None
        self.process = None
        self.arguments = None
        self.catalog_workspace = workspace
        self.catalog_skill_root = Path(skill_root).resolve()
        self._catalog_stop_requested = False
        self._catalog_terminal_status = None
        self._catalog_exit_code = None
        self._catalog_error_text = ""
        arguments = [
            self.python_executable,
            "-m",
            "media_catalog.cli",
            "start",
            str(workspace.root),
        ]
        self.catalog_process = self.catalog_process_factory(arguments)
        return int(getattr(self.catalog_process, "pid", 1))

    def start(self, root: Path, skill_root: Path) -> int:
        if self.process is not None and self.process.poll() is None:
            return self._process_id()
        workspace = MediaWorkspace.from_root(root)
        if not workspace.database_path.is_file() or not workspace.excel_path.is_file():
            raise WorkspacePathError(
                f"找不到既有媒體清冊，請先建立清冊：{workspace.result_root}"
            )
        store = self.store_factory(workspace)
        records = CatalogDatabase(workspace.database_path).list_records()
        run = store.ensure_run(
            root_path=workspace.root,
            video_count=sum(
                record.media_type.startswith("video/") for record in records
            ),
            image_count=sum(
                record.media_type.startswith("image/") for record in records
            ),
            total_bytes=sum(
                record.path.stat().st_size
                for record in records
                if record.path.is_file()
            ),
        )
        store.clear_stop(run.run_id)
        self.workspace = workspace
        self.store = store
        self.run_id = run.run_id
        self.arguments = [
            self.python_executable,
            "-m",
            "media_catalog.cli",
            "analyze-all",
            str(workspace.root),
            "--skill-root",
            str(Path(skill_root).resolve()),
        ]
        self._restart_used = False
        self._safe_stop_requested = False
        self._stale_since = None
        self._launch()
        return self._process_id()

    def poll(self) -> SupervisorSnapshot:
        if self.catalog_process is not None:
            exit_code = self.catalog_process.poll()
            if exit_code is None:
                return SupervisorSnapshot("cataloging", True, None)
            process = self.catalog_process
            self.catalog_process = None
            output, _ = process.communicate()
            if self._catalog_stop_requested:
                self._catalog_terminal_status = "stopped"
                self._catalog_exit_code = exit_code
                return SupervisorSnapshot("stopped", False, None, exit_code)
            if exit_code != 0:
                error_text = _sanitize_process_message(str(output or ""))
                self._catalog_terminal_status = "error"
                self._catalog_exit_code = exit_code
                self._catalog_error_text = error_text
                return SupervisorSnapshot(
                    "error", False, None, exit_code, error_text
                )
            workspace = self.catalog_workspace
            skill_root = self.catalog_skill_root
            if workspace is None or skill_root is None:
                raise RuntimeError("Catalog process lost its workspace")
            self._catalog_terminal_status = None
            self._catalog_exit_code = None
            self._catalog_error_text = ""
            self.start(workspace.root, skill_root)
            return self.poll()

        if self.process is None and self.run_id is None:
            if self._catalog_terminal_status is not None:
                return SupervisorSnapshot(
                    self._catalog_terminal_status,
                    False,
                    None,
                    self._catalog_exit_code,
                    self._catalog_error_text,
                )
            return SupervisorSnapshot("idle", False, None)

        run = self._require_run()
        if self.process is None:
            return SupervisorSnapshot("idle", False, run)
        exit_code = self.process.poll()
        if exit_code is not None:
            if self._safe_stop_requested:
                return SupervisorSnapshot("stopped", False, run, exit_code)
            if exit_code == 0 and run.completed_media == run.total_media:
                return SupervisorSnapshot("completed", False, run, exit_code)
            if not self._restart_used:
                self._restart_worker()
                return SupervisorSnapshot("restarting", True, self._require_run())
            self._checkpoint_crash()
            return SupervisorSnapshot("error", False, run, exit_code)

        if self.heartbeat_is_fresh(run):
            self._stale_since = None
            return SupervisorSnapshot("running", True, run)

        now = self.monotonic()
        if (
            self._launched_at is not None
            and now - self._launched_at < self.startup_grace_seconds
        ):
            return SupervisorSnapshot("starting", True, run)
        if self._stale_since is None:
            self._stale_since = now
            self.store.request_stop(self.run_id)  # type: ignore[arg-type, union-attr]
            return SupervisorSnapshot("stopping_stale_worker", True, run)
        if now - self._stale_since < 10:
            return SupervisorSnapshot("stopping_stale_worker", True, run)

        self.process.terminate()
        wait = getattr(self.process, "wait", None)
        if callable(wait):
            try:
                wait(timeout=5)
            except subprocess.TimeoutExpired:
                return SupervisorSnapshot("error", True, run)
        if self._restart_used:
            self._checkpoint_crash()
            return SupervisorSnapshot("error", False, run)
        self._restart_worker()
        return SupervisorSnapshot("restarting", True, self._require_run())

    def request_safe_stop(self) -> None:
        if self.catalog_process is not None:
            self._catalog_stop_requested = True
            if self.catalog_process.poll() is None:
                self.catalog_process.terminate()
            return
        if self.store is None or self.run_id is None:
            return
        self._safe_stop_requested = True
        self.store.request_stop(self.run_id)

    def _launch(self) -> None:
        if self.arguments is None:
            raise RuntimeError("Supervisor has not been configured")
        self.process = self.process_factory(list(self.arguments))
        self.launch_count += 1
        self._launched_at = (
            self.monotonic() if self.startup_grace_seconds > 0 else None
        )

    def _restart_worker(self) -> None:
        if self.store is None or self.run_id is None:
            raise RuntimeError("Supervisor has no run state")
        self._checkpoint_crash()
        self.store.record_recovery(self.run_id)
        self.store.clear_stop(self.run_id)
        self._restart_used = True
        self._stale_since = None
        self._launch()

    def _checkpoint_crash(self) -> None:
        if self.store is None or self.run_id is None:
            raise RuntimeError("Supervisor has no run state")
        self.store.requeue_stale_processing(self.run_id)
        self.store.fail_repeated_crashes(self.run_id)

    def _require_run(self) -> AnalysisRun:
        if self.store is None or self.run_id is None:
            raise RuntimeError("Supervisor has not been started")
        run = self.store.get_run(self.run_id)
        if run is None:
            raise RuntimeError("Analysis run state disappeared")
        return run

    def _process_id(self) -> int:
        if self.process is None:
            raise RuntimeError("Worker process was not launched")
        return int(getattr(self.process, "pid", self.launch_count))

    def _active_process_id(self) -> int:
        for process in (self.catalog_process, self.process):
            if process is not None and process.poll() is None:
                return int(getattr(process, "pid", 1))
        raise RuntimeError("Supervisor has no active process")
