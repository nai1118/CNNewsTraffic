#!/usr/bin/env python3
"""
CNNewsTraffic — 本機網頁服務

啟動:
    python3 server.py
    → http://127.0.0.1:8000

功能:
    上傳日誌 (PDF/txt/log) → 逐行解析 → 儲存 → 網頁看圖表 → 下載 PDF 報告

資料存在同目錄的 data.json,不會上傳到任何地方。
"""

import base64
import json
import math
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response)

from etka_parse import BUSINESS_HOURS, aggregate, parse_records, read_text

BASE = Path(__file__).parent
DATA = BASE / "data.json"

VERSION = "1.0.0"

app = FastAPI(title="CNNewsTraffic", version=VERSION)

# 由 CLI 設定;有設密碼才會啟用驗證
AUTH = {"user": "", "password": ""}


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """設了密碼就要求 HTTP Basic 驗證。沒設密碼(純本機)則直接放行。"""
    if not AUTH["password"]:
        return await call_next(request)

    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            raw = base64.b64decode(header[6:]).decode("utf-8")
            user, _, pw = raw.partition(":")
            # 用 compare_digest 避免時間差攻擊
            if (secrets.compare_digest(user, AUTH["user"])
                    and secrets.compare_digest(pw, AUTH["password"])):
                return await call_next(request)
        except Exception:
            pass

    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="CNNewsTraffic"'},
        content="Authentication required",
    )


# ---------- 語言 ----------

STRINGS = {
    "zh": {
        "tagline": "CN Edition · 月度閱讀量 · 本機服務",
        "upload": "上傳日誌",
        "parse_btn": "解析並匯入",
        "upload_hint": "支援 PDF / txt / log,可一次選多個月。同月份會覆蓋更新。",
        "empty": "還沒有資料 — 上傳第一份日誌開始。",
        "total": "總造訪數",
        "mom": "較上月",
        "mom_sub": "month over month",
        "avg": "日均",
        "days": "{n} 天",
        "hilo": "最高 / 最低",
        "single_day": "單日",
        "daily": "每日流量",
        "week_line": "平日平均 {a}({an} 天) · 週末平均 {b}({bn} 天)",
        "by_hour": "時段分布",
        "whole_month": "整月",
        "hour_sum": "{cap} · 合計 {n} 次",
        "no_hour_detail": "此筆資料沒有逐日時段明細,請重新上傳日誌",
        "overview": "月度總覽",
        "th_month": "月份",
        "th_visits": "造訪數",
        "th_days": "天數",
        "export": "輸出",
        "dl_pdf": "下載 PDF 報告",
        "dl_json": "下載 JSON",
        "footer": "資料儲存於 {f} · 僅在本機執行 · v{v}",
        "imported": "已匯入 {names} — 共 {n} 筆",
        "dropped": ",另有 {n} 筆落在 {lo:02d}:00–{hi:02d}:59 之外已排除",
        "no_events": "沒有解析到任何事件,請確認日誌格式。",
        "no_data_pdf": "還沒有資料,無法產生報告。",
        "pdf_fail": "PDF 產生失敗:{e}",
        "cleared": "已清空資料。",
        "other_lang": "EN",
    },
    "en": {
        "tagline": "CN Edition · Monthly Readership · Local service",
        "upload": "Upload logs",
        "parse_btn": "Parse and import",
        "upload_hint": "Accepts PDF / txt / log. Select several months at once — same month overwrites.",
        "empty": "No data yet — upload your first log to begin.",
        "total": "Total visits",
        "mom": "vs. last month",
        "mom_sub": "month over month",
        "avg": "Daily average",
        "days": "{n} days",
        "hilo": "High / Low",
        "single_day": "single day",
        "daily": "Daily traffic",
        "week_line": "Weekday avg {a} ({an} days) · Weekend avg {b} ({bn} days)",
        "by_hour": "By hour",
        "whole_month": "Whole month",
        "hour_sum": "{cap} · {n} events",
        "no_hour_detail": "This entry has no per-day detail — upload the log again",
        "overview": "Monthly overview",
        "th_month": "Month",
        "th_visits": "Visits",
        "th_days": "Days",
        "export": "Export",
        "dl_pdf": "Download PDF report",
        "dl_json": "Download JSON",
        "footer": "Stored in {f} · runs locally only · v{v}",
        "imported": "Imported {names} — {n} events",
        "dropped": ". {n} events outside {lo:02d}:00–{hi:02d}:59 were excluded",
        "no_events": "No events were parsed. Check the log format.",
        "no_data_pdf": "No data yet — nothing to report on.",
        "pdf_fail": "PDF generation failed: {e}",
        "cleared": "Data cleared.",
        "other_lang": "中",
    },
}



