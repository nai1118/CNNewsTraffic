# ETKAnews CN — 日誌解析器 & 本機服務

把系統日誌 (PDF 或純文字) 轉成 dashboard 可匯入的 JSON。
逐行以固定規則解析,同一份檔案每次跑結果都一樣。

## 安裝

需要 Python 3.8+(macOS / Linux 通常內建)。確認:

```bash
python3 --version
```

**如果要讀 PDF**,還需要 `pdftotext`:

| 系統 | 指令 |
|---|---|
| macOS | `brew install poppler` |
| Ubuntu / Debian | `sudo apt install poppler-utils` |
| Windows | 下載 [poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases),解壓後把 `bin` 加入 PATH |

純文字日誌 (`.txt` / `.log`) 不需要裝任何東西。

## 使用

```bash
# 單一檔案
python3 etka_parse.py Sample_Log_June_2026.pdf

# 多個月一起(自動依月份分開統計)
python3 etka_parse.py logs/*.pdf -o 2026.json

# 同時輸出 CSV,方便丟進 Excel
python3 etka_parse.py June.pdf --csv

# 不套用營業時間篩選,計入全部時段
python3 etka_parse.py June.pdf --all-hours

# 產生 PDF 月度報告(可直接寄出的成品)
python3 etka_parse.py June.pdf --pdf

# 英文版報告
python3 etka_parse.py June.pdf --pdf --lang en

# 多個月一起產報告,會自動加上趨勢頁
python3 etka_parse.py logs/*.pdf --pdf -o Q2.json
```

### PDF 報告

`--pdf` 會產生排版好的月度報告,內容包含:

- KPI 摘要(總造訪數、較上月增減、日均、最高/最低)
- 每日流量長條圖 + 平日/週末平均
- 時段分布圖(08:00–19:59)
- 事件組成表(含佔比)
- 多月份時另加一頁整體趨勢圖

需要額外套件:

```bash
pip install reportlab matplotlib
```

### 中文字型

報告會自動尋找可**嵌入**的中文字型(macOS 通常用 Arial Unicode、Windows 用微軟正黑體)。
嵌入式字型會把字形打包進 PDF,檔案帶到任何電腦都長一樣。

想確認自己的系統抓到哪一個:

```bash
python3 etka_report.py --check-fonts
```

顯示「找到 N 個可嵌入的字型」就沒問題。

若顯示找不到,產生中文報告時會**自動下載**一份思源黑體(約 7 MB,只需一次),
存在同資料夾的 `NotoSansTC.ttf`。也可以事先手動抓:

```bash
python3 etka_report.py --download-font
```

或指定自己想用的字型:

```bash
python3 etka_report.py data.json --font "/path/to/YourFont.ttf"
```

> 為什麼要嵌入?PDF 有兩種用字方式 —— **嵌入式**把字形打包進檔案,帶到哪台電腦都一樣;
> **CID 字型**只記錄字型名稱,要靠開啟的軟體自己提供,找不到時整段文字會消失。
> macOS 內建的中文字型多為 PostScript 輪廓,reportlab 無法嵌入,所以才需要這個備援。

注意:`.ttf` 字型檔不要跟著 git 一起版控(檔案大且可重新下載)。

執行後會產生 `etka_data.json`(可用 `-o` 改檔名),並在畫面上印出摘要。

## 匯入 dashboard

1. 打開 `etka_data.json`,全選複製
2. 在 dashboard 展開「自動匯入報表」
3. 貼進「貼上 JSON(本機解析器)」欄位 → 按「匯入 JSON」
4. 核對預覽的數字 → 確認匯入

> 若使用網頁服務版(見下方),不需要這一步 —— 上傳完直接就看得到圖表。

## 設定

**營業時間**:預設只計入 08:00–19:59。改 `etka_parse.py` 開頭:

```python
BUSINESS_HOURS = (8, 19)   # 改成 (0, 23) 或 None 表示全部計入
```

**時區**:腳本直接採用日誌裡的時間戳,不做任何換算。
如果日誌存的是 UTC 而你要看中國時間,需要先確認再處理 —— 這會影響跨日事件歸到哪一天。

## 支援的日誌格式

```
2026-06-01 10:47:17 INFO Calling TPI service.
```

即 `YYYY-MM-DD HH:MM:SS 等級 訊息`。已處理的常見狀況:

- PDF 轉文字時多筆事件擠在同一行
- 事件訊息尾巴黏到下一天的日期標題
- 單獨一行、沒有時間的日期標題(視為分隔,不計入次數)

格式不同的話,調整 `LINE_RE` 那段正規表達式即可。

## 輸出的校驗

每次執行都會檢查「每日總和 / 事件總和 / 時段總和 / 總筆數」四個數字是否相等,
印出 `校驗 ✓ 一致`。若出現 `✗ 不一致`,代表解析邏輯與日誌格式對不上,請回報。


