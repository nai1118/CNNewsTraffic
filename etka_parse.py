#!/usr/bin/env python3
"""
ETKAnews CN — 日誌解析器
把系統日誌 (PDF 或純文字) 轉成 dashboard 可匯入的 JSON。

用法:
    python3 etka_parse.py <日誌檔> [更多日誌檔...] [-o 輸出檔] [--csv]

範例:
    python3 etka_parse.py Sample_Log_June_2026.pdf
    python3 etka_parse.py logs/*.pdf -o 2026.json
    python3 etka_parse.py June.pdf --csv          # 另外輸出 CSV 方便丟 Excel
    python3 etka_parse.py June.pdf --pdf          # 產生 PDF 月度報告
    python3 etka_parse.py June.pdf --pdf --lang en   # 英文版報告

PDF 需要 pdftotext (poppler)。純文字 (.txt/.log) 不需要任何額外工具。
    macOS   : brew install poppler
    Ubuntu  : sudo apt install poppler-utils
    Windows : https://github.com/oschwartz10612/poppler-windows/releases
"""

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# 只計入 08:00–19:59 的事件;設成 None 表示全部計入
BUSINESS_HOURS = (8, 19)

# "YYYY-MM-DD HH:MM:SS LEVEL 訊息"
# 訊息一路吃到下一個時間戳為止 —— PDF 轉文字常把多筆擠在同一行
LINE_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}[ T]+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>[A-Z]+)\s+"
    r"(?P<msg>.+?)(?=\d{4}-\d{2}-\d{2}[ T]+\d{2}:\d{2}:\d{2}|$)",
    re.DOTALL,
)

# 事件字串尾巴常黏到下一天的日期標題,要剝掉
TRAILING_DATE_RE = re.compile(r"\s*\d{4}-\d{2}-\d{2}\s*$")

# --- 格式二:ETKA 入口網站匯出(login/news 紀錄)---
# 每行有四個時間欄位,沒有事件名稱 —— 一行就是一次登入。
# 只認第一欄(有逗號的 "Jul 30, 2026 02:41:18.645"),因為實測發現第三、四欄
# 的分鐘欄位會被小時數字覆蓋(02:41 → 02:02),年份也常缺字(226-07-09)。
# 第一欄是 UTC,換算成中國時間要 +8。
# 有逗號的是第一欄(UTC);秒與毫秒都允許缺少,不同匯出版本格式會略有差異
PORTAL_RE = re.compile(
    r"(?P<mon>[A-Z][a-z]{2})\w*\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4})\s+"
    r"(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<sec>\d{2}))?(?:[.,]\d+)?"
)

# 備援:有些匯出沒有逗號(第一、二欄長得一樣),這時要去掉相鄰的重複
PORTAL_RE_LOOSE = re.compile(
    r"(?P<mon>[A-Z][a-z]{2})\w*\s+(?P<day>\d{1,2}),?\s*(?P<year>\d{4})\s+"
    r"(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<sec>\d{2}))?(?:[.,]\d+)?"
)

PORTAL_TZ_SHIFT = 8       # UTC → 中國時間 (UTC+8)
PORTAL_EVENT = "Portal login"


# 第三/四欄的完整時間戳(2026-07-30 02:02:18,645),用來當作切分點
PORTAL_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[.,]\d+")


