# 媒體清冊第二版安全預覽分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an offline, resumable `analyze-all` workflow that analyzes every pending photo and video, stores preview descriptions/highlights/keywords in SQLite and Excel, and never mutates source media.

**Architecture:** Extend the durable catalog with an `analyzed` review state, route videos through Watch/claude-video with a pinned MCP Video Analyzer fallback, and run a sequential batch service that snapshots sources and refreshes Excel after every item. A thin CLI and `media-inventory` Skill resolve the private runtimes and tools; the reversible installer owns the pinned MCP package.

**Tech Stack:** Python 3.11, pytest 8, SQLite, openpyxl 3.1, Ollama CLI with `Qwen3-vl:8b-instruct`, Watch/claude-video, Node.js 18+, `mcp-video-analyzer@0.8.0`, PowerShell 5.1-compatible launchers.

## Global Constraints

- Analyze the approved root `D:\【AIGC-ALL】\2026馬年賀卡AI`, currently 17 images and 3 videos, only after isolated tests and smoke tests pass.
- Keep all visual inference local with Ollama model `Qwen3-vl:8b-instruct`.
- Invoke Watch with `--detail efficient`, `--max-frames 12`, and `--no-whisper`.
- Pin MCP Video Analyzer exactly to `0.8.0`; never install or invoke `latest`.
- Use Watch/claude-video once per video; invoke MCP only if Watch fails or emits no representative frames.
- Write only below `媒體整理成果`; do not write metadata, Markdown, backups, search indexes, or source-adjacent sidecars in this version.
- Never move, rename, delete, or modify source media. Verify path, size, mtime, and SHA-256 before and after analysis.
- One item failure must not stop later items. Excel write failure must stop the batch because catalog synchronization is no longer trustworthy.
- Use UTF-8 subprocess decoding with replacement for diagnostics, and remove cloud provider keys from child environments.
- Use `apply_patch` for source edits and TDD for every production behavior.

---

### Task 1: Add the durable “待確認” catalog state

**Files:**
- Modify: `src/media_catalog/models.py`
- Modify: `src/media_catalog/database.py`
- Modify: `src/media_catalog/excel_catalog.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_excel_catalog.py`
- Modify: `tests/test_processor.py`

**Interfaces:**
- Consumes: existing `Status`, `MediaRecord`, `CatalogDatabase`, `write_excel()`.
- Produces: `Status.ANALYZED`, `CatalogDatabase.list_by_status(statuses)`, `CatalogDatabase.requeue_processing()`, and `save_analysis()` that persists `Status.ANALYZED`.

- [ ] **Step 1: Write failing database and workbook tests**

Add literal behavior tests:

```python
def test_save_analysis_marks_record_analyzed_for_human_review(tmp_path: Path) -> None:
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    media = tmp_path / "photo.jpg"
    media.write_bytes(b"photo")
    record = database.upsert_discovered(media, "fingerprint", "image/jpeg")

    saved = database.save_analysis(
        record.id,
        description="紅色賀卡與金色馬。",
        highlights=("紅底", "金色馬"),
        keywords=("賀卡", "馬年"),
    )

    assert saved.status is Status.ANALYZED
    assert database.list_by_status((Status.ANALYZED,)) == [saved]
```

```python
def test_requeue_processing_only_resets_interrupted_records(tmp_path: Path) -> None:
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    processing_path = tmp_path / "processing.jpg"
    failed_path = tmp_path / "failed.jpg"
    processing_path.write_bytes(b"one")
    failed_path.write_bytes(b"two")
    processing = database.upsert_discovered(processing_path, "one", "image/jpeg")
    failed = database.upsert_discovered(failed_path, "two", "image/jpeg")
    database.set_status(processing.id, Status.PROCESSING)
    database.set_status(failed.id, Status.FAILED, error="bad image")

    assert database.requeue_processing() == 1
    assert database.get_record(processing.id).status is Status.PENDING
    assert database.get_record(failed.id).status is Status.FAILED
```

