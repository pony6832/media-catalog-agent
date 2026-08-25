# Media Catalog A+ 強制 Gemini UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Media Catalog A+ Stable UI 加入可確認費用上限、跳過已審核項目、同時強化影片與照片且可安全恢復的「強制 Gemini 強化」模式。

**Architecture:** 以明確的 `AnalysisMode` 從 UI 經 supervisor、CLI 傳入批次與片段管線，並在 SQLite schema v2 保存 run mode、是否已準備與 force generation。普通模式沿用條件式 Gemini；強制模式先保留本地結果，再對照片建立一個 Gemini 作業，對影片依品質問題與時間覆蓋選最多 12 段。UI 的資料夾選擇只更新清冊，完成後才讓使用者選擇普通或強制啟動。

**Tech Stack:** Python 3.11、Tkinter、SQLite、openpyxl、pytest、FFmpeg／FFprobe、Ollama、Gemini REST client、Windows `pythonw.exe`

## Global Constraints

- 只修改 Media Catalog A+ Stable；不得修改 `media-catalog-hybrid-lite`、`媒體整理成果-HybridLite`、`catalog-hybrid-lite.sqlite`、`媒體清冊-HybridLite.xlsx` 或 `codex/hybrid-lite-v1`。
- 強制模式固定使用有效模型 `gemini-3.7-flash`；若 `GEMINI_MODEL` 已設定，必須完全等於此值。
- 不得上傳完整影片、原始大圖、本機完整路徑或任何 API Key。
- 每張未審核照片每個 force run 最多一個 Gemini 作業；首次失敗後只重試一次。
- 每支未審核影片每個 force run 最多 12 個 Gemini 片段，每片段 1～3 張縮圖。
- Excel 狀態下拉維持 `待確認,已審核`，且任何自動分析都不得覆寫 `已審核`。
- Gemini 失敗保存本地結果，在 Excel「錯誤原因」顯示 `Gemini 強化失敗`，並繼續下一項。
- 原始媒體只讀，不移動、不改名、不覆寫、不在來源旁建立 sidecar。
- 所有新增主控台子程序使用 `HIDDEN_PROCESS_CREATION_FLAGS`，不得閃現 PowerShell／黑色視窗。
- 不得把 API Key 寫入命令列、SQLite、Excel、日誌、捷徑、測試 fixture 或 Git。

---

## File Structure

- Create `src/media_catalog/analysis_mode.py`: 定義 `AnalysisMode`、CLI 值轉換與模式顯示名稱。
- Create `src/media_catalog/force_gemini.py`: 強制任務估算、環境檢查、已審核排除、照片強化與影片片段選擇的集中邊界。
- Modify `src/media_catalog/schema_migration.py`: schema v2，為 run 保存 `analysis_mode`、`force_generation`、`force_prepared`。
- Modify `src/media_catalog/run_state.py`: 建立／恢復 auto 與 force run、一次性 force 準備、模式 round-trip。
- Modify `src/media_catalog/database.py`: 保留既有分析欄位地重排 force 項目，並允許已分析結果帶安全警告。
- Modify `src/media_catalog/analysis_runtime.py`: 暴露強制照片分析器，沿用同一 Gemini client、StageRunner 與安全預覽器。
- Modify `src/media_catalog/segment_pipeline.py`: 普通模式不變；強制模式採本地兩階段分析後最多選 12 段 Gemini。
- Modify `src/media_catalog/batch_analysis.py`: 依 mode 選擇照片／影片流程、跳過已審核、持久化 warning 與恢復進度。
- Modify `src/media_catalog/cli.py`: `analyze-all --mode --run-id` 參數與輸出 marker。
- Modify `src/media_catalog/supervisor.py`: 清冊完成後停在 ready；啟動與重啟保留 mode／run id。
- Modify `src/media_catalog/status_ui.py`: 橘色按鈕、確認視窗、環境檢查、估算與 control state。
- Modify `src/media_catalog/excel_catalog.py`: 保留已審核並顯示非致命 Gemini warning。
- Modify `README.md` and `docs/media-catalog-a-plus-setup.md`: 說明兩種模式、費用上限、隱私與恢復。
- Modify existing tests and create `tests/test_force_gemini.py`: TDD regression coverage.

---

### Task 1: Persistent Analysis Mode and Force Run Identity

**Files:**
- Create: `src/media_catalog/analysis_mode.py`
- Modify: `src/media_catalog/schema_migration.py:11-170`
- Modify: `src/media_catalog/run_state.py:18-151,441-492,513-531`
- Test: `tests/test_schema_migration.py`
- Test: `tests/test_run_state.py`

**Interfaces:**
- Produces: `AnalysisMode(str, Enum)` with values `AUTO = "auto"` and `FORCE_GEMINI = "force-gemini"`.
- Produces: `RunStateStore.begin_run(*, root_path: Path, video_count: int, image_count: int, total_bytes: int, mode: AnalysisMode) -> tuple[AnalysisRun, bool]`; boolean is true only for a newly created force run that still needs preparation.
- Produces: `AnalysisRun.analysis_mode: AnalysisMode`, `force_generation: int`, `force_prepared: bool`.
- Produces: `RunStateStore.mark_force_prepared(run_id: str) -> None`.
- Consumes later: supervisor, CLI and batch orchestration use the returned `run_id` and `analysis_mode` without environment toggles.

- [ ] **Step 1: Write failing schema and run-mode tests**

