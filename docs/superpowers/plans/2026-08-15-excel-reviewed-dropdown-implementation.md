# Excel 已審核下拉選單 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在媒體清冊 Excel 的「狀態」資料列加入 `待確認,已審核` 下拉選單，供使用者在活頁簿內人工標記審核結果。

**Architecture:** 只擴充現有 `write_excel()` 的活頁簿輸出層，使用 openpyxl `DataValidation` 對實際資料範圍加入 list validation。SQLite、`Status` 列舉、分析流程與來源媒體均保持不變；「已審核」不回寫資料庫，重新輸出 Excel 時可被覆蓋。

**Tech Stack:** Python 3.11、openpyxl 3.1、pytest 8、PowerShell 5.1-compatible Skill installer。

## Global Constraints

- 沿用 `.xlsx`，不得引入 VBA、Excel 外掛、背景監看或網路服務。
- 下拉選項精確為 `待確認,已審核`，套用範圍只包含本次輸出的資料列。
- 不新增欄位，不修改現有欄位順序、樣式、凍結窗格、自動篩選或欄寬。
- 不修改 SQLite schema、`Status` 列舉或媒體處理流程。
- 不讀取或匯入 Excel 中的「已審核」，重新產生清冊時允許還原成 SQLite 對應狀態。
- 不修改、移動、重新命名或刪除來源媒體。
- 使用 TDD；在 production code 之前先看到新測試以預期原因失敗。

---

### Task 1: 在 Excel 狀態欄加入人工審核下拉選單

**Files:**
- Modify: `tests/test_excel_catalog.py`
- Modify: `src/media_catalog/excel_catalog.py`

**Interfaces:**
- Consumes: `write_excel(records: Iterable[MediaRecord], output_path: Path) -> Path`、既有 `CATALOG_HEADERS` 與 openpyxl `Workbook`。
- Produces: 每個非空清冊中的單一 list `DataValidation`，`formula1='"待確認,已審核"'`，範圍由 `f"A2:A{len(catalog_records) + 1}"` 計算；空清冊不產生 data validation。

- [ ] **Step 1: 寫入有資料列的失敗測試**

在 `tests/test_excel_catalog.py` 新增：

```python
def test_write_excel_adds_review_dropdown_to_status_rows(tmp_path: Path) -> None:
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    first = database.upsert_discovered(first_path, "first", "image/png")
    second = database.upsert_discovered(second_path, "second", "image/png")

    saved_path = write_excel([first, second], tmp_path / "媒體清冊.xlsx")

    workbook = load_workbook(saved_path)
    sheet = workbook["媒體清冊"]
    validations = list(sheet.data_validations.dataValidation)
    assert len(validations) == 1
    validation = validations[0]
    assert validation.type == "list"
    assert validation.formula1 == '"待確認,已審核"'
    assert str(validation.sqref) == "A2:A3"
    assert validation.showDropDown is False
    workbook.close()
```

- [ ] **Step 2: 寫入空清冊的失敗測試**

同一檔案新增：

```python
def test_write_excel_omits_review_dropdown_for_empty_catalog(
    tmp_path: Path,
) -> None:
    saved_path = write_excel([], tmp_path / "媒體清冊.xlsx")

    workbook = load_workbook(saved_path)
    sheet = workbook["媒體清冊"]
    assert list(sheet.data_validations.dataValidation) == []
    assert sheet.max_row == 1
    workbook.close()
```

- [ ] **Step 3: 執行聚焦測試並確認 RED**

執行：

```powershell
$env:PYTHONPATH='src;C:\Users\pony6832\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages'
python -m pytest `
  tests/test_excel_catalog.py::test_write_excel_adds_review_dropdown_to_status_rows `
  tests/test_excel_catalog.py::test_write_excel_omits_review_dropdown_for_empty_catalog `
  -v
