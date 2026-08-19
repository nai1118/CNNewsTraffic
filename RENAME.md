# 專案改名 — 操作步驟

`etka-parser` → `CNNewsTraffic`

**重點:改資料夾名稱會讓 `venv` 失效**,因為虛擬環境裡記錄的是絕對路徑。
所以要重建一次 —— 只要幾分鐘,套件會重新裝好。

---

## 步驟

### 1. 停掉服務

終端機按 `Control + C`

### 2. 改資料夾名稱

```bash
cd ~
```

```bash
mv etka-parser CNNewsTraffic
```

### 3. 進新資料夾,放入新檔案

```bash
cd ~/CNNewsTraffic
```

把下載的 `server.py`、`FEATURES.md`、`GITHUB.md` 覆蓋進來:

```bash
mv ~/Downloads/server.py ~/Downloads/FEATURES.md ~/Downloads/GITHUB.md .
```

### 4. 重建虛擬環境

舊的已經失效,刪掉重建:

```bash
rm -rf venv
```

```bash
python3 -m venv venv
```

```bash
source venv/bin/activate
```

```bash
pip install -r requirements.txt
```

這步會下載套件,等一兩分鐘。

### 5. 確認

```bash
python3 server.py
```

應該看到:

```
CNNewsTraffic v1.0.0 — http://127.0.0.1:8000
```

開瀏覽器確認資料還在(`data.json` 沒有被動到,6/7 月的紀錄應該都在)。

### 6. 更新啟動捷徑(如果之前有做)

```bash
printf '#!/bin/bash\ncd "$(dirname "$0")"\nsource venv/bin/activate\npython3 server.py "$@"\n' > start.sh && chmod +x start.sh
```

---

## 沒有改到的地方

**網頁報頭仍然是 ETKAnews**,PDF 報告檔名也還是 `ETKAnews_2026-07.pdf`。

這是刻意的 —— `CNNewsTraffic` 是**工具的名字**,`ETKAnews` 是**被統計的對象**
(那份電子報本身)。報表上印的應該是後者,主管看到 ETKAnews 才知道這是什麼的數據。

如果你希望報表上也改掉,跟我說一聲。

---

## 之後上 GitHub

改名完成後再照 `GITHUB.md` 走,儲存庫名稱用 `CNNewsTraffic`。
先改名再上傳,順序比較單純。