Update the Excel test to assert the saved row contains `待確認` and a non-empty processing timestamp for `Status.ANALYZED`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
$env:PYTHONPATH='src;C:\Users\pony6832\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages'
python -m pytest tests/test_database.py tests/test_excel_catalog.py tests/test_processor.py -v
```

Expected: FAIL because `Status.ANALYZED`, `list_by_status()`, and `requeue_processing()` do not exist and `save_analysis()` still records `processing`.

- [ ] **Step 3: Implement the minimal state changes**

Add:

```python
class Status(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    ANALYZED = "analyzed"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
```

Implement parameterized queries only:

```python
def list_by_status(self, statuses: Sequence[Status]) -> list[MediaRecord]:
    if not statuses:
        return []
    placeholders = ", ".join("?" for _ in statuses)
    with self._connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM media_records WHERE status IN ({placeholders}) "
            "ORDER BY discovered_at, id",
            tuple(status.value for status in statuses),
        ).fetchall()
    return [self._to_record(row) for row in rows]

def requeue_processing(self) -> int:
    with self._connect() as connection:
        cursor = connection.execute(
            "UPDATE media_records SET status = ?, error = NULL, updated_at = ? "
            "WHERE status = ?",
            (Status.PENDING.value, _now(), Status.PROCESSING.value),
        )
    return cursor.rowcount
```

Change `save_analysis()` to store `Status.ANALYZED.value`. Add `Status.ANALYZED: "待確認"` to `_STATUS_LABELS`, and emit processing time for `ANALYZED` as well as `COMPLETED`.

- [ ] **Step 4: Run focused and full regression tests**

Run the focused command from Step 2, then `python -m pytest -q`.

Expected: all tests pass; the existing processor expectation now asserts `Status.ANALYZED`.

- [ ] **Step 5: Commit the review state**

```powershell
git add src/media_catalog/models.py src/media_catalog/database.py src/media_catalog/excel_catalog.py tests/test_database.py tests/test_excel_catalog.py tests/test_processor.py
git commit -m "feat: add analyzed review state"
```

---

### Task 2: Route videos through Watch with MCP fallback

**Files:**
- Modify: `src/media_catalog/inference.py`
- Modify: `tests/test_inference.py`

**Interfaces:**
- Consumes: `VideoEvidence`, `AnalysisError`, `WatchVideoExtractor`, `McpVideoExtractor`.
- Produces: `VideoExtractor` protocol and `FallbackVideoExtractor(primary, fallback).extract(source) -> VideoEvidence`.

- [ ] **Step 1: Write failing routing and privacy tests**

Use small fakes that return real `VideoEvidence` objects:

```python
class RecordingExtractor:
    def __init__(self, evidence: VideoEvidence) -> None:
        self.evidence = evidence
        self.sources: list[Path] = []

    def extract(self, source: str | Path) -> VideoEvidence:
        self.sources.append(Path(source).resolve())
        return self.evidence


class FailingExtractor:
    def __init__(self, message: str) -> None:
        self.message = message

    def extract(self, source: str | Path) -> VideoEvidence:
        raise AnalysisError(self.message)


def test_fallback_video_extractor_does_not_call_mcp_when_watch_succeeds(
    tmp_path: Path,
) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    primary = RecordingExtractor(VideoEvidence((tmp_path / "watch.jpg",), {"backend": "watch"}))
    fallback = RecordingExtractor(VideoEvidence((tmp_path / "mcp.jpg",), {"backend": "mcp"}))

    evidence = FallbackVideoExtractor(primary, fallback).extract(source)

    assert evidence.metadata["backend"] == "watch"
    assert primary.sources == [source.resolve()]
    assert fallback.sources == []
```

```python
def test_fallback_video_extractor_calls_mcp_once_after_watch_failure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    primary = FailingExtractor("watch produced no frames")
    fallback = RecordingExtractor(VideoEvidence((tmp_path / "mcp.jpg",), {"backend": "mcp"}))

    evidence = FallbackVideoExtractor(primary, fallback).extract(source)

    assert evidence.metadata["backend"] == "mcp"
    assert fallback.sources == [source.resolve()]
```

Add a third test asserting combined `AnalysisError` text when both fail. Extend the MCP environment test to assert `TWELVELABS_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, and `ANTHROPIC_API_KEY` are absent.

- [ ] **Step 2: Run the inference tests and verify RED**

Run: `python -m pytest tests/test_inference.py -v`

Expected: FAIL because `FallbackVideoExtractor` and the expanded privacy sanitization do not exist.

- [ ] **Step 3: Implement the fallback protocol and privacy boundary**

Add:

