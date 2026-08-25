# Media Catalog A+ Stable v1.2 實作計畫

> **給代理工作者：** 必須使用子 Skill：建議採用 `superpowers:subagent-driven-development`，或使用 `superpowers:executing-plans`，逐項完成本計畫。所有步驟使用核取方塊（`- [ ]`）追蹤進度。

**目標：** 在原始 Media Catalog Agent 上加入可靠的長影片分段、選擇性 Gemini 3.7 Flash 強化、Excel 媒體超連結、可恢復 worker 與精簡 Tkinter 狀態 UI，同時保留原始命令、成果目錄與來源安全契約。

**架構：** 既有 `media-catalog` CLI 保留無 UI 模式；Codex Skill launcher 改由 Tkinter UI 啟動獨立 worker。worker 將影片拆成可 checkpoint 的片段，本機 Qwen3-VL 8B 先分析，不足時才使用受每支影片上限控制的 Gemini；所有進度先寫 SQLite，最後原子重建 Excel。

**技術堆疊：** Python 3.11+、SQLite、Tkinter、`openpyxl>=3.1,<4`、`Pillow>=10,<12`、Ollama CLI、Qwen3-VL 8B、FFmpeg、Watch Skill、MCP Video Analyzer 0.8.0、Gemini REST API、PowerShell、pytest 8、PyYAML 6。

## 全域限制

- 工作分支固定為 `codex/legacy-a-plus-v1.2`，基準 commit 為 `bfa92f7`。
- 原始 CLI 保持 `media-catalog`；自然語言入口保持 `整理並分析這個資料夾：<path>`。
- 成果目錄保持 `媒體整理成果`，SQLite 保持 `catalog.sqlite`，Excel 保持 `媒體清冊.xlsx`。
- schema 升級前必須把 SQLite 與 Excel 備份到 `媒體整理成果/APlus備份/YYYYMMDD-HHMMSS/` 並驗證。
- 影片使用 FFmpeg scene threshold `0.35`；單片段最長 `300` 秒；不足 `2` 秒的尾段併入前段。
- 每個片段選取 `1`～`3` 張、最長邊不超過 `1280` 像素的代表影格。
- 本機模型保持 `Qwen3-vl:8b-instruct`；Gemini 預設模型為 `gemini-3.7-flash`。
- 每支影片最多 Gemini 強化 `12` 個片段、最多傳送 `36` 張縮放影格；續跑不得重置額度。
- Gemini 只讀取 `GEMINI_API_KEY`；選用覆寫只讀取 `GEMINI_MODEL`；任何密鑰不得進入參數、檔案、日誌、SQLite、Excel、例外或測試快照。
- worker 每 `5` 秒心跳；`15` 秒未收到心跳視為異常。
- 預設逾時：分段／擷取 `20` 分鐘、本機片段分析 `10` 分鐘、Gemini `90` 秒；每階段只重試一次。
- UI 使用 Tkinter，不加入 Electron、Web UI、開機自動執行或隱藏常駐服務。
- 來源媒體保持原位且 SHA-256 不變；自動分析不得覆蓋 Excel 的 `已審核` 狀態。
- 只有 `failed=0`、`remaining=0` 且 Excel 同步成功時可輸出 `MEDIA_ANALYSIS_READY`。

---

## 檔案結構

新增檔案：

- `src/media_catalog/schema_migration.py`：A+ 備份、完整性驗證與可重入 schema migration。
- `src/media_catalog/run_state.py`：analysis run、片段狀態、心跳、停止請求與 Gemini 計數。
- `src/media_catalog/stage_runner.py`：可逾時、有限重試且可測試的階段執行器。
- `src/media_catalog/scene_segments.py`：FFmpeg 場景分段與 300 秒保護切分。
- `src/media_catalog/frame_selector.py`：Pillow 清晰度／差異評分及 1～3 張影格選取。
- `src/media_catalog/gemini_client.py`：安全 Gemini REST 客戶端與輸出解析。
- `src/media_catalog/segment_pipeline.py`：片段本機分析、品質判定、Gemini 升級與影片彙整。
- `src/media_catalog/supervisor.py`：worker 啟動、心跳監督、單次恢復與安全停止。
- `src/media_catalog/status_ui.py`：Tkinter 精簡監控面板。
- `skills/media-inventory/scripts/run_media_analysis_ui.ps1`：由 Skill 啟動 UI。

修改檔案：

