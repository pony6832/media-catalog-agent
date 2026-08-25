# Media Catalog Agent

本專案會掃描指定的本機照片／影片資料夾，將 SQLite、Excel 清冊與分析暫存集中寫入來源根目錄下的 `媒體整理成果`。來源媒體保持原位，不移動、不改名、不修改 metadata。

## 換電腦安裝

不要複製舊電腦中已安裝 Skill 的 `.runtime` 或 `.tools`。Python 虛擬環境和 Node 工具含有電腦專屬路徑，必須在每一台電腦重新安裝。

新電腦需先準備：

- Python 3.11 以上
- Node.js 18 以上與 `npm.cmd`
- FFmpeg（`ffmpeg -version` 可執行）
- Ollama 與本機模型 `Qwen3-vl:8b-instruct`
- Codex 的 Watch Skill；若 Watch 不可用，安裝器提供的 MCP Video Analyzer 0.8.0 會作為影片備援

在專案根目錄執行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-media-inventory-skill.ps1
```

安裝器會在目前 Windows 使用者的桌面建立或更新唯一一份 `Media Catalog A+ Stable` 捷徑。捷徑只包含已安裝 Skill 的啟動位置，不包含 Gemini Key、模型名稱或媒體路徑。

## 使用方式

### 在 Codex 貼上路徑

可直接在對話輸入：

```text
整理並分析這個資料夾：D:\你的媒體資料夾
```

指令會先建立或更新清冊，再開啟 Media Catalog A+ 狀態視窗。新清冊完成後會顯示「清冊就緒，請選擇分析模式」；普通辨識按「開始／繼續」，指定資料夾需要雲端加強時按「強制 Gemini 強化」。綠燈代表 15 秒內有 worker 心跳；紅燈會顯示未執行、心跳逾時、正在重新啟動或等待 Excel 關閉。詳細說明見 [`docs/media-catalog-a-plus-setup.md`](docs/media-catalog-a-plus-setup.md)。

### 不開 Codex，從桌面啟動

1. 雙擊桌面的 `Media Catalog A+ Stable`。
2. 在紅燈顯示「尚未選擇資料夾」時按「選擇資料夾」。
3. 選定單一媒體根目錄；UI 會先顯示「正在建立／更新清冊」。
4. 顯示「清冊就緒，請選擇分析模式」後，普通辨識按「開始／繼續」；需要指定加強時按橘色「強制 Gemini 強化」。
5. 強制模式會先列出未審核照片／影片數、正常請求上限與含重試上限；確認後才開始。

取消資料夾選擇不會建立 `媒體整理成果`。分析或清冊掃描進行中不能切換根目錄；先按「安全停止」，等程序退出後才能重新選擇。桌面啟動只顯示 A+ UI，不會顯示 PowerShell 或 Python 黑色終端視窗。

### 普通辨識與強制 Gemini 強化

- 「開始／繼續」維持本地優先，只在本地結果資訊不足時自動使用 Gemini。
- 「強制 Gemini 強化」會重新分析未審核項目；Excel 已標成「已審核」的列不會重跑。
- 照片每張只傳一張縮放預覽；影片每支最多選 12 個片段，每段只傳 1～3 張縮圖，不上傳完整影片或完整本機路徑。
- Gemini 每次失敗會重試一次；仍失敗時保留本地結果，Excel 顯示「Gemini 強化失敗」供人工確認，不中斷整批。
- API Key 只從私人環境變數讀取，不寫入 Git、Skill、捷徑、Excel、SQLite 或程序參數。

## 如何判讀執行結果

- `MEDIA_ANALYSIS_PROGRESS`：逐項進度，程序仍在執行。
- `MEDIA_ANALYSIS_READY ... remaining=0`：所有清冊項目都有分析結果。
- `MEDIA_ANALYSIS_INCOMPLETE`：仍有失敗或空白辨識列；查看 Excel 的「錯誤原因」，修正後重新執行即可自動重試。
- `MEDIA_ANALYSIS_ERROR`：環境預檢、來源完整性、Excel 鎖定或重複執行失敗。

所有會修改 SQLite／Excel 的命令都會鎖定單一清冊，避免兩個程序同時寫入。若上次異常中斷，重新執行會自動恢復 `processing`、`skipped`、重試 `failed`，並修復舊版狀態已完成但描述／重點／關鍵字不完整的項目。批次結束前會從 SQLite 原子式重建 Excel，因此前一次中止留下的半成品會在重跑時補齊。