```python
from media_catalog.analysis_mode import AnalysisMode


def test_force_run_mode_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    store = RunStateStore(path)
    run, needs_prepare = store.begin_run(
        root_path=tmp_path,
        video_count=1,
        image_count=2,
        total_bytes=30,
        mode=AnalysisMode.FORCE_GEMINI,
    )

    reopened = RunStateStore(path).get_run(run.run_id)

    assert needs_prepare is True
    assert reopened is not None
    assert reopened.analysis_mode is AnalysisMode.FORCE_GEMINI
    assert reopened.force_generation == 1
    assert reopened.force_prepared is False


def test_incomplete_force_run_is_resumed_instead_of_recreated(tmp_path: Path) -> None:
    store = RunStateStore(tmp_path / "catalog.sqlite")
    first, _ = store.begin_run(
        root_path=tmp_path,
        video_count=1,
        image_count=0,
        total_bytes=10,
        mode=AnalysisMode.FORCE_GEMINI,
    )
    store.mark_force_prepared(first.run_id)
    resumed, needs_prepare = store.begin_run(
        root_path=tmp_path,
        video_count=1,
        image_count=0,
        total_bytes=10,
        mode=AnalysisMode.FORCE_GEMINI,
    )

    assert resumed.run_id == first.run_id
    assert needs_prepare is False
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest -q tests/test_schema_migration.py tests/test_run_state.py
```

Expected: FAIL because `analysis_mode.py`, schema v2 columns and `begin_run` do not exist.

- [ ] **Step 3: Add the explicit mode type and schema v2 migration**

```python
# src/media_catalog/analysis_mode.py
from enum import Enum


class AnalysisMode(str, Enum):
    AUTO = "auto"
    FORCE_GEMINI = "force-gemini"

    @property
    def label(self) -> str:
        return "Gemini 強制強化" if self is self.FORCE_GEMINI else "普通分析"
```

Set `LATEST_SCHEMA_VERSION = 2`. Migration v2 must add these columns only when absent:

```sql
ALTER TABLE analysis_runs ADD COLUMN analysis_mode TEXT NOT NULL DEFAULT 'auto';
ALTER TABLE analysis_runs ADD COLUMN force_generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE analysis_runs ADD COLUMN force_prepared INTEGER NOT NULL DEFAULT 1;
```

Retain the existing verified SQLite／Excel backup before migration. Insert schema version 2 only after all statements succeed.

- [ ] **Step 4: Implement run creation and resume rules**

```python
def begin_run(
    self,
    *,
    root_path: Path,
    video_count: int,
    image_count: int,
    total_bytes: int,
    mode: AnalysisMode,
) -> tuple[AnalysisRun, bool]:
    """Resume an incomplete matching run or create the next run generation."""
```

Rules:

- Auto mode keeps the deterministic `run_id_for_root()` identity for backward compatibility.
- Force mode resumes the latest non-completed force run for the root.
- If no incomplete force run exists, create `f"force-{root_digest}-{generation:04d}"` with generation equal to previous maximum plus one and `force_prepared = 0`.
- `mark_force_prepared()` flips the flag in one committed SQLite transaction.
- `_to_run()` validates `analysis_mode` through `AnalysisMode(row["analysis_mode"])`; an unknown stored value raises `ValueError` instead of silently switching modes.

- [ ] **Step 5: Run focused tests and full run-state tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_schema_migration.py tests/test_run_state.py
```

Expected: PASS, including existing atomic 12-slot tests.

- [ ] **Step 6: Commit**

```powershell
git add src/media_catalog/analysis_mode.py src/media_catalog/schema_migration.py src/media_catalog/run_state.py tests/test_schema_migration.py tests/test_run_state.py
git commit -m "feat: persist analysis mode across force runs"
```

---

### Task 2: Force Eligibility, Estimate, and Reviewed Protection

**Files:**
- Create: `src/media_catalog/force_gemini.py`
- Modify: `src/media_catalog/database.py:157-229`
- Test: `tests/test_force_gemini.py`
- Test: `tests/test_database.py`

**Interfaces:**
- Consumes: `AnalysisMode` and `MediaRecord`.
- Produces: `ForceGeminiEstimate(video_count, image_count, reviewed_count, normal_request_limit, retry_attempt_limit)`.
- Produces: `plan_force_run(records: Iterable[MediaRecord], reviewed_paths: set[str]) -> tuple[ForceGeminiEstimate, tuple[str, ...]]`; second value contains eligible record IDs.
- Produces: `CatalogDatabase.requeue_for_force(record_ids: Sequence[str]) -> int`, preserving analysis fields while setting status to pending and clearing only the prior runtime error.
- Produces: `CatalogDatabase.save_analysis(record_id: str, *, description: str, highlights: tuple[str, ...], keywords: tuple[str, ...], warning: str | None = None) -> MediaRecord`.

- [ ] **Step 1: Write failing estimate and reviewed-path tests**

```python
def _record(identity: str, path: Path, media_type: str) -> MediaRecord:
    return MediaRecord(
        id=identity,
        path=path,
        fingerprint=f"fingerprint-{identity}",
        media_type=media_type,
        status=Status.ANALYZED,
        description="舊描述",
        highlights=("舊重點",),
        keywords=("舊關鍵字",),
    )


def test_force_plan_excludes_reviewed_and_reports_both_limits(tmp_path: Path) -> None:
    records = (
        _record("photo", tmp_path / "a.jpg", "image/jpeg"),
        _record("video", tmp_path / "b.mp4", "video/mp4"),
        _record("reviewed", tmp_path / "c.jpg", "image/jpeg"),
    )

    estimate, eligible = plan_force_run(
        records, {str((tmp_path / "c.jpg").resolve())}
    )

    assert eligible == ("photo", "video")
    assert estimate == ForceGeminiEstimate(
        video_count=1,
        image_count=1,
        reviewed_count=1,
        normal_request_limit=13,
        retry_attempt_limit=26,
    )


def test_requeue_for_force_preserves_old_analysis_until_replacement(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"photo")
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    discovered = database.upsert_discovered(source, "fingerprint", "image/jpeg")
    record = database.save_analysis(
        discovered.id,
        description="舊描述",
        highlights=("舊重點",),
        keywords=("舊關鍵字",),
    )

    assert database.requeue_for_force((record.id,)) == 1
    queued = database.get_record(record.id)

    assert queued is not None
    assert queued.status is Status.PENDING
    assert queued.description == "舊描述"
    assert queued.keywords == ("舊關鍵字",)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_force_gemini.py tests/test_database.py
