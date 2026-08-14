# Conversational Root Folder Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user paste one local Windows folder path in Codex and safely create/update `媒體整理成果/catalog.sqlite` and `媒體整理成果/媒體清冊.xlsx` without scanning generated outputs or modifying source media.

**Architecture:** Add a `MediaWorkspace` value object that derives every output path from one validated root. A single `bootstrap_workspace()` service creates missing result directories, calls a prunable scanner that excludes output/reparse subtrees, writes the Excel queue, and returns a structured summary. Both the CLI and the installed `media-inventory` Skill call this service; the Skill installer backs up the pre-existing standalone Skill before replacing it.

**Tech Stack:** Python 3.11+, pathlib, os.scandir/os.lstat, SQLite, openpyxl, pytest, argparse, Windows PowerShell 5.1 installer, Codex Agent Skills format.

## Global Constraints

- Windows-first; pass filesystem and subprocess arguments as arrays or `Path` values, never shell-concatenate user paths.
- Treat the pasted path as the existing media root; do not create a separate input folder.
- Reject missing paths, files, drive roots, `媒體整理成果` itself, symbolic links, junctions, and other Windows reparse points as roots.
- Create/reuse `<root>/媒體整理成果` without deleting or overwriting existing outputs.
- Permanently exclude the complete result subtree, symbolic links, junctions, and other reparse points from scanning.
- The conversational start operation scans and creates a pending queue only; it never analyzes every file or mutates source media automatically.
- Preserve the existing installed `media-inventory` Skill through a timestamped sibling backup before replacement.
- Runtime scanning and catalog generation must not call external APIs or download dependencies.

---

### Task 1: One-root workspace layout and validation

**Files:**
- Create: `src/media_catalog/workspace.py`
- Create: `tests/test_workspace.py`

**Interfaces:**
- Consumes: one `Path` supplied by the user.
- Produces: `MediaWorkspace.from_root(root: Path) -> MediaWorkspace`, `MediaWorkspace.ensure_directories() -> None`, and `is_reparse_point(path: Path) -> bool`.

- [ ] **Step 1: Write failing tests for derived paths and safe directory creation**

```python
def test_workspace_derives_and_creates_fixed_result_layout(tmp_path: Path) -> None:
    root = tmp_path / "家庭照片影片"
    root.mkdir()

    workspace = MediaWorkspace.from_root(root)
    workspace.ensure_directories()

    assert workspace.root == root.resolve()
    assert workspace.result_root == root.resolve() / "媒體整理成果"
    assert workspace.database_path == workspace.result_root / "catalog.sqlite"
    assert workspace.excel_path == workspace.result_root / "媒體清冊.xlsx"
    assert {
        workspace.markdown_dir.name,
        workspace.backup_dir.name,
        workspace.index_dir.name,
        workspace.temp_dir.name,
    } == {"Markdown", "備份", "索引", "工作暫存"}
    assert all(path.is_dir() for path in workspace.directories)


def test_workspace_rejects_result_directory_as_root(tmp_path: Path) -> None:
    result_root = tmp_path / "媒體整理成果"
    result_root.mkdir()

    with pytest.raises(WorkspacePathError, match="成果目錄不能作為掃描根目錄"):
        MediaWorkspace.from_root(result_root)
```

- [ ] **Step 2: Run tests and verify the missing-module failure**

Run: `pytest tests/test_workspace.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'media_catalog.workspace'`.

- [ ] **Step 3: Implement the immutable workspace object and validation**

```python
@dataclass(frozen=True, slots=True)
class MediaWorkspace:
    root: Path
    result_root: Path
    database_path: Path
    excel_path: Path
    markdown_dir: Path
    backup_dir: Path
    index_dir: Path
    temp_dir: Path

    @classmethod
    def from_root(cls, root: Path) -> "MediaWorkspace":
        resolved = Path(root).expanduser().resolve()
        if not resolved.exists():
            raise WorkspacePathError(f"找不到資料夾：{resolved}")
        if not resolved.is_dir():
            raise WorkspacePathError(f"路徑不是資料夾：{resolved}")
        if resolved == Path(resolved.anchor):
            raise WorkspacePathError("磁碟根目錄不能作為掃描根目錄")
        if resolved.name.casefold() == "媒體整理成果".casefold():
            raise WorkspacePathError("成果目錄不能作為掃描根目錄")
        if is_reparse_point(resolved):
            raise WorkspacePathError("符號連結或 reparse point 不能作為掃描根目錄")
        result = resolved / "媒體整理成果"
        return cls(
            root=resolved,
            result_root=result,
            database_path=result / "catalog.sqlite",
            excel_path=result / "媒體清冊.xlsx",
            markdown_dir=result / "Markdown",
            backup_dir=result / "備份",
            index_dir=result / "索引",
            temp_dir=result / "工作暫存",
        )
```