def parse_portal(raw: str):
    """解析入口網站匯出格式。回傳 (records, skipped)。

    不同 poppler 版本會把同一列切在不同位置 —— 有的整列一行,有的把日期和
    時間拆成兩三行。所以先把換行攤平,再以「第三欄的 ISO 時間戳」當分隔,
    每一段就是原始的一列,從中取第一個「Mon D, YYYY」和第一個「HH:MM:SS」。
    """
    flat = " ".join(raw.split())          # 攤平所有換行與多餘空白

    # 用第三欄的 ISO 時間戳切段,每段對應原始的一列
    chunks, last = [], 0
    for m in PORTAL_ISO_RE.finditer(flat):
        chunks.append(flat[last:m.start()])
        last = m.end()
    if not chunks:                        # 沒有 ISO 欄位就整段當一塊
        chunks = [flat]

    date_re = re.compile(r"([A-Z][a-z]{2})\w*\s+(\d{1,2}),\s*(\d{4})")
    time_re = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")

    records, skipped, seen_iso = [], 0, set()
    for chunk in chunks:
        d = date_re.search(chunk)
        t = time_re.search(chunk)
        if not d or not t:
            continue
        try:
            ts_utc = datetime.strptime(
                f"{d.group(1)[:3]} {d.group(2)} {d.group(3)} "
                f"{int(t.group(1)):02d}:{t.group(2)}:{t.group(3) or '00'}",
                "%b %d %Y %H:%M:%S",
            )
        except ValueError:
            skipped += 1
            continue
        ts = ts_utc + timedelta(hours=PORTAL_TZ_SHIFT)
        records.append({"ts": ts, "date": ts.date(), "hour": ts.hour,
                        "level": "INFO", "event": PORTAL_EVENT})
    return records, skipped


def read_text(path: Path) -> str:
    """讀出檔案的純文字內容。PDF 走 pdftotext,其餘直接讀。"""
    if path.suffix.lower() == ".pdf":
        if not shutil.which("pdftotext"):
            sys.exit(
                "找不到 pdftotext。請先安裝 poppler:\n"
                "  macOS : brew install poppler\n"
                "  Ubuntu: sudo apt install poppler-utils"
            )
        res = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            sys.exit(f"pdftotext 讀取失敗: {path}\n{res.stderr}")
        return res.stdout
    return path.read_text(encoding="utf-8", errors="replace")


def parse_records(raw: str):
    """把原始文字切成一筆筆事件,自動判斷格式。回傳 (records, skipped)。"""
    records, skipped = [], 0
    for m in LINE_RE.finditer(raw):
        ts_text = m.group("ts").replace("T", " ").strip()
        try:
            ts = datetime.strptime(ts_text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            skipped += 1
            continue
        event = " ".join(m.group("msg").split())
        event = TRAILING_DATE_RE.sub("", event).strip()
        if not event:
            skipped += 1
            continue
        records.append({"ts": ts, "date": ts.date(), "hour": ts.hour,
                        "level": m.group("level"), "event": event})

    # 標準格式一筆都沒有 → 試試入口網站匯出格式
    if not records:
        return parse_portal(raw)

    return records, skipped


def aggregate(records):
    """依月份彙總成 dashboard 需要的結構。"""
    by_month = defaultdict(list)
    for r in records:
        by_month[r["date"].strftime("%Y-%m")].append(r)

    out = []
    for month in sorted(by_month):
        rows = by_month[month]
        daily = Counter(r["date"] for r in rows)
        hourly = Counter(r["hour"] for r in rows)
        events = Counter(r["event"] for r in rows)

        hourly_by_date = defaultdict(dict)
        for r in rows:
            d = str(r["date"])
            hourly_by_date[d][str(r["hour"])] = hourly_by_date[d].get(str(r["hour"]), 0) + 1

        out.append({
            "month": month,
            "visits": len(rows),
            "label": f"日誌事件計數:{len(rows)} 筆 / {len(daily)} 天",
            "daily": [{"date": str(d), "visits": c} for d, c in sorted(daily.items())],
            "events": [{"name": n, "total": c} for n, c in events.most_common()],
            "hourly": [{"hour": h, "count": c} for h, c in sorted(hourly.items())],
            "hourlyByDate": {d: dict(sorted(v.items(), key=lambda x: int(x[0])))
                             for d, v in sorted(hourly_by_date.items())},
        })
    return out


def in_hours(r):
    if BUSINESS_HOURS is None:
        return True
    lo, hi = BUSINESS_HOURS
    return lo <= r["hour"] <= hi


def write_csv(months, path: Path):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["date", "count"])
        for m in months:
            for d in m["daily"]:
                w.writerow([d["date"], d["visits"]])
    print(f"  CSV  → {path}")


