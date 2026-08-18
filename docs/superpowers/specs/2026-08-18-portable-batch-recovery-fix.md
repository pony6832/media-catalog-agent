# 跨電腦批次辨識恢復修復

## 問題

跨電腦安裝或長批次中斷後，Excel 可能只有前段項目含描述、重點與關鍵字，後段保持空白。舊流程的 `analyze-all` 只選取 `pending`，會永久跳過殘留的 `processing` 與既有 `failed`；摘要的 `failed` 又只計算本次失敗，`remaining` 不含失敗，因此重跑可能錯誤輸出 `failed=0 remaining=0`。

長批次也只在最後輸出一行，較慢電腦無法分辨仍在運算、已卡住或被宿主中斷。同一清冊亦缺少跨程序互斥保護，若重複啟動可能交錯更新 SQLite 與 Excel。

## 修復行為

1. 所有會修改 SQLite／Excel 的命令先取得清冊專屬的作業系統檔案鎖；第二個程序必須立即拒絕。
2. 在鎖內先完成 Ollama、指定模型與 FFmpeg 預檢，避免環境缺件時改動既有狀態。
3. 預檢成功後，自動將 `processing`、`failed`、舊版 `skipped`，以及描述／重點／關鍵字任一空白的舊版 `analyzed`／`completed` 重新排入；只有三個分析欄皆非空的完成項目才略過。
4. 每個項目寫入 SQLite 並刷新 Excel 後，立即 flush 一行 `MEDIA_ANALYSIS_PROGRESS`。
5. 批次結束前無條件從 SQLite 產生暫存工作簿，再以同目錄原子替換同步正式 Excel；替換失敗時保留原工作簿並回報錯誤。
6. `failed` 代表清冊結束時的失敗總數；`remaining` 代表所有尚未同時具備非空描述、重點與關鍵字的項目。
7. 只有 `remaining=0` 且最終 Excel 同步成功才輸出 `MEDIA_ANALYSIS_READY` 並回傳 0；否則輸出 `MEDIA_ANALYSIS_INCOMPLETE` 並回傳 3，或在 Excel 無法寫入時輸出 `MEDIA_ANALYSIS_ERROR`。
8. Excel 被鎖定仍會停止批次，不宣稱同步完成；關閉 Excel 後重跑即使沒有待處理列，也會從 SQLite 重建並修復 Excel。
9. 安裝器先明確安裝 `setuptools>=68`，再使用它進行非隔離建置（`--no-build-isolation`），以涵蓋 Python 3.12+ 不再保證 venv 內含 setuptools 的情況；同時停用可能損壞或卡住的 pip 網頁快取（`--no-cache-dir`）與非必要 bytecode 編譯。npm 則改用 `.tools` 內的獨立快取，避免另一台電腦的全域 npm cache 權限或損壞問題。

## 可攜性規則

不得把已安裝 Skill 的 `.runtime` 或 `.tools` 當成可攜套件複製到另一台電腦。每台電腦需從原始碼重新執行安裝器，並具備 Python 3.11+、Node.js 18+、FFmpeg、Ollama 與 `Qwen3-vl:8b-instruct`。Watch 不可用時才使用安裝器固定的 MCP Video Analyzer 0.8.0。