Implement `is_reparse_point()` with `os.lstat()` and `stat.FILE_ATTRIBUTE_REPARSE_POINT` when `st_file_attributes` is available. `ensure_directories()` must call `mkdir(parents=True, exist_ok=True)` for the six result directories, then create and remove a uniquely named write probe inside `result_root`; convert `OSError` to `WorkspacePathError` without deleting any existing directory.

- [ ] **Step 4: Run workspace tests**

Run: `pytest tests/test_workspace.py -v`

Expected: all workspace tests pass.

- [ ] **Step 5: Commit the workspace boundary**

```powershell
git add src/media_catalog/workspace.py tests/test_workspace.py
git commit -m "feat: derive media workspace from one root"
```

### Task 2: Prunable scanner with result and reparse exclusions

**Files:**
- Modify: `src/media_catalog/scanner.py`
- Modify: `tests/test_scanner.py`

**Interfaces:**
- Consumes: `scan(root: Path, database: CatalogDatabase, *, excluded_roots: Iterable[Path] = ())`.
- Produces: `ScanResult(discovered: int, existing: int, supported: int, unsupported: int)` while never entering excluded/reparse directories.

- [ ] **Step 1: Add failing exclusion and repeat-scan tests**

```python
def test_scan_excludes_complete_result_subtree(tmp_path: Path) -> None:
    root = tmp_path / "media"
    result_root = root / "媒體整理成果"
    result_root.mkdir(parents=True)
    (root / "original.mp4").write_bytes(b"original")
    (result_root / "backup.mp4").write_bytes(b"backup")
    database = CatalogDatabase(result_root / "catalog.sqlite")

    scan_result = scan(root, database, excluded_roots=(result_root,))

    assert scan_result.discovered == 1
    assert scan_result.existing == 0
    assert [record.path.name for record in database.list_records()] == ["original.mp4"]


def test_second_scan_reports_existing_without_duplicate(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (root / "photo.jpg").write_bytes(b"photo")
    database = CatalogDatabase(tmp_path / "catalog.sqlite")

    first = scan(root, database)
    second = scan(root, database)

    assert (first.discovered, first.existing) == (1, 0)
    assert (second.discovered, second.existing) == (0, 1)
```

- [ ] **Step 2: Run scanner tests and verify the signature/result failures**

Run: `pytest tests/test_scanner.py -v`

Expected: FAIL because `scan()` has no `excluded_roots` parameter and `ScanResult` has no `existing` field.

- [ ] **Step 3: Replace `Path.rglob()` with a prunable directory walk**

Implement `_iter_files(root, excluded_roots)` using an explicit `list[Path]` stack plus `os.scandir()`. Resolve and case-fold exclusion roots once. For every directory entry:

```python
if entry.is_symlink() or is_reparse_point(Path(entry.path)):
    continue
if entry.is_dir(follow_symlinks=False):
    if _is_within_excluded(Path(entry.path), exclusions):
        continue
    pending.append(Path(entry.path))
elif entry.is_file(follow_symlinks=False):
    yield Path(entry.path)
```

Increment `existing` when `(normalized path, fingerprint)` already exists; retain existing status and ID through `CatalogDatabase.upsert_discovered()`.

- [ ] **Step 4: Run scanner and database tests**

Run: `pytest tests/test_scanner.py tests/test_database.py -v`

Expected: all tests pass and the result subtree contributes zero media records.

- [ ] **Step 5: Commit safe scanning**

```powershell
git add src/media_catalog/scanner.py tests/test_scanner.py
git commit -m "feat: exclude generated and reparse subtrees"
```

### Task 3: Bootstrap service and one-command CLI