- `pyproject.toml`：加入 Pillow 與測試用 PyYAML。
- `src/media_catalog/cli.py`：完成 CP950 安全輸出並接入 A+ headless worker。
- `src/media_catalog/database.py`：接入 migration／run state，不改變既有 `media_records` 對外行為。
- `src/media_catalog/excel_catalog.py`：讀回人工審核、路徑 hyperlink 與鎖定時延後同步。
- `src/media_catalog/batch_analysis.py`：使用片段 pipeline 與 SQLite checkpoint。
- `src/media_catalog/analysis_runtime.py`：建構分段、本機、Gemini 與 stage runner 依賴。
- `skills/media-inventory/SKILL.md`：說明 UI、心跳、恢復及完成標記。
- `skills/media-inventory/scripts/run_media_analysis.ps1`：轉交 UI launcher。
- `scripts/install-media-inventory-skill.ps1`：安裝新依賴並做 UI／headless 冒煙測試。
- `README.md`：A+ 安裝、操作、狀態與恢復說明。

---

## 階段 1：原始版穩定化

### 任務 1：固定測試環境並完成 CP950 安全輸出

**檔案：**
- 修改：`pyproject.toml`
- 修改：`src/media_catalog/cli.py`
- 修改：`tests/test_cli.py`
- 修改：`tests/test_skill_package.py`

**介面：**
- 使用：現有 `main(argv, runtime_builder=...) -> int`。
- 產出：`_print_console(message: str, *, stream=None, flush: bool = False) -> None`；測試 extra 可安裝 PyYAML。

- [ ] **步驟 1：先寫 CP950 與測試依賴斷言**

```python
def test_cli_escapes_filename_unsupported_by_console_encoding(tmp_path, monkeypatch):
    raw = io.BytesIO()
    cp950 = io.TextIOWrapper(raw, encoding="cp950", errors="strict")
    monkeypatch.setattr(sys, "stdout", cp950)
    exit_code = main([...], runtime_builder=lambda **_: SuccessfulAnalyzer())
    cp950.flush()
    assert exit_code == 0
    assert r"item=\u89c6\u9891.jpg" in raw.getvalue().decode("cp950")


def test_test_extra_declares_pyyaml():
    project = tomllib.loads(Path("pyproject.toml").read_text("utf-8"))
    assert any(item.lower().startswith("pyyaml") for item in project["project"]["optional-dependencies"]["test"])
```

- [ ] **步驟 2：執行測試並確認失敗**

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_cli.py tests/test_skill_package.py -v`

預期：CP950 測試因直接 `print` 失敗；PyYAML 宣告測試失敗。

- [ ] **步驟 3：加入安全輸出與依賴**

```python
def _print_console(message: str, *, stream=None, flush: bool = False) -> None:
    output = sys.stdout if stream is None else stream
    encoding = getattr(output, "encoding", None) or "utf-8"
    safe = message.encode(encoding, errors="backslashreplace").decode(encoding)
    print(safe, file=output, flush=flush)
```

把 CLI 的所有 `print` 改由 `_print_console` 輸出。在 `project.optional-dependencies.test` 加入 `PyYAML>=6,<7`，正式 dependencies 加入 `Pillow>=10,<12`。

- [ ] **步驟 4：重新安裝 editable 套件並跑完整基線**

執行：

```powershell
.\.venv\Scripts\python.exe -m pip install --no-cache-dir -e '.[test]'
.\.venv\Scripts\python.exe -m pytest -q
```

預期：至少 `73 passed, 1 skipped`，沒有失敗。

- [ ] **步驟 5：提交**

```powershell
git add pyproject.toml src/media_catalog/cli.py tests/test_cli.py tests/test_skill_package.py
git commit -m "fix: stabilize Windows console and test runtime"
```

### 任務 2：加入安全 schema migration、備份與 run state

**檔案：**
- 建立：`src/media_catalog/schema_migration.py`
- 建立：`src/media_catalog/run_state.py`
- 建立：`tests/test_schema_migration.py`
- 建立：`tests/test_run_state.py`
- 修改：`src/media_catalog/database.py`

**介面：**
- 使用：`CatalogDatabase.db_path`、`MediaWorkspace.excel_path`。
- 產出：`ensure_a_plus_schema(database_path: Path, excel_path: Path) -> MigrationResult`、`RunStateStore`、`AnalysisRun`、`VideoSegment`。

- [ ] **步驟 1：寫入會失敗的備份與可重入 migration 測試**

```python
def test_migration_backs_up_database_and_excel_before_schema_change(tmp_path):
    db_path, excel_path = make_legacy_catalog(tmp_path)
    before_db = sha256(db_path)
    result = ensure_a_plus_schema(db_path, excel_path, now=fixed_now)
    assert result.backup_dir.name == "20260825-143000"
    assert sha256(result.backup_dir / "catalog.sqlite") == before_db
    assert sqlite_integrity(result.backup_dir / "catalog.sqlite") == "ok"


