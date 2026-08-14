# 媒體整理與搜尋 Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline Windows-first Skill that scans photo/video folders, queues selected items, produces local AI descriptions, safely writes metadata and Markdown, maintains an Excel catalog, and searches it locally.

**Architecture:** A small Python package owns catalog state, media mutation, Markdown, Excel, indexing, and CLI commands. A thin Codex Skill document invokes the CLI. Media inference is behind a local subprocess adapter so Qwen3-VL/Ollama and future local models can change without affecting catalog or safety code.

**Tech Stack:** Python 3.11+, pytest, openpyxl, ExifTool CLI, ffmpeg/ffprobe CLI, local Ollama CLI, SQLite FTS5, a user-provided local sentence-transformers embedding model.

**Lightweight first batch:** Implement Tasks 1-3 first. This delivers the durable catalog, recursive Excel queue, and normalized local video-extraction boundary before metadata mutation or semantic indexing is enabled.

## Global Constraints

- Windows-first; use `pathlib` and never shell-concatenate media paths.
- No external API, cloud analysis, automatic model download, or network call.
- First version scans recursively but changes no media until the user explicitly runs `process` for queued IDs.
- Back up before metadata mutation; verify metadata after writing; never mark a failed item complete.
- Only description, highlights, and keywords are generated in v1. No transcript, chapters, or automatic folder watcher.

---

### Task 1: Package foundation and durable catalog model

**Files:**
- Create: `pyproject.toml`, `src/media_catalog/__init__.py`, `src/media_catalog/models.py`, `src/media_catalog/database.py`, `tests/test_database.py`

**Interfaces:**
- Produces `MediaRecord`, `Status`, and `CatalogDatabase(db_path)` with `upsert_discovered()`, `list_records()`, `set_status()` and `get_record()`.

- [ ] **Step 1: Write failing tests** for an initial scan record and repeat discovery preserving its ID/status.

```python
def test_upsert_discovered_is_idempotent(tmp_path):
    db = CatalogDatabase(tmp_path / "catalog.sqlite")
    first = db.upsert_discovered(tmp_path / "clip.mp4", "abc", "video")
    second = db.upsert_discovered(tmp_path / "clip.mp4", "abc", "video")
    assert first.id == second.id
    assert second.status is Status.PENDING
```

- [ ] **Step 2: Run** `pytest tests/test_database.py -v`; expect failure because the package does not exist.
- [ ] **Step 3: Implement** a SQLite schema with immutable record UUID, normalized absolute path, SHA-256 fingerprint, status, description, highlights JSON, keywords JSON, error, Markdown path, backup path, and timestamps. Use parameterized SQL only.
- [ ] **Step 4: Run** `pytest tests/test_database.py -v`; expect PASS.
- [ ] **Step 5: Commit** with `feat: add catalog database model`.

### Task 2: Recursive discovery and Excel pending catalog

**Files:**
- Create: `src/media_catalog/scanner.py`, `src/media_catalog/excel_catalog.py`, `tests/test_scanner.py`, `tests/test_excel_catalog.py`

**Interfaces:**
- Consumes `CatalogDatabase` and a root `Path`.
- Produces `scan(root) -> ScanResult` and `write_excel(records, output_path) -> Path`.

- [ ] **Step 1: Write failing tests** with nested JPG/MP4 fixtures, an unsupported text file, and a second scan.

```python
def test_scan_recurses_and_does_not_duplicate(tmp_path):
    (tmp_path / "nested").mkdir(); (tmp_path / "nested" / "a.jpg").write_bytes(b"x")
    result = scan(tmp_path, db)
    assert result.discovered == 1
    assert scan(tmp_path, db).discovered == 0
```

- [ ] **Step 2: Run** `pytest tests/test_scanner.py tests/test_excel_catalog.py -v`; expect failure.
- [ ] **Step 3: Implement** extension allowlists, recursive traversal, chunked SHA-256, stable path normalization, and an Excel workbook with the exact spec columns: status, filename, full path, media type, description, highlights, keywords, capture time, processing time, Markdown path, backup path, error reason. Never open or mutate media in this task.
- [ ] **Step 4: Run** those tests; expect PASS and inspect workbook values with openpyxl.
- [ ] **Step 5: Commit** with `feat: scan media folders into Excel queue`.

### Task 3: Offline inference adapter and selected-item processing boundary

**Files:**
- Create: `src/media_catalog/inference.py`, `src/media_catalog/processor.py`, `tests/test_inference.py`, `tests/test_processor.py`

**Interfaces:**
- Produces `LocalAnalyzer.analyze(path) -> Analysis(description, highlights, keywords)` and `process_selected(ids, catalog, analyzer)`.