**Files:**
- Create: `src/media_catalog/bootstrap.py`
- Create: `src/media_catalog/cli.py`
- Modify: `pyproject.toml`
- Create: `tests/test_bootstrap.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `bootstrap_workspace(root: Path) -> BootstrapResult`.
- Produces: `BootstrapResult(workspace: MediaWorkspace, scan: ScanResult, total_records: int)` and console command `media-catalog start <root>`.

- [ ] **Step 1: Write failing end-to-end bootstrap tests**

```python
def test_bootstrap_creates_catalog_below_pasted_root(tmp_path: Path) -> None:
    root = tmp_path / "家庭照片影片"
    root.mkdir()
    source = root / "旅行.jpg"
    source.write_bytes(b"photo")

    result = bootstrap_workspace(root)

    assert result.workspace.database_path.is_file()
    assert result.workspace.excel_path.is_file()
    assert result.scan.discovered == 1
    assert result.total_records == 1
    assert source.read_bytes() == b"photo"


def test_bootstrap_is_idempotent_and_ignores_its_outputs(tmp_path: Path) -> None:
    root = tmp_path / "家庭照片影片"
    root.mkdir()
    (root / "旅行.jpg").write_bytes(b"photo")

    first = bootstrap_workspace(root)
    second = bootstrap_workspace(root)

    assert (first.scan.discovered, first.scan.existing) == (1, 0)
    assert (second.scan.discovered, second.scan.existing) == (0, 1)
    assert second.total_records == 1
```

- [ ] **Step 2: Run bootstrap tests and verify the missing service failure**

Run: `pytest tests/test_bootstrap.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'media_catalog.bootstrap'`.

- [ ] **Step 3: Implement bootstrap orchestration**

```python
@dataclass(frozen=True, slots=True)
class BootstrapResult:
    workspace: MediaWorkspace
    scan: ScanResult
    total_records: int


def bootstrap_workspace(root: Path) -> BootstrapResult:
    workspace = MediaWorkspace.from_root(root)
    workspace.ensure_directories()
    database = CatalogDatabase(workspace.database_path)
    scan_result = scan(
        workspace.root,
        database,
        excluded_roots=(workspace.result_root,),
    )
    records = database.list_records()
    write_excel(records, workspace.excel_path)
    return BootstrapResult(workspace, scan_result, len(records))