def test_migration_is_idempotent(tmp_path):
    db_path, excel_path = make_legacy_catalog(tmp_path)
    first = ensure_a_plus_schema(db_path, excel_path)
    second = ensure_a_plus_schema(db_path, excel_path)
    assert first.migrated is True
    assert second.migrated is False
```

- [ ] **步驟 2：執行測試並確認模組不存在**

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_schema_migration.py tests/test_run_state.py -v`

預期：FAIL，因為 migration 與 run state 尚未實作。

- [ ] **步驟 3：實作版本表、備份與新資料表**

建立 `schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)`、`analysis_runs` 與 `video_segments`。SQLite 備份使用 `sqlite3.Connection.backup()`，完成後執行 `PRAGMA integrity_check`；Excel 使用位元組複製到時間戳目錄並驗證存在與大小非零。

```python
@dataclass(frozen=True, slots=True)
class AnalysisRun:
    run_id: str
    root_path: Path
    status: str
    video_count: int
    image_count: int
    total_bytes: int
    total_media: int
    completed_media: int
    failed_media: int
    current_media_id: str | None
    current_segment_id: str | None
    worker_pid: int | None
    last_heartbeat: str | None
    stop_requested: bool
    recovery_count: int
    excel_sync_pending: bool
```

`RunStateStore` 必須提供 `create_run`、`update_counts`、`heartbeat`、`request_stop`、`clear_stop`、`set_excel_sync_pending`、`upsert_segments`、`mark_segment_status`、`requeue_stale_processing` 與 `consume_gemini_slot`。

- [ ] **步驟 4：驗證 Gemini 額度為原子且可續跑**

```python
def test_gemini_slot_stops_at_twelve_across_reopen(tmp_path):
    store = RunStateStore(tmp_path / "catalog.sqlite")
    for _ in range(12):
        assert store.consume_gemini_slot("video-1") is True
    assert RunStateStore(store.path).consume_gemini_slot("video-1") is False
```

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_schema_migration.py tests/test_run_state.py -v`

預期：PASS。

- [ ] **步驟 5：提交**

```powershell
git add src/media_catalog/schema_migration.py src/media_catalog/run_state.py src/media_catalog/database.py tests/test_schema_migration.py tests/test_run_state.py
git commit -m "feat: add recoverable A+ catalog state"
```

### 任務 3：加入可逾時、有限重試與心跳的階段執行器

**檔案：**
- 建立：`src/media_catalog/stage_runner.py`
- 建立：`tests/test_stage_runner.py`

**介面：**
- 使用：`RunStateStore.heartbeat(run_id, worker_pid)`。
- 產出：`StagePolicy`、`StageResult[T]`、`StageRunner.run(name, operation, policy) -> StageResult[T]`、`HeartbeatThread`。

- [ ] **步驟 1：寫入逾時、一次重試與心跳測試**

```python
def test_stage_runner_retries_once_then_returns_failure():
    operation = AlwaysFails(AnalysisError("bad frame"))
    result = StageRunner().run("local_analysis", operation, StagePolicy(600, 1))
    assert operation.calls == 2
    assert result.ok is False
    assert result.error_type == "AnalysisError"


def test_heartbeat_updates_while_stage_waits(tmp_path):
    store = fake_store(tmp_path)
    with HeartbeatThread(store, "run-1", interval_seconds=0.01):
        time.sleep(0.04)
    assert store.heartbeat_count >= 2
```

- [ ] **步驟 2：執行測試並確認失敗**

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_stage_runner.py -v`

預期：FAIL，因為 `stage_runner` 尚不存在。

- [ ] **步驟 3：實作階段政策與結構化結果**

```python
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
```

外部 FFmpeg／Ollama 操作使用既有 subprocess timeout；Gemini 使用 HTTP timeout。`StageRunner` 不做無限重試，不把完整 provider payload 放入錯誤訊息。`HeartbeatThread` 使用 daemon thread，但由 context manager 明確停止並 join。

- [ ] **步驟 4：執行單元與既有恢復測試**

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_stage_runner.py tests/test_batch_analysis.py tests/test_run_lock.py -v`

預期：PASS。

- [ ] **步驟 5：提交並完成階段 1 關卡**

```powershell
git add src/media_catalog/stage_runner.py tests/test_stage_runner.py
git commit -m "feat: supervise analysis stages with heartbeats"
.\.venv\Scripts\python.exe -m pytest -q
```

預期：完整套件通過。此處可以暫停，得到已修復 CP950 且具有資料／階段恢復基礎的版本。

---

## 階段 2：Excel 與長影片基礎

### 任務 4：加入 Excel 媒體 hyperlink 與審核狀態讀回

**檔案：**
- 修改：`src/media_catalog/excel_catalog.py`
- 修改：`tests/test_excel_catalog.py`

**介面：**
- 使用：`write_excel(records, output_path) -> Path`。
- 產出：`read_reviewed_paths(excel_path: Path) -> set[str]`；存在來源的完整路徑儲存格具 file URI hyperlink。

- [ ] **步驟 1：寫入 hyperlink 與審核保留測試**

```python
def test_path_cell_links_to_existing_source(tmp_path):
    source, record = make_record(tmp_path, "片段 01.mp4")
    output = write_excel([record], tmp_path / "媒體清冊.xlsx")
    workbook = load_workbook(output)
    cell = workbook.active.cell(2, 3)
    assert cell.value == str(source.resolve())
    assert cell.hyperlink.target == source.resolve().as_uri()
    assert cell.style == "Hyperlink"