```python
class VideoExtractor(Protocol):
    def extract(self, source: str | Path) -> VideoEvidence:
        raise NotImplementedError


class FallbackVideoExtractor:
    def __init__(
        self,
        primary: VideoExtractor | None,
        fallback: VideoExtractor | None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback

    def extract(self, source: str | Path) -> VideoEvidence:
        failures: list[str] = []
        for name, extractor in (("watch", self.primary), ("mcp", self.fallback)):
            if extractor is None:
                failures.append(f"{name} unavailable")
                continue
            try:
                return extractor.extract(source)
            except AnalysisError as error:
                failures.append(f"{name}: {error}")
        raise AnalysisError("; ".join(failures))
```

Add `TWELVELABS_API_KEY` and `MCP_WRITE_SIDECARS` to `_offline_environment()` removal. Preserve the existing Watch arguments and MCP `analyze` CLI contract from the official project README. Removing the sidecar switch prevents an inherited machine setting from writing `.analysis.json`, `.vtt`, or frame directories next to source videos.

- [ ] **Step 4: Verify focused and full tests**

Run `python -m pytest tests/test_inference.py -v`, then `python -m pytest -q`.

Expected: routing, pinned version, malformed JSON, and privacy tests all pass.

- [ ] **Step 5: Commit the video fallback**

```powershell
git add src/media_catalog/inference.py tests/test_inference.py
git commit -m "feat: fall back to pinned video analyzer"
```

---

### Task 3: Process pending items incrementally without source mutation

**Files:**
- Create: `src/media_catalog/source_guard.py`
- Create: `src/media_catalog/batch_analysis.py`
- Create: `tests/test_source_guard.py`
- Create: `tests/test_batch_analysis.py`
- Modify: `src/media_catalog/processor.py`
- Modify: `tests/test_processor.py`

**Interfaces:**
- Consumes: `MediaWorkspace`, `CatalogDatabase`, `Analyzer`, `write_excel()`.
- Produces: `SourceSnapshot`, `SourceIntegrityError`, `capture_source(path)`, `verify_record_source(record, snapshot)`, `sanitize_error(message, environment=None)`, `BatchAnalysisResult`, and `analyze_pending(workspace, analyzer) -> BatchAnalysisResult`.

- [ ] **Step 1: Write failing source-integrity tests**

```python
def test_source_guard_detects_content_change_even_when_size_is_unchanged(
    tmp_path: Path,
) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"abcd")
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    record = database.upsert_discovered(
        source,
        "88d4266fd4e6338d13b845fcf289579d209c897823b9217da3e161936f031589",
        "image/jpeg",
    )
    snapshot = capture_source(source)
    source.write_bytes(b"wxyz")

    with pytest.raises(SourceIntegrityError, match="指紋"):
        verify_record_source(record, snapshot)
```

Also test path disappearance and successful verification with identical bytes, size, and mtime.

Add a literal redaction test that passes `{"OPENAI_API_KEY": "test-secret-value"}` as the injected environment and asserts `sanitize_error("failure test-secret-value", environment)` returns `failure [redacted]`. Assert the returned diagnostic is capped at 1000 characters.

- [ ] **Step 2: Write failing batch behavior tests**

Use a real SQLite catalog and real Excel workbook with a deterministic fake analyzer:

```python
class PathAwareAnalyzer:
    def __init__(self, fail_name: str) -> None:
        self.fail_name = fail_name

    def analyze(self, source: Path) -> Analysis:
        if source.name == self.fail_name:
            raise AnalysisError("invalid image")
        return Analysis("賀卡預覽", ("紅色",), ("賀卡",))


def create_workspace_with_media(
    tmp_path: Path, names: Sequence[str]
) -> MediaWorkspace:
    root = tmp_path / "media"
    root.mkdir()
    for name in names:
        (root / name).write_bytes(name.encode("utf-8"))
    workspace = MediaWorkspace.from_root(root)
    workspace.ensure_directories()
    return workspace


def test_analyze_pending_continues_after_one_item_fails_and_updates_excel(
    tmp_path: Path,
) -> None:
    workspace = create_workspace_with_media(tmp_path, ("bad.jpg", "good.jpg"))
    database = CatalogDatabase(workspace.database_path)
    scan(workspace.root, database, excluded_roots=(workspace.result_root,))

    result = analyze_pending(workspace, PathAwareAnalyzer(fail_name="bad.jpg"))

    records = {record.path.name: record for record in database.list_records()}
    assert result == BatchAnalysisResult(analyzed=1, failed=1, skipped=0, remaining=0)
    assert records["bad.jpg"].status is Status.FAILED
    assert records["good.jpg"].status is Status.ANALYZED
    workbook = load_workbook(workspace.excel_path, read_only=True)
    assert [row[0] for row in workbook.active.iter_rows(min_row=2, values_only=True)] == ["失敗", "待確認"]
```