def tw(key, lang, **args):
    """輸出可即時切換語言的文字節點。

    兩種語言都先在後端格式化好,前端只要換 textContent,
    不需要任何樣板解析或正則,避免跨語言的轉義問題。
    """
    def fill(lg):
        tpl = STRINGS[lg][key]
        return tpl.format(**args) if args else tpl

    zh, en = fill("zh"), fill("en")
    cur = en if lang == "en" else zh
    return (f'<span data-zh="{html_attr(zh)}" data-en="{html_attr(en)}">{cur}</span>')


def html_attr(v):
    return str(v).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def qs(lang, **kw):
    """組出保留語言設定的查詢字串。"""
    parts = [f"{k}={v}" for k, v in kw.items() if v]
    if lang == "en":
        parts.append("lang=en")
    return ("?" + "&".join(parts)) if parts else ""


# ---------- 資料存取 ----------

# 有設 DATABASE_URL(例如部署在 Groovedeploy)就存進 Postgres,重啟/重新部署也不會遺失。
# 沒設(本機執行)就照舊只用同目錄的 data.json。
DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _db():
    import psycopg
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    conn.execute("CREATE TABLE IF NOT EXISTS app_state "
                 "(key text PRIMARY KEY, value text NOT NULL)")
    return conn


def _write_file(months: list):
    DATA.write_text(json.dumps(months, ensure_ascii=False, indent=1), encoding="utf-8")


