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
| `runner.py` | 核心：檢查參數、用命令列開 FlexSim 跑模擬、讀指標 |
| `check_flexsim.py` | 連線檢查：確認這台電腦能用命令列控制 FlexSim |
| `index.html` | 網頁（英文介面），啟動時自動複製到共用資料夾成為 `Simulator.html` |
| `start.bat` | 正式上線用：雙擊啟動，不用開 VS Code |
| `worker.log` | 執行紀錄（自動產生），出問題先看這裡 |
| `runs/` | 每次模擬產生的腳本和結果（自動產生，保留最近 30 次），出問題時用來除錯 |

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

## 第一步：檢查 FlexSim，然後啟動

在 VS Code 按 `` Ctrl + ` ``（Esc 下面那個鍵）開啟下方終端機。

**1. 先做連線檢查**（第一次、或改過 config.json 後），它也會試著匯出輸出表格：
```
python check_flexsim.py
```
它會自動找 flexsim.exe、在背景開啟模型、讀 `WUS_year`、再用預設日期完整跑一次模擬。每一步都打勾就沒問題。
只想快速確認能不能開 FlexSim（不跑完整模擬）：`python check_flexsim.py --quick`

**2. 啟動主程式：**
```
python onedrive_worker.py
```
看到 `Watching C:\Users\...\OneDrive - 公司名稱\FlexSimShare` 就成功了。

- 程式會自動在公司 OneDrive 裡建立 `FlexSimShare` 資料夾，**不用填路徑**
- 終端機要一直開著。按 `Ctrl + C` 停止：如果正在跑模擬，會先關掉背景的 FlexSim 再結束（再按一次 `Ctrl + C` 立刻強制結束）。停止後網頁會顯示 offline
- 程式中斷時正在跑的工作，下次啟動會標記為 Interrupted，**不會自動重跑**；還沒開始的工作則會照常執行
- 想先不開 FlexSim、只測網頁流程：把 `config.json` 的 `"mock_mode"` 改成 `true`（用假資料，網頁上方會有黃色 Demo 提示）

### 網頁分成三頁

- **參數調整**：設定期間、控制按鈕（重設／執行／快轉／暫停／停止）、時間軸、執行中的即時數值
- **指標呈現**：選要看哪一次模擬、切換產品（Bianca／Strata）、各站總產出、每日產出長條圖（點站別切換，滑鼠移到長條上看數字）、每日明細表（可下載 CSV）、各次結果比較
- **執行紀錄**：所有送出過的模擬，包含情境、狀態、期間、**執行者、裝置、時區**、送出時間、模式、執行時間和指標，可下載 CSV

送出時「你的名字」是必填。因為網頁是直接從 OneDrive 資料夾開的檔案，**瀏覽器不會讓網頁讀取 IP**，所以改記錄：裝置代號（每台電腦每個瀏覽器固定一組）、作業系統、瀏覽器、時區、送出時的當地時間。

模擬完成後，按控制區的「查看指標」，或在執行紀錄點一下那一筆，就會切到指標呈現頁。

指標呈現頁的資料來自 `config.json` 的 `tables`：模擬跑完後，把 FlexSim 裡的 Global Table 整張匯出（目前是 `WUS_Daily_Record` 和 `WUS_Daily_Record_Strara`），`skip_rows` 裡的列（`TOTAL`）不顯示。數字欄會自動加總、畫圖；`key_columns`（Date、Weekday）當作文字欄顯示。

### 網頁功能

| 按鈕 | 作用 |
|---|---|
| **Reset 重設** | 把輸入恢復成預設值，清掉畫面上的結果（不會動到 FlexSim） |
| **Run 執行** | 執行模擬，期間顯示進度、時間軸（模擬到哪一天），以及**即時指標**（約每 10 秒更新，附小折線圖） |
| **Fast forward 快轉** | 直接跑到結束，中途不回報進度，結果稍微快一點出來 |
| **Pause 暫停** | 把背景的 FlexSim 整個凍結在當下（不吃 CPU、不丟進度）。暫停後按 **執行** 或 **快轉** 從原處繼續。暫停超過 `max_pause_minutes`（預設 60 分鐘）會自動停止 |
| **Stop 停止** | 結束這次模擬，背景的 FlexSim 會被關閉，不保留結果 |

- 執行中也可以直接在「執行」和「快轉」之間切換
- 暫停、繼續、切換、停止都要等 OneDrive 同步，按下去後大約幾秒到幾十秒才生效
- 暫停是用 Windows 的「凍結程式」功能，不需要改模型；如果你的 FlexSim 授權是網路授權，暫停太久可能會讓授權連線逾時

- 右上角可以切換 **English / 中文**，選擇會記在瀏覽器裡。標題、指標名稱等來自 config.json 的文字，中文用 `title_zh`、`subtitle_zh`、`label_zh`、`unit_zh`、`help_zh`（沒填就顯示英文）
- 日期用兩個日期選擇器輸入，送出時自動拆成 `year`、`st_m`、`st_d`、`ed_m`、`ed_d`（設定在 config.json 的 `date_inputs`）
- 想清掉舊的執行紀錄：先停止程式，再把 `FlexSimShare` 裡 `jobs/` 和 `results/` 的檔案刪掉

`FlexSimShare` 裡會自動出現：

| 自動建立的檔案 | 用途 |
|---|---|
| `Simulator.html` | 給美國同事開的網頁（每次啟動自動更新成最新版） |
| `config.json` | 網頁用的公開設定（**不含**模型路徑和 FlexScript） |
| `status.json` | 讓網頁顯示「Simulator online / offline」 |
| `jobs/` | 網頁送來的工作，一個工作一個檔案 |
| `cancel/` | 網頁按「停止」時寫入的停止要求 |
| `control/` | 網頁按「暫停／執行／快轉」時寫入的控制指令 |
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

## FlexSim 怎麼被控制

不需要 FlexSimPy、不需要編譯。每次有工作時，程式會：
1. 產生一個 FlexScript 腳本（在 `runs/` 資料夾裡）：
   - 把同事填的日期寫進 `WUS_year` 的第 1 欄（第 1～5 列：year、st_m、st_d、ed_m、ed_d）
   - `resetmodel()`
   - 一步一步執行到「結束日當天 24:00」
   - 把指標寫成文字檔，然後關閉 FlexSim
2. 用命令列在背景開啟 FlexSim 執行這個腳本：
   `flexsim.exe 模型.fsm /maintenance nogui_disablemsg_runscript /scriptpath 腳本`
3. 讀回指標，寫到 OneDrive 給網頁顯示

**模型檔不會被修改或存檔。** 每次都會重新開啟 FlexSim，所以每次會多花十幾秒到一分鐘的開啟時間。

### config.json 重點設定

| 設定 | 說明 |
|---|---|
| `flexsim.exe` | 留空會自動在 `C:/Program Files` 找；找不到時填完整路徑，斜線用 `/` |
| `flexsim.model_path` | 模型檔，目前是 `D:/FlexSim_D1.fsm` |
| `flexsim.max_run_seconds` | 單次模擬最多幾秒，超過就強制結束（預設 1800） |
| `stop.set_model_start` | `true`（預設）：每次把 FlexSim 的模型開始時間改成使用者選的開始日，直接從開始日跑到結束日，不用暖機。`false`：用固定的 `model_start` |
| `stop.start_time_of_day` | 模型從開始日的幾點開始，預設 `00:00` |
| `stop.model_start` | 只在 `set_model_start` 為 `false` 時使用，要和 FlexSim 工具列 Run Time 左邊的開始日期時間一致 |
| `stop.time_unit_seconds` | 模型時間單位：秒=1、分鐘=60、小時=3600 |
| `parameters[].set_script` | 寫入模型的 FlexScript。`Table("WUS_year")[列][欄]`，第 1 欄是第一個產品；要同時寫第 2 欄就再加 `Table("WUS_year")[列][2] = {value};` |
| `metrics[].script` | 讀取指標的 FlexScript，要回傳數字。執行中也會定時讀取來顯示即時數值 |
| `metrics[].live` | 設成 `false` 就不在執行中讀取這個指標（適合只有跑完才算得出來的指標） |

### 新增指標

在 `metrics` 加一筆，例如：
```json
{ "name": "smt_output", "label": "SMT output", "unit": "pcs", "decimals": 0,
  "script": "Table(\"Result\")[1][2]" }