Add tests that already-analyzed and failed records are skipped on rerun, each success preserves source bytes/size/mtime, and an injected Excel writer failure stops before the next item.

- [ ] **Step 3: Run the new tests and verify RED**

Run: `python -m pytest tests/test_source_guard.py tests/test_batch_analysis.py -v`

Expected: collection FAIL because the two new modules do not exist.

- [ ] **Step 4: Implement source snapshots**

Use chunked SHA-256 and nanosecond mtime:

```python
@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    path: Path
    size: int
    mtime_ns: int
    sha256: str


def capture_source(path: Path) -> SourceSnapshot:
    resolved = Path(path).resolve(strict=True)
    stat_result = resolved.stat()
    return SourceSnapshot(
        path=resolved,
        size=stat_result.st_size,
        mtime_ns=stat_result.st_mtime_ns,
        sha256=_sha256(resolved),
    )
```

`verify_record_source()` must compare the current snapshot with both the pre-analysis snapshot and `MediaRecord.fingerprint`; report which invariant changed without including file contents. `sanitize_error()` must replace non-empty values of the known cloud-provider key variables with `[redacted]`, remove control characters other than tabs, and cap the result at 1000 characters.

- [ ] **Step 5: Implement the sequential batch service**

Define:

```python
@dataclass(frozen=True, slots=True)
class BatchAnalysisResult:
    analyzed: int
    failed: int
    skipped: int
    remaining: int


def analyze_pending(
    workspace: MediaWorkspace,
    analyzer: Analyzer,
    *,
    excel_writer: Callable[[Iterable[MediaRecord], Path], Path] = write_excel,
) -> BatchAnalysisResult:
    database = CatalogDatabase(workspace.database_path)
    initial_records = database.list_records()
    pending = [record for record in initial_records if record.status is Status.PENDING]
    analyzed = 0
    failed = 0

    for record in pending:
        try:
            snapshot = capture_source(record.path)
            verify_record_source(record, snapshot)
        except (OSError, SourceIntegrityError) as error:
            database.set_status(
                record.id, Status.FAILED, error=sanitize_error(str(error))
            )
            failed += 1
            excel_writer(database.list_records(), workspace.excel_path)
            continue

        database.set_status(record.id, Status.PROCESSING)
        excel_writer(database.list_records(), workspace.excel_path)
        try:
            result = analyzer.analyze(record.path)
            verify_record_source(record, snapshot)
        except (AnalysisError, OSError, SourceIntegrityError) as error:
            database.set_status(
                record.id, Status.FAILED, error=sanitize_error(str(error))
            )
            failed += 1
        else:
            database.save_analysis(
                record.id,
                description=result.description,
                highlights=result.highlights,
                keywords=result.keywords,
            )
            analyzed += 1
        excel_writer(database.list_records(), workspace.excel_path)

    remaining = len(
        database.list_by_status((Status.PENDING, Status.PROCESSING))
    )
    return BatchAnalysisResult(
        analyzed=analyzed,
        failed=failed,
        skipped=len(initial_records) - len(pending),
        remaining=remaining,
    )
```

Implementation order per item must be: capture and validate source, set `PROCESSING`, write Excel, call analyzer, verify the source again, save analysis as `ANALYZED` or record a sanitized failure, then write Excel again. Catch `AnalysisError`, `SourceIntegrityError`, and source-read `OSError` per item. Do not catch Excel write failures. Count `skipped` from records not pending at batch start and calculate `remaining` from `PENDING` plus `PROCESSING` after the loop.

Keep `process_selected()` as the selected-ID primitive, update its success state to `ANALYZED`, and do not duplicate batch orchestration inside it.

- [ ] **Step 6: Verify source and batch tests**

Run the Step 3 command, then `python -m pytest -q`.

Expected: all tests pass, including real workbook assertions and source-invariant failures.