def main():
    ap = argparse.ArgumentParser(description="ETKAnews 日誌解析器")
    ap.add_argument("files", nargs="+", help="日誌檔 (.pdf / .txt / .log)")
    ap.add_argument("-o", "--out", default="etka_data.json", help="輸出 JSON 檔名")
    ap.add_argument("--csv", action="store_true", help="同時輸出 CSV")
    ap.add_argument("--pdf", action="store_true", help="同時產生 PDF 月度報告")
    ap.add_argument("--lang", choices=["zh", "en"], default="zh", help="PDF 報告語言")
    ap.add_argument("--debug", action="store_true",
                    help="印出從檔案抽取到的前 15 行原始文字,用於排查格式")
    ap.add_argument("--all-hours", action="store_true",
                    help="不套用 08:00–19:59 篩選,計入全部時段")
    args = ap.parse_args()

    global BUSINESS_HOURS
    if args.all_hours:
        BUSINESS_HOURS = None

    all_records, total_skipped = [], 0
    for name in args.files:
        p = Path(name)
        if not p.exists():
            print(f"找不到檔案,略過: {p}", file=sys.stderr)
            continue
        text = read_text(p)
        if args.debug:
            print(f"\n--- {p.name} 抽取到的原始文字(前 15 行)---")
            lines = text.splitlines()
            print(f"總字元數:{len(text)},總行數:{len(lines)}")
            for ln in lines[:15]:
                print(repr(ln))
            print("--- 結束 ---\n")
        recs, skipped = parse_records(text)
        total_skipped += skipped
        print(f"讀取 {p.name}: {len(recs)} 筆事件" + (f" (略過 {skipped})" if skipped else ""))
        all_records.extend(recs)

    if not all_records:
        sys.exit("沒有解析到任何事件。請確認檔案格式是否為 'YYYY-MM-DD HH:MM:SS LEVEL 訊息'。")

    kept = [r for r in all_records if in_hours(r)]
    dropped = len(all_records) - len(kept)

    months = aggregate(kept)
    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(months, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    # --- 摘要 ---
    print("\n" + "=" * 46)
    for m in months:
        vals = [d["visits"] for d in m["daily"]]
        print(f"\n{m['month']}  合計 {m['visits']} 筆 / {len(vals)} 天")
        print(f"  每日  平均 {sum(vals)/len(vals):.1f} · 最高 {max(vals)} · 最低 {min(vals)}")
        print(f"  事件  {len(m['events'])} 種,最多:{m['events'][0]['name']} ({m['events'][0]['total']})")
        hrs = m["hourly"]
        print(f"  時段  {hrs[0]['hour']:02d}:00–{hrs[-1]['hour']:02d}:00"
              f",尖峰 {max(hrs, key=lambda x: x['count'])['hour']:02d}:00")
        # 一致性自我檢查:三種切法的總和必須相同
        s_d = sum(vals)
        s_e = sum(e["total"] for e in m["events"])
        s_h = sum(h["count"] for h in hrs)
        flag = "✓ 一致" if s_d == s_e == s_h == m["visits"] else f"✗ 不一致 {s_d}/{s_e}/{s_h}"
        print(f"  校驗  {flag}")

    if dropped:
        rng = f"{BUSINESS_HOURS[0]:02d}:00–{BUSINESS_HOURS[1]:02d}:59"
        print(f"\n※ {dropped} 筆落在 {rng} 之外,已排除(要全部計入請加 --all-hours)")
    if total_skipped:
        print(f"※ {total_skipped} 行無法解析,已略過")

    print(f"\nJSON → {out_path.resolve()}")
    if args.csv:
        write_csv(months, out_path.with_suffix(".csv"))
    if args.pdf:
        try:
            from etka_report import build
            pdf_path = out_path.with_suffix(".pdf")
            build(months, pdf_path, lang=args.lang)
            print(f"  PDF  → {pdf_path}")
        except ImportError:
            print("  PDF  ✗ 需要先安裝: pip install reportlab matplotlib")
    print("把 JSON 內容貼進 dashboard 的「貼上 JSON」欄位即可匯入。")


if __name__ == "__main__":
    main()
