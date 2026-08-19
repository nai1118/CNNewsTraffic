"""ETKAnews CN — 月度報告 PDF 產生器

由 etka_parse.py 呼叫,也可單獨使用:
    python3 etka_report.py etka_data.json -o report.pdf
"""

import io
import json
import sys
from datetime import date, datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate,
                               Spacer, Table, TableStyle)

# --- 配色(與 dashboard 一致)---
INK = colors.HexColor("#242320")
PAPER = colors.HexColor("#F1EEE6")
BRASS = colors.HexColor("#9C7A45")
SAGE = colors.HexColor("#5C6B4F")
RULE = colors.HexColor("#CFC8B6")
MUTED = colors.HexColor("#8A8474")

HOUR_START, HOUR_END = 8, 19

# 優先用可嵌入的 TrueType 中文字型;找不到就退回 reportlab 內建 CID 字型
TTF_CANDIDATES = [
    # macOS —— Arial Unicode 是真正的 TrueType 且含完整中日韓,最適合嵌入
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Supplemental/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Microsoft/MSJH.ttf",
    # Windows
    "C:/Windows/Fonts/msjh.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/mingliu.ttc",
    "C:/Windows/Fonts/simsun.ttc",
    # Linux
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]

# 這幾個字一定要有字型能畫,否則報告會缺字
PROBE = "總造訪數月度閱讀報告事件組成"

# 系統沒有可嵌入的中文字型時,下載這一份備用(思源黑體繁中,開源授權)
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf"
BUNDLED_FONT = Path(__file__).parent / "NotoSansTC.ttf"


def download_font(dest=BUNDLED_FONT, quiet=False):
    """下載可嵌入的中文字型到程式所在資料夾(約 12 MB,只需下載一次)。"""
    import urllib.request
    if not quiet:
        print(f"下載中文字型中… → {dest.name}")
    try:
        urllib.request.urlretrieve(FONT_URL, dest)
    except Exception as e:
        if not quiet:
            print(f"下載失敗:{e}")
        return None

    # 這是可變字型,預設字重是 Thin(100),直接用會過淡 —— 固定成 Regular
    try:
        from fontTools.ttLib import TTFont as _FT
        from fontTools.varLib.instancer import instantiateVariableFont
        vf = _FT(str(dest))
        if "fvar" in vf:
            instantiateVariableFont(vf, {"wght": 400}, inplace=True)
            vf.save(str(dest))
            if not quiet:
                print("  已固定字重為 Regular (400)")
    except Exception as e:
        if not quiet:
            print(f"  字重調整略過({e});字型仍可使用")

    if not quiet:
        print(f"完成({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def _try_font(path, name="CJK"):
    """嘗試註冊字型;.ttc 內可能有多個子字型,逐一試。"""
    for idx in range(0, 8):
        try:
            f = TTFont(name, path, subfontIndex=idx)
            # 確認真的畫得出中文,不是只註冊成功
            missing = [c for c in PROBE if f.face.charToGlyph.get(ord(c)) in (None, 0)]
            if missing:
                continue
            pdfmetrics.registerFont(f)
            return True
        except Exception:
            continue
    return False


def find_fonts(verbose=False):
    """回傳可用字型清單,供 --check-fonts 診斷。"""
    found = []
    for path in TTF_CANDIDATES:
        if Path(path).exists():
            ok = _try_font(path, name=f"probe{len(found)}")
            found.append((path, ok))
            if verbose:
                print(("  可用    " if ok else "  不支援  ") + path)
        elif verbose:
            pass
    return found


def setup_fonts(custom=None, allow_download=True):
    """挑一個可嵌入的中文字型。

    嵌入式字型最保險 —— PDF 走到哪台電腦都長一樣。
    順序:使用者指定 → 同資料夾備用字型 → 系統字型 → 自動下載 → CID(可能缺字)。
    """
    paths = ([custom] if custom else []) + [str(BUNDLED_FONT)] + TTF_CANDIDATES
    for path in paths:
        if path and Path(path).exists() and _try_font(path):
            return "CJK"

    # 系統上真的沒有可嵌入的中文字型,抓一份下來
    if allow_download and not BUNDLED_FONT.exists():
        if download_font() and _try_font(str(BUNDLED_FONT)):
            return "CJK"

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"))
        return "MSung-Light"
    except Exception:
        return "Helvetica"


FONT = setup_fonts(allow_download=False)
CJK_OK = FONT != "Helvetica"


LANG = "zh"          # 由 build() / CLI 設定


def set_lang(lang):
    """設定報告語言。沒有中文字型時一律用英文,避免變成方框。"""
    global LANG
    LANG = "en" if (lang == "en" or not CJK_OK) else "zh"
    return LANG


def L(zh, en):
    """依目前語言回傳對應文字。"""
    return en if LANG == "en" else zh


def _chart(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight",
                facecolor="#F1EEE6")
    plt.close(fig)
    buf.seek(0)
    return buf


def daily_chart(month):
    days = [d["date"][-2:] for d in month["daily"]]
    vals = [d["visits"] for d in month["daily"]]
    fig, ax = plt.subplots(figsize=(9, 2.8))
    ax.bar(days, vals, color="#5C6B4F", width=0.7)
    ax.set_facecolor("#F1EEE6")
    ax.grid(axis="y", color="#CFC8B6", linestyle=":", linewidth=0.7)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#CFC8B6")
    ax.tick_params(colors="#8A8474", labelsize=7)
    ax.set_xticks(range(0, len(days), 2))
    ax.set_xticklabels(days[::2])
    return _chart(fig)


def hourly_chart(month):
    counts = {h["hour"]: h["count"] for h in month["hourly"]}
    hours = list(range(HOUR_START, HOUR_END + 1))
    vals = [counts.get(h, 0) for h in hours]
    fig, ax = plt.subplots(figsize=(9, 2.4))
    ax.bar([f"{h:02d}" for h in hours], vals, color="#9C7A45", width=0.65)
    ax.set_facecolor("#F1EEE6")
    ax.grid(axis="y", color="#CFC8B6", linestyle=":", linewidth=0.7)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#CFC8B6")
    ax.tick_params(colors="#8A8474", labelsize=7)
    return _chart(fig)


def trend_chart(months):
    labels = [m["month"] for m in months]
    vals = [m["visits"] for m in months]
    fig, ax = plt.subplots(figsize=(9, 2.4))
    ax.plot(labels, vals, color="#9C7A45", linewidth=2, marker="o", markersize=4)
    ax.set_facecolor("#F1EEE6")
    ax.grid(axis="y", color="#CFC8B6", linestyle=":", linewidth=0.7)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#CFC8B6")
    ax.tick_params(colors="#8A8474", labelsize=8)
    ax.set_ylim(bottom=0)
    return _chart(fig)


def styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=ss["Title"], fontName=FONT,
                                fontSize=24, textColor=INK, spaceAfter=2),
        "sub": ParagraphStyle("s", parent=ss["Normal"], fontName=FONT, fontSize=9,
                              textColor=MUTED, alignment=TA_CENTER, spaceAfter=14),
        "h2": ParagraphStyle("h", parent=ss["Heading2"], fontName=FONT, fontSize=11,
                             textColor=INK, spaceBefore=14, spaceAfter=6),
        "body": ParagraphStyle("b", parent=ss["Normal"], fontName=FONT, fontSize=9,
                               textColor=INK, leading=14),
        "note": ParagraphStyle("n", parent=ss["Normal"], fontName=FONT, fontSize=8,
                               textColor=MUTED, leading=12),
    }