- [ ] **Step 1: Write failing tests** using a fake analyzer; assert unselected pending records are untouched and malformed model JSON changes selected records to `FAILED` with an error.
- [ ] **Step 2: Run** `pytest tests/test_inference.py tests/test_processor.py -v`; expect failure.
- [ ] **Step 3: Implement** a subprocess-only Ollama adapter (`ollama run <configured-model>`) with JSON-only prompt/output validation. For video, expose one normalized extractor interface with two selectable local backends: (a) installed `watch-skill`, whose underlying implementation is `claude-video`, and (b) a preinstalled, pinned `guimatheus92/mcp-video-analyzer` CLI. Do not invoke `watch-skill` and `claude-video` separately. Restrict the MCP analyzer to local paths and local metadata/frames/OCR fields; reject URL sources, `@latest`, cloud summaries, and external transcription fallbacks. Pass argument lists to `subprocess.run`, set timeouts, and never use network clients. Require configuration paths/model names to exist before work starts.
- [ ] **Step 4: Run** tests with controlled subprocess runners; expect PASS. Cover both normalized video backends, URL rejection, pinned-command validation, malformed JSON, and a dry-run command that proves no media write occurs.
- [ ] **Step 5: Commit** with `feat: add offline selected-media analysis`.

### Task 4: Backup, metadata verification, and Markdown sidecars

**Files:**
- Create: `src/media_catalog/media_writer.py`, `src/media_catalog/markdown.py`, `tests/test_media_writer.py`, `tests/test_markdown.py`

**Interfaces:**
- Produces `backup_media(record, backup_root) -> Path`, `write_verified_metadata(record, analysis) -> WriteResult`, and `render_sidecar(record, analysis) -> str`.

- [ ] **Step 1: Write failing tests** verifying backup precedes fake ExifTool mutation, a failed verification leaves status failed, and Markdown contains description/highlights/keywords/paths.
- [ ] **Step 2: Run** `pytest tests/test_media_writer.py tests/test_markdown.py -v`; expect failure.
- [ ] **Step 3: Implement** collision-safe backup names, ExifTool invocation with argument arrays, supported-format checks, post-write ExifTool readback comparison, and UTF-8 same-name `.md` output. Update Excel and local index only after the write result is verified.
- [ ] **Step 4: Run** tests; expect PASS. Add a fixture test that refuses a locked/unsupported file without overwriting it.
- [ ] **Step 5: Commit** with `feat: add verified metadata and Markdown output`.

### Task 5: Offline fuzzy and semantic search

**Files:**
- Create: `src/media_catalog/search.py`, `tests/test_search.py`

**Interfaces:**
- Produces `SearchIndex`, `index_completed(record)`, and `search(query, limit=10) -> list[SearchHit]`.

- [ ] **Step 1: Write failing tests** for keyword matching, a typo match, and semantic match with a deterministic fake embedder.

```python
def test_semantic_search_returns_related_record(index):
    index.add("1", "海灘 黃昏 海浪", [1.0, 0.0])
    assert index.search("海邊夕陽", embed=lambda _: [1.0, 0.0])[0].record_id == "1"
```

- [ ] **Step 2: Run** `pytest tests/test_search.py -v`; expect failure.
- [ ] **Step 3: Implement** SQLite FTS5 for textual/fuzzy ranking and a local-only embedding adapter that accepts an existing model path, refuses missing models, and stores vectors locally. Combine normalized FTS and cosine scores; retain the matching text/reason in every hit.
- [ ] **Step 4: Run** test suite; expect PASS without network access.
- [ ] **Step 5: Commit** with `feat: add local fuzzy and semantic search`.

### Task 6: CLI, Codex Skill, documentation, and end-to-end safety tests

**Files:**
- Create: `src/media_catalog/cli.py`, `skills/media-catalog-search/SKILL.md`, `README.md`, `tests/test_cli.py`, `tests/test_e2e.py`

**Interfaces:**
- Produces commands `scan`, `list-pending`, `process --ids`, `search`, and `doctor`; all require explicit catalog, Excel, backup, and model configuration paths.

- [ ] **Step 1: Write failing CLI/e2e tests** for `scan` causing no media mutation, `process` requiring explicit IDs, and `doctor` rejecting unavailable Ollama/ExifTool/model paths.
- [ ] **Step 2: Run** `pytest tests/test_cli.py tests/test_e2e.py -v`; expect failure.
- [ ] **Step 3: Implement** argparse commands, clear Chinese status/error messages, configuration example, and Skill instructions which explicitly prohibit cloud/API flags and automatic processing. Document Google Drive as a user-managed sync location only.
- [ ] **Step 4: Run** `pytest -v`, `python -m media_catalog.cli doctor --help`, and a temporary-directory scan/process simulation with fake dependencies; expect all pass and no external network client invoked.
- [ ] **Step 5: Commit** with `feat: deliver offline media catalog skill`.

## Plan Self-Review

- Spec coverage: tasks 1-2 cover queue/Excel/recursive dedup; task 3 covers offline Qwen/watch integration and selected-only processing; task 4 covers backup, metadata verification and Markdown; task 5 covers local fuzzy/semantic search; task 6 covers Skill UX, documentation, and safety verification.
- No-placeholder check: all tasks name files, interfaces, commands, expected test behavior, and commit boundaries.
- Type consistency: `MediaRecord` originates in task 1 and is consumed by tasks 2-5; `Analysis` originates in task 3 and is consumed by task 4; completed records feed task 5.
