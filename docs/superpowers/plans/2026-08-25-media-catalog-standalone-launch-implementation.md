# Media Catalog A+ Standalone Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 Media Catalog A+ Stable v1.2 可從桌面捷徑開啟無路徑 UI，手動選擇單一媒體資料夾後自動建立清冊並開始分析，同時保留原有 Codex 帶路徑自動執行方式。

**Architecture:** 保留現有 `WorkerSupervisor` 對分析 worker 的心跳、checkpoint 與單次恢復責任，新增獨立 `cataloging` 子程序階段；UI 允許 `media_root=None`，以純函式決定空白狀態與按鈕狀態，再由 Tkinter 資料夾選擇器接上 Supervisor。Skill launcher 改用私有 `pythonw.exe`，安裝器呼叫獨立 PowerShell 捷徑建立器，以結構化 `.lnk` 欄位提供無終端桌面入口。

**Tech Stack:** Python 3.11、Tkinter、SQLite、openpyxl、pytest 8、PowerShell 5.1、Windows Script Host COM (`WScript.Shell`)

## Global Constraints

- 適用版本固定為 Media Catalog A+ Stable v1.2，分支固定為 `codex/legacy-a-plus-v1.2`。
- 保留 CLI `media-catalog`、成果目錄 `媒體整理成果`、SQLite `catalog.sqlite`、Excel `媒體清冊.xlsx`，不得改成 Hybrid Lite 名稱。
- Codex 有 `--root` 時直接開始或繼續既有分析；桌面無 `--root` 時只開 UI，等待使用者選擇資料夾。
- 每次只管理一個根目錄；來源媒體不移動、不改名、不修改 metadata。
- 拒絕不存在、非資料夾、磁碟根目錄、`媒體整理成果`、符號連結與 reparse point。
- UI 主執行緒不得執行掃描、容量總和、FFmpeg、Ollama 或 Gemini；清冊建立與分析都由子程序完成。
- Gemini Key 只能來自私人環境變數；捷徑、日誌、SQLite、Excel、錯誤視窗、測試 fixture 與 Git 不得包含 Key。
- Gemini 模型契約維持 `gemini-3.7-flash`，單一媒體最多 12 次雲端強化，不變更用量上限。
- Excel 的「已審核」狀態、單一 worker 鎖、checkpoint、安全停止、心跳與單次自動恢復契約全部保留。
- 一般 `pytest` 不得呼叫 Gemini；live test 仍須明確 opt-in。
- 桌面捷徑固定命名為 `Media Catalog A+ Stable.lnk`，重複安裝原地更新，不建立編號副本。
- 不新增 EXE、MSIX、Windows Installer、常駐服務、系統匣或開機自動執行。

---

## File Structure

- `src/media_catalog/supervisor.py`：管理 `cataloging` 與既有 analysis 子程序、狀態轉換、錯誤清理及安全停止。
- `src/media_catalog/status_ui.py`：提供無路徑 UI、資料夾選擇、按鈕狀態、CLI 入口與 messagebox 錯誤顯示。
- `skills/media-inventory/scripts/run_media_analysis_ui.ps1`：以 Skill 私有 `pythonw.exe` 啟動 UI，選用 `RootPath`。
- `skills/media-inventory/scripts/run_media_analysis.ps1`：維持 Codex 帶 `RootPath` 的相容 wrapper。
- `scripts/create-media-catalog-shortcut.ps1`：只負責建立或更新 Windows `.lnk`，可用暫存 Desktop 做整合測試。
- `scripts/install-media-inventory-skill.ps1`：驗證 `pythonw.exe`、安裝 Skill、執行 smoke test 並建立桌面捷徑。
- `tests/test_supervisor.py`：覆蓋清冊子程序、轉入分析、錯誤與既有恢復行為。
- `tests/test_status_ui.py`：覆蓋空白 ViewModel、選擇／取消／拒絕路徑及控制項狀態。
- `tests/test_status_ui_main.py`：以 fake Tk 驗證有／無 `--root` 的入口，不開真實視窗。
- `tests/test_a_plus_launcher.py`：靜態驗證 `pythonw.exe`、選用路徑與 credential-free launcher。
- `tests/test_shortcut_installer.py`：在暫存 Desktop 實際建立、重建並讀回 `.lnk` 欄位。
- `tests/test_installer.py`：確認主安裝器驗證 runtime 並呼叫捷徑建立器。
- `tests/test_skill_package.py`：確認新增腳本進入正式 Skill 套件。
- `README.md`、`docs/media-catalog-a-plus-setup.md`、`skills/media-inventory/SKILL.md`：分別記錄桌面使用、安裝／恢復與 Codex 相容流程。

### Task 1: Supervisor cataloging lifecycle

**Files:**
- Modify: `src/media_catalog/supervisor.py:17-216`
- Modify: `tests/test_supervisor.py:11-199`

**Interfaces:**
- Consumes: `MediaWorkspace.from_root(root: Path) -> MediaWorkspace`、既有 `WorkerSupervisor.start(root: Path, skill_root: Path) -> int`。
- Produces: `SupervisorSnapshot.run: AnalysisRun | None`、`SupervisorSnapshot.error_text: str`、`WorkerSupervisor.start_catalog(root: Path, skill_root: Path) -> int`、`WorkerSupervisor.is_busy: bool`。
- Produces: `catalog_process_factory` 只為短時間 catalog command 擷取 stdout/stderr；既有 analysis `process_factory` 仍導向 `DEVNULL`，避免長時間 worker 因 pipe 填滿而停止。
- Produces: catalog command 必須是 `[python, "-m", "media_catalog.cli", "start", root]`；成功後自動呼叫既有 analysis 啟動路徑。

