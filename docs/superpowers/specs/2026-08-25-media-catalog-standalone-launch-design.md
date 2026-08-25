# Media Catalog A+ 桌面啟動與手動資料夾選擇設計

**日期：** 2026-08-25
**狀態：** 設計已口頭批准，待書面規格確認
**適用版本：** Media Catalog A+ Stable v1.2（`codex/legacy-a-plus-v1.2`）

## 1. 目標

在不改變現有 Codex 自然語言指令的前提下，新增可離開 Codex 獨立使用的桌面入口。使用者雙擊 Windows 桌面圖示後，可在 A+ UI 內使用 Windows 原生資料夾選擇器，選取單一媒體根目錄，接著自動建立或更新清冊並開始分析。

本功能不變更 A+ 的版本隔離、來源安全、SQLite checkpoint、Excel 審核狀態、Gemini 用量上限與單一 worker 鎖定契約。

## 2. 使用入口

### 2.1 Codex 入口

使用者輸入：

```text
整理並分析這個資料夾：D:\你的媒體資料夾
```

Skill 先依現行流程建立或更新清冊，再以 `--root` 啟動 A+ UI。UI 直接顯示該路徑並開始／繼續分析，不重複要求使用者選擇資料夾。

### 2.2 桌面入口

安裝器在目前 Windows 使用者的 Desktop 建立或更新：

```text
Media Catalog A+ Stable.lnk
```

雙擊後直接開啟 A+ UI，不顯示 PowerShell 或 Python 終端視窗。UI 初始狀態不啟動 worker，顯示「尚未選擇資料夾」與「選擇資料夾」按鈕。

## 3. UI 交互

### 3.1 未選擇資料夾

- 狀態燈為紅燈，文字為「尚未選擇資料夾」。
- 路徑欄顯示「尚未選擇」。
- 媒體統計與進度顯示 0。
- 「選擇資料夾」可用。
- 「開始／繼續」、「安全停止」、「開啟 Excel」與「開啟成果資料夾」在無有效根目錄時停用。

### 3.2 選擇資料夾

按下「選擇資料夾」時使用 `tkinter.filedialog.askdirectory(mustexist=True)` 開啟 Windows 原生選擇器。

- 按「取消」：回到原狀態，不建立 `媒體整理成果`、SQLite 或 Excel。
- 選定路徑：使用 `MediaWorkspace.from_root()` 驗證。不存在、非資料夾、磁碟根目錄、`媒體整理成果`、符號連結或 reparse point 一律拒絕，並以 UI 訊息框顯示可行動錯誤。
- 驗證通過：路徑欄立即顯示選定路徑，狀態變為「正在建立／更新清冊」。

### 3.3 非阻塞掃描與自動開始

UI 不直接執行掃描、快速總和、FFmpeg、Ollama 或 Gemini。`WorkerSupervisor` 新增「cataloging」階段，以獨立子程序執行：

```text
python -m media_catalog.cli start <root>
```

UI 每秒 poll 該程序：

1. `start` 成功後開啟或升級 A+ SQLite state。
2. 讀取影片數、影像數、總容量與待處理數。
3. 以同一根目錄啟動現有 `analyze-all` worker。
4. UI 轉入現有心跳、恢復、Excel 鎖定與 checkpoint 顯示流程。

掃描失敗時不啟動分析 worker，UI 顯示紅燈與經清理的錯誤原因。

### 3.4 切換資料夾

當 worker 執行中時，「選擇資料夾」停用，避免同一 UI 同時管理兩個根目錄。使用者需先按「安全停止」，等 worker 完成目前 checkpoint 並退出後，才能重新選擇。

## 4. Supervisor 狀態

`SupervisorSnapshot` 擴充為可在 SQLite run 建立前表示狀態。桌面模式狀態順序為：

```text
idle
  -> cataloging
  -> starting
  -> running
  -> completed | stopped | error
```

現有 `restarting`、`stopping_stale_worker`、Excel sync pending 與單次自動恢復行為保留。`cataloging` 進程死亡時不自動進入分析；使用者可在錯誤狀態下重新按「開始／繼續」。

## 5. Launcher 契約

`media_catalog.status_ui` 的 `--root` 改為選用：