```
- `name`：英文代號（不能重複、不能有空格）
- `script`：先在 FlexSim 的 **Script Console** 輸入 `return Table("Result")[1][2];` 測試，有回傳正確數字再貼進來
- 字串裡的 `"` 要寫成 `\"`
- 改完執行 `python check_flexsim.py` 確認，再重新啟動 `onedrive_worker.py`

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
- 每個工作都在背景重新開一次 FlexSim，跑完自動關閉；某次出錯不影響下一次
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
| `Could not find flexsim.exe` | 在 config.json 的 `flexsim.exe` 填完整路徑 |
| `FlexSim closed without writing results` | 腳本中途出錯。到 `runs/` 最新的資料夾打開 `script.txt`，複製內容到 FlexSim 的 Script Console 執行，看哪一行報錯 |
| `did not finish within ... s` | 模擬太久或 FlexSim 卡在對話框；加大 `max_run_seconds`，或看工作管理員有沒有殘留的 flexsim.exe |
| 指標數字和日期對不上 | 模型可能在 reset 時把 `WUS_year` 蓋掉（例如從 Excel 重新匯入），告訴我改成 reset 後才寫入 |
| 某個指標顯示「—」 | 該指標的 FlexScript 回傳錯誤，到 Script Console 測試 |
| 狀態 Failed | 看網頁上的錯誤訊息，以及專案資料夾裡的 `worker.log` |

## 授權提醒
FlexSim 授權屬於公司，公司內部同仁使用一般沒問題；若使用者是外部客戶，請先向代理商確認授權條款。