- [ ] **Step 1: Extend fake processes and write failing catalog lifecycle tests**

在 `tests/test_supervisor.py` 的 `FakeProcess` 增加可讀回輸出的介面，並新增三個測試：

```python
@dataclass
class FakeProcess:
    returncode: int | None = None
    terminate_calls: int = 0
    output: str = ""

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = -15

    def communicate(self) -> tuple[str, None]:
        return self.output, None


def test_start_catalog_launches_refresh_without_creating_workspace_on_ui_thread(
    tmp_path: Path,
) -> None:
    root = tmp_path / "媒體 資料"
    root.mkdir()
    catalog = FakeProcess()
    factory = ProcessFactory([catalog])
    supervisor = WorkerSupervisor(
        python_executable=Path(r"C:\runtime\python.exe"),
        catalog_process_factory=factory,
    )

    supervisor.start_catalog(root, Path(r"C:\skill"))
    snapshot = supervisor.poll()

    assert factory.arguments == [[
        r"C:\runtime\python.exe",
        "-m",
        "media_catalog.cli",
        "start",
        str(root.resolve()),
    ]]
    assert snapshot.status == "cataloging"
    assert snapshot.run is None
    assert snapshot.worker_alive is True
    assert supervisor.is_busy is True


def test_successful_catalog_automatically_starts_analysis(tmp_path: Path) -> None:
    root = tmp_path / "中文 & media"
    root.mkdir()
    (root / "sample.jpg").write_bytes(b"image")
    catalog = FakeProcess(returncode=0, output="MEDIA_CATALOG_READY")
    analysis = FakeProcess()

    def catalog_factory(arguments: list[str]) -> FakeProcess:
        if arguments[3] == "start":
            bootstrap_workspace(root)
            return catalog
        raise AssertionError(arguments)

    analysis_factory = ProcessFactory([analysis])

    supervisor = WorkerSupervisor(
        python_executable=Path(r"C:\runtime\python.exe"),
        process_factory=analysis_factory,
        catalog_process_factory=catalog_factory,
    )

    supervisor.start_catalog(root, Path(r"C:\skill"))
    snapshot = supervisor.poll()

    assert snapshot.status == "starting"
    assert snapshot.run is not None
    assert supervisor.arguments is not None
    assert supervisor.arguments[3] == "analyze-all"


def test_failed_catalog_does_not_start_analysis_or_expose_environment_value(
    tmp_path: Path,
) -> None:
    root = tmp_path / "media"
    root.mkdir()
    catalog = FakeProcess(
        returncode=2,
        output=(
            "MEDIA_CATALOG_ERROR GEMINI_"
            "API_KEY=secret-value permission denied"
        ),
    )
    factory = ProcessFactory([catalog])
    supervisor = WorkerSupervisor(catalog_process_factory=factory)

    supervisor.start_catalog(root, tmp_path / "skill")
    snapshot = supervisor.poll()

    assert snapshot.status == "error"
    assert snapshot.worker_alive is False
    assert "permission denied" in snapshot.error_text
    assert "secret-value" not in snapshot.error_text
    assert len(factory.arguments) == 1
```

再加入 cataloging 安全停止測試：

```python
def test_safe_stop_terminates_only_the_active_catalog_process(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    catalog = FakeProcess()
    supervisor = WorkerSupervisor(
        catalog_process_factory=ProcessFactory([catalog])
    )
    supervisor.start_catalog(root, tmp_path / "skill")

    supervisor.request_safe_stop()
    snapshot = supervisor.poll()

    assert catalog.terminate_calls == 1
    assert snapshot.status == "stopped"
    assert snapshot.run is None
```

- [ ] **Step 2: Run the new tests and verify the expected failures**

Run:

```powershell
$env:PYTHONUTF8='1'
python -m pytest `
  tests/test_supervisor.py::test_start_catalog_launches_refresh_without_creating_workspace_on_ui_thread `
  tests/test_supervisor.py::test_successful_catalog_automatically_starts_analysis `
  tests/test_supervisor.py::test_failed_catalog_does_not_start_analysis_or_expose_environment_value -v
```

Expected: FAIL because `start_catalog`、`is_busy` and `error_text` do not exist and `SupervisorSnapshot.run` is not optional.

- [ ] **Step 3: Implement the minimal cataloging state machine**

在 `src/media_catalog/supervisor.py` 將 snapshot 改為：

```python
@dataclass(frozen=True, slots=True)
class SupervisorSnapshot:
    status: str
    worker_alive: bool
    run: AnalysisRun | None
    exit_code: int | None = None
    error_text: str = ""
```

先讓 `WorkerProcess` protocol 明確包含所有 `subprocess.Popen` 都具備的 `communicate()`：

```python
class WorkerProcess(Protocol):
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def communicate(self) -> tuple[str | None, object]: ...
```

新增 `_spawn_catalog_process()`，只供短時間 `start` command 使用：

```python
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
```

`WorkerSupervisor.__init__()` 新增 `catalog_process_factory: Callable[[list[str]], WorkerProcess] = _spawn_catalog_process`，但既有 `process_factory` 仍使用 `_spawn_process()` 的 `DEVNULL`。新增 `self.catalog_process`、`self.catalog_workspace`、`self.catalog_skill_root`、`self._catalog_stop_requested`，並實作：

```python
@property
def is_busy(self) -> bool:
    processes = (self.catalog_process, self.process)
    return any(process is not None and process.poll() is None for process in processes)