- [ ] **Step 7: Commit the safe batch service**

```powershell
git add src/media_catalog/source_guard.py src/media_catalog/batch_analysis.py src/media_catalog/processor.py tests/test_source_guard.py tests/test_batch_analysis.py tests/test_processor.py
git commit -m "feat: analyze pending media safely"
```

---

### Task 4: Build the runtime preflight and CLI commands

**Files:**
- Create: `src/media_catalog/analysis_runtime.py`
- Create: `tests/test_analysis_runtime.py`
- Modify: `src/media_catalog/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `FallbackVideoExtractor`, `LocalAnalyzer`, `MediaWorkspace`, `analyze_pending()`.
- Produces: `RuntimePreflightError`, `build_local_analyzer(skill_root, workspace, model, runner)`, injectable `main(argv=None, runtime_builder=build_local_analyzer)`, and CLI commands `analyze-all`, `resume-processing`, and `verify-sources`.

- [ ] **Step 1: Write failing runtime path and model tests**

Create a fake Skill tree with:

```text
<skills>/media-inventory/
  .tools/mcp-video-analyzer/node_modules/.bin/mcp-video-analyzer.cmd
  .tools/mcp-video-analyzer/node_modules/mcp-video-analyzer/package.json
<skills>/watch/scripts/watch.py
```

Test that `build_local_analyzer()`:

- accepts exact Ollama list entry `Qwen3-vl:8b-instruct` case-insensitively;
- resolves Watch from `skill_root.parent / "watch" / "scripts" / "watch.py"`;
- resolves the pinned MCP executable and package JSON below the private tool directory;
- raises `RuntimePreflightError` when Ollama or the requested model is absent;
- invokes `<skills>/watch/scripts/setup.py --check` silently and omits Watch when it returns nonzero;
- still returns an analyzer when one video extractor is unavailable.

- [ ] **Step 2: Write failing CLI behavior tests**

Add tests around an injected runtime builder and analyzer:

```python
class SuccessfulAnalyzer:
    def analyze(self, source: Path) -> Analysis:
        return Analysis("賀卡預覽", ("紅色",), ("賀卡",))


def catalog_root_with_one_pending_photo(tmp_path: Path) -> Path:
    root = tmp_path / "media"
    root.mkdir()
    (root / "photo.jpg").write_bytes(b"photo")
    bootstrap_workspace(root)
    return root