---

# 網頁服務版(建議)

不用記指令,開瀏覽器上傳檔案就好。整套跑在自己電腦上,資料不會外傳。

## 安裝

```bash
pip install -r requirements.txt
```

## 啟動

```bash
python3 server.py
```

然後開瀏覽器到 **http://127.0.0.1:8000**

## 怎麼用

1. 選檔案(可一次選多個月的 PDF / txt / log)→ 按「解析並匯入」
2. 頁面直接顯示 KPI、每日流量、時段分布、事件組成
3. 上方下拉選單切換月份
4. 按「下載 PDF 報告」拿到可以直接寄出的成品

## 畫面功能

- **中英即時切換** — 右上角按鈕,不重新載入頁面,會記住上次選的語言。
  PDF 報告也會跟著出對應語言版本(英文版檔名結尾帶 `_EN`)
- **時段分布可選日期** — 下拉選單切「整月」或任一天,圖表即時重畫
- **滑鼠懸停** — 顯示該日/該時段的確切數字,其餘長條淡化
- **Y 軸刻度** — 自動選用好讀的間距(1/2/5 系列),換資料時重算

資料存在同目錄的 `data.json`。同一個月重複上傳會覆蓋更新,不會重複累加。

## 路由

| 路徑 | 用途 |
|---|---|
| `GET /` | 主畫面(可加 `?month=2026-06` 指定月份) |
| `POST /upload` | 上傳並解析日誌 |
| `GET /report.pdf` | 下載 PDF 報告 |
| `GET /data.json` | 取得原始 JSON |
| `POST /reset` | 清空所有資料 |

`/data.json` 是標準 JSON,想接自己的 BI 工具或排程可以直接打這支。

## 開機自動啟動(選用)

**macOS / Linux** — 加進 crontab:

```bash
@reboot cd /path/to/etka-parser && python3 server.py
```

**Windows** — 建立 `start.bat`,放進「啟動」資料夾:

```bat
cd /d C:\path\to\etka-parser
python server.py
```

## 給同事一起用

預設只綁 `127.0.0.1`,別台電腦連不進來 —— 這是刻意的,日誌是內部資料。

### 情況一:同一個辦公室網路

```bash
python3 server.py --share --password 你設的密碼
```

啟動後畫面會印出你的內網 IP,例如:

```
ETKAnews CN — http://192.168.1.23:8000
同網路的人可用上面網址連線
已啟用密碼保護,帳號:etka
```

把那個網址給同事,瀏覽器會跳出登入框,輸入帳號 `etka` 和你設的密碼即可。
他們能看圖表、切日期、下載 PDF,也能上傳 log。

帳號想改用 `--user 名稱`。

> **不要省略 `--password`。** 沒設密碼的話,同網路上任何人都能看到全部資料、
> 也能上傳或覆蓋。沒設密碼時啟動畫面會出現警告。

注意:你的電腦要保持開機、服務要一直跑著,別人才連得到。闔上筆電就斷了。

### 情況二:遠端 / 不同地點

內網 IP 在外面連不到,需要多一層。由簡到繁:

| 做法 | 說明 |
|---|---|
| **寄 PDF** | 最省事。跑 `--pdf` 產生報告寄出,對方不需要任何設定 |
| **Tailscale** | 免費,裝了之後大家像在同一個網路,網址照樣用內網 IP。適合固定幾個人 |
| **Cloudflare Tunnel** | 免費,產生一個對外網址。**務必同時設 `--password`** |
| **架在公司伺服器** | 最正規,但要 IT 協助 |

前三種都仰賴你的電腦開著。如果這會變成每個月固定要交的東西,長期還是架在
公司伺服器上比較穩。

### 安全提醒

這個服務原本設計就是給本機用的,`--password` 是基本防護(HTTP Basic Auth),
足以擋掉「同網路的人隨手打開」,但**不是為了對抗有心攻擊**:

- 沒有 HTTPS,同網路上密碼是明文傳輸
- 沒有多使用者、沒有權限分級,登入了就是全部權限
- 任何人都能上傳檔案覆蓋資料

放在信任的辦公室網路沒問題;要直接開到公開網際網路,前面請加一層
反向代理(nginx / Caddy)處理 HTTPS,並找 IT 確認。

## 兩種用法怎麼選

| | 指令列 `etka_parse.py` | 網頁服務 `server.py` |
|---|---|---|
| 適合 | 排程、批次、接自動化流程 | 每月手動跑一次、要看圖表 |
| 輸出 | JSON / CSV / PDF | 網頁 + PDF + JSON |
| 需要 | Python(PDF 才需 reportlab) | 全部套件 |

兩者共用同一份解析邏輯,結果完全一致。
