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

3. 將 `MEDIA_ANALYSIS_READY` 視為整批已跑完；`failed=<n>` 是清冊中逐項保留的失敗數，不可隱藏。只有 `remaining=0` 才可稱為批次完成。
4. 回傳 `catalog=` 指向的 Excel，說明 `analyzed`、`failed`、`skipped` 數量。成果仍是待人工確認的預覽資料。

停在 SQLite 與 Excel 預覽清冊。不要繼續寫入媒體 metadata、Markdown、備份或搜尋索引。

## 常見錯誤

- 路徑不存在或指向磁碟根目錄：回報固定錯誤，不建立替代資料夾。
- `MEDIA_ANALYSIS_ERROR`：回報環境或來源驗證錯誤；不要繞過 Ollama、Watch 或版本檢查。
- 中斷後存在 processing 項目：先用私有 runtime 執行 `media_catalog.cli resume-processing <path>`，再重新執行分析 launcher。