```

Expected: FAIL because planning and force requeue APIs are missing.

- [ ] **Step 3: Implement deterministic eligibility and limits**

```python
@dataclass(frozen=True, slots=True)
class ForceGeminiEstimate:
    video_count: int
    image_count: int
    reviewed_count: int
    normal_request_limit: int
    retry_attempt_limit: int


def plan_force_run(
    records: Iterable[MediaRecord], reviewed_paths: set[str]
) -> tuple[ForceGeminiEstimate, tuple[str, ...]]:
    reviewed = {str(Path(item).resolve()).casefold() for item in reviewed_paths}
    catalog = tuple(records)
    eligible = tuple(
        item for item in catalog
        if str(item.path.resolve()).casefold() not in reviewed
        and item.media_type.startswith(("image/", "video/"))
    )
    video_count = sum(item.media_type.startswith("video/") for item in eligible)
    image_count = sum(item.media_type.startswith("image/") for item in eligible)
    reviewed_count = sum(
        str(item.path.resolve()).casefold() in reviewed for item in catalog
    )
    normal_limit = image_count + video_count * 12
    return (
        ForceGeminiEstimate(
            video_count,
            image_count,
            reviewed_count,
            normal_limit,
            normal_limit * 2,
        ),
        tuple(item.id for item in eligible),
    )
```

Normalize both record and reviewed paths with `str(path.resolve()).casefold()`. Count only `image/` and `video/` records. Compute `normal_request_limit = image_count + video_count * 12` and `retry_attempt_limit = normal_request_limit * 2`.

- [ ] **Step 4: Implement atomic force requeue and warning persistence**

`requeue_for_force()` must use one SQLite transaction and an explicit ID list; an empty list performs no SQL update. It updates only `status`, `error`, and `updated_at`, leaving description／highlights／keywords intact. Extend `save_analysis()` with `warning=None` and persist the sanitized warning into `error` while keeping status `ANALYZED`.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_force_gemini.py tests/test_database.py tests/test_excel_catalog.py
```

Expected: PASS; existing Excel reviewed-status tests remain green.

- [ ] **Step 6: Commit**

```powershell
git add src/media_catalog/force_gemini.py src/media_catalog/database.py tests/test_force_gemini.py tests/test_database.py
git commit -m "feat: plan force Gemini work without touching reviewed media"
```

---

### Task 3: CLI and Supervisor Mode Propagation

**Files:**
- Modify: `src/media_catalog/cli.py:29-145`
- Modify: `src/media_catalog/supervisor.py:82-336`
- Test: `tests/test_cli.py`
- Test: `tests/test_supervisor.py`

**Interfaces:**
- Consumes: `AnalysisMode`, `RunStateStore.begin_run()`.
- Produces: CLI `analyze-all ROOT --skill-root PATH --mode {auto,force-gemini} --run-id ID`.
- Produces: `WorkerSupervisor.start(root, skill_root, *, mode=AnalysisMode.AUTO) -> int`.
- Produces: catalog terminal snapshot status `catalog_ready`; catalog completion no longer auto-starts analysis.
- Produces: worker restart reuses the exact original mode and run id arguments.

- [ ] **Step 1: Write failing CLI and supervisor tests**

```python
def test_force_cli_passes_mode_and_run_id_to_batch(tmp_path: Path, monkeypatch) -> None:
    root = _catalog_root_with_one_pending_photo(tmp_path)
    captured: dict[str, object] = {}

    def fake_batch(*_args, **kwargs):
        captured.update(kwargs)
        return BatchAnalysisResult(1, 0, 0, 0)

    monkeypatch.setattr(cli_module, "analyze_pending", fake_batch)

    exit_code = main([
        "analyze-all", str(root),
        "--skill-root", str(tmp_path),
        "--mode", "force-gemini",
        "--run-id", "force-root-1",
    ], runtime_builder=lambda **_kwargs: SuccessfulAnalyzer())

    assert exit_code == 0
    assert captured["mode"] is AnalysisMode.FORCE_GEMINI
    assert captured["run_id"] == "force-root-1"


def test_catalog_completion_waits_for_user_mode_choice(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (root / "sample.jpg").write_bytes(b"image")
    catalog = FakeProcess(returncode=0, output="MEDIA_CATALOG_READY")
    analysis_factory = ProcessFactory([])

    def catalog_factory(_arguments: list[str]) -> FakeProcess:
        bootstrap_workspace(root)
        return catalog

    supervisor = WorkerSupervisor(
        catalog_process_factory=catalog_factory,
        process_factory=analysis_factory,
    )
    supervisor.start_catalog(root, tmp_path / "skill")

    snapshot = supervisor.poll()

    assert snapshot.status == "catalog_ready"
    assert snapshot.worker_alive is False
    assert analysis_factory.arguments == []
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_cli.py tests/test_supervisor.py
```

Expected: FAIL because CLI options, mode-aware start and `catalog_ready` do not exist.

- [ ] **Step 3: Add strict CLI options and markers**

Use `choices=tuple(mode.value for mode in AnalysisMode)` and parse to `AnalysisMode`. Require `--run-id` for `force-gemini`; reject an auto run receiving a force-prefixed ID. Progress and terminal output include `f"mode={mode.value}"` but never credentials.

- [ ] **Step 4: Stop automatic analysis after catalog completion**

Change `WorkerSupervisor.poll()` so a successful catalog process returns:

```python
SupervisorSnapshot("catalog_ready", False, None, exit_code=0)
```

It must not call `self.start()`.

- [ ] **Step 5: Preserve mode through start and restart**

