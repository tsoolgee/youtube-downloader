#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
בונה את YT-DLP Studio לקובץ EXE בודד שרץ במחשב ללא פייתון וללא FFmpeg.

    python build.py                 # בנייה רגילה
    python build.py --ffmpeg PATH   # תיקייה שמכילה ffmpeg.exe/ffprobe.exe לצירוף
    python build.py --no-ffmpeg     # בלי לצרף FFmpeg (EXE קטן, דורש FFmpeg במחשב היעד)

התוצאה:  dist/YT-DLP Studio.exe
"""

import argparse
import lzma
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(ROOT, "vendor")
NAME = "YT-DLP Studio"
NEEDED = ("ffmpeg.exe", "ffprobe.exe") if os.name == "nt" else ("ffmpeg", "ffprobe")


def log(msg):
    print("  " + msg, flush=True)


def find_ffmpeg_dir(hint=""):
    cands = []
    if hint:
        cands.append(hint)
    cands.append(os.path.join(ROOT, "bin"))
    w = shutil.which("ffmpeg")
    if w:
        cands.append(os.path.dirname(os.path.realpath(w)))
    for d in cands:
        if d and os.path.isfile(os.path.join(d, NEEDED[0])):
            return d
    return ""


def prepare_vendor(hint=""):
    """דוחס את FFmpeg ל-vendor/*.xz. הקבצים נפרסים במחשב היעד בהפעלה הראשונה."""
    os.makedirs(VENDOR, exist_ok=True)
    have = [n for n in NEEDED if os.path.isfile(os.path.join(VENDOR, n + ".xz"))]
    if len(have) == len(NEEDED):
        total = sum(os.path.getsize(os.path.join(VENDOR, n + ".xz")) for n in NEEDED)
        log("vendor מוכן (%.1f MB דחוס)" % (total / 1048576))
        return True

    src = find_ffmpeg_dir(hint)
    if not src:
        log("לא נמצא FFmpeg לצירוף — בונה בלעדיו (--ffmpeg PATH כדי לצרף)")
        return False

    log("דוחס FFmpeg מתוך %s (לוקח כמה דקות, פעם אחת)" % src)
    for n in NEEDED:
        s = os.path.join(src, n)
        if not os.path.isfile(s):
            log("חסר %s — מדלג" % n)
            continue
        dst = os.path.join(VENDOR, n + ".xz")
        tmp = dst + ".partial"
        with open(s, "rb") as fin, lzma.open(tmp, "wb", preset=6) as fout:
            shutil.copyfileobj(fin, fout, 1024 * 1024)
        os.replace(tmp, dst)
        log("  %s: %.0f MB -> %.1f MB" % (n, os.path.getsize(s) / 1048576,
                                          os.path.getsize(dst) / 1048576))
    return os.path.isfile(os.path.join(VENDOR, NEEDED[0] + ".xz"))


def make_icon():
    """אייקון לתוכנה. אם Pillow לא מותקן פשוט מדלגים."""
    ico = os.path.join(ROOT, "icon.ico")
    if os.path.isfile(ico):
        return ico
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return ""
    S = 512
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(S):                                    # גרדיאנט סגול -> תכלת
        t = y / (S - 1)
        d.line([(0, y), (S, y)], fill=(int(139 + (34 - 139) * t),
                                       int(92 + (211 - 92) * t),
                                       int(246 + (238 - 246) * t), 255))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), fill=255)
    img.putalpha(mask)
    d = ImageDraw.Draw(img)
    w, cx = int(S * 0.105), S // 2
    d.line([(cx, int(S * 0.24)), (cx, int(S * 0.60))], fill="white", width=w)
    d.polygon([(cx - int(S * 0.17), int(S * 0.50)), (cx + int(S * 0.17), int(S * 0.50)),
               (cx, int(S * 0.74))], fill="white")
    d.rounded_rectangle([int(S * 0.24), int(S * 0.79), int(S * 0.76), int(S * 0.85)],
                        radius=int(S * 0.03), fill="white")
    img.save(ico, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    log("נוצר icon.ico")
    return ico


def build(with_ffmpeg, icon):
    sep = ";" if os.name == "nt" else ":"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--onefile", "--windowed", "--name", NAME,
           "--add-data", "ui.html" + sep + ".",
           "--collect-all", "yt_dlp",
           "--collect-all", "webview",
           "--hidden-import", "clr_loader",
           "--exclude-module", "tkinter",
           "--exclude-module", "matplotlib",
           "--exclude-module", "numpy",
           "--exclude-module", "PIL"]
    if with_ffmpeg:
        cmd += ["--add-data", "vendor" + sep + "vendor"]
    if icon:
        cmd += ["--icon", icon]
    cmd.append("app.py")
    log("מריץ PyInstaller…")
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        sys.exit(r.returncode)
    out = os.path.join(ROOT, "dist", NAME + (".exe" if os.name == "nt" else ""))
    if os.path.isfile(out):
        print("\n  מוכן: %s  (%.1f MB)" % (out, os.path.getsize(out) / 1048576))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", default="", help="תיקייה עם ffmpeg.exe/ffprobe.exe")
    ap.add_argument("--no-ffmpeg", action="store_true", help="לבנות בלי FFmpeg מצורף")
    a = ap.parse_args()
    print("=== בניית %s ===" % NAME)
    ok = False if a.no_ffmpeg else prepare_vendor(a.ffmpeg)
    build(ok, make_icon())