def start_catalog(self, root: Path, skill_root: Path) -> int:
    if self.is_busy:
        return self._active_process_id()
    workspace = MediaWorkspace.from_root(root)
    self.catalog_workspace = workspace
    self.catalog_skill_root = Path(skill_root).resolve()
    arguments = [
        self.python_executable,
        "-m",
        "media_catalog.cli",
        "start",
        str(workspace.root),
    ]
    self._catalog_stop_requested = False
    self.catalog_process = self.catalog_process_factory(arguments)
    return int(getattr(self.catalog_process, "pid", 1))

def _active_process_id(self) -> int:
    for process in (self.catalog_process, self.process):
        if process is not None and process.poll() is None:
            return int(getattr(process, "pid", 1))
    raise RuntimeError("Supervisor has no active process")
```

在 `poll()` 最前面處理 catalog process：存活時回傳 `SupervisorSnapshot("cataloging", True, None)`；若 `_catalog_stop_requested` 且程序已退出，回傳 `stopped`；exit 0 時清除 catalog handle、呼叫 `start(workspace.root, skill_root)` 並回傳 analysis `poll()`；非 0 時讀取 `communicate()[0]`、清理 credential assignment 與控制字元、截斷至 240 字元後回傳 `error`。沒有任何 process／run 時回傳 `SupervisorSnapshot("idle", False, None)`。

`request_safe_stop()` 若 catalog process 存活，設定 `_catalog_stop_requested=True` 並只 terminate 該 exact handle；清冊掃描沒有可續傳的單檔 checkpoint，但來源唯讀，下一次選擇同一 root 時由 `start` 安全重建。analysis process 仍沿用既有資料庫 stop request，不直接 terminate。

錯誤清理使用明確 helper，不將任意環境值帶進 UI：

```python
def _sanitize_process_message(value: str) -> str:
    cleaned = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    cleaned = re.sub(
        r"(?i)(GEMINI_API_KEY|GOOGLE_API_KEY)\s*=\s*\S+",
        r"\1=[REDACTED]",
        cleaned,
    )
    return cleaned[-240:] or "建立媒體清冊失敗"
```

既有 analysis `start()`、stale heartbeat、單次 restart、checkpoint 與 safe stop 分支只需配合 optional `run` 型別，不更改決策順序。

- [ ] **Step 4: Run Supervisor tests**

Run:

```powershell
$env:PYTHONUTF8='1'
python -m pytest tests/test_supervisor.py -v
```

Expected: all Supervisor tests PASS；既有 `analyze-all` argument-array、一次重啟、重複崩潰與安全停止測試仍通過。

- [ ] **Step 5: Commit the catalog lifecycle**

```powershell
git add src/media_catalog/supervisor.py tests/test_supervisor.py
git diff --cached --check
git commit -m "feat: catalog media before desktop analysis"
```

### Task 2: Idle UI, folder selection, and control states

**Files:**
- Modify: `src/media_catalog/status_ui.py:17-339`
- Modify: `tests/test_status_ui.py:1-101`

**Interfaces:**
- Consumes: `WorkerSupervisor.start_catalog(root, skill_root)`、`WorkerSupervisor.is_busy`、optional `SupervisorSnapshot.run` from Task 1。
- Produces: `StatusViewModel.idle() -> StatusViewModel`、`ControlState.from_context(has_workspace: bool, has_outputs: bool, busy: bool) -> ControlState`、`begin_selected_root(selected: str, supervisor: WorkerSupervisor, skill_root: Path) -> tuple[MediaWorkspace | None, str]`。
- Produces: `StatusApplication(root, *, skill_root: Path, media_root: Path | None = None, supervisor: WorkerSupervisor | None = None, folder_picker: Callable[[], str] | None = None)`。

- [ ] **Step 1: Write failing pure-state and selection tests**

在 `tests/test_status_ui.py` 新增：

```python
from media_catalog.status_ui import (
    ControlState,
    StatusViewModel,
    begin_selected_root,
)


class RecordingSupervisor:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    def start_catalog(self, root: Path, skill_root: Path) -> int:
        self.calls.append((root, skill_root))
        return 1


def test_idle_view_model_has_no_selected_root_or_progress() -> None:
    model = StatusViewModel.idle()

    assert model.light_color == "red"
    assert model.status_text == "尚未選擇資料夾"
    assert model.root_text == "尚未選擇"
    assert model.progress_text == "0 / 0"
    assert model.remaining_text == "0"


def test_cancel_selection_does_not_create_results_or_start_process(
    tmp_path: Path,
) -> None:
    supervisor = RecordingSupervisor()

    workspace, error = begin_selected_root("", supervisor, tmp_path / "skill")

    assert workspace is None
    assert error == ""
    assert supervisor.calls == []
    assert list(tmp_path.iterdir()) == []


def test_valid_selection_starts_catalog_without_creating_results_in_ui(
    tmp_path: Path,
) -> None:
    root = tmp_path / "中文 & media"
    root.mkdir()
    supervisor = RecordingSupervisor()

    workspace, error = begin_selected_root(
        str(root), supervisor, tmp_path / "skill"
    )

    assert error == ""
    assert workspace is not None
    assert supervisor.calls == [(root.resolve(), (tmp_path / "skill").resolve())]
    assert not (root / "媒體整理成果").exists()