def load() -> list:
    if DATABASE_URL:
        with _db() as conn:
            row = conn.execute(
                "SELECT value FROM app_state WHERE key = 'months'").fetchone()
        return json.loads(row[0]) if row else []
    if DATA.exists():
        try:
            return json.loads(DATA.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def save(months: list):
    if DATABASE_URL:
        with _db() as conn:
            conn.execute(
                "INSERT INTO app_state (key, value) VALUES ('months', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (json.dumps(months, ensure_ascii=False),))
    _write_file(months)


def clear():
    if DATABASE_URL:
        with _db() as conn:
            conn.execute("DELETE FROM app_state WHERE key = 'months'")
    if DATA.exists():
        DATA.unlink()


def merge(existing: list, incoming: list) -> list:
    """同月份直接覆蓋,其餘新增,依月份排序。"""
    by_month = {m["month"]: m for m in existing}
    for m in incoming:
        by_month[m["month"]] = m
    return [by_month[k] for k in sorted(by_month)]


# ---------- 畫面 ----------

CSS = """
:root { --ink:#242320; --paper:#F1EEE6; --deep:#E7E2D5; --brass:#9C7A45;
        --sage:#5C6B4F; --rule:#CFC8B6; --muted:#8A8474; --red:#9C4A3C; }
* { box-sizing: border-box; }
body { background: var(--paper); color: var(--ink); margin: 0;
       font-family: -apple-system, "PingFang TC", "Microsoft JhengHei", sans-serif; }
.wrap { max-width: 860px; margin: 0 auto; padding: 40px 24px 60px; }
header { text-align: center; border-bottom: 2px solid var(--ink); padding-bottom: 18px; }
h1 { font-size: 44px; margin: 0; letter-spacing: -.02em; font-weight: 600; }
.sub { color: var(--muted); font-size: 12px; margin-top: 6px; }
h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .2em; color: var(--muted);
     font-weight: 500; border-bottom: 1px solid var(--rule); padding-bottom: 6px;
     margin: 40px 0 18px; }
.chart { display: block; margin: 4px 0 10px; overflow: visible; }
.kpi { display: flex; border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule);
       margin-top: 22px; }
.kpi div { flex: 1; text-align: center; padding: 14px 6px; }
.kpi span { display: block; font-size: 10px; color: var(--muted); text-transform: uppercase;
            letter-spacing: .1em; margin-bottom: 6px; }
.kpi b { font-size: 26px; font-weight: 600; }
.kpi em { display: block; font-size: 11px; color: var(--muted); font-style: normal; margin-top: 4px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; color: var(--muted); font-weight: 500;
     border-bottom: 1px solid var(--ink); padding: 8px 4px; font-size: 11px;
     text-transform: uppercase; letter-spacing: .06em; }
td { padding: 8px 4px; border-bottom: 1px solid var(--rule); }
.n { text-align: right; font-family: ui-monospace, monospace; }
.note { font-size: 12px; color: #59564C; font-family: ui-monospace, monospace; margin: 10px 0 0; }
.drop { border: 1px dashed var(--rule); padding: 26px; text-align: center; border-radius: 2px; }
.drop input { display: block; margin: 0 auto 12px; font-size: 13px; }
button:disabled { opacity: .35; cursor: not-allowed; }
button, .btn { background: var(--ink); color: var(--paper); border: 0; padding: 10px 18px;
               font-size: 13px; cursor: pointer; text-decoration: none; display: inline-block; }
button:hover, .btn:hover { opacity: .88; }
.ghost { background: transparent; color: var(--ink); border: 1px solid var(--ink); }
.btn.disabled { opacity: .35; cursor: not-allowed; }
.preview-bar { background: var(--deep); border: 1px dashed var(--rule); padding: 10px 14px;
               font-size: 12px; color: #59564C; margin-bottom: 20px; text-align: center; }
.bar { height: 9px; background: var(--sage); display: inline-block; vertical-align: middle; }
.msg { padding: 10px 14px; font-size: 13px; margin-bottom: 18px; border-left: 3px solid var(--sage);
       background: var(--deep); }
.msg.err { border-color: var(--red); }
select { padding: 7px 10px; border: 1px solid var(--ink); background: transparent;
         font-family: ui-monospace, monospace; font-size: 13px; }
.chart .hit:hover ~ .hit { }
.chart .hit { cursor: crosshair; }
#tip { position: fixed; pointer-events: none; z-index: 99; background: var(--paper);
       border: 1px solid var(--ink); padding: 6px 10px; font-size: 12px;
       font-family: ui-monospace, monospace; display: none; white-space: nowrap;
       box-shadow: 2px 2px 0 rgba(36,35,32,.12); }
#tip b { display: block; font-size: 15px; margin-top: 2px; }
footer { margin-top: 46px; padding-top: 12px; border-top: 1px solid var(--rule);
         text-align: center; font-size: 11px; color: #A9A38F; }
"""

HOUR_START, HOUR_END = 8, 19

PREVIEW_NOTE = {
    "zh": "預覽模式 — 下載功能需啟動本機服務 (python3 server.py) 後才能使用。",
    "en": "Preview mode — downloads require the local service (python3 server.py).",
}


def nice_scale(mx):
    """回傳 (軸頂端, 刻度間距)。

    從 1/2/2.5/5 × 10^n 的候選中,挑刻度數落在 4–6 之間、
    且軸頂最貼近資料的一組,避免圖面上留下大片空白。
    """
    mx = max(int(math.ceil(mx)), 1)
    best = None
    for mag in (10 ** e for e in range(0, 7)):
        for m in (1, 2, 2.5, 5):
            step = m * mag
            if step < 1:
                continue
            step = int(step) if step == int(step) else step
            top = math.ceil(mx / step) * step
            n = int(round(top / step))
            if 4 <= n <= 6:
                cand = (top - mx, top)          # 先比空白大小,再比軸頂
                if best is None or cand < best[0]:
                    best = (cand, int(top), step)
    if best is None:                            # 極端值的保險
        step = max(1, mx // 4)
        return int(math.ceil(mx / step) * step), step
    return best[1], best[2]


PAD_L = 32   # 左側留給 Y 軸刻度
PAD_T = 12   # 上方留白,避免頂端刻度數字被裁掉


def svg_bars(points, color, height=150):
    if not points:
        return ""
    w = 760
    plot_w = w - PAD_L
    top, step = nice_scale(max((p[1] for p in points), default=1))
    bw = plot_w / len(points)

    # Y 軸刻度線與數字
    grid = []
    v = 0
    while v <= top:
        y = height - v / top * height
        grid.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{w}" y2="{y:.1f}" '
            f'stroke="#CFC8B6" stroke-width="0.8"'
            + ('' if v == 0 else ' stroke-dasharray="2 4"') + '/>'
        )
        grid.append(
            f'<text x="{PAD_L-6}" y="{y+3.5:.1f}" font-size="10" fill="#8A8474" '
            f'text-anchor="end" font-family="monospace">{v}</text>'
        )
        v += step

    rects, hits, labels = [], [], []
    for i, (label, val) in enumerate(points):
        h = val / top * height
        x = PAD_L + i * bw
        rects.append(
            f'<rect class="bar-r" x="{x+bw*.15:.1f}" y="{height-h:.1f}" '
            f'width="{bw*.7:.1f}" height="{h:.1f}" fill="{color}" pointer-events="none"/>'
        )
        hits.append(
            f'<rect class="hit" x="{x:.1f}" y="0" width="{bw:.1f}" height="{height}" '
            f'fill="transparent" data-l="{label}" data-v="{val}"/>'
        )
        if len(points) <= 14 or i % 2 == 0:
            labels.append(
                f'<text x="{x+bw/2:.1f}" y="{height+14}" font-size="10" fill="#8A8474" '
                f'text-anchor="middle" font-family="monospace">{label}</text>'
            )

    return (
        f'<svg class="chart" viewBox="0 {-PAD_T} {w} {height+22+PAD_T}" width="100%" '
        f'preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">'
        f'{"".join(grid)}{"".join(rects)}{"".join(labels)}{"".join(hits)}</svg>'
    )


def weekday_split(m):
    wd, we = [], []
    for d in m["daily"]:
        y, mo, dd = map(int, d["date"].split("-"))
        (we if date(y, mo, dd).weekday() >= 5 else wd).append(d["visits"])
    f = lambda a: f"{sum(a)/len(a):.1f}" if a else "—"
    return f(wd), len(wd), f(we), len(we)


def render(months, msg=None, err=False, sel=None, day=None, lang="zh", preview=False):
    T = STRINGS.get(lang, STRINGS["zh"])
    if not months:
        body = f'<div class="msg">{tw("empty", lang)}</div>'
    else:
        sel = sel or months[-1]["month"]
        cur = next((m for m in months if m["month"] == sel), months[-1])
        prev = None
        idx = months.index(cur)
        if idx > 0:
            prev = months[idx - 1]

        delta = "—"
        if prev and prev["visits"]:
            diff = cur["visits"] - prev["visits"]
            delta = f'{diff:+,} ({diff/prev["visits"]*100:+.1f}%)'

        vals = [d["visits"] for d in cur["daily"]] or [0]
        avg = sum(vals) / len(vals)
        a, an, b, bn = weekday_split(cur)

        opts = "".join(
            f'<option value="{m["month"]}"{" selected" if m["month"]==cur["month"] else ""}>{m["month"]}</option>'
            for m in months
        )

        hbd = cur.get("hourlyByDate") or {}
        day_keys = sorted(hbd)
        if day and day in hbd:
            hours = {int(k): v for k, v in hbd[day].items()}
            hour_caption = day
        else:
            day = ""
            hours = {h["hour"]: h["count"] for h in cur["hourly"]}
            hour_caption = f'{T["whole_month"]} {cur["month"]}'
        hour_pts = [(f"{h:02d}", hours.get(h, 0)) for h in range(HOUR_START, HOUR_END + 1)]
        hour_total = sum(v for k, v in hours.items() if HOUR_START <= k <= HOUR_END)

        day_opts = f'<option value="">{T["whole_month"]}</option>' + "".join(
            f'<option value="{d}"{" selected" if d == day else ""}>{d}</option>'
            for d in day_keys
        )
        day_picker = (
            f'<select id="dayPick">{day_opts}</select>'
            if day_keys else
            f'<span style="font-size:11px;color:var(--red)">{tw("no_hour_detail", lang)}</span>'
        )
        # 前端切換用:整月 + 每一天的時段數字
        hour_data = {"": {str(h["hour"]): h["count"] for h in cur["hourly"]}}
        for d_, v_ in hbd.items():
            hour_data[d_] = v_

        trend = "".join(
            f'<tr><td>{m["month"]}</td><td class="n">{m["visits"]:,}</td>'
            f'<td class="n">{len(m["daily"])}</td></tr>'
            for m in months
        )

        body = f"""
        <form method="get" style="margin-top:22px">
          {'<input type="hidden" name="lang" value="en">' if lang == "en" else ""}
          <select name="month" onchange="this.form.submit()">{opts}</select>
        </form>

        <div class="kpi">
          <div><span>{tw("total", lang)}</span><b>{cur['visits']:,}</b><em>{cur['month']}</em></div>
          <div><span>{tw("mom", lang)}</span><b style="font-size:19px">{delta}</b><em>{tw("mom_sub", lang)}</em></div>
          <div><span>{tw("avg", lang)}</span><b>{avg:.1f}</b><em>{tw("days", lang, n=len(vals))}</em></div>
          <div><span>{tw("hilo", lang)}</span><b>{max(vals)} / {min(vals)}</b><em>{tw("single_day", lang)}</em></div>
        </div>

        <h2>{tw("daily", lang)}</h2>
        {svg_bars([(d["date"][-2:], d["visits"]) for d in cur["daily"]], "#5C6B4F")}
        <p class="note">{tw("week_line", lang, a=a, an=an, b=b, bn=bn)}</p>

        <h2>{tw("by_hour", lang)} · {HOUR_START:02d}:00–{HOUR_END:02d}:59</h2>
        <div style="margin:-4px 0 12px">{day_picker}
          <span class="note" id="hourCap" style="margin-left:10px">{T["hour_sum"].format(cap=hour_caption, n=f"{hour_total:,}")}</span>
        </div>
        <div id="hourChart">{svg_bars(hour_pts, "#9C7A45", 120)}</div>
        <script>window.HOUR_DATA = {json.dumps(hour_data)};
        window.HOUR_RANGE = [{HOUR_START}, {HOUR_END}];
        window.HOUR_MONTH = "{cur["month"]}";
        window.HOUR_WHOLE = {json.dumps(T["whole_month"] + " " + cur["month"])};
        window.HOUR_SUMFMT = {json.dumps(T["hour_sum"].replace("{n:,}", "__N__").replace("{cap}", "__CAP__"))};</script>

        <h2>{tw("overview", lang)}</h2>
        <table><thead><tr><th>{tw("th_month", lang)}</th><th class="n">{tw("th_visits", lang)}</th><th class="n">{tw("th_days", lang)}</th></tr></thead>
        <tbody>{trend}</tbody></table>

        <h2>{tw("export", lang)}</h2>
        <a class="btn{' disabled' if preview else ''}" href="{'javascript:void(0)' if preview else f'/report.pdf{qs(lang)}'}">{tw("dl_pdf", lang)}</a>
        <a class="btn ghost{' disabled' if preview else ''}" href="{'javascript:void(0)' if preview else f'/data.json{qs(lang)}'}" style="margin-left:8px">{tw("dl_json", lang)}</a>
        {f'<div class="note" style="margin-top:10px;color:var(--muted)">{PREVIEW_NOTE[lang]}</div>' if preview else ''}
        """

    banner = f'<div class="msg{" err" if err else ""}">{msg}</div>' if msg else ""

    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CNNewsTraffic</title><style>{CSS}</style></head><body><div class="wrap">
{f'<div class="preview-bar">{PREVIEW_NOTE[lang]}</div>' if preview else ''}
<div style="text-align:right;margin-bottom:10px">
  <button id="langBtn" class="btn ghost" style="padding:5px 12px;font-size:11px">{T["other_lang"]}</button>
</div>
<header><h1>ETKAnews</h1><div class="sub">{tw("tagline", lang)}</div></header>
{banner}
<h2>{tw("upload", lang)}</h2>
<form class="drop" method="post" action="/upload{qs(lang)}" enctype="multipart/form-data"
      {'onsubmit="return false"' if preview else ''}>
  <input type="file" name="files" multiple accept=".pdf,.txt,.log" {"disabled" if preview else ""}>
  <button type="submit" {"disabled" if preview else ""} class="{'disabled' if preview else ''}">{tw("parse_btn", lang)}</button>
  <div style="font-size:11px;color:var(--muted);margin-top:10px">
    {tw("upload_hint", lang)}
  </div>
</form>
{body}
<footer>{tw("footer", lang, f=DATA.name, v=VERSION)}</footer>
</div>
<div id="tip"></div>
<script>window.I18N = {json.dumps(STRINGS, ensure_ascii=False)}; window.LANG = "{lang}";</script>
<script>
(function () {{
  var tip = document.getElementById("tip");
  document.addEventListener("mouseover", function (e) {{
    var t = e.target;
    if (!t.classList || !t.classList.contains("hit")) return;
    tip.innerHTML = t.dataset.l + "<b>" + Number(t.dataset.v).toLocaleString() + "</b>";
    tip.style.display = "block";
    // 同時把對應的長條加深
    var bars = t.ownerSVGElement.querySelectorAll(".bar-r");
    var hits = Array.prototype.slice.call(t.ownerSVGElement.querySelectorAll(".hit"));
    var i = hits.indexOf(t);
    bars.forEach(function (b, j) {{ b.style.opacity = j === i ? "1" : ".45"; }});
  }});
  document.addEventListener("mousemove", function (e) {{
    if (tip.style.display !== "block") return;
    var x = e.clientX + 14, y = e.clientY - 10;
    if (x + tip.offsetWidth > innerWidth - 8) x = e.clientX - tip.offsetWidth - 14;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }});
  document.addEventListener("mouseout", function (e) {{
    if (!e.target.classList || !e.target.classList.contains("hit")) return;
    tip.style.display = "none";
    e.target.ownerSVGElement.querySelectorAll(".bar-r").forEach(function (b) {{
      b.style.opacity = "1";
    }});
  }});

  // --- 時段圖:選日期即時重繪,不重新載入頁面 ---
  var pick = document.getElementById("dayPick");
  if (!pick || !window.HOUR_DATA) return;

  function niceScale(mx) {{
    mx = Math.max(Math.ceil(mx), 1);
    var best = null;
    for (var e = 0; e <= 6; e++) {{
      var mag = Math.pow(10, e);
      [1, 2, 2.5, 5].forEach(function (m) {{
        var step = m * mag;
        if (step < 1) return;
        var top = Math.ceil(mx / step) * step;
        var n = Math.round(top / step);
        if (n < 4 || n > 6) return;
        if (!best || top - mx < best[0] || (top - mx === best[0] && top < best[1])) {{
          best = [top - mx, top, step];
        }}
      }});
    }}
    if (!best) {{ var st = Math.max(1, Math.floor(mx / 4)); return [Math.ceil(mx / st) * st, st]; }}
    return [best[1], best[2]];
  }}

  window.drawHours = function (key) {{
    var counts = window.HOUR_DATA[key] || {{}};
    var lo = window.HOUR_RANGE[0], hi = window.HOUR_RANGE[1];
    var vals = [], total = 0;
    for (var h = lo; h <= hi; h++) {{
      var v = Number(counts[String(h)] || 0);
      vals.push([("0" + h).slice(-2), v]);
      total += v;
    }}
    var mx = Math.max.apply(null, vals.map(function (p) {{ return p[1]; }})) || 1;
    var sc = niceScale(mx), top = sc[0], step = sc[1];
    var W = 760, H = 120, PAD = {PAD_L}, plotW = W - PAD, bw = plotW / vals.length;
    var grid = "", rects = "", hits = "", labels = "";
    for (var t = 0; t <= top + 1e-9; t += step) {{
      var y = H - t / top * H;
      grid += '<line x1="' + PAD + '" y1="' + y.toFixed(1) + '" x2="' + W + '" y2="' +
        y.toFixed(1) + '" stroke="#CFC8B6" stroke-width="0.8"' +
        (t === 0 ? '' : ' stroke-dasharray="2 4"') + '/>';
      grid += '<text x="' + (PAD - 6) + '" y="' + (y + 3.5).toFixed(1) +
        '" font-size="10" fill="#8A8474" text-anchor="end" font-family="monospace">' +
        t + '</text>';
    }}
    vals.forEach(function (p, i) {{
      var bh = p[1] / top * H, x = PAD + i * bw;
      rects += '<rect class="bar-r" x="' + (x + bw * 0.15).toFixed(1) +
        '" y="' + (H - bh).toFixed(1) + '" width="' + (bw * 0.7).toFixed(1) +
        '" height="' + bh.toFixed(1) + '" fill="#9C7A45" pointer-events="none"/>';
      hits += '<rect class="hit" x="' + x.toFixed(1) + '" y="0" width="' +
        bw.toFixed(1) + '" height="' + H + '" fill="transparent" data-l="' +
        p[0] + ':00" data-v="' + p[1] + '"/>';
      labels += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H + 14) +
        '" font-size="10" fill="#8A8474" text-anchor="middle" font-family="monospace">' +
        p[0] + '</text>';
    }});
    document.getElementById("hourChart").innerHTML =
      '<svg class="chart" viewBox="0 -{PAD_T} ' + W + ' ' + (H + 22 + {PAD_T}) +
      '" width="100%" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">' +
      grid + rects + labels + hits + '</svg>';
    document.getElementById("hourCap").textContent = window.HOUR_SUMFMT
      .replace("__CAP__", key ? key : window.HOUR_WHOLE)
      .replace("__N__", total.toLocaleString());
  }};

  pick.addEventListener("change", function () {{ window.drawHours(pick.value); }});
}})();

// --- 中英即時切換 ---
(function () {{
  var btn = document.getElementById("langBtn");
  if (!btn || !window.I18N) return;

  function apply(lang) {{
    var D = window.I18N[lang];
    var attr = lang === "en" ? "en" : "zh";
    document.querySelectorAll("[data-zh]").forEach(function (el) {{
      var v = el.getAttribute("data-" + attr);
      if (v !== null) el.textContent = v;
    }});

    var opt = document.querySelector("#dayPick option[value='']");
    if (opt) opt.textContent = D.whole_month;

    window.HOUR_WHOLE = D.whole_month + " " + window.HOUR_MONTH;
    window.HOUR_SUMFMT = D.hour_sum.split("{{n}}").join("__N__").split("{{cap}}").join("__CAP__");

    var pick = document.getElementById("dayPick");
    if (typeof window.drawHours === "function") {{
      window.drawHours(pick ? pick.value : "");
    }}

    btn.textContent = D.other_lang;
    document.documentElement.lang = lang === "en" ? "en" : "zh-Hant";
    window.LANG = lang;

    document.querySelectorAll('a[href^="/report.pdf"], a[href^="/data.json"]').forEach(function (a) {{
      a.href = a.pathname + (lang === "en" ? "?lang=en" : "");
    }});
    var f = document.querySelector("form.drop");
    if (f) f.action = "/upload" + (lang === "en" ? "?lang=en" : "");
    var msel = document.querySelector('form select[name="month"]');
    if (msel) {{
      var form = msel.form, hid = form.querySelector('input[name="lang"]');
      if (lang === "en") {{
        if (!hid) {{
          hid = document.createElement("input");
          hid.type = "hidden"; hid.name = "lang"; form.appendChild(hid);
        }}
        hid.value = "en";
      }} else if (hid) {{ hid.remove(); }}
    }}
    try {{ localStorage.setItem("etka_lang", lang); }} catch (e) {{}}
  }}

  btn.addEventListener("click", function () {{
    apply(window.LANG === "en" ? "zh" : "en");
  }});

  try {{
    var saved = localStorage.getItem("etka_lang");
    if (saved && saved !== window.LANG) apply(saved);
  }} catch (e) {{}}
}})();
</script>
</body></html>"""


# ---------- 路由 ----------

@app.get("/", response_class=HTMLResponse)
def index(month: str = None, day: str = None, msg: str = None, err: int = 0, lang: str = "zh"):
    return render(load(), msg=msg, err=bool(err), sel=month, day=day, lang=lang)


@app.post("/upload")
async def upload(files: list[UploadFile] = File(...), lang: str = "zh"):
    T = STRINGS.get(lang, STRINGS["zh"])
    suffix = "&lang=en" if lang == "en" else ""
    records, names = [], []
    tmp = Path(tempfile.mkdtemp())
    try:
        for f in files:
            if not f.filename:
                continue
            p = tmp / f.filename
            p.write_bytes(await f.read())
            try:
                recs, _ = parse_records(read_text(p))
            except SystemExit as e:
                return RedirectResponse(f"/?msg={e}&err=1{suffix}", status_code=303)
            records.extend(recs)
            names.append(f"{f.filename} ({len(recs)})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if not records:
        return RedirectResponse(f"/?msg={T['no_events']}&err=1{suffix}", status_code=303)

    lo, hi = BUSINESS_HOURS if BUSINESS_HOURS else (0, 23)
    kept = [r for r in records if lo <= r["hour"] <= hi]
    dropped = len(records) - len(kept)

    months = merge(load(), aggregate(kept))
    save(months)

    msg = T["imported"].format(names=", ".join(names), n=len(kept))
    if dropped:
        msg += T["dropped"].format(n=dropped, lo=lo, hi=hi)
    return RedirectResponse(f"/?msg={msg}{suffix}", status_code=303)


@app.get("/report.pdf")
def report(lang: str = "zh"):
    T = STRINGS.get(lang, STRINGS["zh"])
    suffix = "&lang=en" if lang == "en" else ""
    months = load()
    if not months:
        return RedirectResponse(f"/?msg={T['no_data_pdf']}&err=1{suffix}", status_code=303)
    _write_file(months)  # 報告程式讀 data.json,先確保內容是最新的
    out = BASE / "etka_report.pdf"
    # 在子行程產生 PDF:matplotlib 在 web worker 執行緒中不穩定,會拖垮整個服務
    proc = subprocess.run(
        [sys.executable, str(BASE / "etka_report.py"), str(DATA),
         "-o", str(out), "--lang", "en" if lang == "en" else "zh"],
        capture_output=True, text=True, timeout=180,
    )
    # 把子行程的訊息轉印出來(字型下載進度、字型狀態等)
    for line in (proc.stdout or "").splitlines():
        if line.strip():
            print("  " + line)
    for line in (proc.stderr or "").splitlines()[-3:]:
        if line.strip():
            print("  ! " + line)
    if "未嵌入" in (proc.stdout or ""):
        print("  提醒:找不到可嵌入的中文字型,PDF 可能缺字。"
              "請執行 python3 etka_report.py --check-fonts 檢查。")
    if proc.returncode != 0 or not out.exists():
        detail = (proc.stderr or "").strip().splitlines()[-1:] or ["未知錯誤"]
        return RedirectResponse(f"/?msg={T['pdf_fail'].format(e=detail[0])}&err=1{suffix}", status_code=303)
    span = months[0]["month"] if len(months) == 1 else f"{months[0]['month']}_{months[-1]['month']}"
    return FileResponse(out, media_type="application/pdf",
                        filename=f"ETKAnews_{span}{'_EN' if lang == 'en' else ''}.pdf")


@app.get("/data.json")
def data_json():
    return JSONResponse(load())


@app.post("/reset")
def reset():
    clear()
    return RedirectResponse("/?msg=" + STRINGS["zh"]["cleared"], status_code=303)


def main():
    import argparse
    import socket

    ap = argparse.ArgumentParser(description="CNNewsTraffic — ETKA 入口網站閱讀量統計")
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 = 只有本機;0.0.0.0 = 開放同網路的人連線")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--version", action="version", version=f"CNNewsTraffic {VERSION}")
    ap.add_argument("--user", default="etka", help="登入帳號(需搭配 --password)")
    ap.add_argument("--password", default="", help="登入密碼;設了才會啟用驗證")
    ap.add_argument("--share", action="store_true",
                    help="等同 --host 0.0.0.0,開放同網路連線")
    a = ap.parse_args()

    host = "0.0.0.0" if a.share else a.host
    AUTH["user"], AUTH["password"] = a.user, a.password

    print()
    if host == "127.0.0.1":
        print(f"  CNNewsTraffic v{VERSION} — http://127.0.0.1:{a.port}")
        print("  僅限本機。要讓同事連線請加 --share --password 你的密碼")
    else:
        try:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sk.connect(("8.8.8.8", 80))
            ip = sk.getsockname()[0]
            sk.close()
        except Exception:
            ip = "你的內網IP"
        print(f"  CNNewsTraffic v{VERSION} — http://{ip}:{a.port}")
        print(f"  同網路的人可用上面網址連線(本機用 http://127.0.0.1:{a.port})")
        if a.password:
            print(f"  已啟用密碼保護,帳號:{a.user}")
        else:
            print("  ⚠ 沒有設密碼 —— 同網路任何人都能看到並修改資料!")
            print("    強烈建議加上 --password 你的密碼")
    print()
    uvicorn.run(app, host=host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
