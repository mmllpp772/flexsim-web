# FlexSim 網頁模擬執行器

FlexSim 跑在你的桌機上，美國同事用瀏覽器打開 `http://你的電腦名稱:8000`，輸入參數 → 按 Run → 看指標。

```
美國同事的瀏覽器  ──公司內網──>  你的桌機 (server.py)  ──FlexSimPy──>  FlexSim
```

## 檔案

| 檔案 | 用途 |
|---|---|
| `config.json` | **唯一要改的檔案**：模型路徑、開放的參數、要讀的指標 |
| `server.py` | 後端：排隊、呼叫 FlexSim、回傳結果 |
| `index.html` | 美國同事看到的網頁（英文介面） |
| `start.bat` | 雙擊啟動 |
| `results/` | 每次模擬結果自動存成 JSON |
| `server.log` | 執行紀錄，出問題先看這裡 |

## 第一步：先用 Demo 模式測網頁（5 分鐘）

1. 安裝 Python（建議和你要編譯的 FlexSimPy 同版本）
2. 在這個資料夾開命令列：`pip install -r requirements.txt`
3. 雙擊 `start.bat`
4. 瀏覽器開 `http://localhost:8000`，按 Run 應該會看到假資料

`config.json` 裡 `"mock_mode": true` 時完全不會碰 FlexSim，網頁上方會有黃色 Demo 提示。

## 第二步：接上 FlexSim

### 1. 安裝 FlexSimPy
FlexSimPy 是 FlexSim 官方的 Python 控制模組，要自己用 Visual Studio 編譯成 `.pyd`：
- 原始碼：https://github.com/flexsim/FlexSimPy
- 依 README 選對應 Python 版本的組態編譯，把產生的 `FlexSimPy.pyd` 放到 Python 找得到的地方（例如這個資料夾）
- 測試：`python -c "import FlexSimPy"` 沒報錯就成功

### 2. 整理模型
- 要讓客戶調的值，在 FlexSim 裡做成 **Model Parameters**，名稱要和 `config.json` 的 `name` 一模一樣
- 每個指標寫一段回傳數字的 FlexScript，先在 FlexSim 的 **Script Console** 測過
- 比較難直接算的指標（例如利用率），建議在模型的 OnRunStop 把結果寫進 Global Table `WebResults`，再用 `Table("WebResults")[1][1]` 讀出來

### 3. 修改 config.json
```json
"mock_mode": false,
"flexsim": {
  "program_dir": "C:/Program Files/FlexSim 2025/program/",
  "model_path":  "C:/FlexSimModels/MyLine.fsm",
  "launch_kwargs": { "showGUI": false }
}
```
- `launch_kwargs` 會直接傳給 `FlexSimPy.launch()`。不同版本的參數名稱可能不同，若啟動失敗請看 FlexSimPy 原始碼裡 `launch` 接受哪些參數再調整
- 第一次測試可以先設 `"showGUI": true`，看得到 FlexSim 視窗比較好除錯

### 4. 重啟 `start.bat`，按一次 Run 確認數字和你在 FlexSim 手動跑的一致

## 第三步：開給美國同事用（找 IT）

- 防火牆開放 TCP **8000** 輸入連線（建議只允許美國辦公室網段）
- 桌機給固定 IP 或 DNS 名稱，例如 `sim-pc01.company.local`
- 確認台灣 ↔ 美國網段互通（請美國同事開 `http://sim-pc01.company.local:8000/api/status`，看到 `{"ok":true...}` 就通了）
- 電源設定：不睡眠、Windows Update 不在美國上班時間（台灣晚上）自動重開
- 想開機自動啟動：用「工作排程器」在登入時執行 `start.bat`

## 安全

- `config.json` 裡 `"access_key"` 填一組字串，使用者第一次開網頁要輸入，避免公司內其他人亂跑
- 參數都會在後端檢查 min / max，超出範圍會被擋
- 網頁只能改 config 裡列出的參數，不能執行任意 FlexScript

## 運作方式

- 同一時間只跑一個模擬，其他人會排隊，網頁會顯示「position 2 in line」
- FlexSim 只啟動一次並保持開著，每次只做：寫參數 → reset → 全速跑到結束 → 讀指標
- 某次執行出錯時會自動重開 FlexSim，下一次照常
- 單次執行超過 `max_run_seconds`（預設 1800 秒）會視為失敗

## 常見問題

| 狀況 | 檢查 |
|---|---|
| 網頁顯示 Cannot reach the simulation server | `start.bat` 有沒有在跑、防火牆、網址 |
| 狀態一直 Failed | 看 `server.log` 最後的錯誤訊息 |
| 某個指標顯示「—」 | 該指標的 FlexScript 回傳錯誤，到 Script Console 測試 |
| 參數改了但結果沒變 | 參數名稱是否和 Model Parameters 完全一致；必要時在 config 該參數加 `"set_script"` 自訂寫法 |

## 授權提醒
FlexSim 授權屬於公司，公司內部同仁透過內網使用一般沒問題；若使用者是外部客戶（即使用公司網域帳號），請先向代理商確認授權條款。