`WorkerSupervisor.start()` calls `begin_run()`, stores `analysis_mode`, and builds a fixed argument array containing `--mode` and `--run-id`. `_restart_worker()` reuses the array without recomputing a run. The worker process continues to use `HIDDEN_PROCESS_CREATION_FLAGS`.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_cli.py tests/test_supervisor.py tests/test_status_ui.py
```

Expected: PASS after updating old tests that previously expected automatic post-catalog analysis.

- [ ] **Step 7: Commit**

```powershell
git add src/media_catalog/cli.py src/media_catalog/supervisor.py tests/test_cli.py tests/test_supervisor.py tests/test_status_ui.py
git commit -m "feat: pass explicit analysis mode through workers"
```

---

### Task 4: Forced Photo Gemini Analyzer

**Files:**
- Modify: `src/media_catalog/force_gemini.py`
- Modify: `src/media_catalog/analysis_runtime.py:32-171`
- Test: `tests/test_force_gemini.py`
- Test: `tests/test_analysis_runtime.py`

**Interfaces:**
- Produces: `ForceImageResult(analysis: Analysis, warning: str | None, gemini_used: bool)`.
- Produces: `ForceImageAnalyzer.analyze(source: Path) -> ForceImageResult`.
- Consumes: `LocalAnalyzer`, `FfmpegImagePreparer`, `GeminiClient`, `StageRunner`.
- `AnalysisRuntime.force_image_analyzer` is available to batch analysis.

- [ ] **Step 1: Write failing successful and failed-cloud tests**

```python
GOOD = Analysis("本地清楚描述。", ("本地重點",), ("本地",))
STRONG = Analysis("Gemini 強化描述。", ("雲端重點",), ("Gemini",))


class StaticLocalAnalyzer:
    def __init__(self, result: Analysis) -> None:
        self.result = result

    def analyze(self, _source: Path) -> Analysis:
        return self.result


class Previewer:
    def __init__(self, root: Path) -> None:
        self.root = root

    def prepare(self, _source: Path) -> Path:
        preview = self.root / "preview.jpg"
        preview.write_bytes(b"preview")
        return preview


class RecordingGemini:
    is_configured = True

    def __init__(self, result: Analysis) -> None:
        self.result = result
        self.requests: list[GeminiSegmentRequest] = []

    def analyze(self, request: GeminiSegmentRequest) -> Analysis:
        self.requests.append(request)
        return self.result


class AlwaysFailingGemini:
    is_configured = True

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, _request: GeminiSegmentRequest) -> Analysis:
        self.calls += 1
        raise GeminiError("provider unavailable")


def _photo(tmp_path: Path) -> Path:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"photo")
    return source


def _force_image_analyzer(
    tmp_path: Path, local: Analysis, gemini
) -> ForceImageAnalyzer:
    return ForceImageAnalyzer(
        local_analyzer=StaticLocalAnalyzer(local),
        image_preparer=Previewer(tmp_path),
        gemini_client=gemini,
        stage_runner=StageRunner(),
    )


def test_force_photo_calls_gemini_even_when_local_result_is_good(tmp_path: Path) -> None:
    gemini = RecordingGemini(STRONG)
    analyzer = _force_image_analyzer(tmp_path, local=GOOD, gemini=gemini)

    result = analyzer.analyze(_photo(tmp_path))

    assert result.analysis == STRONG
    assert result.warning is None
    assert result.gemini_used is True
    assert len(gemini.requests) == 1
    assert len(gemini.requests[0].frames) == 1


def test_force_photo_keeps_local_result_after_two_cloud_attempts(tmp_path: Path) -> None:
    gemini = AlwaysFailingGemini()
    analyzer = _force_image_analyzer(tmp_path, local=GOOD, gemini=gemini)

    result = analyzer.analyze(_photo(tmp_path))

    assert result.analysis == GOOD
    assert result.warning == "Gemini 強化失敗:GeminiError"
    assert gemini.calls == 2
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_force_gemini.py tests/test_analysis_runtime.py
```

Expected: FAIL because `ForceImageAnalyzer` is missing.

- [ ] **Step 3: Implement local-first forced photo flow**

```python
@dataclass(frozen=True, slots=True)
class ForceImageResult:
    analysis: Analysis
    warning: str | None
    gemini_used: bool


class ForceImageAnalyzer:
    def analyze(self, source: Path) -> ForceImageResult:
        local = self.local_analyzer.analyze(source)
        preview = self.image_preparer.prepare(source)
        cloud = self.stage_runner.run(
            "gemini_force_image",
            lambda _timeout: self.gemini_client.analyze(
                GeminiSegmentRequest((preview,), "", local)
            ),
            StagePolicy(90, 1),
        )
        if cloud.ok and cloud.value is not None:
            return ForceImageResult(cloud.value, None, True)
        warning = f"Gemini 強化失敗:{cloud.error_type or 'unknown'}"
        return ForceImageResult(local, warning, False)
```

`StagePolicy(90, 1)` means one retry after the first failure. If Gemini is not configured, raise `GeminiError` before any database status changes. Sanitize warning to error type only; never include provider payload or key.

- [ ] **Step 4: Wire the analyzer into `AnalysisRuntime`**

Build one shared `GeminiClient`, `FfmpegImagePreparer`, and `StageRunner`. Return them through `AnalysisRuntime` so ordinary and forced flows do not create incompatible clients or preview locations.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_force_gemini.py tests/test_analysis_runtime.py tests/test_gemini_client.py
```

Expected: PASS; no live API call runs unless `RUN_GEMINI_LIVE_TEST=1`.

- [ ] **Step 6: Commit**

```powershell
git add src/media_catalog/force_gemini.py src/media_catalog/analysis_runtime.py tests/test_force_gemini.py tests/test_analysis_runtime.py
git commit -m "feat: force Gemini enhancement for photos"
```

---

### Task 5: Forced Video Selection and Two-Phase Enhancement

**Files:**
- Modify: `src/media_catalog/force_gemini.py`
- Modify: `src/media_catalog/segment_pipeline.py:33-269`
- Modify: `src/media_catalog/run_state.py:236-381,441-492`
- Test: `tests/test_force_gemini.py`
- Test: `tests/test_segment_pipeline.py`

