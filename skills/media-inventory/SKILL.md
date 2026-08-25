---
name: media-inventory
description: Use when the user 貼上單一本機資料夾路徑，或要求整理本機照片、影片、建立媒體清冊、媒體整理成果、預覽描述、亮點或關鍵字。
---

# 媒體資料夾整理

## 核心原則

只處理使用者明確指定的單一根目錄。來源媒體保持原位且不可修改；所有清冊與暫存結果只寫入根目錄下的 `媒體整理成果`。

## 只建立或更新清冊

收到「整理這個資料夾：<path>」或只要求媒體清冊時：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-root>\scripts\run_media_catalog.ps1" -RootPath '<path>'
```

將 `MEDIA_CATALOG_READY` 視為完成，並回傳 `catalog=` 指向的 Excel 清冊。若出現 `MEDIA_CATALOG_ERROR`，回報錯誤且不要改用其他根目錄。

## 建立清冊並分析全部待處理媒體

收到「整理並分析這個資料夾：<path>」、要求預覽描述、亮點或關鍵字時：

1. 先執行上方清冊命令，確認 `MEDIA_CATALOG_READY`。
2. 執行：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-root>\scripts\run_media_analysis.ps1" -RootPath '<path>'
   ```

3. launcher 會自動開啟 Media Catalog A+ 單視窗並開始分析，不需要再選資料夾或按開始。
4. 綠燈代表 worker 在 15 秒內有心跳；紅燈會顯示未執行、心跳逾時、正在恢復或等待 Excel 關閉。
5. UI 顯示影片數、影像數、總容量、完成／總數、未完成、目前媒體／片段與該影片 Gemini 強化使用量。
6. `安全停止` 只寫入 SQLite stop request；worker 完成目前片段與 checkpoint 後才退出。關閉視窗時也要使用「安全停止後關閉」，不強制終止正在寫入的 worker。
7. 分析器取得單一批次鎖後執行 Ollama、FFmpeg、Watch／MCP 預檢，並從 SQLite 已完成片段繼續。每支影片最多以 Gemini 3.7 Flash 強化 12 片段／36 張影格，只會傳送縮放預覽與最小分析證據。
8. 只有 `MEDIA_ANALYSIS_READY ... remaining=0 excel_sync_pending=false` 才代表完成。`MEDIA_ANALYSIS_INCOMPLETE` 表示仍有失敗、空白辨識列或 Excel 尚未同步，修正後重新執行同一 launcher 即可繼續。

停在 SQLite 與 Excel 預覽清冊。不要繼續寫入媒體 metadata、Markdown、備份或搜尋索引。

## 常見錯誤

- 路徑不存在或指向磁碟根目錄：回報固定錯誤，不建立替代資料夾。
- `MEDIA_ANALYSIS_ERROR`：回報環境、來源驗證、Excel 鎖定或重複批次錯誤；不要繞過 Ollama、FFmpeg、Watch 或版本檢查。
- 同一根目錄不可同時執行兩個分析程序。異常中斷後直接重新執行分析 launcher，它會在取得批次鎖後安全恢復。
- Excel 正開啟時分析會繼續寫入 SQLite，UI 顯示「等待 Excel 關閉」；關閉 Excel 後再按「開始／繼續」。
- Gemini 只從私人環境變數 `GEMINI_API_KEY` 讀取。更換或撤銷金鑰後重新啟動 UI；不要把金鑰寫入指令、Skill、Excel 或專案檔案。

## 換電腦使用

不要直接複製已安裝 Skill 裡的 `.runtime` 或 `.tools`。Python 虛擬環境與 Node 工具包含電腦專屬路徑；每台新電腦都必須從專案原始碼重新執行安裝器。分析前需有 Python 3.11+、Node.js 18+、FFmpeg、Ollama 與 `Qwen3-vl:8b-instruct` 模型。