def test_invalid_selection_returns_actionable_error_without_starting(
    tmp_path: Path,
) -> None:
    supervisor = RecordingSupervisor()

    workspace, error = begin_selected_root(
        str(tmp_path / "missing"), supervisor, tmp_path / "skill"
    )

    assert workspace is None
    assert "找不到資料夾" in error
    assert supervisor.calls == []


@pytest.mark.parametrize(
    ("has_workspace", "has_outputs", "busy", "select", "start", "open_outputs"),
    [
        (False, False, False, True, False, False),
        (True, False, True, False, False, False),
        (True, True, True, False, False, True),
        (True, True, False, True, True, True),
    ],
)
def test_control_state_prevents_root_switch_while_busy(
    has_workspace: bool,
    has_outputs: bool,
    busy: bool,
    select: bool,
    start: bool,
    open_outputs: bool,
) -> None:
    state = ControlState.from_context(
        has_workspace=has_workspace,
        has_outputs=has_outputs,
        busy=busy,
    )

    assert state.select_enabled is select
    assert state.start_enabled is start
    assert state.open_outputs_enabled is open_outputs
```

- [ ] **Step 2: Run the UI state tests and verify they fail**

Run:

```powershell
$env:PYTHONUTF8='1'
python -m pytest tests/test_status_ui.py -v
```

Expected: FAIL because `idle`、`ControlState` and `begin_selected_root` are not defined.

- [ ] **Step 3: Add testable UI state helpers**

在 `status_ui.py` 新增 immutable control model：

```python
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
```

為 `StatusViewModel` 新增 `idle()`，所有數值為 0、紅燈、路徑為「尚未選擇」。新增選擇 helper：

```python
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
```

- [ ] **Step 4: Wire the helpers into Tkinter without blocking the event loop**

將 `StatusApplication` 建構子改為 optional root，並保存按鈕物件：

```python
def __init__(
    self,
    root,
    *,
    skill_root: Path,
    media_root: Path | None = None,
    supervisor: WorkerSupervisor | None = None,
    folder_picker: Callable[[], str] | None = None,
) -> None:
    from tkinter import filedialog, messagebox, ttk

    self.media_root = Path(media_root).resolve() if media_root else None
    self.workspace = (
        MediaWorkspace.from_root(self.media_root) if self.media_root else None
    )
    self.supervisor = supervisor or WorkerSupervisor()
    self.folder_picker = folder_picker or (
        lambda: filedialog.askdirectory(mustexist=True)
    )
```

初始字串使用 `StatusViewModel.idle()`；新增並保存 `self.select_button`、`self.start_button`、`self.stop_button`、`self.excel_button`、`self.result_button`。`_choose_folder()` 呼叫 picker 與 `begin_selected_root()`，錯誤時 `messagebox.showerror("無法使用此資料夾", error)`；成功時設定 `media_root/workspace`、狀態為「正在建立／更新清冊」，但不在 UI thread 呼叫 bootstrap、資料庫或容量掃描。

建構完成後永遠以 `root.after(1000, self._refresh)` 啟動 poll loop；只有 `media_root` 非空的 Codex 模式再以 `root.after(0, self._start)` 自動開始既有 analysis。`_start()` 檢查 workspace：SQLite 與 Excel 都存在時呼叫既有 `supervisor.start()`；尚未建立或 cataloging 失敗後重試時呼叫 `supervisor.start_catalog()`。`_refresh()` 能處理 `snapshot.run is None` 的 `idle`、`cataloging`、`stopped` 與 `error`；`_on_close()` 在 idle 時直接 destroy，不呼叫尚未開始的 run。

每次 render 後呼叫 `_apply_control_state()`，以 Tk `state="normal"`／`"disabled"` 套用 `ControlState`。`has_outputs` 只有在 SQLite 與 Excel 都存在時為 true，避免 cataloging 尚未完成就開啟不存在的檔案。分析或 cataloging 存活時選擇按鈕停用；safe stop 完成後重新啟用。所有開啟按鈕改經 `_open_workspace_path(path)` 包裝，若檔案在按下前被移除，以 `messagebox.showerror()` 顯示錯誤而不是讓 Tk callback traceback 消失。

- [ ] **Step 5: Run UI and workspace regression tests**

Run:

```powershell
$env:PYTHONUTF8='1'
python -m pytest tests/test_status_ui.py tests/test_workspace.py -v
```

Expected: all tests PASS；取消選擇不產生成果目錄，中文與 `&` 路徑以 `Path` 物件原樣傳遞。

- [ ] **Step 6: Commit the manual-selection UI**

```powershell
git add src/media_catalog/status_ui.py tests/test_status_ui.py
git diff --cached --check
git commit -m "feat: select media folders from the status UI"
```

### Task 3: Optional-root CLI entry and credential-free pythonw launcher

**Files:**
- Modify: `src/media_catalog/status_ui.py:341-370`
- Create: `tests/test_status_ui_main.py`
- Modify: `skills/media-inventory/scripts/run_media_analysis_ui.ps1:1-19`
- Modify: `tests/test_a_plus_launcher.py:16-25`

**Interfaces:**
- Consumes: optional-root `StatusApplication` from Task 2。
- Produces: `_parser()` with optional `--root` and optional `--skill-root`；`main([])` derives the Skill root from the private runtime and creates an idle UI without starting a worker。
- Produces: PowerShell `RootPath` optional；launcher uses `.runtime\Scripts\pythonw.exe` and structured argument array。

- [ ] **Step 1: Write failing parser and fake-Tk main tests**

建立 `tests/test_status_ui_main.py`：

```python
from pathlib import Path

import media_catalog.status_ui as status_ui