def test_cli_analyze_all_prints_fixed_summary(tmp_path: Path, capsys) -> None:
    root = catalog_root_with_one_pending_photo(tmp_path)

    exit_code = main(
        ["analyze-all", str(root), "--skill-root", str(tmp_path / "media-inventory")],
        runtime_builder=lambda *_args, **_kwargs: SuccessfulAnalyzer(),
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out.startswith("MEDIA_ANALYSIS_READY analyzed=1 failed=0")
    assert f"catalog={root / '媒體整理成果' / '媒體清冊.xlsx'}" in captured.out
```

Add tests for preflight exit `2`, explicit `resume-processing`, and `verify-sources` emitting `MEDIA_SOURCES_VERIFIED total=<n>` only when every DB fingerprint matches.

- [ ] **Step 3: Run runtime and CLI tests and verify RED**

Run: `python -m pytest tests/test_analysis_runtime.py tests/test_cli.py -v`

Expected: FAIL because the runtime module and new commands do not exist.

- [ ] **Step 4: Implement the runtime factory**

Use exact private paths:

```python
watch_script = skill_root.parent / "watch" / "scripts" / "watch.py"
mcp_root = skill_root / ".tools" / "mcp-video-analyzer"
mcp_executable = mcp_root / "node_modules" / ".bin" / "mcp-video-analyzer.cmd"
mcp_package = mcp_root / "node_modules" / "mcp-video-analyzer" / "package.json"
```

Run `ollama list` with UTF-8 decoding and require the exact model name in the first whitespace-delimited column. If Watch files exist, run the sibling `setup.py --check` with the same Python executable that will run `watch.py`; treat nonzero as Watch unavailable rather than an entire-batch error. Construct MCP only after its executable and package version validate. Give both extractors `workspace.temp_dir / "analysis"` as output root. Return `LocalAnalyzer(model=model, video_extractor=FallbackVideoExtractor(watch, mcp))`.

- [ ] **Step 5: Implement CLI parsing and fixed outputs**

Add:

```text
media-catalog analyze-all ROOT --skill-root SKILL_ROOT [--model Qwen3-vl:8b-instruct]
media-catalog resume-processing ROOT
media-catalog verify-sources ROOT
```

`analyze-all` must open the existing workspace without rescanning, require both SQLite and Excel, build the runtime, run the batch, and print exactly one final `MEDIA_ANALYSIS_READY` line. Return `0` even when individual items fail, because the batch completed and failures are represented in the catalog. Return `2` for workspace or preflight errors and do not catch unexpected exceptions.

`resume-processing` requeues only `PROCESSING` records and prints `MEDIA_ANALYSIS_RESUMED count=<n>`. `verify-sources` hashes every record and prints `MEDIA_SOURCES_VERIFIED total=<n>` or returns `2` with `MEDIA_ANALYSIS_ERROR`.

- [ ] **Step 6: Verify runtime, CLI, and full suite**

Run the Step 3 command, then `python -m pytest -q` and `git diff --check`.

Expected: all tests pass and no whitespace errors.

- [ ] **Step 7: Commit the runtime and CLI**

```powershell
git add src/media_catalog/analysis_runtime.py src/media_catalog/cli.py tests/test_analysis_runtime.py tests/test_cli.py
git commit -m "feat: add offline analyze-all command"
```

---

### Task 5: Upgrade the Skill and install the pinned MCP tool reversibly

**Files:**
- Modify: `skills/media-inventory/SKILL.md`
- Create: `skills/media-inventory/scripts/run_media_analysis.ps1`
- Modify: `skills/media-inventory/agents/openai.yaml`
- Modify: `scripts/install-media-inventory-skill.ps1`
- Modify: `tests/test_skill_package.py`

**Interfaces:**
- Consumes: CLI `analyze-all ROOT --skill-root SKILL_ROOT` and the existing reversible installer.
- Produces: conversational command `分析全部待處理：<root>`, private MCP installation, and `MEDIA_INVENTORY_SKILL_READY` only after catalog and analysis launchers validate.

- [ ] **Step 1: Read required Skill authoring instructions and establish RED behavior**

Before editing the Skill, read completely:

```text
C:\Users\pony6832\.codex\skills\.system\skill-creator\SKILL.md
C:\Users\pony6832\.codex\skills\writing-skills\SKILL.md
C:\Users\pony6832\.codex\skills\.system\skill-creator\references\openai_yaml.md
```

Run the current project Skill against the prompt shape `分析全部待處理：C:\media`; record that it has no analysis launcher. Add a package test that runs `quick_validate.py` on the project Skill and asserts the new launcher exists. Do not test human prose with substring assertions.

- [ ] **Step 2: Run the package test and verify RED**

Run: `python -m pytest tests/test_skill_package.py -v`

Expected: FAIL because `scripts/run_media_analysis.ps1` does not exist.

- [ ] **Step 3: Implement the analysis launcher and Skill workflow**

Create a PowerShell 5.1-compatible launcher:

```powershell
param(
    [Parameter(Mandatory = $true)]
    [string]$RootPath
)

$ErrorActionPreference = 'Stop'
$skillRoot = Split-Path -Parent $PSScriptRoot
$runtimePython = Join-Path $skillRoot '.runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    [Console]::Error.WriteLine("MEDIA_ANALYSIS_ERROR 找不到 Skill 私有執行環境：$runtimePython")
    exit 2
}
& $runtimePython -m media_catalog.cli analyze-all $RootPath --skill-root $skillRoot
exit $LASTEXITCODE
```

Update `SKILL.md` with two separate recipes: scan-only and analyze-all. The analyze-all recipe must treat `MEDIA_ANALYSIS_READY` as a completed batch with per-item failures represented by `failed=<n>`; it must link the emitted catalog and must not proceed to metadata or Markdown.

Regenerate `agents/openai.yaml` with the official generator in UTF-8 mode and a default prompt that explicitly mentions `$media-inventory`.

- [ ] **Step 4: Extend the reversible installer with the pinned npm dependency**

After the private Python project installs, resolve `node.exe` and `npm.cmd`, require Node major version at least 18, and install exactly:

```powershell
$mcpRoot = Join-Path $destinationFull '.tools\mcp-video-analyzer'
New-Item -ItemType Directory -Path $mcpRoot -Force | Out-Null
& $npmCommand.Source install --prefix $mcpRoot --no-save --omit=dev 'mcp-video-analyzer@0.8.0'
```

Validate:

```text
<mcpRoot>\node_modules\.bin\mcp-video-analyzer.cmd
<mcpRoot>\node_modules\mcp-video-analyzer\package.json
```

Parse the JSON with Python or `ConvertFrom-Json`, require `name == "mcp-video-analyzer"` and `version == "0.8.0"`, and abort with the exact backup path on mismatch. Never invoke `npx mcp-video-analyzer@latest` at runtime. Preserve the existing refusal to overwrite a timestamp collision and the existing backup-on-upgrade behavior.

- [ ] **Step 5: Test fresh and upgrade installs in a temporary destination**

Run the installer twice against:

```text
$env:TEMP\media-inventory-v2-install-test\media-inventory
```

Expected after the second run:

- one current `media-inventory` directory;
- one `media-inventory.backup-<timestamp>` directory;
- both contain their original `SKILL.md`;
- current private Python exists;
- current MCP `.cmd` and package JSON exist;
- package version is exactly `0.8.0`;
- output contains `MEDIA_INVENTORY_SKILL_READY` and the exact backup path.

- [ ] **Step 6: Validate the Skill and full project**

Run:

```powershell
$env:PYTHONUTF8='1'
python -m pytest -q
python "C:\Users\pony6832\.codex\skills\.system\skill-creator\scripts\quick_validate.py" skills/media-inventory
git diff --check
```

Expected: all tests pass, Skill is valid, and Git reports no whitespace errors.

- [ ] **Step 7: Commit the Skill upgrade**

```powershell
git add skills/media-inventory scripts/install-media-inventory-skill.ps1 tests/test_skill_package.py
git commit -m "feat: add conversational preview analysis"
```

---

### Task 6: Perform isolated smoke tests, install, and analyze the approved 20 items

**Files:**
- No tracked production files unless a smoke test exposes a defect; every defect requires a failing regression test before a fix.
- External reversible update: `C:\Users\pony6832\.codex\skills\media-inventory`
- User-authorized result updates: `D:\【AIGC-ALL】\2026馬年賀卡AI\媒體整理成果\catalog.sqlite`
- User-authorized result updates: `D:\【AIGC-ALL】\2026馬年賀卡AI\媒體整理成果\媒體清冊.xlsx`

**Interfaces:**
- Consumes: installed Skill launchers, `verify-sources`, and `analyze-all`.
- Produces: verified preview analysis for the approved catalog plus exact success/failure evidence.

- [ ] **Step 1: Run static preflight without loading the vision model**

Verify:

```powershell
ollama list
python "C:\Users\pony6832\.codex\skills\watch\scripts\setup.py" --check
node --version
npm.cmd --version
ffmpeg -version
```

Require exact model `Qwen3-vl:8b-instruct`, Watch exit `0`, Node at least 18, and available ffmpeg. Do not claim generation from this step.

- [ ] **Step 2: Install the upgraded Skill reversibly**

Inspect the exact current destination and candidate backup path, then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-media-inventory-skill.ps1 `
  -Destination "$env:USERPROFILE\.codex\skills\media-inventory" `
  -ProjectRoot "$PWD"
```

