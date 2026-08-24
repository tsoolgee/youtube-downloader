#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
בונה את YT-DLP Studio לקובץ EXE בודד שרץ במחשב ללא פייתון וללא FFmpeg.

    python build.py                 # בנייה רגילה
    python build.py --ffmpeg PATH   # תיקייה שמכילה ffmpeg.exe/ffprobe.exe לצירוף
    python build.py --no-ffmpeg     # בלי לצרף FFmpeg (EXE קטן, דורש FFmpeg במחשב היעד)

התוצאה:  dist/הורדה ניידת מיוטיוב צול גאה.exe
"""

import argparse
import lzma
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(ROOT, "vendor")
NAME = "YT-DLP Studio"                      # שם הבנייה הפנימי של PyInstaller (חייב אנגלית)
DIST_NAME = "הורדה ניידת מיוטיוב צול גאה"     # שם הקובץ שהמשתמש מקבל
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


def make_splash():
    """תמונת מסך הטעינה שמוצגת בזמן שה-EXE מחלץ את עצמו (לפני שפייתון עולה)."""
    png = os.path.join(ROOT, "splash.png")
    if os.path.isfile(png):
        return png
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return ""
    W, H = 460, 200
    img = Image.new("RGB", (W, H), (8, 11, 20))
    d = ImageDraw.Draw(img)
    for y in range(H):                                   # רקע עם נגיעת גרדיאנט
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=(int(8 + 14 * t), int(11 + 16 * t), int(20 + 32 * t)))
    d.rectangle([0, 0, W - 1, H - 1], outline=(70, 60, 130))
    d.rectangle([0, 0, W - 1, 3], fill=(139, 92, 246))

    right = W - 34                                       # שוליים ימניים (פריסה RTL)
    ico = os.path.join(ROOT, "icon.ico")
    if os.path.isfile(ico):
        try:
            logo = Image.open(ico)
            logo.size = (48, 48)
            logo.load()
            logo = logo.convert("RGBA").resize((56, 56), Image.LANCZOS)
            img.paste(logo, (right - 56, 44), logo)
            right -= 56 + 18                             # הטקסט מתחיל משמאל לאייקון
        except Exception:
            pass

    def font(name, size):
        for f in (name, "segoeui.ttf", "arial.ttf"):
            try:
                return ImageFont.truetype(os.path.join(os.environ.get("WINDIR", "C:/Windows"),
                                                       "Fonts", f), size)
            except Exception:
                continue
        return ImageFont.load_default()

    # PIL מצייר משמאל לימין; היפוך התווים נותן סדר נכון לטקסט עברי
    rtl = lambda t: t[::-1]
    d.text((right, 46), rtl("יוטיוב הורדה"), font=font("segoeuib.ttf", 25),
           fill=(233, 238, 250), anchor="ra")
    d.text((right, 82), rtl("התוכנה נטענת"), font=font("segoeui.ttf", 17),
           fill=(152, 164, 192), anchor="ra")
    d.text((right, 110), rtl("רגע אחד, מכינים את הקבצים"), font=font("segoeui.ttf", 12),
           fill=(105, 116, 146), anchor="ra")

    d.rounded_rectangle([34, H - 46, W - 34, H - 40], radius=3, fill=(30, 36, 58))
    d.rounded_rectangle([34, H - 46, 34 + int((W - 68) * 0.42), H - 40], radius=3, fill=(124, 92, 246))
    img.save(png)
    log("נוצר splash.png")
    return png


def build(with_ffmpeg, icon, splash):
    sep = ";" if os.name == "nt" else ":"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--onefile", "--windowed", "--name", NAME,
           "--add-data", "ui.html" + sep + ".",
           "--collect-all", "yt_dlp",
           "--collect-all", "webview",
           "--hidden-import", "clr_loader",
           "--exclude-module", "matplotlib",
           "--exclude-module", "numpy",
           "--exclude-module", "PIL"]
    if with_ffmpeg:
        cmd += ["--add-data", "vendor" + sep + "vendor"]
    if icon:
        cmd += ["--icon", icon]
    if splash:
        cmd += ["--splash", splash]
    cmd.append("app.py")
    log("מריץ PyInstaller…")
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        sys.exit(r.returncode)
    ext = ".exe" if os.name == "nt" else ""
    built = os.path.join(ROOT, "dist", NAME + ext)
    out = os.path.join(ROOT, "dist", DIST_NAME + ext)
    if os.path.isfile(built) and os.path.abspath(built) != os.path.abspath(out):
        if os.path.isfile(out):
            os.remove(out)
        os.replace(built, out)
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
    build(ok, make_icon(), make_splash())