**Interfaces:**
- Produces: `select_force_segments(segments: Sequence[VideoSegment], *, limit: int = 12) -> tuple[str, ...]`.
- Produces: `SegmentPipeline.analyze_video(record, run_id, *, mode=AnalysisMode.AUTO) -> VideoAnalysisResult`.
- Extends: `VideoAnalysisResult.warning: str | None = None`; force mode returns `f"Gemini 強化失敗:{count}個片段"` when cloud failures fall back to local results.
- Produces: `RunStateStore.reset_video_for_force(run_id, video_id) -> None`, called only during one-time force preparation.
- Consumes: selected frame count, local analysis JSON and `assess_analysis_quality()`.

- [ ] **Step 1: Write failing deterministic selection tests**

```python
def _segment(index: int, *, weak: bool) -> VideoSegment:
    analysis = WEAK if weak else GOOD
    return VideoSegment(
        segment_id=f"video-1:{index}",
        run_id="run-1",
        video_id="video-1",
        segment_index=index,
        start_seconds=float(index * 10),
        end_seconds=float((index + 1) * 10),
        status="completed",
        selected_frames=(Path(f"frame-{index}.jpg"),),
        local_result_json=json.dumps({
            "description": analysis.description,
            "highlights": list(analysis.highlights),
            "keywords": list(analysis.keywords),
        }, ensure_ascii=False),
    )


def test_force_selection_prioritizes_weak_segments_and_covers_timeline() -> None:
    segments = tuple(
        _segment(index, weak=index in {1, 18}) for index in range(20)
    )

    chosen = select_force_segments(segments, limit=12)

    assert len(chosen) == 12
    assert "video-1:1" in chosen
    assert "video-1:18" in chosen
    chosen_indexes = sorted(int(item.rsplit(":", 1)[1]) for item in chosen)
    assert chosen_indexes[0] <= 1
    assert chosen_indexes[-1] >= 18
```

- [ ] **Step 2: Write failing forced-video behavior tests**

```python
def _pipeline(
    tmp_path: Path, *, count: int, local: Analysis
) -> tuple[SegmentPipeline, RecordingGemini]:
    gemini = RecordingGemini()
    pipeline = SegmentPipeline(
        segmenter=FakeSegmenter(count),
        selector=ThreeFrameSelector(),
        local_analyzer=RecordingLocalAnalyzer(local),
        gemini_client=gemini,
        store=_store(tmp_path),
        output_root=tmp_path / "segments",
    )
    return pipeline, gemini


def test_force_video_calls_gemini_for_good_segments_up_to_twelve(tmp_path: Path) -> None:
    pipeline, gemini = _pipeline(tmp_path, count=15, local=GOOD)

    result = pipeline.analyze_video(
        _video_record(tmp_path), "run-1", mode=AnalysisMode.FORCE_GEMINI
    )

    assert result.gemini_segments == 12
    assert result.warning is None
    assert len(gemini.requests) == 12
    assert all(1 <= len(request.frames) <= 3 for request in gemini.requests)


def test_auto_video_still_skips_gemini_for_good_segments(tmp_path: Path) -> None:
    pipeline, gemini = _pipeline(tmp_path, count=2, local=GOOD)

    result = pipeline.analyze_video(
        _video_record(tmp_path), "run-1", mode=AnalysisMode.AUTO
    )

    assert result.gemini_segments == 0
    assert gemini.requests == []
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_force_gemini.py tests/test_segment_pipeline.py
```

Expected: FAIL because mode-aware two-phase video analysis and selection are missing.

- [ ] **Step 4: Implement deterministic issue-first, time-covered selection**

Parse each local result and classify it with `assess_analysis_quality()`. If weak candidates exceed 12, choose 12 evenly across the ordered weak list. Otherwise include all weak candidates and fill remaining positions by evenly spaced indexes from non-weak candidates. Return IDs ordered by original `segment_index` for predictable processing.

- [ ] **Step 5: Split force video into local and cloud phases**

For `FORCE_GEMINI`:

1. Segment and locally analyze every incomplete segment, persisting selected frames and local JSON.
2. Call `select_force_segments()` after all local checkpoints exist.
3. For each chosen segment lacking cloud JSON, atomically consume one existing 12-segment／36-frame slot and call Gemini with `StagePolicy(90, 1)`.
4. Cloud failure sets `needs_review` and a safe `f"cloud_failed:{error_type}"` error but leaves the local checkpoint completed.
5. Summarize with cloud JSON when present, otherwise local JSON.
6. If any selected cloud operation failed, return a nonfatal aggregate `warning`; do not expose provider response text.

For `AUTO`, retain the current conditional call path and all existing tests.

- [ ] **Step 6: Implement one-time video reset for a new force run**

`reset_video_for_force()` deletes prior `video_segments` and the prior per-video `gemini_usage` row inside one transaction, but never deletes the catalog record's description／highlights／keywords. It is called only before `mark_force_prepared()`, never during resume.

- [ ] **Step 7: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_force_gemini.py tests/test_segment_pipeline.py tests/test_run_state.py
```

Expected: PASS, including auto-mode quota, restart and local fallback tests.

- [ ] **Step 8: Commit**

```powershell
git add src/media_catalog/force_gemini.py src/media_catalog/segment_pipeline.py src/media_catalog/run_state.py tests/test_force_gemini.py tests/test_segment_pipeline.py tests/test_run_state.py
git commit -m "feat: force Gemini on representative video segments"
```

---

### Task 6: Batch Preparation, Safe Warnings, and Resume

**Files:**
- Modify: `src/media_catalog/batch_analysis.py:27-203`
- Modify: `src/media_catalog/excel_catalog.py:88-151`
- Modify: `src/media_catalog/cli.py:100-147`
- Test: `tests/test_batch_analysis.py`
- Test: `tests/test_excel_catalog.py`
- Test: `tests/test_a_plus_headless_smoke.py`

**Interfaces:**
- Consumes: `mode: AnalysisMode`, `run_id: str`, force planner, `force_image_analyzer`, mode-aware video pipeline.
- Produces: `analyze_pending(workspace: MediaWorkspace, analyzer: Analyzer, *, excel_writer=write_excel, progress=None, mode: AnalysisMode = AnalysisMode.AUTO, run_id: str | None = None, reviewed_paths: set[str] | None = None) -> BatchAnalysisResult`.
- Produces: force result warnings persisted into `MediaRecord.error` while record status is `ANALYZED`.

- [ ] **Step 1: Write failing batch protection tests**

```python
class RecordingForceImages:
    def __init__(self, result: ForceImageResult) -> None:
        self.result = result
        self.sources: list[Path] = []

    def analyze(self, source: Path) -> ForceImageResult:
        self.sources.append(source.resolve())
        return self.result


