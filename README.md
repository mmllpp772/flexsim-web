# FlexSim 網頁模擬執行器（OneDrive 中轉）

FlexSim 跑在你的桌機上，美國同事在網頁輸入參數 → 按 Run → 看指標。
資料透過 OneDrive 共用資料夾傳遞，**不用開 port、不用找 IT**。

```
美國同事的網頁 ──寫入工作檔──> OneDrive 共用資料夾 <──你的電腦定時去拿、跑完寫回結果
```

- 送出到看到結果，會比直接連線多等約 10 秒～1 分鐘（OneDrive 同步時間）
- 網頁要用 **Edge 或 Chrome** 開啟

## 檔案

| 檔案 | 用途 |
|---|---|
| `config.json` | **唯一要改的檔案**：模型路徑、開放的參數、要讀的指標 |
| `onedrive_worker.py` | 主程式：監看共用資料夾、跑 FlexSim、寫回結果 |
| `runner.py` | 核心：檢查參數、呼叫 FlexSim、讀指標 |
| `index.html` | 網頁（英文介面），啟動時自動複製到共用資料夾成為 `Simulator.html` |
| `start.bat` | 正式上線用：雙擊啟動，不用開 VS Code |
| `worker.log` | 執行紀錄（自動產生），出問題先看這裡 |

## 事前準備

- **Python 3.12**：python.org 下載，安裝第一個畫面要勾「Add python.exe to PATH」
- **VS Code**，並安裝 Python 擴充套件（Extensions 搜尋 `Python`，Microsoft 出的那個）
- 電腦已登入**公司 OneDrive**

不需要 `pip install` 任何套件，只用 Python 內建功能。

## 下載專案

**方法 A：VS Code 直接 Clone**（不用登入 GitHub）
1. VS Code 按 `Ctrl + Shift + P` → 輸入 `Git: Clone`
2. 貼上 `https://github.com/mmllpp772/flexsim-web`
3. 選擇要放的資料夾 → 跳出提示時按 **Open**

> 如果出現找不到 Git，代表電腦沒裝 Git，改用方法 B。

**方法 B：下載 ZIP**
1. 打開 https://github.com/mmllpp772/flexsim-web → 綠色 **Code** 按鈕 → **Download ZIP**
2. 解壓縮 → VS Code 選 **File → Open Folder** 開啟 `flexsim-web` 資料夾

> ⚠️ 專案資料夾**不要**放在 OneDrive 共用資料夾裡面，兩者分開放。

## 第一步：啟動（Demo 模式）

