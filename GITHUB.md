# GitHub 版本管理 — 操作手冊

專門為 CNNewsTraffic 專案寫的。不需要先懂 Git,照著貼指令就行。

---

## 為什麼要做

- 每次改版都看得到**改了哪幾行**(綠色新增、紅色刪除)
- 改壞了可以**退回上一版**
- 不用再靠檔案大小判斷版本新舊
- 同事可以直接下載,不用傳 zip

---

## 一次性設定

### 1. 註冊 GitHub

到 https://github.com 註冊帳號(免費)。

### 2. 確認 Git 已安裝

```bash
git --version
```

沒有的話 macOS 會跳出視窗問你要不要安裝開發者工具,按同意即可。

### 3. 設定身分

換成你自己的名字和 email(email 要跟 GitHub 註冊的一樣):

```bash
git config --global user.name "Yen Nai-sheng"
git config --global user.email "你的email@example.com"
```

### 4. 建立儲存庫

在 GitHub 網站右上角 **+** → **New repository**

| 欄位 | 填什麼 |
|---|---|
| Repository name | `CNNewsTraffic` |
| Description | ETKA 入口網站閱讀量統計(private) |
| 公開/私人 | **務必選 Private** |
| Initialize with README | **不要勾** |

按 **Create repository**,下一頁會顯示一串網址,長這樣:

```
https://github.com/你的帳號/CNNewsTraffic.git
```

**把這串網址記下來**,下一步要用。

### 5. 把專案上傳

終端機依序貼(最後一行的網址換成你自己的):

```bash
cd ~/CNNewsTraffic
```

```bash
git init
```

```bash
git add .
```

```bash
git status
```

> **這一步務必看一下**:列出的檔案裡**不應該**出現 `data.json`、`venv`、
> `NotoSansTC.ttf`、任何 `.pdf`。如果出現了,代表 `.gitignore` 沒放好,
> 先停下來確認,不要繼續。

確認沒問題後:

```bash
git commit -m "v1.0.0 首個穩定版本"
```

```bash
git branch -M main
```

```bash
git remote add origin https://github.com/你的帳號/CNNewsTraffic.git
```

```bash
git push -u origin main
```

第一次 push 會要求登入。**密碼欄位不能用 GitHub 密碼**,要用 Token:

1. GitHub 網站 → 右上角頭像 → Settings
2. 最下面 Developer settings → Personal access tokens → Tokens (classic)
3. Generate new token (classic)
4. Note 填 `mac`,Expiration 選 90 days,勾選 **repo**
5. 產生後**立刻複製**(只會顯示一次),貼到終端機的密碼欄

完成後重新整理 GitHub 頁面,檔案就都在上面了。

---

## 之後每次改版

改完檔案後,四行:

```bash
cd ~/CNNewsTraffic
git add .
git commit -m "簡短說明改了什麼"
git push
```

commit 訊息寫具體一點,以後才看得懂:

| 好 | 不好 |
|---|---|
| `修正時段圖 Y 軸刻度被裁切` | `修正` |
| `新增週別統計功能` | `更新` |
| `v1.1.0 支援多月份趨勢圖` | `改了一些東西` |

---

## 常用操作

### 看改了哪些行

```bash
git diff
```

按 `q` 離開。

### 看歷史紀錄

```bash
git log --oneline
```

### 還沒 commit,想放棄修改

```bash
git checkout -- server.py
```

### 已經 commit,想退回上一版

```bash
git log --oneline          # 先找到要回去的那筆代碼
git checkout 代碼 -- server.py
```

### 標記版本

發布新版時打個標籤,以後好找:

```bash
git tag v1.0.0
git push --tags
```

---

## 換電腦或給同事

```bash
git clone https://github.com/你的帳號/CNNewsTraffic.git
cd CNNewsTraffic
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 server.py
```

注意 `data.json` 不在儲存庫裡(刻意的),所以新機器上是空的,
要重新上傳日誌。

---

## 重要提醒

**`.gitignore` 是防護線。** 它擋住 `data.json` 和 PDF 報告不被上傳 ——
這些是公司內部的存取紀錄,不該進到雲端儲存庫,即使是 private 也一樣。

**Token 不要存進專案資料夾。** 如果不小心 commit 上去了,那個 Token 就算作廢,
要立刻到 GitHub 撤銷並重新產生。

**儲存庫務必保持 Private。** 建立時選錯的話,到儲存庫的 Settings 最下面
Danger Zone 可以改成 private。