```

預期：第一個測試 FAIL，因為目前活頁簿沒有 data validation；第二個測試可先通過，並作為空範圍安全基線。至少必須看到第一個測試以 `len(validations) == 0` 失敗，才能進入 production code。

- [ ] **Step 4: 實作最小下拉選單輸出**

在 `src/media_catalog/excel_catalog.py` 加入 import：

```python
from openpyxl.worksheet.datavalidation import DataValidation
```

在 `destination.parent.mkdir(parents=True, exist_ok=True)` 後加入：

```python
catalog_records = list(records)
```

把既有迴圈標頭：

```python
for record in records:
```

改為：

```python
for record in catalog_records:
```

現有 12 欄 `sheet.append()` tuple 不做任何變更。在迴圈結束後、`header_fill = PatternFill("solid", fgColor="1F4E78")` 前加入：

```python
if catalog_records:
    review_validation = DataValidation(
        type="list",
        formula1='"待確認,已審核"',
        allow_blank=False,
        showDropDown=False,
    )
    review_validation.errorTitle = "無效狀態"
    review_validation.error = "請從下拉選單選擇待確認或已審核。"
    review_validation.showErrorMessage = True
    sheet.add_data_validation(review_validation)
    review_validation.add(f"A2:A{len(catalog_records) + 1}")
```

`catalog_records = list(records)` 確保 generator 只消費一次且可計算最後資料列。

- [ ] **Step 5: 執行 Excel 聚焦測試並確認 GREEN**

執行：

```powershell
$env:PYTHONPATH='src;C:\Users\pony6832\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages'
python -m pytest tests/test_excel_catalog.py -v
```

預期：所有 Excel 測試通過；既有欄位值、`待確認` 標籤與處理時間斷言保持通過。

- [ ] **Step 6: 執行完整回歸與差異檢查**

執行：

```powershell
$env:PYTHONPATH='src;C:\Users\pony6832\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages'
$env:PYTHONUTF8='1'
python -m pytest -q
python 'C:\Users\pony6832\.codex\skills\.system\skill-creator\scripts\quick_validate.py' 'skills/media-inventory'
git diff --check
```

預期：完整測試零失敗、Skill 有效、Git 無 whitespace error。

- [ ] **Step 7: 提交 Excel 下拉選單功能**

```powershell
git add -- src/media_catalog/excel_catalog.py tests/test_excel_catalog.py
git commit -m "feat: add reviewed status dropdown"
```

---

### Task 2: 可回復安裝並更新正式媒體清冊

**Files:**
- No tracked file changes expected。
- Reversible external update: `C:\Users\pony6832\.codex\skills\media-inventory`
- Authorized workbook refresh: `D:\【AIGC-ALL】\2026馬年賀卡AI\媒體整理成果\媒體清冊.xlsx`

**Interfaces:**
- Consumes: `scripts/install-media-inventory-skill.ps1`、已提交的 `write_excel()`、正式 Skill `run_media_catalog.ps1`。
- Produces: 已安裝的新版私有 runtime，以及狀態資料列 `A2:A21` 具有 `待確認,已審核` 下拉選單的正式清冊。

- [ ] **Step 1: 驗證正式 Skill 目的地與來源清冊**

執行只讀檢查：

```powershell
$destination = 'C:\Users\pony6832\.codex\skills\media-inventory'
$root = 'D:\【AIGC-ALL】\2026馬年賀卡AI'
Get-Item -LiteralPath $destination -Force |
  Select-Object FullName, PSIsContainer, Attributes, LinkType
Get-Item -LiteralPath (Join-Path $root '媒體整理成果\媒體清冊.xlsx') -Force |
  Select-Object FullName, Length, LastWriteTime
& (Join-Path $destination '.runtime\Scripts\python.exe') `
  -m media_catalog.cli verify-sources $root
```

預期：目的地是一般目錄而非 reparse point，清冊存在，輸出 `MEDIA_SOURCES_VERIFIED total=20`。