def kpi_table(month, prev, S):
    vals = [d["visits"] for d in month["daily"]]
    avg = sum(vals) / len(vals) if vals else 0
    delta = ""
    if prev:
        diff = month["visits"] - prev["visits"]
        pct = (diff / prev["visits"] * 100) if prev["visits"] else 0
        delta = f"{diff:+,} ({pct:+.1f}%)"
    else:
        delta = "—"

    heads = [L("總造訪數", "Total visits"), L("較上月", "vs. last month"),
             L("日均", "Daily avg"), L("最高 / 最低", "High / Low")]
    cells = [f"{month['visits']:,}", delta, f"{avg:.1f}",
             f"{max(vals)} / {min(vals)}" if vals else "—"]

    t = Table([heads, cells], colWidths=[42 * mm] * 4)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("FONTSIZE", (0, 1), (-1, 1), 15),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("TEXTCOLOR", (0, 1), (-1, 1), INK),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("LINEABOVE", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, 1), 0.6, RULE),
    ]))
    return t


def events_table(month, S):
    total = month["visits"] or 1
    rows = [[L("事件", "Event"), L("次數", "Count"), "%"]]
    for e in month["events"]:
        rows.append([e["name"], f"{e['total']:,}", f"{e['total']/total*100:.1f}%"])
    t = Table(rows, colWidths=[100 * mm, 34 * mm, 34 * mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def weekday_split(month):
    wd, we = [], []
    for d in month["daily"]:
        y, m, dd = map(int, d["date"].split("-"))
        (we if date(y, m, dd).weekday() >= 5 else wd).append(d["visits"])
    f = lambda a: sum(a) / len(a) if a else 0
    return f(wd), len(wd), f(we), len(we)


def paint_bg(canvas, doc):
    """整頁鋪底色,讓圖表與頁面融為一體(圖表 facecolor 也是同一個色)。"""
    canvas.saveState()
    canvas.setFillColor(PAPER)
    canvas.rect(0, 0, doc.pagesize[0], doc.pagesize[1], stroke=0, fill=1)
    canvas.restoreState()


def build(months, out_path, lang="zh"):
    global FONT, CJK_OK
    if FONT != "CJK" and lang != "en":
        # 中文報告卻沒有可嵌入字型 → 這時才嘗試下載
        FONT = setup_fonts()
        CJK_OK = FONT != "Helvetica"
    set_lang(lang)
    S = styles()
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="ETKAnews CN Readership Report",
    )
    story = []
    span = months[0]["month"] if len(months) == 1 else f"{months[0]['month']} – {months[-1]['month']}"

    story.append(Paragraph("ETKAnews", S["title"]))
    story.append(Paragraph(
        f"CN Edition · {L('月度閱讀報告', 'Monthly Readership Report')} · {span}", S["sub"]))

    if len(months) > 1:
        story.append(Paragraph(L("整體趨勢", "Overall trend"), S["h2"]))
        story.append(Image(trend_chart(months), width=170 * mm, height=45 * mm))

    for i, m in enumerate(months):
        if i > 0 or len(months) > 1:
            story.append(PageBreak())
        prev = months[i - 1] if i > 0 else None
        story.append(Paragraph(f"{m['month']}", S["h2"]))
        story.append(kpi_table(m, prev, S))
        story.append(Spacer(1, 12))

        story.append(Paragraph(L("每日流量", "Daily traffic"), S["h2"]))
        story.append(Image(daily_chart(m), width=170 * mm, height=53 * mm))

        a, an, b, bn = weekday_split(m)
        story.append(Paragraph(
            L(f"平日平均 {a:.1f}({an} 天) · 週末平均 {b:.1f}({bn} 天)",
              f"Weekday avg {a:.1f} ({an} days) · Weekend avg {b:.1f} ({bn} days)"),
            S["note"]))

        story.append(Paragraph(
            L(f"時段分布({HOUR_START:02d}:00–{HOUR_END:02d}:59)",
              f"By hour ({HOUR_START:02d}:00–{HOUR_END:02d}:59)"), S["h2"]))
        story.append(Image(hourly_chart(m), width=170 * mm, height=45 * mm))

        story.append(Paragraph(L("事件組成", "Event breakdown"), S["h2"]))
        story.append(events_table(m, S))

    story.append(Spacer(1, 14))
    story.append(Paragraph(
        L(f"由 etka_parse.py 產生 · {datetime.now():%Y-%m-%d %H:%M} · 數據取自系統日誌逐行計數",
          f"Generated by etka_parse.py · {datetime.now():%Y-%m-%d %H:%M} · Counted line-by-line from system logs"),
        S["note"]))

    doc.build(story, onFirstPage=paint_bg, onLaterPages=paint_bg)
    return out_path


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("json_file", nargs="?")
    ap.add_argument("-o", "--out", default="etka_report.pdf")
    ap.add_argument("--lang", choices=["zh", "en"], default="zh", help="報告語言")
    ap.add_argument("--font", help="指定中文字型檔 (.ttf/.ttc)")
    ap.add_argument("--check-fonts", action="store_true", help="檢查系統有哪些可用中文字型")
    ap.add_argument("--download-font", action="store_true", help="下載備用中文字型")
    a = ap.parse_args()

    if a.download_font:
        download_font()
        return

    if a.check_fonts:
        print("掃描系統中文字型…\n")
        found = find_fonts(verbose=True)
        usable = [p for p, ok in found if ok]
        print()
        if usable:
            print(f"找到 {len(usable)} 個可嵌入的字型,報告會使用:\n  {usable[0]}")
        else:
            print("系統上沒有可嵌入的中文字型。")
            if BUNDLED_FONT.exists():
                print(f"不過已有備用字型可用:{BUNDLED_FONT.name}")
            else:
                print("產生中文報告時會自動下載一份(約 12 MB,只需一次),")
                print("也可以現在先抓:python3 etka_report.py --download-font")
        return
    if not a.json_file:
        ap.error("需要指定 JSON 檔")
    if a.font:
        global FONT, CJK_OK
        FONT = setup_fonts(a.font)
        CJK_OK = FONT != "Helvetica"
    months = json.loads(Path(a.json_file).read_text(encoding="utf-8"))
    build(months, Path(a.out), lang=a.lang)
    print(f"字型:{FONT}" + ("(已嵌入)" if FONT == "CJK" else "(未嵌入,可能缺字)"))
    print(f"PDF → {Path(a.out).resolve()}")


if __name__ == "__main__":
    main()
