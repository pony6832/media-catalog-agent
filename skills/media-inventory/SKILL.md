---
name: media-inventory
description: Use when the user 貼上單一本機資料夾路徑，或要求整理本機照片、影片、建立媒體清冊、媒體整理成果。
---

# 媒體整理與搜尋

## 工作流程

接受恰好一個已存在的本機資料夾路徑。路徑可以單獨貼上，也可以出現在「整理這個資料夾：`<path>`」中。

1. 從使用者訊息取出完整路徑，不要猜測或改用其他路徑。
2. 執行：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-root>\scripts\run_media_catalog.ps1" -RootPath '<path>'
   ```

3. 收到 `MEDIA_CATALOG_READY` 時，回報新增、既有、略過及總數，並提供該行 `catalog=` 後方 Excel 的可點擊絕對路徑。成果固定放在來源根目錄下的 `媒體整理成果`。
4. 收到 `MEDIA_CATALOG_ERROR` 時，原樣說明錯誤並停止；不要嘗試替代路徑。

這個入口只建立或更新待處理清冊。不要自動分析待處理項目，也不要修改或移動原始媒體。