class ForceRuntime:
    def __init__(self, store: RunStateStore, force_images: RecordingForceImages) -> None:
        self.run_state = store
        self.segment_pipeline = object()
        self.force_image_analyzer = force_images

    def analyze(self, _source: Path) -> Analysis:
        raise AssertionError("auto photo analysis must not run in force mode")


LOCAL = Analysis("本地描述。", ("本地重點",), ("本地",))


def test_force_batch_skips_reviewed_and_requeues_unreviewed_once(tmp_path: Path) -> None:
    workspace = _workspace_with_media(tmp_path, ("reviewed.jpg", "eligible.jpg"))
    database = CatalogDatabase(workspace.database_path)
    records = {item.path.name: item for item in database.list_records()}
    workbook = load_workbook(workspace.excel_path)
    sheet = workbook["媒體清冊"]
    reviewed_path = str(records["reviewed.jpg"].path.resolve())
    for row in range(2, sheet.max_row + 1):
        if sheet.cell(row, 3).value == reviewed_path:
            sheet.cell(row, 1).value = "已審核"
    workbook.save(workspace.excel_path)
    workbook.close()
    reviewed_paths = read_reviewed_paths(workspace.excel_path)
    store = RunStateStore(workspace.database_path, excel_path=workspace.excel_path)
    run, _ = store.begin_run(
        root_path=workspace.root,
        video_count=0,
        image_count=2,
        total_bytes=10,
        mode=AnalysisMode.FORCE_GEMINI,
    )
    force_images = RecordingForceImages(ForceImageResult(LOCAL, None, True))
    runtime = ForceRuntime(store, force_images)

    first = analyze_pending(
        workspace,
        runtime,
        mode=AnalysisMode.FORCE_GEMINI,
        run_id=run.run_id,
        reviewed_paths=reviewed_paths,
    )
    second = analyze_pending(
        workspace,
        runtime,
        mode=AnalysisMode.FORCE_GEMINI,
        run_id=run.run_id,
        reviewed_paths=reviewed_paths,
    )

    assert force_images.sources == [records["eligible.jpg"].path.resolve()]
    assert first.analyzed == 1
    assert second.analyzed == 0


def test_force_batch_saves_local_result_with_nonfatal_gemini_warning(tmp_path: Path) -> None:
    workspace = _workspace_with_media(tmp_path, ("photo.jpg",))
    store = RunStateStore(workspace.database_path, excel_path=workspace.excel_path)
    run, _ = store.begin_run(
        root_path=workspace.root,
        video_count=0,
        image_count=1,
        total_bytes=5,
        mode=AnalysisMode.FORCE_GEMINI,
    )
    force_images = RecordingForceImages(
        ForceImageResult(LOCAL, "Gemini 強化失敗:GeminiError", False)
    )
    runtime = ForceRuntime(store, force_images)

    analyze_pending(
        workspace,
        runtime,
        mode=AnalysisMode.FORCE_GEMINI,
        run_id=run.run_id,
        reviewed_paths=set(),
    )

    record = CatalogDatabase(workspace.database_path).list_records()[0]
    assert record.status is Status.ANALYZED
    assert record.description == LOCAL.description
    assert record.error == "Gemini 強化失敗:GeminiError"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_batch_analysis.py tests/test_excel_catalog.py
```

Expected: FAIL because batch mode and nonfatal warning persistence are absent.

- [ ] **Step 3: Implement one-time force preparation**

When the run is force mode and `force_prepared` is false:

1. Compute eligible IDs from the reviewed path snapshot.
2. Reset video segment state only for eligible videos.
3. `requeue_for_force(eligible_ids)` without clearing old analysis fields.
4. Commit those changes, then call `mark_force_prepared(run_id)`.

If any step fails, do not mark prepared; the next run can retry the whole preparation transactionally.

The CLI reads `read_reviewed_paths(workspace.excel_path)` after acquiring the analysis lock and passes that immutable snapshot to `analyze_pending()`. It never serializes reviewed paths into process arguments.

- [ ] **Step 4: Dispatch photos and videos by explicit mode**

```python
if record.media_type.startswith("video/"):
    result = segment_pipeline.analyze_video(record, run_id, mode=mode)
    warning = result.warning
elif mode is AnalysisMode.FORCE_GEMINI:
    forced = analyzer.force_image_analyzer.analyze(record.path)
    result, warning = forced.analysis, forced.warning
else:
    result, warning = analyzer.analyze(record.path), None
```

Pass `warning` to `save_analysis()`. Continue source snapshot verification before saving either local or cloud results.

For force runs, `failed_media` counts both fatal `Status.FAILED` records and analyzed records whose safe warning starts with `Gemini 強化失敗`; auto-mode counting remains unchanged.

- [ ] **Step 5: Preserve reviewed Excel status and show warnings**

Keep `read_reviewed_paths()` before every workbook rebuild. An `ANALYZED` record with `error="Gemini 強化失敗:GeminiError"` must render status `待確認`, retain analysis columns, and place the warning in「錯誤原因」.

- [ ] **Step 6: Run batch, Excel and headless smoke tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_batch_analysis.py tests/test_excel_catalog.py tests/test_a_plus_headless_smoke.py
```