- [ ] **Step 2: 可回復地安裝新版 Skill**

執行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File 'scripts/install-media-inventory-skill.ps1' `
  -Destination 'C:\Users\pony6832\.codex\skills\media-inventory' `
  -ProjectRoot (Get-Location).Path
```

預期：輸出 `MEDIA_INVENTORY_SKILL_READY`，`backup=` 指向新的時間戳備份；備份中的 `SKILL.md` 仍存在。不得刪除任何既有備份。

- [ ] **Step 3: 以正式 launcher 重新輸出清冊**

執行：

```powershell
$root = 'D:\【AIGC-ALL】\2026馬年賀卡AI'
powershell -NoProfile -ExecutionPolicy Bypass `
  -File 'C:\Users\pony6832\.codex\skills\media-inventory\scripts\run_media_catalog.ps1' `
  -RootPath $root
```

預期：輸出 `MEDIA_CATALOG_READY added=0 existing=20 skipped=0 total=20`，並重新產生正式 `媒體清冊.xlsx`。這一步依規格允許把目前 Excel 人工狀態重建為 SQLite 狀態。

- [ ] **Step 4: 驗證正式活頁簿下拉選單與資料完整性**

執行：

```powershell
$root = 'D:\【AIGC-ALL】\2026馬年賀卡AI'
$workbook = Join-Path $root '媒體整理成果\媒體清冊.xlsx'
$python = 'C:\Users\pony6832\.codex\skills\media-inventory\.runtime\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
$checkScript = @'
from openpyxl import load_workbook
import sys

workbook = load_workbook(sys.argv[1])
sheet = workbook["媒體清冊"]
validations = list(sheet.data_validations.dataValidation)
valid = (
    len(validations) == 1
    and validations[0].type == "list"
    and validations[0].formula1 == '"待確認,已審核"'
    and str(validations[0].sqref) == "A2:A21"
    and sheet.max_row == 21
)
print(
    "MEDIA_REVIEW_DROPDOWN_READY"
    f" rows={sheet.max_row - 1}"
    f" range={validations[0].sqref if validations else None}"
    f" options={validations[0].formula1 if validations else None}"
)
workbook.close()
raise SystemExit(0 if valid else 1)
'@
& $python -c $checkScript $workbook
& $python -m media_catalog.cli verify-sources $root
```

預期：輸出 `MEDIA_REVIEW_DROPDOWN_READY rows=20 range=A2:A21 options="待確認,已審核"` 與 `MEDIA_SOURCES_VERIFIED total=20`。

- [ ] **Step 5: 執行最後回歸並確認沒有未提交變更**

執行：

```powershell
$env:PYTHONPATH='src;C:\Users\pony6832\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages'
$env:PYTHONUTF8='1'
python -m pytest -q
python 'C:\Users\pony6832\.codex\skills\.system\skill-creator\scripts\quick_validate.py' 'skills/media-inventory'
git diff --check
git status --short --branch
```

預期：完整測試零失敗、Skill 有效、Git 無 whitespace error，`master` 沒有未提交變更。若部署驗證暴露缺陷，先新增會失敗的回歸測試，再修正並以 `fix: correct reviewed status dropdown` 提交；不得直接修改正式檔案來繞過產品程式。

## Plan Self-Review

- Spec coverage: Task 1 實作狀態欄下拉選單與空清冊安全行為；Task 2 可回復安裝並更新、驗證正式 20 筆清冊。
- Scope: 沒有 SQLite 審核狀態、監看、匯入、VBA、額外欄位或來源媒體操作。
- Type consistency: 全程沿用 `write_excel(records: Iterable[MediaRecord], output_path: Path) -> Path` 與 openpyxl `DataValidation`；測試與正式驗證使用相同 formula 與範圍規則。
- Failure behavior: RED 測試必須先證明目前缺少 validation；空清冊、活頁簿可重開、完整回歸及來源不可變性均有明確驗證指令。