def test_atomic_rebuild_preserves_reviewed_status_by_path(tmp_path):
    output, record = workbook_with_status(tmp_path, "已審核")
    write_excel([record], output)
    workbook = load_workbook(output)
    assert workbook.active.cell(2, 1).value == "已審核"
```

- [ ] **步驟 2：執行測試並確認失敗**

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_excel_catalog.py -v`

預期：hyperlink 尚不存在，審核狀態會被重建為待確認。

- [ ] **步驟 3：實作路徑 hyperlink 與審核讀回**

重建前以 `read_only=True` 讀取既有 Excel，依標題找出 `狀態` 與 `完整路徑` 欄，只接受 `已審核`。存在來源檔時設定：

```python
path_cell.value = str(record.path)
path_cell.hyperlink = record.path.resolve().as_uri()
path_cell.style = "Hyperlink"
```

來源不存在時只保留文字。仍使用同目錄暫存檔與 `os.replace` 原子替換；鎖定失敗時保留原工作簿。

- [ ] **步驟 4：跑 Excel 與 CLI 重建測試**

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_excel_catalog.py tests/test_cli.py -v`

預期：PASS。

- [ ] **步驟 5：提交**

```powershell
git add src/media_catalog/excel_catalog.py tests/test_excel_catalog.py
git commit -m "feat: link Excel paths to source media"
```

### 任務 5：加入場景分段與 1～3 張代表影格

**檔案：**
- 建立：`src/media_catalog/scene_segments.py`
- 建立：`src/media_catalog/frame_selector.py`
- 建立：`tests/test_scene_segments.py`
- 建立：`tests/test_frame_selector.py`

**介面：**
- 使用：FFmpeg runner、Pillow `Image`／`ImageChops`／`ImageFilter`／`ImageStat`。
- 產出：`SceneSegmenter.segment(source: Path) -> tuple[SegmentRange, ...]`、`FrameSelector.select(candidates, ocr_text="") -> tuple[Path, ...]`。

- [ ] **步驟 1：寫入邊界與保護切分測試**

```python
def test_scene_boundaries_are_split_and_tail_is_merged():
    ranges = build_ranges(duration=607, scene_times=(10, 310), max_seconds=300, min_tail=2)
    assert ranges[0] == SegmentRange(0, 10)
    assert all(item.duration <= 300 for item in ranges)
    assert ranges[-1].duration >= 2


def test_static_video_is_forced_into_five_minute_segments():
    assert build_ranges(720, (), 300, 2) == (
        SegmentRange(0, 300), SegmentRange(300, 600), SegmentRange(600, 720)
    )
```

- [ ] **步驟 2：寫入代表影格數量與去重測試**

```python
@pytest.mark.parametrize(("difference", "ocr", "expected"), [
    ("low", "", 1), ("medium", "標題", 2), ("high", "大量文字" * 30, 3)
])
def test_selector_returns_one_to_three_distinct_frames(difference, ocr, expected, tmp_path):
    candidates = make_candidate_images(tmp_path, difference)
    selected = FrameSelector().select(candidates, ocr_text=ocr)
    assert len(selected) == expected
    assert len(set(selected)) == expected
```

- [ ] **步驟 3：執行測試並確認失敗**

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_scene_segments.py tests/test_frame_selector.py -v`

預期：FAIL，因為兩個新模組尚不存在。

- [ ] **步驟 4：實作 FFmpeg scene 探測與影格擷取**

`SceneSegmenter` 以參數陣列執行 `ffmpeg`／`ffprobe`，scene threshold 固定 `0.35`，解析 `pts_time` 後呼叫純函式 `build_ranges(...)` 加入 300 秒保護切分。候選影格在各段 20%、50%、80% 擷取，輸出縮放至最長邊 1280 的 JPEG。

`FrameSelector` 使用 FIND_EDGES variance 計算清晰度、`ImageChops.difference` 計算影格差異；OCR 長度與視覺差異共同決定選 1、2 或 3 張。所有排序必須穩定，以路徑作同分 tie-breaker。