class FakeRoot:
    def __init__(self) -> None:
        self.mainloop_calls = 0
        self.destroy_calls = 0

    def mainloop(self) -> None:
        self.mainloop_calls += 1

    def destroy(self) -> None:
        self.destroy_calls += 1


def test_parser_accepts_no_arguments_for_desktop_mode() -> None:
    arguments = status_ui._parser().parse_args([])

    assert arguments.root is None
    assert arguments.skill_root is None


def test_main_builds_idle_application_without_root(
    tmp_path: Path, monkeypatch,
) -> None:
    fake_root = FakeRoot()
    captured: dict[str, object] = {}
    monkeypatch.setattr(status_ui, "_create_tk_root", lambda: fake_root)
    monkeypatch.setattr(status_ui, "_default_skill_root", lambda: tmp_path)
    monkeypatch.setattr(
        status_ui,
        "StatusApplication",
        lambda root, **kwargs: captured.update(root=root, **kwargs),
    )

    result = status_ui.main([])

    assert result == 0
    assert captured["media_root"] is None
    assert captured["skill_root"] == tmp_path.resolve()
    assert fake_root.mainloop_calls == 1


def test_main_keeps_existing_catalog_requirement_for_codex_root(
    tmp_path: Path, monkeypatch,
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    fake_root = FakeRoot()
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(status_ui, "_create_tk_root", lambda: fake_root)
    monkeypatch.setattr(
        status_ui,
        "_show_startup_error",
        lambda _root, title, message: errors.append((title, message)),
    )

    result = status_ui.main([
        "--root", str(media_root), "--skill-root", str(tmp_path)
    ])

    assert result == 2
    assert "找不到媒體清冊" in errors[0][1]
```

- [ ] **Step 2: Update launcher assertions before implementation**

將 `tests/test_a_plus_launcher.py` 的 UI launcher 測試改為：

```python
def test_ui_launcher_uses_private_pythonw_and_optional_root() -> None:
    launcher = (SKILL_ROOT / "scripts/run_media_analysis_ui.ps1").read_text(
        encoding="utf-8"
    )

    assert "$env:PYTHONUTF8 = '1'" in launcher
    assert ".runtime\\Scripts\\pythonw.exe" in launcher
    assert "$uiArguments = @(" in launcher
    assert "'-m', 'media_catalog.status_ui'" in launcher
    assert "if (-not [string]::IsNullOrWhiteSpace($RootPath))" in launcher
    assert "GEMINI_API_KEY" not in launcher
    assert "gemini-3.7-flash" not in launcher
```

- [ ] **Step 3: Run entry and launcher tests and verify failure**

Run:

```powershell
$env:PYTHONUTF8='1'
python -m pytest tests/test_status_ui_main.py tests/test_a_plus_launcher.py -v
```

Expected: FAIL because `--root` is required, Tk helpers are absent, and launcher still uses `python.exe`.

- [ ] **Step 4: Implement optional-root main with visible startup errors**

在 `status_ui.py` 新增：

```python
def _create_tk_root():
    import tkinter as tk
    return tk.Tk()


def _show_startup_error(root, title: str, message: str) -> None:
    from tkinter import messagebox
    root.withdraw()
    messagebox.showerror(title, message, parent=root)
    root.destroy()


def _default_skill_root() -> Path:
    candidate = Path(sys.prefix).resolve().parent
    launcher = candidate / "scripts/run_media_analysis_ui.ps1"
    if not launcher.is_file():
        raise WorkspacePathError(
            "無法從私有 runtime 判斷 Skill 路徑，請重新執行安裝器"
        )
    return candidate
```

將 `--root` 與 `--skill-root` 都改為 `required=False, default=None`。`main()` 在未提供 skill root 時呼叫 `_default_skill_root()`；正式私有 runtime 的 `sys.prefix` 是 `<installed-skill>\.runtime`，因此 parent 即 installed Skill。若有 `--root`，保留「SQLite 與 Excel 必須存在」檢查並將錯誤交給 `_show_startup_error()`；若無 root，直接建立 `StatusApplication(..., media_root=None)`。Skill root 推導錯誤同樣顯示 messagebox；只有 Tk 本身無法初始化時使用 `MEDIA_STATUS_UI_ERROR` stderr fallback。

- [ ] **Step 5: Convert the Skill UI launcher to pythonw and argument arrays**

將 `run_media_analysis_ui.ps1` 改為：

```powershell
param(
    [string]$RootPath = ''
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$skillRoot = Split-Path -Parent $PSScriptRoot
$runtimePythonw = Join-Path $skillRoot '.runtime\Scripts\pythonw.exe'

if (-not (Test-Path -LiteralPath $runtimePythonw -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "MEDIA_STATUS_UI_ERROR missing_skill_runtime=$runtimePythonw"
    )
    exit 2
}

$uiArguments = @(
    '-m', 'media_catalog.status_ui',
    '--skill-root', $skillRoot
)
if (-not [string]::IsNullOrWhiteSpace($RootPath)) {
    $uiArguments += @('--root', $RootPath)
}

& $runtimePythonw @uiArguments
exit $LASTEXITCODE
```

不更動 `run_media_analysis.ps1` 的 mandatory `RootPath`，確保 Codex Skill 仍將使用者路徑轉交給 UI。

- [ ] **Step 6: Run entry, launcher, and PowerShell syntax tests**

Run:

```powershell
$env:PYTHONUTF8='1'
python -m pytest tests/test_status_ui_main.py tests/test_a_plus_launcher.py -v
$errors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path 'skills/media-inventory/scripts/run_media_analysis_ui.ps1'),
  [ref]$null,
  [ref]$errors
)
if ($errors.Count -ne 0) { throw ($errors | Out-String) }
```

Expected: pytest PASS and PowerShell parser reports zero errors.

- [ ] **Step 7: Commit the standalone launcher entry**

```powershell
git add `
  src/media_catalog/status_ui.py `
  tests/test_status_ui_main.py `
  skills/media-inventory/scripts/run_media_analysis_ui.ps1 `
  tests/test_a_plus_launcher.py