Expected: PASS; old auto smoke remains unchanged and a new forced smoke uses fake Gemini only.

- [ ] **Step 7: Commit**

```powershell
git add src/media_catalog/batch_analysis.py src/media_catalog/excel_catalog.py src/media_catalog/cli.py tests/test_batch_analysis.py tests/test_excel_catalog.py tests/test_a_plus_headless_smoke.py
git commit -m "feat: run resumable force Gemini batches"
```

---

### Task 7: UI Button, Confirmation, and Control States

**Files:**
- Modify: `src/media_catalog/status_ui.py:17-537`
- Modify: `src/media_catalog/supervisor.py:126-286`
- Test: `tests/test_status_ui.py`
- Test: `tests/test_status_ui_main.py`

**Interfaces:**
- Consumes: `ForceGeminiEstimate`, `plan_force_run()`, `AnalysisMode`, `read_reviewed_paths()`.
- Produces: `validate_force_environment(environ: Mapping[str, str]) -> str | None`; returns a user-facing error or `None` without returning the key.
- Produces: `format_force_confirmation(estimate: ForceGeminiEstimate) -> str`.
- Extends: `ControlState.force_start_enabled: bool`.
- Extends: `StatusViewModel.failure_text: str`, rendered as `失敗／降級：N` from `AnalysisRun.failed_media`.

- [ ] **Step 1: Write failing pure validation and confirmation tests**

```python
def test_force_environment_requires_key_and_exact_model() -> None:
    assert "API Key" in validate_force_environment({})
    assert validate_force_environment({
        "GEMINI_API_KEY": "configured",
    }) is None
    assert "gemini-3.7-flash" in validate_force_environment({
        "GEMINI_API_KEY": "configured",
        "GEMINI_MODEL": "gemini-other",
    })
    assert validate_force_environment({
        "GEMINI_API_KEY": "configured",
        "GEMINI_MODEL": "gemini-3.7-flash",
    }) is None


def test_force_confirmation_shows_normal_and_retry_limits() -> None:
    text = format_force_confirmation(ForceGeminiEstimate(2, 3, 4, 27, 54))
    assert "影片：2" in text
    assert "照片：3" in text
    assert "已審核：4" in text
    assert "正常強化請求上限：27" in text
    assert "含重試的最壞上限：54" in text
    assert "完整影片" in text


def test_force_view_model_reports_nonfatal_fallback_count() -> None:
    model = StatusViewModel.from_run(
        sample_run(failed_media=2, analysis_mode=AnalysisMode.FORCE_GEMINI),
        worker_alive=True,
        supervisor_status="running",
    )

    assert model.failure_text == "失敗／降級：2"
```

- [ ] **Step 2: Write failing UI interaction tests**

```python
def _force_app(tmp_path: Path, monkeypatch) -> tuple[StatusApplication, Mock, Mock]:
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "photo.jpg").write_bytes(b"photo")
    workspace = bootstrap_workspace(media_root).workspace
    supervisor = Mock()
    supervisor.is_busy = False
    messagebox = Mock()
    app = StatusApplication.__new__(StatusApplication)
    app.media_root = media_root.resolve()
    app.workspace = workspace
    app.skill_root = tmp_path.resolve()
    app.supervisor = supervisor
    app.messagebox = messagebox
    monkeypatch.setenv("GEMINI_API_KEY", "configured-for-test")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.7-flash")
    return app, supervisor, messagebox


def test_force_button_confirms_then_starts_force_mode(tmp_path: Path, monkeypatch) -> None:
    app, supervisor, messagebox = _force_app(tmp_path, monkeypatch)
    messagebox.askokcancel.return_value = True

    app._start_force_gemini()

    assert supervisor.start.call_args.kwargs["mode"] is AnalysisMode.FORCE_GEMINI
    assert "正常強化請求上限" in messagebox.askokcancel.call_args.args[1]


def test_cancelled_force_confirmation_starts_nothing(tmp_path: Path, monkeypatch) -> None:
    app, supervisor, messagebox = _force_app(tmp_path, monkeypatch)
    messagebox.askokcancel.return_value = False

    app._start_force_gemini()

    supervisor.start.assert_not_called()
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_status_ui.py tests/test_status_ui_main.py
```

Expected: FAIL because the new button, validators and control state are missing.

- [ ] **Step 4: Change folder selection to catalog-only readiness**

`_choose_folder()` starts catalog refresh. On `catalog_ready`, set workspace, render `清冊就緒，請選擇分析模式`, and enable both start buttons. Do not start auto mode from `poll()`.

- [ ] **Step 5: Add the orange force button and confirmation flow**

Create the button with text `強制 Gemini 強化`, background `#C2410C`, active background `#EA580C`, and command `_start_force_gemini`. Before confirmation:

1. Reject busy state.
2. Validate key and exact model.
3. Read records and reviewed paths.
4. Calculate estimate.
5. If zero eligible items, show information and do not start.
6. Ask `messagebox.askokcancel` with the formatted counts and privacy text.
7. On approval call `supervisor.start(self.media_root, self.skill_root, mode=AnalysisMode.FORCE_GEMINI)`.

- [ ] **Step 6: Render force status and controls**

When `run.analysis_mode` is force, `StatusViewModel.from_run()` uses `Gemini 強制強化中` for active states and shows `失敗／降級：N`. Both start buttons and folder selection are disabled while busy; safe stop remains enabled. Output buttons remain enabled when files exist.

