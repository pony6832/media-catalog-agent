from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from .database import CatalogDatabase
from .run_state import AnalysisRun
from .supervisor import SupervisorSnapshot, WorkerSupervisor
from .workspace import MediaWorkspace, WorkspacePathError


@dataclass(frozen=True, slots=True)
class StatusViewModel:
    light_color: str
    status_text: str
    root_text: str
    video_count: int
    image_count: int
    total_size_text: str
    progress_text: str
    progress_percent: int
    remaining_text: str
    current_text: str
    gemini_text: str

    @classmethod
    def idle(cls) -> "StatusViewModel":
        return cls.without_run(
            status_text="尚未選擇資料夾",
            root_text="尚未選擇",
        )

    @classmethod
    def without_run(
        cls, *, status_text: str, root_text: str
    ) -> "StatusViewModel":
        return cls(
            light_color="red",
            status_text=status_text,
            root_text=root_text,
            video_count=0,
            image_count=0,
            total_size_text="0 B",
            progress_text="0 / 0",
            progress_percent=0,
            remaining_text="0",
            current_text="尚未開始",
            gemini_text="0 / 12",
        )

    @classmethod
    def from_run(
        cls,
        run: AnalysisRun,
        *,
        worker_alive: bool,
        now: datetime | None = None,
        supervisor_status: str | None = None,
        current_media_name: str = "",
        segment_number: int = 0,
        segment_total: int = 0,
        gemini_used: int = 0,
    ) -> "StatusViewModel":
        now = now or datetime.now(timezone.utc)
        heartbeat_age = cls._heartbeat_age(run.last_heartbeat, now)
        light_color = "red"
        if run.excel_sync_pending:
            status_text = "等待 Excel 關閉"
        elif supervisor_status == "restarting":
            status_text = "正在重新啟動"
        elif supervisor_status == "stopping_stale_worker":
            status_text = "正在安全停止無回應 worker"
        elif supervisor_status == "error":
            status_text = "worker 異常結束"
        elif supervisor_status == "stopped" or run.stop_requested:
            status_text = "已安全停止"
        elif worker_alive and heartbeat_age is not None and heartbeat_age <= 15:
            light_color = "green"
            status_text = "執行中"
        elif worker_alive and heartbeat_age is None:
            status_text = "正在啟動"
        elif worker_alive:
            status_text = "心跳逾時"
        elif supervisor_status == "completed" or run.status == "completed":
            status_text = "已完成"
        else:
            status_text = "worker 未執行"

        total = max(0, run.total_media)
        completed = min(max(0, run.completed_media), total)
        percent = round(completed * 100 / total) if total else 0
        current_text = current_media_name or "尚未開始"
        if current_media_name and segment_number and segment_total:
            current_text += f"｜第 {segment_number} / {segment_total} 段"
        return cls(
            light_color=light_color,
            status_text=status_text,
            root_text=str(run.root_path),
            video_count=run.video_count,
            image_count=run.image_count,
            total_size_text=cls._format_bytes(run.total_bytes),
            progress_text=f"{completed} / {total}",
            progress_percent=percent,
            remaining_text=str(max(0, total - completed)),
            current_text=current_text,
            gemini_text=f"{min(max(gemini_used, 0), 12)} / 12",
        )

    @staticmethod
    def _heartbeat_age(value: str | None, now: datetime) -> float | None:
        if not value:
            return None
        try:
            heartbeat = datetime.fromisoformat(value)
        except ValueError:
            return None
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        return max(0.0, (now - heartbeat).total_seconds())

    @staticmethod
    def _format_bytes(value: int) -> str:
        size = max(0, value)
        units = ("B", "KB", "MB", "GB", "TB")
        amount = float(size)
        unit = units[0]
        for unit in units:
            if amount < 1024 or unit == units[-1]:
                break
            amount /= 1024
        if unit == "B":
            return f"{int(amount)} {unit}"
        return f"{amount:.1f} {unit}"