git diff --cached --check
git commit -m "feat: launch Media Catalog UI without a preset root"
```

### Task 4: Idempotent Windows desktop shortcut installation

**Files:**
- Create: `scripts/create-media-catalog-shortcut.ps1`
- Create: `tests/test_shortcut_installer.py`
- Modify: `scripts/install-media-inventory-skill.ps1:57-148`
- Modify: `tests/test_installer.py:29-37`

**Interfaces:**
- Consumes: installed Skill `scripts/run_media_analysis_ui.ps1` and `.runtime\Scripts\pythonw.exe` from Task 3。
- Produces: `create-media-catalog-shortcut.ps1 -SkillRoot <path> [-DesktopPath <path>]`；成功輸出 `MEDIA_CATALOG_SHORTCUT_READY path=<absolute-link>`。
- Produces: `.lnk` fields: `TargetPath=powershell.exe`、hidden launcher arguments、installed Skill working directory、`shell32.dll,3` icon and fixed description。

- [ ] **Step 1: Write failing end-to-end shortcut tests against a temporary Desktop**

建立 `tests/test_shortcut_installer.py`：

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def run_powershell(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, *arguments],
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        capture_output=True,
        check=False,
    )


def test_shortcut_script_creates_one_credential_free_link_and_updates_in_place(
    tmp_path: Path,
) -> None:
    project = Path(__file__).resolve().parents[1]
    script = project / "scripts/create-media-catalog-shortcut.ps1"
    skill = tmp_path / "installed skill"
    scripts = skill / "scripts"
    runtime = skill / ".runtime/Scripts"
    desktop = tmp_path / "桌面"
    scripts.mkdir(parents=True)
    runtime.mkdir(parents=True)
    desktop.mkdir()
    (scripts / "run_media_analysis_ui.ps1").write_text("# fixture", encoding="utf-8")
    (runtime / "pythonw.exe").write_bytes(b"fixture")

    first = run_powershell(
        str(script), "-SkillRoot", str(skill), "-DesktopPath", str(desktop)
    )
    second = run_powershell(
        str(script), "-SkillRoot", str(skill), "-DesktopPath", str(desktop)
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    links = list(desktop.glob("Media Catalog A+ Stable.lnk"))
    assert len(links) == 1

    inspect = subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($args[0]);"
            "[pscustomobject]@{TargetPath=$s.TargetPath;Arguments=$s.Arguments;"
            "WorkingDirectory=$s.WorkingDirectory;IconLocation=$s.IconLocation;"
            "Description=$s.Description}|ConvertTo-Json -Compress",
            str(links[0]),
        ],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    fields = json.loads(inspect.stdout)
    serialized = json.dumps(fields, ensure_ascii=False)

    assert fields["TargetPath"].lower().endswith("powershell.exe")
    assert "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass" in fields["Arguments"]
    assert "run_media_analysis_ui.ps1" in fields["Arguments"]
    assert fields["WorkingDirectory"] == str(skill.resolve())
    assert "shell32.dll,3" in fields["IconLocation"]
    assert fields["Description"] == "Media Catalog A+ Stable 媒體整理與分析"
    assert "GEMINI_API_KEY" not in serialized
    assert "gemini-3.7-flash" not in serialized
```

另加無效 Desktop 測試，確認不向其他位置 fallback：

```python
def test_shortcut_script_rejects_missing_desktop(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    (skill / ".runtime/Scripts").mkdir(parents=True)
    (skill / "scripts/run_media_analysis_ui.ps1").write_text(
        "# fixture", encoding="utf-8"
    )
    (skill / ".runtime/Scripts/pythonw.exe").write_bytes(b"fixture")
    result = run_powershell(
        str(project / "scripts/create-media-catalog-shortcut.ps1"),
        "-SkillRoot", str(skill),
        "-DesktopPath", str(tmp_path / "missing desktop"),
    )

    assert result.returncode == 1
    assert "Desktop" in result.stderr
    assert list(tmp_path.rglob("*.lnk")) == []
```

- [ ] **Step 2: Run shortcut tests and verify the script-missing failure**

Run:

```powershell
$env:PYTHONUTF8='1'
python -m pytest tests/test_shortcut_installer.py -v
```

Expected: FAIL because `scripts/create-media-catalog-shortcut.ps1` does not exist.

- [ ] **Step 3: Implement the validated `.lnk` creator**

建立 `scripts/create-media-catalog-shortcut.ps1`，依序：

1. `Resolve-Path -LiteralPath $SkillRoot` 並拒絕非資料夾／reparse point。
2. 驗證 installed UI launcher 與 `.runtime\Scripts\pythonw.exe` 都是檔案。
3. 若未傳 `DesktopPath`，使用 `[Environment]::GetFolderPath('Desktop')`；將其解析為既有、非 reparse point 資料夾。
4. 以 `(Get-Command powershell.exe -CommandType Application).Source` 取得 TargetPath。
5. 使用 `New-Object -ComObject WScript.Shell` 與 `CreateShortcut()` 設定欄位後 `Save()`。
6. 讀回同一捷徑，逐欄確認 TargetPath、Arguments、WorkingDirectory、IconLocation、Description；任一不符即 exit 1。
7. Arguments 固定為 `-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "<installed launcher>"`，不加入 root、model 或 credential。