- [ ] **步驟 5：執行場景、影格與既有 inference 測試**

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_scene_segments.py tests/test_frame_selector.py tests/test_inference.py -v`

預期：PASS，且既有 Watch／MCP 行為不變。

- [ ] **步驟 6：提交並完成階段 2 關卡**

```powershell
git add src/media_catalog/scene_segments.py src/media_catalog/frame_selector.py tests/test_scene_segments.py tests/test_frame_selector.py
git commit -m "feat: segment long videos into representative evidence"
.\.venv\Scripts\python.exe -m pytest -q
```

此處可以暫停，得到具有 Excel 可點路徑與長影片分段的純本機版本。

---

## 階段 3：Gemini 3.7 Flash 精準強化

### 任務 6：加入品質判定與安全 Gemini 客戶端

**檔案：**
- 建立：`src/media_catalog/gemini_client.py`
- 建立：`tests/test_gemini_client.py`
- 建立：`tests/test_analysis_quality.py`
- 修改：`src/media_catalog/inference.py`

**介面：**
- 使用：既有 `Analysis`、縮放代表影格、OCR、本機摘要。
- 產出：`assess_analysis_quality(analysis, *, ocr_text, frame_count) -> tuple[str, ...]`、`GeminiClient.analyze(request: GeminiSegmentRequest) -> Analysis`。

- [ ] **步驟 1：寫入固定品質判定測試**

```python
def test_quality_flags_blank_generic_and_ocr_missing_output():
    issues = assess_analysis_quality(
        Analysis("一個場景。", ("畫面",), ("內容",)),
        ocr_text="會議標題" * 20,
        frame_count=3,
    )
    assert {"generic_description", "generic_keywords", "ocr_not_reflected"} <= set(issues)
