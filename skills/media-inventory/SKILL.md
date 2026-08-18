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

3. 分析器會先取得該清冊的單一批次鎖，再執行 Ollama、FFmpeg、Watch／MCP 預檢。預檢通過後，會自動恢復上次中斷的 `processing`、舊版 `skipped`、先前的 `failed`，以及舊版狀態已完成但描述／重點／關鍵字不完整的項目。
4. 執行期間會持續輸出 `MEDIA_ANALYSIS_PROGRESS completed=<n> total=<n> ...`；這是正常進度，不是完成標記。
5. 批次結束前會從 SQLite 原子式重建 Excel；只有 `MEDIA_ANALYSIS_READY ... remaining=0` 才代表每筆資料的描述、重點與關鍵字皆非空。回傳 `catalog=` 指向的 Excel，說明 `analyzed`、`failed`、`skipped`、`recovered_incomplete`、`recovered_processing` 與 `retried_failed` 數量。
6. `MEDIA_ANALYSIS_INCOMPLETE` 表示仍有失敗或空白辨識列，不得稱為完成。先讀取 Excel 的「錯誤原因」，修正本機環境或關閉鎖住的 Excel，再重新執行同一 launcher；下一次會自動重試。

停在 SQLite 與 Excel 預覽清冊。不要繼續寫入媒體 metadata、Markdown、備份或搜尋索引。

## 常見錯誤

- 路徑不存在或指向磁碟根目錄：回報固定錯誤，不建立替代資料夾。
- `MEDIA_ANALYSIS_ERROR`：回報環境、來源驗證、Excel 鎖定或重複批次錯誤；不要繞過 Ollama、FFmpeg、Watch 或版本檢查。
- 同一根目錄不可同時執行兩個分析程序。異常中斷後直接重新執行分析 launcher，它會在取得批次鎖後安全恢復。

## 換電腦使用

不要直接複製已安裝 Skill 裡的 `.runtime` 或 `.tools`。Python 虛擬環境與 Node 工具包含電腦專屬路徑；每台新電腦都必須從專案原始碼重新執行安裝器。分析前需有 Python 3.11+、Node.js 18+、FFmpeg、Ollama 與 `Qwen3-vl:8b-instruct` 模型。
