# Media Catalog A+ Stable 使用與恢復指南

## 啟動方式一：Codex 貼上路徑

在 Codex 對話輸入：

```text
整理並分析這個資料夾：D:\你的媒體資料夾
```

Skill 會先掃描指定的單一根目錄，將 SQLite、Excel 與工作暫存放在該根目錄下的 `媒體整理成果`，再開啟 A+ 狀態視窗。新清冊就緒後由使用者選擇普通辨識或強制 Gemini 強化。來源照片與影片不會被移動、改名或修改 metadata。

## 啟動方式二：Windows 桌面捷徑

安裝完成後，雙擊目前使用者桌面的 `Media Catalog A+ Stable`。這個入口不需要開啟 Codex，也不會顯示黑色 PowerShell／Python 終端。

1. 初始紅燈「尚未選擇資料夾」是等待狀態，不是錯誤。
2. 按「選擇資料夾」，使用 Windows 原生選擇器指定單一媒體根目錄。
3. UI 顯示「正在建立／更新清冊」並在背景執行掃描。
4. 顯示「清冊就緒，請選擇分析模式」後，普通辨識按「開始／繼續」；指定加強按橘色「強制 Gemini 強化」。
5. 強制模式先確認未審核數量、正常請求上限與含重試的最壞上限；按確認後才開始。
6. 按取消不會建立成果目錄、SQLite、Excel 或分析程序。

清冊建立或分析執行中，「選擇資料夾」會停用。若要切換根目錄，先按「安全停止」並等待程序退出。桌面捷徑固定命名為 `Media Catalog A+ Stable.lnk`，重複執行安裝器會原地更新，不建立多份編號副本。

## 狀態視窗

- 綠燈 `執行中`：worker 在 15 秒內更新過 SQLite 心跳。
- 紅燈：尚未開始、已安全停止、worker 不存在、心跳逾時、正在重新啟動或等待 Excel 關閉。
- 數字：影片數、影像數、總容量、完成／總數、未完成、目前媒體／片段與該影片的 Gemini 強化用量。
- `開始／繼續`：從 SQLite checkpoint 繼續，不重跑已完成片段。
- `強制 Gemini 強化`：略過 Excel「已審核」列，對其餘照片與影片執行本地分析後再指定使用 Gemini 3.7 Flash 加強。
- `安全停止`：只設定 stop request，等目前片段寫入後退出。
- `開啟 Excel`／`開啟成果資料夾`：由 Windows 預設程式開啟成果。Excel `完整路徑` 欄可點擊回到原始媒體。

關閉視窗時選擇「安全停止後關閉」。UI 會等 worker 完成目前 checkpoint 與釋放分析鎖，不使用廣泛 `taskkill` 或強制終止程序樹。

## 長影片與 Gemini

普通模式下，影片先依場景切分，單一長場景最長 300 秒；每片段選 1～3 張代表影格給本地 Qwen3-VL 8B。只在本地結果資訊不足時，系統才使用 Gemini 3.7 Flash。

強制模式仍先完成全部本地分段分析，再依「問題片段優先、時間分散」挑選；每支影片最多 12 片段，每片段 1～3 張縮放預覽。照片每張只傳一張縮放預覽。兩種模式都不上傳完整影片、原尺寸照片或完整本機路徑。Gemini 失敗會重試一次，第二次仍失敗則保留本地結果，在 Excel「錯誤原因」顯示「Gemini 強化失敗」，並繼續下一個媒體。

金鑰只由私人環境變數讀取：

```powershell
[Environment]::SetEnvironmentVariable('GEMINI_API_KEY', '<your-private-key>', 'User')
[Environment]::SetEnvironmentVariable('GEMINI_MODEL', 'gemini-3.7-flash', 'User')
```

設定後重新開啟 Codex 與 A+ UI。未設定 `GEMINI_MODEL` 時預設使用 `gemini-3.7-flash`；若強制模式偵測到其他模型名稱，會拒絕啟動。若金鑰曾貼在對話、終端、文件或原始碼，應在 Google AI Studio 撤銷與輪替，再私下更新環境變數。不要把金鑰寫入 Skill、捷徑、程序參數、Excel、SQLite 或 Git。

## 中斷、Excel 鎖定與恢復

- worker 意外結束或心跳超過 15 秒時，Supervisor 會先請求安全停止，等待 10 秒後最多自動重新啟動一次。
- 同一片段連續兩次導致 worker 結束時，該片段標記失敗，避免無限重試。
- 關機或 UI 完全關閉後不會常駐啟動。再次輸入同一句指令即可繼續。
- Excel 開啟導致檔案鎖定時，分析繼續寫入 SQLite，UI 顯示「等待 Excel 關閉」。關閉 Excel 後按「開始／繼續」，工作簿會從 SQLite 重建。
- 強制模式安全停止後，重新開啟 UI 並再次選擇「強制 Gemini 強化」即可從同一模式的 checkpoint 續跑；不要改按普通模式混用同一個未完成批次。
- 只有 `failed=0 remaining=0 excel_sync_pending=false` 才會輸出 `MEDIA_ANALYSIS_READY`。

## 新電腦安裝

每台電腦必須重建私有 runtime，不要複製其他電腦的 `.runtime`、`.tools`、瀏覽器登入或金鑰檔。

1. 安裝 Python 3.11+、Node.js 18+、FFmpeg、Ollama 與 `Qwen3-vl:8b-instruct`。
2. 確認 Python 包含 Tkinter（官方 Windows Python 安裝程式預設包含）。
3. 在專案根目錄執行：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-media-inventory-skill.ps1
   ```

4. 安裝器會重建 Skill 專屬 Python runtime、固定 MCP Video Analyzer 0.8.0，並執行 Tkinter／headless 清冊冒煙測試。
5. 安裝器確認私有 runtime 含有 `pythonw.exe` 後，建立或更新桌面捷徑；成功輸出同時包含 `MEDIA_CATALOG_SHORTCUT_READY` 與 `MEDIA_INVENTORY_SKILL_READY`。

若看到 `MEDIA_INVENTORY_SKILL_ERROR`，依訊息修正後重跑安裝器。私有 runtime 缺少 `pythonw.exe` 時不要改用系統 Python，應重新安裝。若桌面位置不存在或不可寫，安裝器會停止，不會把捷徑改放到其他目錄；已安裝 Skill 與 timestamped 舊版備份會保留供檢查。