```

固定規則：描述少於 12 個非空白字、亮點重複、關鍵字全在 `內容／畫面／場景／影像／人物`、OCR 至少 80 字但描述未涵蓋主要 OCR token，或三張影格分析結果互相衝突。

- [ ] **步驟 2：寫入金鑰、路徑與錯誤遮罩測試**

```python
def test_request_has_preview_bytes_but_no_key_or_local_path(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-secret")
    transport = RecordingTransport(valid_response())
    GeminiClient(transport=transport).analyze(make_request(tmp_path))
    body = json.dumps(transport.body, ensure_ascii=False)
    assert "unit-test-secret" not in body
    assert str(tmp_path) not in body
    assert transport.headers["x-goog-api-key"] == "unit-test-secret"


def test_provider_error_redacts_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-secret")
    with pytest.raises(GeminiError) as captured:
        GeminiClient(transport=FailingTransport("unit-test-secret quota")).analyze(sample_request())
    assert "unit-test-secret" not in str(captured.value)
```

- [ ] **步驟 3：執行測試並確認失敗**

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_analysis_quality.py tests/test_gemini_client.py -v`

預期：FAIL，因為品質函式與 Gemini 客戶端尚不存在。

- [ ] **步驟 4：實作 REST 請求與 schema 驗證**

使用 `urllib.request.Request`，端點為 `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`，標頭使用 `x-goog-api-key`。預設模型：

```python
model = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
```

JSON MIME parts 只包含 base64 縮放影格、OCR 與本機摘要。使用 90 秒 timeout，回傳內容必須通過既有 `Analysis` schema；所有例外先以實際 key 做字串遮罩，再轉成短錯誤類型。

- [ ] **步驟 5：執行完全離線的單元測試**

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_analysis_quality.py tests/test_gemini_client.py tests/test_inference.py -v`

預期：PASS，沒有真實網路請求。

- [ ] **步驟 6：提交**

```powershell
git add src/media_catalog/gemini_client.py src/media_catalog/inference.py tests/test_gemini_client.py tests/test_analysis_quality.py
git commit -m "feat: add selective Gemini 3.7 Flash analysis"
```

### 任務 7：整合可續跑片段 pipeline 與批次流程

**檔案：**
- 建立：`src/media_catalog/segment_pipeline.py`
- 建立：`tests/test_segment_pipeline.py`
- 修改：`src/media_catalog/batch_analysis.py`
- 修改：`src/media_catalog/analysis_runtime.py`
- 修改：`tests/test_batch_analysis.py`
- 修改：`tests/test_analysis_runtime.py`

**介面：**
- 使用：`SceneSegmenter`、`FrameSelector`、`LocalAnalyzer`、`GeminiClient`、`RunStateStore`、`StageRunner`。
- 產出：`SegmentPipeline.analyze_video(record, run_id) -> VideoAnalysisResult`；批次逐媒體 checkpoint。

- [ ] **步驟 1：寫入本機優先、選擇性升級與上限測試**

```python
def test_good_local_segment_never_calls_gemini():
    pipeline = make_pipeline(local=good_analysis(), gemini=ForbiddenClient())
    result = pipeline.analyze_segment(sample_segment())
    assert result.provider == "ollama"


def test_only_twelve_segments_can_use_gemini_across_resume(tmp_path):
    pipeline = make_pipeline_with_thirteen_weak_segments(tmp_path)
    result = pipeline.analyze_video(sample_record(), "run-1")
    assert result.gemini_segments == 12
    assert result.needs_review_segments == 1
    assert pipeline.sent_frame_count <= 36
```

- [ ] **步驟 2：寫入片段 checkpoint 與 crash 恢復測試**

```python
def test_resume_skips_completed_segments_and_requeues_stale_processing(tmp_path):
    store, pipeline = interrupted_video_pipeline(tmp_path, completed=(1, 2), processing=(3,))
    pipeline.analyze_video(sample_record(), "run-1")
    assert pipeline.local_calls_for_segments == (3, 4, 5)
    assert all(segment.status == "completed" for segment in store.list_segments("video-1"))
```

- [ ] **步驟 3：執行測試並確認失敗**

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_segment_pipeline.py tests/test_batch_analysis.py -v`

預期：FAIL，因為片段 pipeline 尚不存在。

- [ ] **步驟 4：實作片段狀態機與本機文字彙整**

處理順序固定為：建立／重用片段 → 逐片段擷取候選 → 選代表影格 → 本機分析 → 品質判定 → 原子取得 Gemini slot → 選擇性雲端強化 → 保存片段 checkpoint。Gemini 失敗時保存本機結果與 `cloud_failed:<error_type>`。

所有片段結束後，以本機 8B 的文字提示彙整片段描述；不得額外呼叫 Gemini。存在無可用結果的失敗片段時，影片保持 incomplete；只有 Gemini 失敗但本機結果完整時可完成並記錄警告。

- [ ] **步驟 5：整合 batch，讓 Excel 鎖定不阻斷 SQLite 分析**

`analyze_pending` 每完成一段寫 SQLite，但只在媒體完成或批次結束時嘗試 Excel 重建。`PermissionError` 記為 `excel_sync_pending`，不中止 worker；最後 Excel 仍鎖定時輸出 incomplete。分析前後繼續呼叫 `verify_record_source`。

- [ ] **步驟 6：執行 pipeline、batch、來源完整性與 runtime 測試**

執行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_segment_pipeline.py tests/test_batch_analysis.py tests/test_analysis_runtime.py tests/test_source_guard.py -v
.\.venv\Scripts\python.exe -m pytest -q
```

預期：完整套件通過。

- [ ] **步驟 7：提交並完成階段 3 關卡**

```powershell
git add src/media_catalog/segment_pipeline.py src/media_catalog/batch_analysis.py src/media_catalog/analysis_runtime.py tests/test_segment_pipeline.py tests/test_batch_analysis.py tests/test_analysis_runtime.py
git commit -m "feat: resume long-video analysis by segment"
```

此處可以暫停，得到沒有 UI、但可從 CLI 完整使用長影片與選擇性 Gemini 的版本。

---

## 階段 4：精簡 UI、Supervisor 與可攜安裝

### 任務 8：加入 worker Supervisor 與 Tkinter 狀態 UI

**檔案：**
- 建立：`src/media_catalog/supervisor.py`
- 建立：`src/media_catalog/status_ui.py`
- 建立：`tests/test_supervisor.py`
- 建立：`tests/test_status_ui.py`
- 修改：`src/media_catalog/cli.py`

**介面：**
- 使用：`RunStateStore`、既有分析鎖、headless `media-catalog analyze-all`。
- 產出：`WorkerSupervisor.start(root, skill_root) -> int`、`poll() -> SupervisorSnapshot`、`request_safe_stop()`、`StatusViewModel.from_run(run) -> StatusViewModel`、`status_ui.main(argv=None) -> int`。

- [ ] **步驟 1：寫入燈號與顯示數字測試**

```python
@pytest.mark.parametrize(("age", "worker_alive", "color"), [
    (5, True, "green"), (16, True, "red"), (1, False, "red")
])
def test_view_model_uses_heartbeat_for_light(age, worker_alive, color):
    model = StatusViewModel.from_run(run_with_heartbeat_age(age), worker_alive=worker_alive)
    assert model.light_color == color


def test_view_model_formats_progress_and_media_totals():
    model = StatusViewModel.from_run(sample_run(total=100, completed=55, videos=72, images=28))
    assert model.progress_text == "55 / 100"
    assert model.remaining_text == "45"
    assert model.video_count == 72 and model.image_count == 28
```

- [ ] **步驟 2：寫入 worker crash、單次重啟與安全停止測試**

```python
def test_supervisor_restarts_dead_worker_only_once(tmp_path):
    supervisor = supervisor_with_two_crashing_workers(tmp_path)
    supervisor.start(root, skill_root)
    supervisor.poll()
    supervisor.poll()
    assert supervisor.launch_count == 2
    assert supervisor.snapshot.status == "error"


def test_safe_stop_sets_request_without_terminating_worker(tmp_path):
    supervisor = running_supervisor(tmp_path)
    supervisor.request_safe_stop()
    assert supervisor.store.get_run(supervisor.run_id).stop_requested is True
    assert supervisor.process.terminate_calls == 0
```

- [ ] **步驟 3：執行測試並確認失敗**

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_supervisor.py tests/test_status_ui.py -v`

預期：FAIL，因為 Supervisor 與 UI 尚不存在。

- [ ] **步驟 4：實作 Supervisor，不讓 UI 直接碰分析工作**

Supervisor 以參數陣列啟動 Skill runtime 的 Python：

```python
[python_executable, "-m", "media_catalog.cli", "analyze-all", str(root), "--skill-root", str(skill_root)]
```

每秒 poll 程序與 SQLite 心跳。程序消失時只自動重啟一次。若程序仍存在但心跳超過 15 秒，Supervisor 先寫入 stop request 並等待 10 秒；若該 worker 仍未退出，只終止 Supervisor 自己持有 handle 的確切 worker 程序（不可使用廣泛的 `taskkill`、不可終止程序樹），等待 handle 關閉後執行 `requeue_stale_processing`，再啟動唯一一次替代 worker。同一 segment 的 `crash_count >= 2` 時先標記失敗再處理下一段。使用者按下「安全停止」時只寫 stop request，絕不強制終止程序。

- [ ] **步驟 5：實作 Tkinter 單視窗與非阻塞更新**

UI 只透過 `root.after(1000, refresh)` 更新。顯示路徑、紅／綠燈、影片數、照片數、總容量、完成／總數、未完成、目前媒體／片段、Gemini `used / 12` 及進度條。按鈕呼叫 Supervisor、`os.startfile(excel_path)` 與 `os.startfile(result_root)`。

視窗關閉時顯示 `安全停止後關閉`／`取消關閉`；選擇停止後等待 worker checkpoint 與退出，UI 不直接 kill 程序。

- [ ] **步驟 6：執行 Supervisor、UI 與 CLI 測試**

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_supervisor.py tests/test_status_ui.py tests/test_cli.py -v`

預期：PASS；測試不開啟真實視窗，ViewModel 與 Supervisor 以 fake process 驗證。

- [ ] **步驟 7：提交**

```powershell
git add src/media_catalog/supervisor.py src/media_catalog/status_ui.py src/media_catalog/cli.py tests/test_supervisor.py tests/test_status_ui.py tests/test_cli.py
git commit -m "feat: monitor catalog analysis in a desktop UI"
```

### 任務 9：接入 Skill、安裝器、文件與完整驗收

**檔案：**
- 建立：`skills/media-inventory/scripts/run_media_analysis_ui.ps1`
- 修改：`skills/media-inventory/scripts/run_media_analysis.ps1`
- 修改：`skills/media-inventory/SKILL.md`
- 修改：`scripts/install-media-inventory-skill.ps1`
- 修改：`README.md`
- 建立：`docs/media-catalog-a-plus-setup.md`
- 建立：`tests/test_a_plus_launcher.py`
- 修改：`tests/test_installer.py`
- 修改：`tests/test_skill_package.py`
- 建立：`tests/integration/test_gemini_37_live.py`
- 建立：`tests/test_a_plus_security.py`

**介面：**
- 使用：`media_catalog.status_ui`、現有 Skill runtime／tools。
- 產出：自然語言指令自動開啟 UI 並開始分析；可重現安裝與安全驗收流程。

- [ ] **步驟 1：寫入 launcher、安裝器與密鑰掃描測試**

```python
def test_analysis_launcher_delegates_to_ui():
    launcher = Path("skills/media-inventory/scripts/run_media_analysis.ps1").read_text("utf-8")
    assert "run_media_analysis_ui.ps1" in launcher
    assert "GEMINI_API_KEY" not in launcher


def test_tracked_files_have_no_gemini_key_shape():
    tracked = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
    matches = [name for name in tracked if re.search(r"AIza[0-9A-Za-z_-]{30,}", Path(name).read_text("utf-8", errors="ignore"))]
    assert matches == []
```

- [ ] **步驟 2：執行封裝測試並確認失敗**

執行：`.\.venv\Scripts\python.exe -m pytest tests/test_a_plus_launcher.py tests/test_installer.py tests/test_skill_package.py tests/test_a_plus_security.py -v`

預期：UI launcher 與 A+ 文件尚不存在，測試失敗。

- [ ] **步驟 3：實作 PowerShell launcher 與安裝冒煙測試**

`run_media_analysis_ui.ps1` 設定 `$ErrorActionPreference='Stop'`、`$env:PYTHONUTF8='1'`，解析 Skill 私有 runtime，呼叫：

```powershell
& $runtimePython -m media_catalog.status_ui --root $RootPath --skill-root $skillRoot
```

安裝器繼續使用專屬 `.runtime` 與 `.tools`，安裝包含 Pillow 的專案、固定 MCP 0.8.0，執行 headless catalog smoke test，並以 `python -c "import tkinter"` 驗證 Tkinter 可用。不得讀取或輸出 API Key。

- [ ] **步驟 4：撰寫 A+ 使用與恢復文件**

文件包含：相同自然語言指令、UI 燈號、開始／安全停止、Excel hyperlink、場景分段規則、Gemini 上限、`GEMINI_API_KEY`／`GEMINI_MODEL` 私人環境變數、金鑰輪替、Excel 鎖定、worker 恢復、完成標記及新電腦重新安裝。不得放入外觀近似真實金鑰的範例。

- [ ] **步驟 5：加入明確 opt-in 的 Gemini 即時測試**

```python
@pytest.mark.skipif(os.getenv("RUN_GEMINI_LIVE_TEST") != "1", reason="explicit opt-in required")
def test_live_gemini_37_flash(tmp_path):
    assert os.getenv("GEMINI_API_KEY")
    result = GeminiClient().analyze(tiny_preview_request(tmp_path))
    assert result.description.strip()
```

一般 `pytest` 必須跳過此測試。即時測試只回報通過／失敗與模型名稱，不輸出 header、request body、環境變數或 provider payload。

- [ ] **步驟 6：執行完整離線測試與靜態安全檢查**

執行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
git grep -n -E 'AIza[0-9A-Za-z_-]{30,}' -- . ':!*.xlsx'
```

預期：pytest 全部通過，Gemini live test 為 skipped；`git diff --check` 為 0；密鑰 grep 沒有命中並以 1 結束。

- [ ] **步驟 7：執行隔離端到端冒煙測試**

在系統暫存目錄建立不含私人資料的測試根目錄，放入一張照片及一支以 FFmpeg 產生的短多場景影片。記錄兩個來源 SHA-256，執行 UI worker 的 headless 等價流程，驗證：

```text
MEDIA_ANALYSIS_READY ... failed=0 remaining=0
MEDIA_SOURCES_VERIFIED total=2
```

再以 openpyxl 確認兩筆 `完整路徑` hyperlink、非空描述／重點／關鍵字與保存的審核狀態；最後確認來源 SHA-256 不變。此冒煙測試使用 fake Gemini，不消耗 API。

- [ ] **步驟 8：使用新輪替金鑰選擇性執行一次 live test**

只有使用者確認先前曝光金鑰已撤銷，且新金鑰已私下設定時執行：

```powershell
$env:RUN_GEMINI_LIVE_TEST='1'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_gemini_37_live.py -v
Remove-Item Env:RUN_GEMINI_LIVE_TEST
```

預期：PASS，終端不顯示任何金鑰值。失敗時保留已通過的離線版本，只回報經清理的錯誤類型。

- [ ] **步驟 9：提交並完成階段 4 關卡**

```powershell
git add skills/media-inventory scripts/install-media-inventory-skill.ps1 README.md docs/media-catalog-a-plus-setup.md tests/test_a_plus_launcher.py tests/test_installer.py tests/test_skill_package.py tests/integration/test_gemini_37_live.py tests/test_a_plus_security.py
git commit -m "docs: package and verify Media Catalog A+ Stable"
```

---

## 最終發布前關卡

1. 執行 `.\.venv\Scripts\python.exe -m pytest -q` 並記錄精確 pass／skip 數量。
2. 執行 `git diff --check` 與已追蹤檔案密鑰掃描。
3. 確認 `git status --short` 不含 SQLite、Excel、影格、`.runtime`、`.tools`、`.venv` 或 API 材料。
4. 確認所有 A+ commit 只存在於 `codex/legacy-a-plus-v1.2`。
5. 確認主工作區 `codex/hybrid-lite-v1`、Hybrid Lite 規格與未提交 CP950 修改沒有被此 worktree 變更。
6. 確認 UI 關閉後沒有殘留 worker，分析鎖已釋放。
7. 未取得使用者另行授權前，不得 push、開 PR、合併、建立 tag 或取代 `master`。
