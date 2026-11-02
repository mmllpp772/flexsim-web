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
| `start.bat` | 正式上線用：雙擊啟動，不用開 VS Code（可設開機自動執行） |
| `results/` | 每次模擬結果自動存成 JSON（自動產生，不會上傳 GitHub） |
| `server.log` | 執行紀錄，出問題先看這裡（自動產生，不會上傳 GitHub） |

## 事前準備

- **Python 3.12**：python.org 下載，安裝第一個畫面要勾「Add python.exe to PATH」
- **VS Code**，並安裝 Python 擴充套件（Extensions 搜尋 `Python`，Microsoft 出的那個）

## 下載專案

**方法 A：VS Code 直接 Clone**（不用登入 GitHub）
1. VS Code 按 `Ctrl + Shift + P` → 輸入 `Git: Clone`
2. 貼上 `https://github.com/mmllpp772/flexsim-web`
3. 選擇要放的資料夾 → 跳出提示時按 **Open**

> 如果出現找不到 Git，代表電腦沒裝 Git，改用方法 B。

**方法 B：下載 ZIP**
1. 打開 https://github.com/mmllpp772/flexsim-web → 綠色 **Code** 按鈕 → **Download ZIP**
2. 解壓縮 → VS Code 選 **File → Open Folder** 開啟 `flexsim-web` 資料夾

## 第一步：用 VS Code 以 Demo 模式啟動（5 分鐘）

1. 在 VS Code 按 `` Ctrl + ` ``（Esc 下面那個鍵）開啟下方終端機，它已經在專案資料夾裡
2. 安裝套件（只要做一次）：
   ```
   pip install -r requirements.txt
   ```
3. 啟動伺服器：
   ```
   python -m uvicorn server:app --host 127.0.0.1 --port 8000 --reload
   ```
   看到 `Uvicorn running on http://127.0.0.1:8000` 就成功了。終端機要一直開著，按 `Ctrl + C` 停止

   > 用 `127.0.0.1` 只有自己這台電腦連得到，所以 **Windows 防火牆不會跳出詢問**。開發、測試時都用這個。
   > 要讓美國同事連進來時，才改成 `--host 0.0.0.0`（見「第三步」）。
4. 瀏覽器開 `http://localhost:8000`，按 **Run simulation**，幾秒後會看到假資料的指標

`config.json` 裡 `"mock_mode": true` 時完全不會碰 FlexSim，網頁上方會有黃色 Demo 提示。

### 改檔案後要做什麼

| 改了哪個檔 | 怎麼生效 |
|---|---|
| `server.py` | 存檔即可，`--reload` 會自動重啟 |
| `index.html` | 瀏覽器按 F5 重新整理 |
| `config.json` | 終端機按 `Ctrl + C`，再按 `↑` 鍵叫回啟動指令，Enter 重新執行 |

### 終端機常見錯誤

| 訊息 | 解法 |
|---|---|
| 紅字「無法載入…因為這個系統上已停用指令碼執行」 | 終端機右上角 `+` 旁的下拉選單 → 改選 **Command Prompt**，指令相同 |
| `'python' 不是內部或外部命令` | 安裝 Python 時沒勾 Add to PATH，重新安裝並勾選，再重開 VS Code |
| `'pip' 不是內部或外部命令` | 改用 `python -m pip install -r requirements.txt` |
| `address already in use` | 8000 被占用，指令裡的 `8000` 改成 `8080`，網址也跟著改 |
| `No module named 'fastapi'` | 還沒執行第 2 步的 pip install |

## 第二步：接上 FlexSim

### 1. 安裝 FlexSimPy
FlexSimPy 是 FlexSim 官方的 Python 控制模組，要自己用 Visual Studio 編譯成 `.pyd`：
- 原始碼：https://github.com/flexsim/FlexSimPy
- 依 README 選對應 Python 版本的組態編譯，把產生的 `FlexSimPy.pyd` 放到 Python 找得到的地方（例如這個資料夾）
- 測試：在 VS Code 終端機輸入 `python -c "import FlexSimPy"`，沒有錯誤訊息就成功

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

### 4. 在 VS Code 終端機按 `Ctrl + C` 再重新啟動，按一次 Run 確認數字和你在 FlexSim 手動跑的一致

## 第三步：開給美國同事用（找 IT）

要讓別台電腦連進來，啟動指令要改成 `--host 0.0.0.0`（`start.bat` 已經是這樣設定）：
```
python -m uvicorn server:app --host 0.0.0.0 --port 8000
```
這時 Windows 會跳出「Windows 安全性警示 / 防火牆」詢問，**這是正常的**，因為程式要接收來自其他電腦的連線。
- 公司電腦通常需要系統管理員權限才能按「允許」。按「取消」的話，伺服器還是會啟動，但其他電腦連不進來
- 最好的做法是請 IT 加一條固定的防火牆規則，之後就不會再跳：允許 **TCP 8000 輸入連線**（或允許 `python.exe`），網路類型選「網域」，來源限定美國辦公室網段

- 防火牆開放 TCP **8000** 輸入連線（建議只允許美國辦公室網段）
- 桌機給固定 IP 或 DNS 名稱，例如 `sim-pc01.company.local`
- 確認台灣 ↔ 美國網段互通（請美國同事開 `http://sim-pc01.company.local:8000/api/status`，看到 `{"ok":true...}` 就通了）
- 電源設定：不睡眠、Windows Update 不在美國上班時間（台灣晚上）自動重開
- 正式上線後不需要一直開著 VS Code：雙擊 `start.bat` 也能啟動；想開機自動啟動，用「工作排程器」在登入時執行 `start.bat`

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
| 網頁顯示 Cannot reach the simulation server | VS Code 終端機（或 `start.bat`）的伺服器有沒有在跑、防火牆、網址 |
| 狀態一直 Failed | 看 `server.log` 最後的錯誤訊息 |
| 某個指標顯示「—」 | 該指標的 FlexScript 回傳錯誤，到 Script Console 測試 |
| 參數改了但結果沒變 | 參數名稱是否和 Model Parameters 完全一致；必要時在 config 該參數加 `"set_script"` 自訂寫法 |

## 授權提醒
FlexSim 授權屬於公司，公司內部同仁透過內網使用一般沒問題；若使用者是外部客戶（即使用公司網域帳號），請先向代理商確認授權條款。