- 有 `--root`：Codex 模式，依現行契約自動開始。
- 無 `--root`：桌面模式，開啟無路徑 UI 並等待手動選擇。

`run_media_analysis_ui.ps1` 的 `RootPath` 同樣改為選用。它使用 Skill 私有 `.runtime\Scripts\pythonw.exe` 啟動 Tkinter，不將 API Key、根目錄或其他私人資料寫入桌面捷徑。若 `pythonw.exe` 不存在，launcher 以固定錯誤結束，不改用系統 Python。

## 6. 桌面捷徑

安裝器使用 `WScript.Shell.CreateShortcut()` 建立 `.lnk`：

- `TargetPath`：Windows PowerShell `powershell.exe`。
- `Arguments`：`-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "<installed-skill>\scripts\run_media_analysis_ui.ps1"`。
- `WorkingDirectory`：已安裝 Skill 根目錄。
- `IconLocation`：Windows `shell32.dll` 中的系統資料夾／媒體圖示，不新增來源不明的圖片。
- `Description`：`Media Catalog A+ Stable 媒體整理與分析`。

桌面路徑以 `[Environment]::GetFolderPath('Desktop')` 取得，不假設 Desktop 英文名稱或 OneDrive 位置。安裝器重複執行時原地更新同名捷徑，不建立多份。捷徑不包含 Gemini Key、模型名稱、媒體根目錄或其他 credential。

## 7. 錯誤處理

- 無 Tkinter 或 `pythonw.exe`：安裝器失敗，不建立無法使用的捷徑。
- 桌面路徑不可用：安裝器回報錯誤且保留已安裝 Skill 與舊版備份，不對其他目錄建立捷徑。
- UI 在無終端模式遇到啟動錯誤：使用 `messagebox.showerror()` 顯示經清理訊息，不只寫入 stderr。
- 路徑含空白、中文或 CP950 不支援字元：全程使用參數陣列或 `.lnk` 結構化欄位，不拼接 shell 指令字串。

## 8. 安全與資料範圍

- 只掃描使用者在當次 UI 選定的單一根目錄。
- 來源媒體保持原位，不移動、不改名、不修改 metadata。
- 成果仍只寫入 `<root>\媒體整理成果`。
- 排除成果目錄、符號連結與 reparse point。
- Gemini 仍只從私人環境變數讀取 Key，模型契約保持 `gemini-3.7-flash`。
- 桌面捷徑、日誌、SQLite、Excel、錯誤視窗與 Git 不得出現 API Key。
- 人工審核的「已審核」狀態在重建 Excel 後保留。

## 9. 測試與驗收標準

1. `status_ui.main([])` 可在無 `--root` 時建立 UI，不啟動 worker。
2. 取消資料夾選擇時不建立成果目錄或程序。
3. 有效路徑以非阻塞 `cataloging -> starting -> running` 順序執行。
4. 無效路徑不啟動 catalog 或 analysis worker，UI 顯示原因。
5. worker 執行時不能切換資料夾；安全停止後可重新選擇。
6. 安裝器在實際使用者 Desktop 建立唯一同名 `.lnk`，重跑後數量仍為 1。
7. `.lnk` 的 TargetPath、Arguments、WorkingDirectory、IconLocation 與 Description 符合本規格，且不含 API Key 或媒體路徑。
8. 雙擊捷徑後只顯示 A+ UI，不顯示黑色終端視窗。
9. 手動選定包含中文與特殊字元的路徑可建立清冊並開始分析。
10. 一般 `pytest` 不呼叫 Gemini；現有 live test 仍只在明確 opt-in 時執行。
11. 完整離線測試、PowerShell 語法、Tkinter 匯入、密鑰掃描與安裝後 UI 實機啟動均通過。
12. UI 關閉後 A+ UI／worker 程序數為 0，分析鎖可重新取得。

## 10. 不在本次範圍

- 打包獨立 EXE、MSIX 或 Windows Installer。
- 開機自動執行、常駐背景服務或系統匣圖示。
- 一個 UI 同時管理多個根目錄。
- 記憶最近路徑、自動重開上次資料夾或開機後自動繼續。
- 自訂應用圖示、簽署可執行檔或雲端安裝程式。