Expected: `MEDIA_INVENTORY_SKILL_READY` with a new timestamped backup path. Verify the old Skill remains readable in that backup.

- [ ] **Step 3: Create valid temporary smoke media and snapshot it**

Create a new unique directory below `%TEMP%`. Use ffmpeg to generate:

```powershell
ffmpeg -f lavfi -i "color=c=red:s=640x480" -frames:v 1 smoke.png
ffmpeg -f lavfi -i "color=c=blue:s=640x480:d=2" -vf "drawtext=text='Horse 2026':x=40:y=40:fontsize=48:fontcolor=white" -c:v libx264 -pix_fmt yuv420p smoke.mp4
```

Run the scan launcher, then `verify-sources`. Record the literal output `MEDIA_SOURCES_VERIFIED total=2`.

- [ ] **Step 4: Run real local image and video analysis smoke tests**

Invoke the installed `run_media_analysis.ps1` on the temporary root. Allow enough time for the local model, but do not accept command dispatch as success.

Expected evidence:

- `MEDIA_ANALYSIS_READY analyzed=2 failed=0 skipped=0 remaining=0`;
- both rows are `待確認` in the real Excel workbook;
- both rows contain non-empty description, highlights, and keywords;
- the video extraction output shows Watch as the primary path;
- a second `verify-sources` returns `total=2`;
- original smoke file hashes, sizes, and mtimes are unchanged.