在 VS Code 按 `` Ctrl + ` ``（Esc 下面那個鍵）開啟下方終端機，輸入：
```
python onedrive_worker.py
```
看到 `Watching C:\Users\...\OneDrive - 公司名稱\FlexSimShare` 就成功了。

- 程式會自動在公司 OneDrive 裡建立 `FlexSimShare` 資料夾，**不用填路徑**
- 終端機要一直開著，按 `Ctrl + C` 停止（停止後網頁會顯示 offline）
- `config.json` 裡 `"mock_mode": true` 時完全不會碰 FlexSim，用假資料跑，網頁上方會有黃色 Demo 提示

`FlexSimShare` 裡會自動出現：

| 自動建立的檔案 | 用途 |
|---|---|
| `Simulator.html` | 給美國同事開的網頁（每次啟動自動更新成最新版） |
| `config.json` | 網頁用的公開設定（**不含**模型路徑和 FlexScript） |
| `status.json` | 讓網頁顯示「Simulator online / offline」 |
| `jobs/` | 網頁送來的工作，一個工作一個檔案 |
| `results/` | 模擬結果，一個工作一個檔案 |

### 共用資料夾的位置
`config.json` 裡預設是：
```json
"share_dir": "%OneDriveCommercial%/FlexSimShare"
```
`%OneDriveCommercial%` 是 Windows 內建的設定，會自動指到你的公司 OneDrive（例如 `C:\Users\工號\OneDrive - 公司名稱`），所以換一台電腦也不用改。
想用別的資料夾，再改成完整路徑，例如 `C:/Users/工號/OneDrive - 公司名稱/MyFolder`（斜線用 `/`）。

## 第二步：自己先測試

你的電腦本來就同步著 `FlexSimShare`，所以可以自己先走一次美國同事的流程：
1. 檔案總管 → `FlexSimShare` → 對 `Simulator.html` 按右鍵 → 開啟檔案 → **Microsoft Edge**
2. 按 **Choose shared folder** → 選 `FlexSimShare` 資料夾 → 允許「檢視和編輯」
3. 按 **Run simulation**，狀態會依序顯示 **Sent → Queued → Running → Complete**，然後出現指標

## 第三步：分享給美國同事

**你這邊：**
1. 檔案總管 → 左邊 **OneDrive - 公司名稱** → 對 `FlexSimShare` 按右鍵 → 有雲朵圖示的 **共用**
2. 輸入美國同事的 email，權限選 **可以編輯** → 傳送
3. 建議再對資料夾按右鍵 → **一律保留在此裝置上**

> ⚠️ 不要用資料夾「內容」裡的「共用」或「安全性」分頁，那是 Windows 網路共用和檔案權限，跟 OneDrive 分享無關。

**美國同事那邊（只要設定一次）：**
1. 打開分享連結 → 按 **新增捷徑到我的檔案**（Add shortcut to My files），讓資料夾同步到他的電腦
2. 在他的檔案總管 OneDrive 裡找到 `FlexSimShare`，**用 Edge 或 Chrome 開啟 `Simulator.html`**
3. 第一次會出現 **Choose shared folder** → 選 `FlexSimShare` → 允許「檢視和編輯」
4. 之後就能輸入參數、按 Run。下次開啟時可能要按一次 **Reconnect** 重新授權（瀏覽器的安全規定）

## 第四步：接上 FlexSim

### 1. 安裝 FlexSimPy
FlexSimPy 是 FlexSim 官方的 Python 控制模組，要自己用 Visual Studio 編譯成 `.pyd`：
- 原始碼：https://github.com/flexsim/FlexSimPy
- 依 README 選對應 Python 版本的組態編譯，把產生的 `FlexSimPy.pyd` 放到這個專案資料夾
- 測試：在 VS Code 終端機輸入 `python -c "import FlexSimPy"`，沒有錯誤訊息就成功

### 2. 整理模型
- 要讓美國同事調的值，在 FlexSim 裡做成 **Model Parameters**，名稱要和 `config.json` 的 `name` 一模一樣
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
- 模型檔 (.fsm) **不要**放進 OneDrive 共用資料夾，放在你電腦的其他位置即可

### 4. 重新啟動，按一次 Run 確認數字和你在 FlexSim 手動跑的一致

## 改檔案後要做什麼

| 改了哪個檔 | 怎麼生效 |
|---|---|
| `config.json`、`index.html`、`.py` 檔 | 終端機按 `Ctrl + C`，再按 `↑` 鍵叫回啟動指令，Enter 重新執行 |

改 `index.html` 後重新啟動，共用資料夾裡的 `Simulator.html` 會自動更新，美國同事重新整理網頁即可。

## 正式上線
- 雙擊 `start.bat` 就能啟動，不用開 VS Code
- 想開機自動啟動：用「工作排程器」在登入時執行 `start.bat`
- 電源設定：不睡眠；Windows Update 不要在美國上班時間（台灣晚上）自動重開機

## 運作方式
- 同一時間只跑一個模擬，其他人排隊，網頁會顯示排隊順位
- FlexSim 只啟動一次並保持開著，每次只做：寫參數 → reset → 全速跑到結束 → 讀指標
- 某次執行出錯時會自動重開 FlexSim，下一次照常
- 單次執行超過 `max_run_seconds`（預設 1800 秒）會視為失敗
- 參數只接受 config 裡列出的項目和範圍，工作檔無法執行任意 FlexScript
- 只有被分享資料夾的人能使用
- 你的電腦重開後，會自動接續處理還沒跑完的工作

## 常見問題

| 狀況 | 檢查 |
|---|---|
| `'python' 不是內部或外部命令` | 安裝 Python 時沒勾 Add to PATH，重新安裝並勾選，再重開 VS Code |
| 終端機紅字「已停用指令碼執行」 | 終端機右上角 `+` 旁的下拉選單 → 改選 **Command Prompt** |
| `找不到環境變數 %OneDriveCommercial%` | 這台電腦沒登入公司 OneDrive；登入後重試，或在 `share_dir` 填完整路徑 |
| `找不到共用資料夾` | `config.json` 的 `share_dir` 路徑是否正確、OneDrive 是否已同步 |
| `JSONDecodeError` | `config.json` 格式錯誤：路徑的 `\` 要改 `/`，或少了逗號、引號 |
| 網頁顯示 This browser can't open local folders | 改用 Edge 或 Chrome 開 `Simulator.html` |
| 網頁顯示 config.json was not found | 選錯資料夾，或你的電腦還沒啟動過 `onedrive_worker.py` |
| 網頁一直停在 Sent | 你的電腦沒在跑、或 OneDrive 同步卡住（看工作列 OneDrive 圖示） |
| 網頁顯示 Simulator offline | `onedrive_worker.py` 沒在執行，或你的電腦睡眠／關機 |
| 某個指標顯示「—」 | 該指標的 FlexScript 回傳錯誤，到 Script Console 測試 |
| 狀態 Failed | 看專案資料夾裡的 `worker.log` |

## 授權提醒
FlexSim 授權屬於公司，公司內部同仁使用一般沒問題；若使用者是外部客戶，請先向代理商確認授權條款。