@dataclass(frozen=True, slots=True)
class ControlState:
    select_enabled: bool
    start_enabled: bool
    stop_enabled: bool
    open_outputs_enabled: bool

    @classmethod
    def from_context(
        cls,
        *,
        has_workspace: bool,
        has_outputs: bool,
        busy: bool,
    ) -> "ControlState":
        return cls(
            select_enabled=not busy,
            start_enabled=has_workspace and not busy,
            stop_enabled=busy,
            open_outputs_enabled=has_outputs,
        )


def begin_selected_root(
    selected: str,
    supervisor: WorkerSupervisor,
    skill_root: Path,
) -> tuple[MediaWorkspace | None, str]:
    if not selected:
        return None, ""
    try:
        workspace = MediaWorkspace.from_root(Path(selected))
        supervisor.start_catalog(workspace.root, Path(skill_root).resolve())
    except (OSError, RuntimeError, WorkspacePathError) as error:
        return None, str(error)
    return workspace, ""


class StatusApplication:
    def __init__(
        self,
        root,
        *,
        skill_root: Path,
        media_root: Path | None = None,
        supervisor: WorkerSupervisor | None = None,
        folder_picker: Callable[[], str] | None = None,
    ) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk = tk
        self.messagebox = messagebox
        self.ttk = ttk
        self.root = root
        self.media_root = Path(media_root).resolve() if media_root else None
        self.skill_root = Path(skill_root).resolve()
        self.workspace = (
            MediaWorkspace.from_root(self.media_root)
            if self.media_root is not None
            else None
        )
        self.supervisor = supervisor or WorkerSupervisor()
        self.folder_picker = folder_picker or (
            lambda: filedialog.askdirectory(mustexist=True)
        )
        self.closing = False

        root.title("Media Catalog A+ 狀態監控")
        root.geometry("840x500")
        root.minsize(760, 460)
        root.configure(bg="#0F172A")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        initial = (
            StatusViewModel.without_run(
                status_text="正在啟動",
                root_text=str(self.media_root),
            )
            if self.media_root is not None
            else StatusViewModel.idle()
        )
        self.status_var = tk.StringVar(value=initial.status_text)
        self.path_var = tk.StringVar(value=initial.root_text)
        self.counts_var = tk.StringVar(value="影片：0　影像：0　總容量：0 B")
        self.progress_var = tk.StringVar(value="進度：0 / 0　未完成：0")
        self.current_var = tk.StringVar(value="目前：尚未開始")
        self.gemini_var = tk.StringVar(value="Gemini 強化：0 / 12")

        self._build_layout()
        self._render(initial)
        self._apply_control_state()
        self.root.after(1000, self._refresh)
        if self.media_root is not None:
            self.root.after(0, self._start)

    def _build_layout(self) -> None:
        tk = self.tk
        ttk = self.ttk
        panel = tk.Frame(self.root, bg="#0F172A", padx=28, pady=16)
        panel.pack(fill="both", expand=True)

        title = tk.Label(
            panel,
            text="Media Catalog A+",
            bg="#0F172A",
            fg="#F8FAFC",
            font=("Segoe UI Semibold", 22),
            anchor="w",
        )
        title.pack(fill="x")

        status_row = tk.Frame(panel, bg="#0F172A", pady=8)
        status_row.pack(fill="x")
        self.light = tk.Canvas(
            status_row,
            width=22,
            height=22,
            bg="#0F172A",
            highlightthickness=0,
        )
        self.light.pack(side="left")
        self.light_dot = self.light.create_oval(4, 4, 18, 18, fill="#EF4444", outline="")
        tk.Label(
            status_row,
            textvariable=self.status_var,
            bg="#0F172A",
            fg="#F8FAFC",
            font=("Segoe UI Semibold", 14),
        ).pack(side="left", padx=(8, 0))

        self._label(panel, "指定路徑", self.path_var, wraplength=650)
        self._label(panel, "媒體統計", self.counts_var)
        self._label(panel, "分析進度", self.progress_var)
        self._label(panel, "目前項目", self.current_var)
        self._label(panel, "雲端強化", self.gemini_var)

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Catalog.Horizontal.TProgressbar",
            troughcolor="#272F42",
            background="#22C55E",
            bordercolor="#475569",
            lightcolor="#22C55E",
            darkcolor="#22C55E",
            thickness=16,
        )
        self.progress = ttk.Progressbar(
            panel,
            mode="determinate",
            maximum=100,
            style="Catalog.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x", pady=(12, 14))

        buttons = tk.Frame(panel, bg="#0F172A")
        buttons.pack(fill="x", side="bottom")
        self.select_button = self._button(
            buttons, "選擇資料夾", self._choose_folder
        )
        self.select_button.pack(side="left", padx=(0, 6))
        self.start_button = self._button(buttons, "開始／繼續", self._start)
        self.start_button.pack(side="left", padx=6)
        self.stop_button = self._button(buttons, "安全停止", self._safe_stop)
        self.stop_button.pack(side="left", padx=6)
        self.excel_button = self._button(
            buttons,
            "開啟 Excel",
            lambda: self._open_workspace_path("excel"),
        )
        self.excel_button.pack(side="left", padx=6)
        self.result_button = self._button(
            buttons,
            "開啟成果資料夾",
            lambda: self._open_workspace_path("result"),
        )
        self.result_button.pack(side="left", padx=6)

    def _label(self, parent, title: str, variable, *, wraplength: int = 0) -> None:
        row = self.tk.Frame(parent, bg="#1E293B", padx=14, pady=6)
        row.pack(fill="x", pady=3)
        self.tk.Label(
            row,
            text=title,
            width=10,
            anchor="w",
            bg="#1E293B",
            fg="#94A3B8",
            font=("Segoe UI", 10),
        ).pack(side="left")
        self.tk.Label(
            row,
            textvariable=variable,
            anchor="w",
            justify="left",
            wraplength=wraplength,
            bg="#1E293B",
            fg="#F8FAFC",
            font=("Segoe UI", 11),
        ).pack(side="left", fill="x", expand=True)

    def _button(self, parent, text: str, command):
        return self.tk.Button(
            parent,
            text=text,
            command=command,
            bg="#334155",
            fg="#F8FAFC",
            activebackground="#475569",
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
            takefocus=True,
        )

    def _start(self) -> None:
        if self.workspace is None or self.media_root is None:
            return
        if self.supervisor.is_busy:
            return
        try:
            if (
                self.workspace.database_path.is_file()
                and self.workspace.excel_path.is_file()
            ):
                self.supervisor.start(self.media_root, self.skill_root)
            else:
                self.supervisor.start_catalog(
                    self.media_root, self.skill_root
                )
        except (OSError, RuntimeError, WorkspacePathError) as error:
            self.status_var.set(f"啟動失敗：{error}")
            self.light.itemconfigure(self.light_dot, fill="#EF4444")

    def _choose_folder(self) -> None:
        if self.supervisor.is_busy:
            return
        selected = self.folder_picker()
        workspace, error = begin_selected_root(
            selected, self.supervisor, self.skill_root
        )
        if error:
            self.messagebox.showerror("無法使用此資料夾", error)
            return
        if workspace is None:
            return
        self.workspace = workspace
        self.media_root = workspace.root
        self._render(
            StatusViewModel.without_run(
                status_text="正在建立／更新清冊",
                root_text=str(workspace.root),
            )
        )
        self._apply_control_state()

    def _refresh(self) -> None:
        try:
            snapshot = self.supervisor.poll()
            model = self._view_model(snapshot)
            self._render(model)
            self._apply_control_state()
            if self.closing and not snapshot.worker_alive:
                self.root.destroy()
                return
        except (OSError, RuntimeError) as error:
            self.status_var.set(f"狀態讀取失敗：{error}")
            self.light.itemconfigure(self.light_dot, fill="#EF4444")
        self.root.after(1000, self._refresh)

    def _view_model(self, snapshot: SupervisorSnapshot) -> StatusViewModel:
        run = snapshot.run
        if run is None:
            root_text = (
                str(self.media_root)
                if self.media_root is not None
                else "尚未選擇"
            )
            status_text = {
                "idle": "尚未選擇資料夾",
                "cataloging": "正在建立／更新清冊",
                "stopped": "已安全停止",
                "error": snapshot.error_text or "建立媒體清冊失敗",
            }.get(snapshot.status, "worker 未執行")
            return StatusViewModel.without_run(
                status_text=status_text,
                root_text=root_text,
            )
        media_name = ""
        segment_number = 0
        segment_total = 0
        gemini_used = 0
        if run.current_media_id:
            if self.workspace is None or self.supervisor.store is None:
                raise RuntimeError("Analysis run has no workspace state")
            record = CatalogDatabase(self.workspace.database_path).get_record(
                run.current_media_id
            )
            media_name = record.path.name if record is not None else run.current_media_id
            segment_number, segment_total = self.supervisor.store.segment_progress(
                run.current_media_id, run.current_segment_id
            )
            gemini_used, _ = self.supervisor.store.gemini_usage(run.current_media_id)
        return StatusViewModel.from_run(
            run,
            worker_alive=snapshot.worker_alive,
            supervisor_status=snapshot.status,
            current_media_name=media_name,
            segment_number=segment_number,
            segment_total=segment_total,
            gemini_used=gemini_used,
        )

    def _render(self, model: StatusViewModel) -> None:
        color = "#22C55E" if model.light_color == "green" else "#EF4444"
        self.light.itemconfigure(self.light_dot, fill=color)
        self.status_var.set(model.status_text)
        self.path_var.set(model.root_text)
        self.counts_var.set(
            f"影片：{model.video_count}　影像：{model.image_count}"
            f"　總容量：{model.total_size_text}"
        )
        self.progress_var.set(
            f"進度：{model.progress_text}　未完成：{model.remaining_text}"
        )
        self.current_var.set(f"目前：{model.current_text}")
        self.gemini_var.set(f"Gemini 強化：{model.gemini_text}")
        self.progress["value"] = model.progress_percent

    def _safe_stop(self) -> None:
        self.supervisor.request_safe_stop()

    def _apply_control_state(self) -> None:
        has_workspace = self.workspace is not None
        has_outputs = bool(
            self.workspace is not None
            and self.workspace.database_path.is_file()
            and self.workspace.excel_path.is_file()
        )
        state = ControlState.from_context(
            has_workspace=has_workspace,
            has_outputs=has_outputs,
            busy=self.supervisor.is_busy,
        )
        self.select_button.configure(
            state="normal" if state.select_enabled else "disabled"
        )
        self.start_button.configure(
            state="normal" if state.start_enabled else "disabled"
        )
        self.stop_button.configure(
            state="normal" if state.stop_enabled else "disabled"
        )
        output_state = "normal" if state.open_outputs_enabled else "disabled"
        self.excel_button.configure(state=output_state)
        self.result_button.configure(state=output_state)

    def _open_workspace_path(self, kind: str) -> None:
        if self.workspace is None:
            self.messagebox.showerror("無法開啟", "尚未選擇資料夾")
            return
        path = (
            self.workspace.excel_path
            if kind == "excel"
            else self.workspace.result_root
        )
        try:
            self._open(path)
        except OSError as error:
            self.messagebox.showerror("無法開啟", str(error))

    @staticmethod
    def _open(path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(path)
        os.startfile(str(path))

    def _on_close(self) -> None:
        snapshot = self.supervisor.poll()
        if not snapshot.worker_alive:
            self.root.destroy()
            return
        confirmed = self.messagebox.askokcancel(
            "安全停止後關閉",
            "程式會完成目前片段並寫入進度後關閉。\n"
            "要安全停止後關閉嗎？",
        )
        if confirmed:
            self.closing = True
            self.supervisor.request_safe_stop()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="media-catalog-status")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--skill-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        workspace = MediaWorkspace.from_root(arguments.root)
        if not workspace.database_path.is_file() or not workspace.excel_path.is_file():
            raise WorkspacePathError("找不到媒體清冊，請先建立清冊")
        import tkinter as tk

        root = tk.Tk()
        StatusApplication(
            root,
            media_root=workspace.root,
            skill_root=arguments.skill_root,
        )
        root.mainloop()
        return 0
    except (OSError, RuntimeError, WorkspacePathError) as error:
        print(f"MEDIA_STATUS_UI_ERROR {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