```

- [ ] **Step 4: Write failing CLI tests for path-only execution and errors**

```python
def test_cli_start_prints_machine_readable_ready_marker(tmp_path: Path, capsys) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (root / "clip.mp4").write_bytes(b"video")

    exit_code = main(["start", str(root)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "MEDIA_CATALOG_READY" in output
    assert "added=1" in output
    assert str(root / "媒體整理成果" / "媒體清冊.xlsx") in output
```

- [ ] **Step 5: Implement CLI and package entry point**

Add to `pyproject.toml`:

```toml
[project.scripts]
media-catalog = "media_catalog.cli:main"
```

Implement `main(argv: Sequence[str] | None = None) -> int` with argparse command `start ROOT`. On success print exactly one UTF-8 ready line:

```text
MEDIA_CATALOG_READY added=<n> existing=<n> skipped=<n> total=<n> catalog=<absolute-xlsx-path>
```

On `WorkspacePathError`, print `MEDIA_CATALOG_ERROR <message>` to stderr and return exit code `2`. Do not catch unexpected exceptions.

- [ ] **Step 6: Run bootstrap/CLI/full regression tests**

Run: `pytest tests/test_bootstrap.py tests/test_cli.py -v && pytest -v`

Expected: bootstrap and CLI tests pass; the complete suite remains green.

- [ ] **Step 7: Commit the one-command entry**

```powershell
git add pyproject.toml src/media_catalog/bootstrap.py src/media_catalog/cli.py tests/test_bootstrap.py tests/test_cli.py
git commit -m "feat: bootstrap catalog from one folder path"
```

### Task 4: Reversible `media-inventory` Skill upgrade and installation

**Files:**
- Create: `skills/media-inventory/SKILL.md`
- Create: `skills/media-inventory/agents/openai.yaml`
- Create: `skills/media-inventory/scripts/run_media_catalog.ps1`
- Create: `scripts/install-media-inventory-skill.ps1`
- Create: `tests/test_skill_package.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: a path-only or `整理這個資料夾：<path>` Codex request.
- Produces: an installed `media-inventory` Skill that invokes its private runtime and returns the `MEDIA_CATALOG_READY` summary; installer creates a timestamped backup of any previous Skill.

- [ ] **Step 1: Write failing package validation tests**

```python
def test_skill_package_has_required_entrypoints() -> None:
    skill_root = Path("skills/media-inventory")
    assert (skill_root / "SKILL.md").is_file()
    assert (skill_root / "agents/openai.yaml").is_file()
    assert (skill_root / "scripts/run_media_catalog.ps1").is_file()


def test_skill_description_covers_pasted_windows_paths() -> None:
    text = Path("skills/media-inventory/SKILL.md").read_text(encoding="utf-8")
    assert "貼上" in text
    assert "本機資料夾路徑" in text
    assert "媒體整理成果" in text
```

- [ ] **Step 2: Run the package tests and verify missing files**

Run: `pytest tests/test_skill_package.py -v`

Expected: FAIL because the project Skill package does not exist.

- [ ] **Step 3: Create the thin Skill package**

`SKILL.md` must instruct Codex to:

1. Accept exactly one existing local folder path.
2. Run `scripts/run_media_catalog.ps1 -RootPath '<path>'`.
3. Treat `MEDIA_CATALOG_READY` as success and link the emitted Excel path.
4. Treat `MEDIA_CATALOG_ERROR` as failure and report it without guessing another path.
5. Never automatically process pending IDs or modify source media.

`run_media_catalog.ps1` must use `-LiteralPath`, validate `<skill-root>/.runtime/Scripts/python.exe`, then invoke:

```powershell
& $runtimePython -m media_catalog.cli start -- $RootPath
exit $LASTEXITCODE
```

Use the system `skill-creator` generator to create `agents/openai.yaml` with:

```text
display_name=媒體整理與搜尋
short_description=貼上本機資料夾路徑，建立或更新安全的媒體清冊
default_prompt=掃描這個本機資料夾並建立媒體整理成果：
```

- [ ] **Step 4: Implement the reversible installer**

`install-media-inventory-skill.ps1` accepts `-Destination` (default `$env:USERPROFILE\.codex\skills\media-inventory`) and `-ProjectRoot` (default repository root). It must:

1. Resolve and validate `ProjectRoot`, source Skill, and exact destination parent.
2. If the destination exists, move it to `media-inventory.backup-<yyyyMMdd-HHmmss>` in the same parent; abort if that backup path exists.
3. Copy the new Skill package to the exact destination.
4. Create `<destination>/.runtime` with `python -m venv`.
5. Install the local project into that private runtime with `python -m pip install <ProjectRoot>`.
6. Run system `quick_validate.py <destination>` and a temporary-folder `run_media_catalog.ps1` smoke test.
7. Print `MEDIA_INVENTORY_SKILL_READY destination=<path> backup=<path-or-none>` only after both checks pass.

Add `.runtime/` to `.gitignore`. If runtime creation or validation fails after the old Skill was moved, stop and print the exact old backup path; do not delete either copy or automatically overwrite the backup.

- [ ] **Step 5: Test install/backup behavior in a temporary destination**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-media-inventory-skill.ps1 `
  -Destination "$env:TEMP\media-inventory-install-test\media-inventory" `
  -ProjectRoot "$PWD"
```

Expected: `MEDIA_INVENTORY_SKILL_READY`; rerunning creates one timestamped backup and preserves the prior destination.

- [ ] **Step 6: Validate and test the complete project**

Run:

```powershell
pytest -v
python "C:\Users\pony6832\.codex\skills\.system\skill-creator\scripts\quick_validate.py" skills/media-inventory
git diff --check
```

Expected: all tests pass, Skill validation succeeds, and Git reports no whitespace errors.

- [ ] **Step 7: Commit the Skill package and installer**

```powershell
git add .gitignore skills/media-inventory scripts/install-media-inventory-skill.ps1 tests/test_skill_package.py
git commit -m "feat: add conversational media inventory skill"
```

## Plan Self-Review

- Spec coverage: Task 1 derives and creates every fixed result path; Task 2 permanently excludes results and reparse subtrees; Task 3 makes scanning idempotent and exposes one command; Task 4 provides the path-in-chat trigger and reversible upgrade of the existing Skill.
- Safety coverage: root/file/drive/result/reparse rejection is tested; generated outputs are never scanned; source bytes are asserted unchanged; existing Skill content is backed up before installation.
- Type consistency: `MediaWorkspace`, `ScanResult.existing`, `BootstrapResult`, and `bootstrap_workspace()` are introduced before their consumers.
- Scope: automatic media analysis, metadata mutation, Markdown sidecars, semantic search, and Google Drive remain outside this focused conversational-entry plan.