- [ ] **Step 7: Run UI tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_status_ui.py tests/test_status_ui_main.py tests/test_supervisor.py
```

Expected: PASS with deterministic fake messagebox and no real window.

- [ ] **Step 8: Commit**

```powershell
git add src/media_catalog/status_ui.py src/media_catalog/supervisor.py tests/test_status_ui.py tests/test_status_ui_main.py tests/test_supervisor.py
git commit -m "feat: add force Gemini start control to the UI"
```

---

### Task 8: Security, Documentation, and Packaged Skill

**Files:**
- Modify: `README.md`
- Modify: `docs/media-catalog-a-plus-setup.md`
- Modify: `tests/test_a_plus_security.py`
- Modify: `tests/test_skill_package.py`
- Modify: `tests/test_a_plus_launcher.py`

**Interfaces:**
- Consumes: final CLI names, UI labels and privacy contract.
- Produces: user-facing instructions for normal versus force mode and recovery.

- [ ] **Step 1: Extend security tests before documentation changes**

Add repository scans asserting:

```python
assert "AIza" not in repository_text
assert "GEMINI_API_KEY=" not in serialized_shortcut
assert "--api-key" not in serialized_shortcut
```

Add a CLI test that captured process arguments contain `--mode force-gemini` and `--run-id`, but never the configured key value.

- [ ] **Step 2: Run security tests and verify existing behavior**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_a_plus_security.py tests/test_skill_package.py tests/test_a_plus_launcher.py tests/test_shortcut_installer.py
```

Expected: PASS before docs; the new assertions protect later changes.

- [ ] **Step 3: Update Traditional Chinese usage documentation**

Document this exact operator flow:

1. 開啟桌面 `Media Catalog A+ Stable`。
2. 選擇資料夾並等待清冊就緒。
3. 普通辨識按「開始／繼續」；指定加強按「強制 Gemini 強化」。
4. 確認未審核數量、正常請求上限與含重試上限。
5. 執行中可按「安全停止」，重新開啟後選相同模式續跑。

State explicitly that photos use one resized preview, videos use at most 12 segments of 1～3 resized frames, reviewed rows are skipped, and API keys are never stored.

- [ ] **Step 4: Validate package and docs**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_a_plus_security.py tests/test_skill_package.py tests/test_a_plus_launcher.py tests/test_shortcut_installer.py
git diff --check
```

Expected: PASS and no whitespace errors.

- [ ] **Step 5: Commit**

```powershell
git add README.md docs/media-catalog-a-plus-setup.md tests/test_a_plus_security.py tests/test_skill_package.py tests/test_a_plus_launcher.py
git commit -m "docs: explain force Gemini folder analysis"
```

---

### Task 9: Full Regression, Installation, and Controlled UI Acceptance

**Files:**
- Modify only if a verification failure proves a defect in files already listed above.
- Test: entire `tests/` suite.
- Install target: `C:\Users\pony6832\.codex\skills\media-inventory`
- Desktop shortcut: `C:\Users\pony6832\Desktop\Media Catalog A+ Stable.lnk`

**Interfaces:**
- Consumes: completed implementation and installer.
- Produces: verified installed A+ runtime; does not merge or push without user authorization.

- [ ] **Step 1: Run the complete automated suite**

Run:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all tests pass; only explicitly gated live Gemini and unavailable-tool integration tests may skip.

- [ ] **Step 2: Run source integrity and secret scans**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_a_plus_security.py tests/test_analysis_quality.py tests/test_a_plus_headless_smoke.py
git diff --check
git status --short
```

Expected: PASS, no API Key-shaped text, no unexpected files, and a clean committed feature branch.

- [ ] **Step 3: Install the updated Skill with a recoverable backup**

Run from the feature worktree:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-media-inventory-skill.ps1 -ProjectRoot (Get-Location).Path
```

Expected output contains both:

```text
MEDIA_CATALOG_SHORTCUT_READY
MEDIA_INVENTORY_SKILL_READY
```

Record the timestamped backup path. Do not delete the backup during this task.

- [ ] **Step 4: Verify the installed runtime without exposing credentials**

Run:

```powershell
$runtime='C:\Users\pony6832\.codex\skills\media-inventory\.runtime\Scripts\python.exe'
& $runtime -c "from media_catalog.analysis_mode import AnalysisMode; from media_catalog.gemini_client import DEFAULT_MODEL; assert AnalysisMode.FORCE_GEMINI.value == 'force-gemini'; assert DEFAULT_MODEL == 'gemini-3.7-flash'; print('FORCE_GEMINI_RUNTIME_OK')"
```

Expected: `FORCE_GEMINI_RUNTIME_OK`. Check only whether the user-level key is configured; never print its value.

- [ ] **Step 5: Perform a controlled UI acceptance test**

Use a temporary folder containing one generated test image and one short synthetic video. Launch the desktop shortcut and verify visually:

- Folder selection ends at `清冊就緒，請選擇分析模式`.
- `開始／繼續` and orange `強制 Gemini 強化` are enabled.
- Force confirmation shows 1 video, 1 photo, 13 normal requests maximum and 26 retry attempts maximum.
- Cancel leaves all counts and states unchanged.
- During a fake-provider or explicitly approved single live request run, UI shows `Gemini 強制強化中`, both start buttons disable, and safe stop enables.
- No PowerShell／FFmpeg／Ollama console window flashes.

Do not point this acceptance test at the user's production media folders.

- [ ] **Step 6: Verify Excel and source files**

Open the controlled Excel catalog and confirm the status dropdown remains `待確認,已審核`. Mark one row `已審核`, rebuild, and verify it remains reviewed and is skipped by the next force estimate. Compare SHA-256 hashes of both synthetic source files before and after the run.

- [ ] **Step 7: Final full-suite verification and commit any test-only fixture adjustments**

Run:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
git status --short --branch
```

Expected: zero failures, clean worktree, branch still isolated. If any tracked verification adjustment was required, commit it with:

```powershell
git add tests/test_force_gemini.py tests/test_a_plus_headless_smoke.py tests/test_status_ui.py
git commit -m "test: verify forced Gemini desktop workflow"
```

- [ ] **Step 8: Present integration choices**

After verification, use `superpowers:finishing-a-development-branch` and offer exactly:

1. Merge back to `master` locally.
2. Push and create a Pull Request.
3. Keep the branch as-is.

Do not merge, push, delete the branch, or remove its worktree before the user chooses.