Independently invoke the installed private MCP executable on `smoke.mp4` with `analyze`, `--detail standard`, `--max-frames 12`, `--fields metadata,frames,ocrResults`, and a temporary `--out` directory. Clear `MCP_WRITE_SIDECARS` and all cloud-provider keys for that process. Require exit `0`, valid JSON, at least one emitted frame, and package version `0.8.0`. The Task 2 routing test plus this real MCP smoke proves the fallback path without sabotaging the installed Watch path.

- [ ] **Step 5: Snapshot and verify the approved 20 sources before analysis**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  "$env:USERPROFILE\.codex\skills\media-inventory\scripts\run_media_catalog.ps1" `
  -RootPath 'D:\【AIGC-ALL】\2026馬年賀卡AI'
```

Expected: no new unexpected media; total remains 20. Then run `media_catalog.cli verify-sources` through the private runtime and require `MEDIA_SOURCES_VERIFIED total=20`.

If the catalog count or source fingerprints differ, stop and report the exact difference before analysis because the approved set changed.

- [ ] **Step 6: Analyze all 20 pending items and monitor the run**

Run the installed analysis launcher with the approved root. Poll long-running output without blocking user updates for more than 60 seconds. Do not interrupt a healthy Ollama inference merely because it is slow.

Expected final marker shape:

```text
MEDIA_ANALYSIS_READY analyzed=<n> failed=<n> skipped=<n> remaining=0 catalog=D:\【AIGC-ALL】\2026馬年賀卡AI\媒體整理成果\媒體清冊.xlsx
```

The accepted result may include per-item failures, but every failed row must have a concrete error. Do not call the batch successful if the marker is missing, the process is still running, or `remaining` is nonzero.

- [ ] **Step 7: Verify outputs and source immutability after the real run**

Run `verify-sources` again and require `MEDIA_SOURCES_VERIFIED total=20`. Open the workbook with openpyxl and report literal counts by status plus counts of non-empty description/highlights/keywords. Verify the SQLite counts match Excel exactly.

Confirm no source-adjacent `.analysis.json`, `.vtt`, `.srt`, `.frames`, or Markdown files were created. Generated frames must exist only under `媒體整理成果\工作暫存`.

- [ ] **Step 8: Run final regression and commit only tracked fixes, if any**

Run:

```powershell
$env:PYTHONPATH='src;C:\Users\pony6832\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages'
$env:PYTHONUTF8='1'
python -m pytest -q
python "C:\Users\pony6832\.codex\skills\.system\skill-creator\scripts\quick_validate.py" skills/media-inventory
git diff --check
git status --short
```

If smoke testing required a code fix, commit only its regression test and focused implementation with `fix: correct preview analysis integration`. If no tracked file changed, do not create an empty commit.

## Plan Self-Review

- Spec coverage: Task 1 implements the review state; Task 2 implements Watch/claude-video primary plus MCP fallback and privacy; Task 3 implements per-item durability and source guards; Task 4 implements preflight, resume, verification, and CLI summaries; Task 5 implements the conversational Skill and pinned private tool; Task 6 performs isolated and authorized real-world validation.
- Placeholder scan: every implementation step names concrete behavior, commands, failure evidence, and code boundaries.
- Type consistency: `Status.ANALYZED`, `FallbackVideoExtractor`, `SourceSnapshot`, `BatchAnalysisResult`, `analyze_pending()`, and all CLI names are introduced before consumers.
- Scope: metadata mutation, Markdown, backups of media, search indexes, cloud analysis, scheduling, and source-side MCP sidecars remain excluded.
- External source checks: MCP CLI details and version were verified from the official `guimatheus92/mcp-video-analyzer` repository; Ollama CLI image-path usage was verified from official Ollama Vision documentation.

## Primary References

- MCP Video Analyzer official repository and CLI: https://github.com/guimatheus92/mcp-video-analyzer
- Ollama official Vision documentation: https://docs.ollama.com/capabilities/vision