PowerShell 字串只對 `.lnk` Arguments 做雙引號 escaping；所有檔案驗證使用 `-LiteralPath`，不以 shell 拼接執行命令。

- [ ] **Step 4: Add installer checks and shortcut invocation tests**

在 `tests/test_installer.py` 新增靜態契約：

```python
def test_installer_requires_pythonw_and_creates_desktop_shortcut() -> None:
    installer = (
        Path(__file__).resolve().parents[1]
        / "scripts/install-media-inventory-skill.ps1"
    ).read_text(encoding="utf-8")

    assert "Scripts\\pythonw.exe" in installer
    assert "create-media-catalog-shortcut.ps1" in installer
    assert "-SkillRoot $destinationFull" in installer
    assert "MEDIA_CATALOG_SHORTCUT_READY" in installer
```

修改主安裝器：在 tkinter import 成功後驗證 `$runtimePythonw`；smoke catalog 成功後執行 repository 的 shortcut script，收集輸出並要求包含 `MEDIA_CATALOG_SHORTCUT_READY`，最後才輸出 `MEDIA_INVENTORY_SKILL_READY`。捷徑失敗時保留已複製 Skill 與已建立舊版備份，catch 回報現有 `backup=<path>`，不得另選其他 Desktop。

- [ ] **Step 5: Run shortcut and installer tests**

Run:

```powershell
$env:PYTHONUTF8='1'
python -m pytest `
  tests/test_shortcut_installer.py `
  tests/test_installer.py `
  tests/test_a_plus_launcher.py -v
```

Expected: all tests PASS；暫存 Desktop 中只有一份同名捷徑。

- [ ] **Step 6: Parse every changed PowerShell file**

Run:

```powershell
$files = @(
  'scripts/create-media-catalog-shortcut.ps1',
  'scripts/install-media-inventory-skill.ps1',
  'skills/media-inventory/scripts/run_media_analysis_ui.ps1',
  'skills/media-inventory/scripts/run_media_analysis.ps1'
)
foreach ($file in $files) {
  $tokens = $null
  $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path -LiteralPath $file),
    [ref]$tokens,
    [ref]$errors
  )
  if ($errors.Count -ne 0) { throw "$file`n$($errors | Out-String)" }
}
```

Expected: zero parser errors.

- [ ] **Step 7: Commit desktop shortcut installation**

```powershell
git add `
  scripts/create-media-catalog-shortcut.ps1 `
  scripts/install-media-inventory-skill.ps1 `
  tests/test_shortcut_installer.py `
  tests/test_installer.py
git diff --cached --check
git commit -m "feat: install a Media Catalog desktop shortcut"
```

### Task 5: Package documentation, security checks, and full acceptance

**Files:**
- Modify: `README.md:14-31`
- Modify: `docs/media-catalog-a-plus-setup.md:1-80`
- Modify: `skills/media-inventory/SKILL.md:20-38`
- Modify: `tests/test_skill_package.py:1-30`
- Modify: `tests/test_a_plus_security.py:1-end`

**Interfaces:**
- Consumes: completed UI、launcher、shortcut creator and installer from Tasks 1-4。
- Produces: copyable Codex and desktop instructions；package/security tests that prevent omission or secret regression。

- [ ] **Step 1: Write failing package and security assertions**

在 `tests/test_skill_package.py` 加入：

```python
def test_skill_contains_standalone_ui_launcher() -> None:
    launcher = SKILL_ROOT / "scripts/run_media_analysis_ui.ps1"

    assert launcher.is_file()
    text = launcher.read_text(encoding="utf-8")
    assert ".runtime\\Scripts\\pythonw.exe" in text
    assert "[string]$RootPath = ''" in text
```

在 `tests/test_a_plus_security.py` 將新增 shortcut script、launcher 與文件納入既有 credential scan，並明確禁止 launcher／shortcut 出現 `GEMINI_API_KEY`、`GOOGLE_API_KEY` 或 Gemini model literal。

- [ ] **Step 2: Run package and security tests**

Run:

```powershell
$env:PYTHONUTF8='1'
python -m pytest tests/test_skill_package.py tests/test_a_plus_security.py -v
```

Expected: package assertion may fail until documentation/package references are updated；security scan must not invoke network.

- [ ] **Step 3: Document both entry modes and recovery behavior**

更新文件，內容必須包含以下可直接操作流程：

```text
Codex：貼上「整理並分析這個資料夾：D:\你的媒體資料夾」，沿用自動建立清冊與自動分析。

桌面：雙擊「Media Catalog A+ Stable」→ 按「選擇資料夾」→ 選定根目錄；UI 會顯示「正在建立／更新清冊」，完成後自動進入分析。

紅燈「尚未選擇資料夾」不是錯誤；取消選擇不會建立任何成果。
分析中若要換資料夾，先按「安全停止」，等 worker 退出後再重新選擇。
```

`docs/media-catalog-a-plus-setup.md` 另外記錄：捷徑固定名稱與位置、重裝會原地更新、沒有黑色終端視窗、安裝失敗時查看 `MEDIA_INVENTORY_SKILL_ERROR`、私有 runtime 缺少 `pythonw.exe` 時重新執行安裝器。`SKILL.md` 保留 Codex 路徑自動啟動描述，增加「桌面入口不需 Codex」段落，但不要求 Codex Skill 在已帶 root 時再次跳 picker。

- [ ] **Step 4: Run the complete offline suite**

Run:

```powershell
$env:PYTHONUTF8='1'
python -m pytest -v
```

Expected: all tests PASS；Gemini live test skips unless its explicit opt-in variable is set。

- [ ] **Step 5: Run source and credential hygiene checks**

Run:

```powershell
git diff --check
$contentHits = rg -n --hidden `
  --glob '!**/.git/**' `
  --glob '!docs/superpowers/**' `
  'AIza[0-9A-Za-z_-]{20,}|GEMINI_API_KEY\s*=\s*[^$]' .
if ($LASTEXITCODE -eq 0) { throw "疑似 credential 進入工作樹：`n$contentHits" }
if ($LASTEXITCODE -gt 1) { throw 'credential scan failed' }
```

Expected: `git diff --check` clean；沒有真實 Key 或 hard-coded assignment。文件與測試可以提到環境變數名稱，但不得包含值。

- [ ] **Step 6: Commit package documentation**

```powershell
git add `
  README.md `
  docs/media-catalog-a-plus-setup.md `
  skills/media-inventory/SKILL.md `
  tests/test_skill_package.py `
  tests/test_a_plus_security.py
git diff --cached --check
git commit -m "docs: explain standalone Media Catalog startup"
```

- [ ] **Step 7: Install the completed Skill and create the real desktop shortcut**

先確認目前沒有執行中的 A+ worker，再安裝：

```powershell
$env:PYTHONUTF8='1'
$active = Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -match 'media_catalog\.(status_ui|cli)' -and
  $_.CommandLine -match 'media-inventory'
}
if ($active) { throw "請先安全停止現有 Media Catalog 程序：$($active.ProcessId -join ', ')" }

powershell -NoProfile -ExecutionPolicy Bypass `
  -File 'scripts/install-media-inventory-skill.ps1'
if ($LASTEXITCODE -ne 0) { throw "Skill 安裝失敗：$LASTEXITCODE" }
```

Expected: output 同時包含 `MEDIA_CATALOG_SHORTCUT_READY` 與 `MEDIA_INVENTORY_SKILL_READY`；舊版 Skill 被保留在 timestamped backup，Desktop 有唯一 `Media Catalog A+ Stable.lnk`。

- [ ] **Step 8: Inspect the real link and launch the idle UI once**

```powershell
$desktop = [Environment]::GetFolderPath('Desktop')
$linkPath = Join-Path $desktop 'Media Catalog A+ Stable.lnk'
if (-not (Test-Path -LiteralPath $linkPath -PathType Leaf)) {
  throw "找不到桌面捷徑：$linkPath"
}
$link = (New-Object -ComObject WScript.Shell).CreateShortcut($linkPath)
[pscustomobject]@{
  TargetPath = $link.TargetPath
  Arguments = $link.Arguments
  WorkingDirectory = $link.WorkingDirectory
  IconLocation = $link.IconLocation
  Description = $link.Description
} | Format-List

$before = @(Get-Process pythonw -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
Start-Process -FilePath $linkPath
Start-Sleep -Seconds 3
$ui = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -eq 'pythonw.exe' -and
  $_.CommandLine -match 'media_catalog\.status_ui' -and
  $_.CommandLine -match '\\.codex\\skills\\media-inventory'
}
if (@($ui).Count -ne 1) { throw "預期 1 個 idle UI，實際 $(@($ui).Count)" }
if ($ui.CommandLine -match '--root') { throw '桌面 idle UI 不應預載根目錄' }
```

目視確認只有「Media Catalog A+ 狀態監控」視窗、紅燈「尚未選擇資料夾」、可用「選擇資料夾」，且沒有 PowerShell／Python 黑色終端。完成後關閉 UI；以 exact PID 與 command line 確認只處理本次 installed Skill 的 idle UI：

```powershell
$ui = @(Get-CimInstance Win32_Process | Where-Object {
  $_.Name -eq 'pythonw.exe' -and
  $_.CommandLine -match 'media_catalog\.status_ui' -and
  $_.CommandLine -match '\\.codex\\skills\\media-inventory' -and
  $_.CommandLine -notmatch '--root'
})
if ($ui.Count -ne 1) { throw "無法唯一識別 idle UI" }
$pidToClose = [int]$ui[0].ProcessId
Stop-Process -Id $pidToClose -ErrorAction Stop
Start-Sleep -Seconds 1
if (Get-Process -Id $pidToClose -ErrorAction SilentlyContinue) {
  throw "idle UI 未關閉：$pidToClose"
}
$residual = @(Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -match 'media_catalog\.(status_ui|cli)' -and
  $_.CommandLine -match '\\.codex\\skills\\media-inventory'
})
if ($residual.Count -ne 0) {
  throw "仍有 A+ UI/worker 程序：$($residual.ProcessId -join ', ')"
}
```

這個強制關閉只允許用在尚未選擇根目錄、沒有 worker 與沒有寫入工作的 idle 驗證視窗；如果已選擇資料夾，必須改用 UI 的「安全停止」。

- [ ] **Step 9: Final repository verification**

Run:

```powershell
$env:PYTHONUTF8='1'
python -m pytest -q
git diff --check
git status --short --branch
git log --oneline -5
```

Expected: all offline tests pass；工作樹 clean；最近提交依序包含 catalog lifecycle、manual-selection UI、standalone launcher、desktop shortcut 與文件提交。不得推送、建立 PR 或合併，除非使用者另外授權。
