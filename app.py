#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YT-DLP Studio - תוכנת שולחן עבודה להורדה מיוטיוב.
ממשק HTML מלא בתוך חלון pywebview - בלי שרת מקומי, בלי דפדפן חיצוני.

הפעלה מהמקור:  python app.py
בנייה ל-EXE בודד:  python build.py
"""

import ctypes
import hashlib
import json
import lzma
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import webview

try:
    import yt_dlp
except ImportError:
    print("שגיאה: הספרייה yt-dlp אינה מותקנת.\n  pip install -U yt-dlp")
    sys.exit(1)

APP_NAME = "הורדה ניידת מיוטיוב צול גאה"
APP_VERSION = "0.0.6"
UPDATE_REPO = "tsoolgee/youtube-downloader"
UA = "YT-DLP-Studio/" + APP_VERSION
NOTICE_API = "https://api.github.com/repos/%s/contents/notice.txt" % UPDATE_REPO
NOTICE_RAW = "https://raw.githubusercontent.com/%s/main/notice.txt" % UPDATE_REPO
IS_WIN = os.name == "nt"
HOME = os.path.expanduser("~")
FROZEN = getattr(sys, "frozen", False)

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or HOME, "YT-DLP Studio")
BIN_DIR = os.path.join(APP_DIR, "bin")
CFG_PATH = os.path.join(APP_DIR, "settings.json")
LOG_PATH = os.path.join(APP_DIR, "log.txt")
LEGACY_CFG = os.path.join(HOME, ".ytdlp_studio.json")


def res_path(*parts):
    """נתיב לקובץ שנארז יחד עם התוכנה (עובד גם מהמקור וגם מתוך EXE)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def app_base_dir():
    """התיקייה שבה יושבת התוכנה - ליד ה-EXE אחרי קימפול, אחרת ליד app.py."""
    return os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))


def _writable(d):
    try:
        probe = os.path.join(d, ".yts_write_test")
        with open(probe, "w"):
            pass
        os.remove(probe)
        return True
    except Exception:
        return False


_log_lock = threading.Lock()


def log(msg):
    """שורה ללוג. נשמר ב-%LOCALAPPDATA%\\YT-DLP Studio\\log.txt וניתן לפתיחה מההגדרות."""
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with _log_lock:
            os.makedirs(APP_DIR, exist_ok=True)
            if os.path.isfile(LOG_PATH) and os.path.getsize(LOG_PATH) > 512 * 1024:
                with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                    tail = f.readlines()[-500:]
                with open(LOG_PATH, "w", encoding="utf-8") as f:
                    f.writelines(tail)
            new_file = not os.path.isfile(LOG_PATH) or os.path.getsize(LOG_PATH) == 0
            with open(LOG_PATH, "a", encoding="utf-8-sig" if new_file else "utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass
    if not FROZEN:
        print(line, flush=True)


def default_download_dir():
    """כ-EXE: התיקייה של התוכנה עצמה. מהמקור או בתיקייה לא כתיבה: Downloads."""
    if FROZEN:
        d = app_base_dir()
        if os.path.isdir(d) and _writable(d):
            return d
    d = os.path.join(HOME, "Downloads")
    return d if os.path.isdir(d) else HOME


DEFAULT_SETTINGS = {
    "folder": default_download_dir(),
    "folderAuto": True,        # עוקב אחרי מיקום התוכנה עד שהמשתמש בוחר תיקייה
    "kind": "video",           # video | audio
    "quality": "1080",         # best|2160|1440|1080|720|480|360
    "container": "mp4",        # mp4|mkv|webm
    "acodec": "mp3",           # mp3|m4a|opus|flac|wav|best
    "abr": "192",
    "playlist": False,
    "subs": False,
    "sublangs": "he,en",
    "thumb": True,
    "metadata": True,
    "sponsorblock": False,
    "concurrency": 2,
    "ratelimit": "",
    "cookies": "",             # ''|chrome|edge|firefox|brave|opera|vivaldi
    "template": "%(title)s.%(ext)s",
    "perItem": False,          # איכות שונה לכל הורדה (כבוי כברירת מחדל)
    "theme": "dark",
    "ffmpeg": "",
    "insecureSSL": True,       # סינוני אינטרנט מחליפים תעודות - בלי זה החיבור נכשל
    "noticeSeen": "",          # מזהה ההתרעה האחרונה שנסגרה
}

ITEM_KEYS = ("kind", "quality", "container", "acodec", "abr", "playlist",
             "subs", "sublangs", "thumb", "metadata", "sponsorblock", "folder", "template")

URL_RE = re.compile(r"(?:https?://|www\.)[^\s,;\"'<>\]\[)(]+", re.I)
YT_ID_RE = re.compile(
    r"(?:v=|/shorts/|youtu\.be/|/embed/|/live/|/v/)([A-Za-z0-9_-]{11})")


def yt_thumb(url):
    """תמונה ממוזערת ישירות מהקישור - מוצגת מיד, בלי בקשת רשת מצד התוכנה."""
    m = YT_ID_RE.search(url or "")
    return "https://i.ytimg.com/vi/%s/mqdefault.jpg" % m.group(1) if m else ""


# ----------------------------------------------------------------------------- utils
def load_settings():
    s = dict(DEFAULT_SETTINGS)
    for path in (CFG_PATH, LEGACY_CFG):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in s:
                        s[k] = v
                break
        except Exception:
            continue
    if s.get("folderAuto", True):
        s["folder"] = default_download_dir()      # התוכנה זזה? ההורדות זזות איתה
    if not os.path.isdir(str(s.get("folder") or "")):
        s["folder"] = DEFAULT_SETTINGS["folder"]
    return s


def save_settings(s):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


SSL_LAX = ssl.create_default_context()
SSL_LAX.check_hostname = False
SSL_LAX.verify_mode = ssl.CERT_NONE


def apply_ssl_policy(insecure):
    """מתעלם מתעודות שגויות - נדרש מאחורי סינון שמחליף תעודות (נטפרי וכדומה).
    מוחל על urllib, על requests/urllib3 ועל curl_cffi שבהם yt-dlp משתמש."""
    try:
        ssl._create_default_https_context = (
            ssl._create_unverified_context if insecure else ssl.create_default_context)
    except Exception:
        pass
    if not insecure:
        for var in ("PYTHONHTTPSVERIFY", "CURL_SSL_NO_VERIFY"):
            os.environ.pop(var, None)
        return
    os.environ["PYTHONHTTPSVERIFY"] = "0"
    os.environ["CURL_SSL_NO_VERIFY"] = "1"
    try:                                   # שקט מאזהרות של urllib3 על חיבור לא מאומת
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass
    try:
        import warnings
        warnings.filterwarnings("ignore", message=".*[Vv]erif.*")
    except Exception:
        pass


def http_get(url, timeout=20, headers=None):
    """בקשת HTTP שלא נופלת על תעודה של סינון."""
    req = urllib.request.Request(url, headers=dict({"User-Agent": UA}, **(headers or {})))
    ctx = SSL_LAX if SETTINGS.get("insecureSSL", True) else None
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


ERRORS_HE = (
    # תעודות אבטחה נבדקות ראשונות: סינון שמחליף תעודה נראה כמו חסימה אבל אינו כזה
    ("certificate verify failed", "שגיאת תעודת אבטחה של הסינון — נסה שוב"),
    ("certificate_verify_failed", "שגיאת תעודת אבטחה של הסינון — נסה שוב"),
    ("ssl: ", "שגיאת תעודת אבטחה של הסינון — נסה שוב"),
    ("sslerror", "שגיאת תעודת אבטחה של הסינון — נסה שוב"),
    ("ssl certificate", "שגיאת תעודת אבטחה של הסינון — נסה שוב"),
    ("self-signed", "תעודת אבטחה של הסינון — נסה שוב"),
    ("self signed", "תעודת אבטחה של הסינון — נסה שוב"),
    ("unable to get local issuer", "תעודת אבטחה של הסינון — נסה שוב"),
    ("netfree", "נחסם על ידי נטפרי"),
    ("http error 418", "נחסם על ידי נטפרי"),
    ("error 418", "נחסם על ידי נטפרי"),
    ("blocked by", "הקישור חסום על ידי הסינון"),
    ("private video", "הסרטון פרטי"),
    ("members-only", "הסרטון פתוח למנויי הערוץ בלבד"),
    ("confirm your age", "הסרטון מוגבל בגיל — הפעל עוגיות מדפדפן בהגדרות"),
    ("age-restricted", "הסרטון מוגבל בגיל — הפעל עוגיות מדפדפן בהגדרות"),
    ("video unavailable", "הסרטון אינו זמין"),
    ("removed by the uploader", "הסרטון הוסר על ידי המעלה"),
    ("copyright", "הסרטון הוסר בגלל זכויות יוצרים"),
    ("not available in your country", "הסרטון חסום במדינה שלך"),
    ("requested format is not available", "האיכות שנבחרה לא קיימת לסרטון הזה"),
    ("http error 403", "יוטיוב דחה את ההורדה (403) — נסה שוב או הפעל עוגיות מדפדפן"),
    ("http error 404", "הקישור לא נמצא (404)"),
    ("http error 429", "יותר מדי בקשות ליוטיוב — המתן קצת ונסה שוב"),
    ("certificate", "שגיאת תעודת אבטחה של הסינון — נסה שוב"),
    ("timed out", "פג זמן החיבור"),
    ("timeout", "פג זמן החיבור"),
    ("no space left", "אין מקום פנוי בדיסק"),
    ("permission denied", "אין הרשאת כתיבה לתיקיית היעד"),
    ("unable to connect", "אין חיבור לאינטרנט"),
    ("name or service not known", "אין חיבור לאינטרנט"),
    ("getaddrinfo failed", "אין חיבור לאינטרנט"),
)


def friendly_error(msg):
    low = (msg or "").lower()
    for needle, he in ERRORS_HE:
        if needle in low:
            return he
    return msg


class Notice:
    """התרעה חופשית שהמפתח מפרסם בקובץ notice.txt בגיטהאב."""

    def __init__(self):
        self.lock = threading.Lock()
        self.data = {"id": "", "title": "", "text": "", "level": "info", "url": ""}

    def snapshot(self):
        with self.lock:
            return dict(self.data)

    def check_async(self, delay=0.0):
        threading.Thread(target=self._check, args=(delay,), daemon=True).start()

    def _check(self, delay=0.0):
        if delay:
            time.sleep(delay)
        raw = None
        # ה-API מחזיר את התוכן העדכני; raw נשמר בקאש של כמה דקות ומשמש כגיבוי
        for url, hdrs in ((NOTICE_API, {"Accept": "application/vnd.github.raw"}),
                          (NOTICE_RAW + "?t=" + str(int(time.time())), {"Cache-Control": "no-cache"})):
            try:
                with http_get(url, timeout=15, headers=hdrs) as r:
                    raw = r.read().decode("utf-8", "replace").strip()
                break
            except Exception:
                continue
        if raw is None:
            return
        d = {"id": "", "title": "", "text": "", "level": "info", "url": ""}
        if raw and raw not in ("-", "none", "null"):
            if raw.startswith("{"):
                try:
                    j = json.loads(raw)
                    d["title"] = str(j.get("title") or "")
                    d["text"] = str(j.get("text") or "")
                    d["level"] = str(j.get("level") or "info")
                    d["url"] = str(j.get("url") or "")
                except Exception:
                    d["text"] = raw
            else:
                d["text"] = raw
            if d["text"] or d["title"]:
                # hash() של פייתון משתנה בין הרצות - מזהה יציב כדי שסגירה תישאר סגורה
                d["id"] = hashlib.sha1((d["title"] + "\x00" + d["text"]).encode("utf-8")).hexdigest()[:12]
        with self.lock:
            self.data = d


def _exe(name):
    return name + ".exe" if IS_WIN else name


def locate_ffmpeg(custom=""):
    """מחזיר תיקייה שמכילה ffmpeg, או '' אם אין."""
    if custom:
        if os.path.isdir(custom) and os.path.isfile(os.path.join(custom, _exe("ffmpeg"))):
            return custom
        if os.path.isfile(custom):
            return os.path.dirname(custom)
    for d in (BIN_DIR, res_path("bin"), os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")):
        if os.path.isfile(os.path.join(d, _exe("ffmpeg"))):
            return d
    w = shutil.which("ffmpeg")
    return os.path.dirname(w) if w else ""


def unpack_ffmpeg(on_progress=None):
    """פורס את FFmpeg הארוז (vendor/*.xz) לתיקיית המשתמש - פעם אחת בהתקנה."""
    src = res_path("vendor")
    if not os.path.isdir(src):
        return ""
    try:
        os.makedirs(BIN_DIR, exist_ok=True)
        todo = []
        for name in sorted(os.listdir(src)):
            if not name.endswith(".xz"):
                continue
            out = os.path.join(BIN_DIR, name[:-3])
            if os.path.isfile(out) and os.path.getsize(out) > 0:
                continue
            todo.append((os.path.join(src, name), out))
        # הערכת גודל: LZMA על בינארי כזה מכווץ בערך פי ארבעה
        total = sum(os.path.getsize(p) for p, _ in todo) * 4 or 1
        done = 0
        for srcfile, out in todo:
            tmp = out + ".partial"
            with lzma.open(srcfile, "rb") as fin, open(tmp, "wb") as fout:
                while True:
                    chunk = fin.read(1024 * 1024)
                    if not chunk:
                        break
                    fout.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(min(99.0, done * 100.0 / total))
            os.replace(tmp, out)
        if on_progress:
            on_progress(100.0)
        return BIN_DIR if os.path.isfile(os.path.join(BIN_DIR, _exe("ffmpeg"))) else ""
    except Exception:
        return ""


def extract_urls(text):
    out, seen = [], set()
    for raw in URL_RE.findall(text or ""):
        u = raw.strip().rstrip(".,;")
        if u.lower().startswith("www."):
            u = "https://" + u
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def open_path(path):
    try:
        if IS_WIN:
            os.startfile(path)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


def reveal_file(path):
    try:
        if IS_WIN:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
        return True
    except Exception:
        return False


def read_clipboard():
    """קריאת טקסט מהלוח דרך Win32 - בלי tkinter ובלי תלויות."""
    if not IS_WIN:
        return ""
    CF_UNICODETEXT = 13
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u32.GetClipboardData.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u32.OpenClipboard(None):
        return ""
    try:
        if not u32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = k32.GlobalLock(h)
        try:
            return ctypes.c_wchar_p(p).value or ""
        finally:
            k32.GlobalUnlock(h)
    except Exception:
        return ""
    finally:
        u32.CloseClipboard()


def _vtuple(v):
    nums = re.findall(r"\d+", str(v or ""))
    return tuple(int(x) for x in nums[:4]) if nums else (0,)


class Updater:
    """בודק גרסה חדשה בגיטהאב ומחליף את ה-EXE בעצמו."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"current": APP_VERSION, "latest": "", "available": False,
                      "url": "", "notes": "", "busy": False, "percent": 0.0,
                      "error": "", "checked": False, "frozen": FROZEN}

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def check_async(self, delay=0.0):
        threading.Thread(target=self._check, args=(delay,), daemon=True).start()

    def _check(self, delay=0.0):
        if delay:
            time.sleep(delay)
        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/%s/releases/latest" % UPDATE_REPO,
                headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            tag = str(data.get("tag_name") or data.get("name") or "")
            asset = ""
            for a in data.get("assets") or []:
                if str(a.get("name", "")).lower().endswith(".exe"):
                    asset = a.get("browser_download_url") or ""
                    break
            newer = bool(asset) and _vtuple(tag) > _vtuple(APP_VERSION)
            with self.lock:
                self.state.update({"latest": tag.lstrip("vV"), "url": asset,
                                   "notes": (data.get("body") or "").strip()[:500],
                                   "available": newer, "checked": True, "error": ""})
        except Exception as e:
            with self.lock:
                self.state.update({"checked": True, "error": str(e)[:180]})

    def run_async(self):
        with self.lock:
            if self.state["busy"]:
                return False, "עדכון כבר רץ"
            if not self.state["available"] or not self.state["url"]:
                return False, "אין גרסה חדשה להתקנה"
            if not FROZEN:
                return False, "עדכון עצמי זמין רק בגרסת ה-EXE"
            self.state.update({"busy": True, "percent": 0.0, "error": ""})
            url = self.state["url"]
        threading.Thread(target=self._install, args=(url,), daemon=True).start()
        return True, ""

    def _install(self, url):
        tmp = os.path.join(tempfile.gettempdir(), "yts_update_%s.exe" % uuid.uuid4().hex[:8])
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = r.read(262144)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        with self.lock:
                            self.state["percent"] = min(99.0, done * 100.0 / total)
            if os.path.getsize(tmp) < 1024 * 1024:
                raise RuntimeError("הקובץ שהתקבל פגום")
            with self.lock:
                self.state["percent"] = 100.0
            self._swap(tmp, os.path.abspath(sys.executable))
        except Exception as e:
            try:
                os.remove(tmp)
            except Exception:
                pass
            with self.lock:
                self.state.update({"busy": False, "error": str(e)[:180]})

    @staticmethod
    def _swap(new, target):
        """סקריפט חיצוני קצר מחליף את הקובץ אחרי שהתוכנה נסגרת, ומפעיל מחדש."""
        ps = os.path.join(tempfile.gettempdir(), "yts_update_%s.ps1" % uuid.uuid4().hex[:8])
        q = lambda p: p.replace("'", "''")
        script = (
            "$ErrorActionPreference='SilentlyContinue'\r\n"
            "Wait-Process -Id %d -Timeout 90\r\n"
            "Start-Sleep -Milliseconds 600\r\n"
            "Copy-Item -LiteralPath '%s' -Destination '%s' -Force\r\n"
            "Remove-Item -LiteralPath '%s' -Force\r\n"
            "Start-Process -FilePath '%s'\r\n"
            "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force\r\n"
        ) % (os.getpid(), q(new), q(target), q(new), q(target))
        with open(ps, "w", encoding="utf-8-sig") as f:
            f.write(script)
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-WindowStyle", "Hidden", "-File", ps],
                         creationflags=0x08000000 if IS_WIN else 0)
        try:
            for w in list(webview.windows):
                w.destroy()
        except Exception:
            pass
        time.sleep(0.4)
        os._exit(0)


def clean_partials(it):
    """מוחק קבצי ביניים (.part/.ytdl) שנשארו אחרי ביטול הורדה."""
    cand = set()
    for base in (getattr(it, "tmpfile", ""), getattr(it, "dlname", ""),
                 getattr(it, "filepath", "")):
        if not base:
            continue
        cand.add(base)
        cand.add(base + ".part")
        cand.add(base + ".ytdl")
    for p in cand:
        if not (p.endswith(".part") or p.endswith(".ytdl")):
            continue
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass


class Canceled(Exception):
    pass


# ----------------------------------------------------------------------------- item
class Item:
    def __init__(self, url, opts):
        self.id = uuid.uuid4().hex[:10]
        self.url = url
        self.opts = opts
        self.status = "pending"   # pending|downloading|processing|done|error|canceled
        self.title = url
        self.uploader = ""
        self.duration = 0
        self.thumbnail = yt_thumb(url)
        self.percent = 0.0
        self.speed = 0
        self.eta = 0
        self.downloaded = 0
        self.total = 0
        self.stage = ""
        self.error = ""
        self.error_raw = ""
        self.filepath = ""
        self.tmpfile = ""
        self.dlname = ""
        self.playlist_index = 0
        self.playlist_count = 0
        self.added = time.time()
        self.finished = 0
        self.cancel = False

    def label(self):
        o = self.opts
        if o.get("kind") == "audio":
            c = str(o.get("acodec", "mp3"))
            extra = "" if c in ("best", "wav", "flac") else " %sk" % o.get("abr", "192")
            return "אודיו · %s%s" % (c.upper(), extra)
        q = str(o.get("quality", "best"))
        q = "מיטבית" if q == "best" else "%sp" % q
        return "וידאו · %s · %s" % (q, str(o.get("container", "mp4")).upper())

    def to_dict(self):
        return {
            "id": self.id, "url": self.url, "status": self.status, "title": self.title,
            "uploader": self.uploader, "duration": self.duration, "thumbnail": self.thumbnail,
            "percent": round(self.percent, 1), "speed": self.speed, "eta": self.eta,
            "downloaded": self.downloaded, "total": self.total, "stage": self.stage,
            "error": self.error, "errorRaw": self.error_raw,
            "filepath": self.filepath, "label": self.label(),
            "opts": self.opts, "pi": self.playlist_index, "pc": self.playlist_count,
        }


# ----------------------------------------------------------------------------- manager
class Manager:
    def __init__(self, settings):
        self.settings = settings
        self.items = []
        self.lock = threading.RLock()
        self.active = 0
        self.ffmpeg = locate_ffmpeg(settings.get("ffmpeg", ""))
        self.ff_busy = False
        self.ff_percent = 0.0
        self.probe_sem = threading.Semaphore(4)
        if not self.ffmpeg:
            self.ff_busy = True
            threading.Thread(target=self._prepare_ffmpeg, daemon=True).start()
        threading.Thread(target=self._dispatch, daemon=True).start()

    def _prepare_ffmpeg(self):
        def progress(p):
            self.ff_percent = p
        t0 = time.time()
        log("ffmpeg: לא נמצא, פורס את המצורף")
        try:
            self.ffmpeg = unpack_ffmpeg(progress) or locate_ffmpeg()
        except Exception as e:
            log("ffmpeg: פריסה נכשלה: %s" % e)
        finally:
            self.ff_busy = False
            log("ffmpeg: מוכן=%r אחרי %.1f שניות" % (self.ffmpeg or "אין", time.time() - t0))

    # ---------- public
    def add(self, entries):
        added = []
        with self.lock:
            for e in entries:
                url = (e.get("url") or "").strip()
                if not url:
                    continue
                it = Item(url, self.norm_opts(e.get("opts") or {}))
                self.items.append(it)
                added.append(it)
                log("תור: נוסף %s (%s)" % (url, it.label()))
        for it in added:
            threading.Thread(target=self._probe, args=(it,), daemon=True).start()
        return len(added)

    def norm_opts(self, raw):
        o = {}
        for k in ITEM_KEYS:
            o[k] = raw.get(k, self.settings.get(k, DEFAULT_SETTINGS.get(k)))
        if not o.get("folder"):
            o["folder"] = self.settings["folder"]
        if not o.get("template"):
            o["template"] = self.settings.get("template") or "%(title)s.%(ext)s"
        return o

    def _find(self, iid):
        for it in self.items:
            if it.id == iid:
                return it
        return None

    def get(self, iid):
        with self.lock:
            return self._find(iid)

    def update_opts(self, iid, raw):
        with self.lock:
            it = self._find(iid)
            if it and it.status in ("pending", "error", "canceled"):
                it.opts = self.norm_opts({**it.opts, **(raw or {})})
                return True
        return False

    def cancel(self, iid):
        with self.lock:
            it = self._find(iid)
            if not it:
                return False
            if it.status in ("downloading", "processing"):
                it.cancel = True
            elif it.status == "pending":
                it.status = "canceled"
                it.stage = "בוטל"
            return True

    def retry(self, iid):
        with self.lock:
            it = self._find(iid)
            if not it or it.status not in ("error", "canceled", "done"):
                return False
            it.status = "pending"
            it.cancel = False
            it.error = ""
            it.error_raw = ""
            it.percent = 0.0
            it.stage = ""
            it.speed = 0
            it.eta = 0
            it.downloaded = 0
            it.total = 0
            return True

    def remove(self, iid):
        with self.lock:
            it = self._find(iid)
            if not it:
                return False
            if it.status in ("downloading", "processing"):
                it.cancel = True
                return False
            self.items = [x for x in self.items if x.id != iid]
        clean_partials(it)
        return True

    def clear(self, which="done"):
        with self.lock:
            if which == "done":
                self.items = [x for x in self.items if x.status != "done"]
            elif which == "finished":
                self.items = [x for x in self.items if x.status not in ("done", "error", "canceled")]
            elif which == "all":
                for x in self.items:
                    if x.status in ("downloading", "processing"):
                        x.cancel = True
                self.items = [x for x in self.items if x.status in ("downloading", "processing")]

    def cancel_all(self):
        with self.lock:
            for x in self.items:
                if x.status in ("downloading", "processing"):
                    x.cancel = True
                elif x.status == "pending":
                    x.status = "canceled"
                    x.stage = "בוטל"

    def state(self):
        with self.lock:
            items = [i.to_dict() for i in self.items]
        return {
            "items": items,
            "settings": self.settings,
            "ffmpeg": bool(self.ffmpeg),
            "ffmpegPath": self.ffmpeg,
            "ffmpegBusy": self.ff_busy,
            "ffmpegPercent": round(self.ff_percent, 1),
            "active": self.active,
            "version": yt_dlp.version.__version__,
            "app": APP_VERSION,
            "update": UPD.snapshot(),
            "notice": NOTE.snapshot(),
        }

    # ---------- internals
    def _dispatch(self):
        while True:
            time.sleep(0.25)
            try:
                cap = max(1, int(self.settings.get("concurrency", 2)))
            except Exception:
                cap = 2
            if self.ff_busy:
                with self.lock:
                    for it in self.items:
                        if it.status == "pending":
                            it.stage = "ממתין לסיום הכנת FFmpeg"
                continue
            with self.lock:
                if self.active >= cap:
                    continue
                nxt = None
                for it in self.items:
                    if it.status == "pending":
                        nxt = it
                        break
                if not nxt:
                    continue
                nxt.status = "downloading"
                nxt.stage = "מתחיל..."
                self.active += 1
                log("הורדה: מתחיל %s" % nxt.url)
            threading.Thread(target=self._run, args=(nxt,), daemon=True).start()

    def _probe(self, it):
        with self.probe_sem:
            if it.status in ("done", "error", "canceled") or it.cancel:
                return
            try:
                opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                        "nocheckcertificate": bool(self.settings.get("insecureSSL", True)),
                        "noplaylist": not it.opts.get("playlist"),
                        "extract_flat": "in_playlist", "socket_timeout": 20}
                self._auth(opts)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(it.url, download=False)
                # ההורדה עצמה כבר עדכנה פרטים? לא דורסים אותם
                if not info or it.status in ("done", "error", "canceled"):
                    return
                fresh = it.title == it.url or not it.title
                if info.get("_type") == "playlist":
                    ents = [e for e in (info.get("entries") or []) if e]
                    if fresh:
                        it.title = info.get("title") or it.url
                    it.playlist_count = info.get("playlist_count") or len(ents)
                    it.uploader = it.uploader or info.get("uploader") or info.get("channel") or ""
                    it.thumbnail = (self._thumb(ents[0] if ents else None)
                                    or self._thumb(info) or it.thumbnail)
                else:
                    if fresh:
                        it.title = info.get("title") or it.url
                    it.uploader = it.uploader or info.get("uploader") or info.get("channel") or ""
                    it.duration = it.duration or int(info.get("duration") or 0)
                    it.thumbnail = self._thumb(info) or it.thumbnail
            except Exception:
                pass

    @staticmethod
    def _thumb(info):
        if not info:
            return ""
        t = info.get("thumbnail")
        if t:
            return t
        ts = [x for x in (info.get("thumbnails") or []) if x.get("url")]
        return ts[-1]["url"] if ts else ""

    def _auth(self, ydl_opts):
        cb = self.settings.get("cookies") or ""
        if cb:
            ydl_opts["cookiesfrombrowser"] = (cb,)

    def _variant_tag(self, it):
        """שני פריטים של אותו קישור באותה תיקייה חייבים שמות שונים."""
        o = it.opts
        with self.lock:
            twin = any(x is not it and x.url == it.url and x.status != "canceled"
                       and (x.opts.get("folder") or "") == (o.get("folder") or "")
                       and (x.opts.get("template") or "") == (o.get("template") or "")
                       for x in self.items)
        if not twin:
            return ""
        if o.get("kind") == "audio":
            return str(o.get("acodec", "mp3")).lower()
        q = str(o.get("quality", "best"))
        return "best" if q == "best" else q + "p"

    def _build(self, it):
        o = it.opts
        has_ff = bool(self.ffmpeg)
        folder = o.get("folder") or self.settings["folder"]
        os.makedirs(folder, exist_ok=True)
        tmpl = o.get("template") or "%(title)s.%(ext)s"
        tag = self._variant_tag(it)
        if tag:
            tmpl = "[%s] %s" % (tag, tmpl)
        if o.get("playlist"):
            tmpl = os.path.join("%(playlist_title,channel,uploader|Playlist)s",
                                "%(playlist_index|0)03d - " + tmpl)
        outtmpl = os.path.join(folder, tmpl)

        y = {
            "outtmpl": outtmpl,
            "quiet": True, "no_warnings": True, "noprogress": True,
            "ignoreerrors": False, "retries": 10, "fragment_retries": 10,
            "concurrent_fragment_downloads": 4,
            "noplaylist": not o.get("playlist"),
            "windowsfilenames": IS_WIN,
            "trim_file_name": 160,
            "overwrites": False, "continuedl": True,
            "progress_hooks": [lambda d: self._hook(it, d)],
            "postprocessor_hooks": [lambda d: self._pp_hook(it, d)],
        }
        if self.settings.get("insecureSSL", True):
            y["nocheckcertificate"] = True
            y["legacy_server_connect"] = True      # סינונים ישנים עם TLS legacy
        if has_ff:
            y["ffmpeg_location"] = self.ffmpeg
        rl = str(self.settings.get("ratelimit") or "").strip().lower()
        if rl:
            mult = 1
            if rl.endswith("k"):
                mult, rl = 1024, rl[:-1]
            elif rl.endswith("m"):
                mult, rl = 1024 * 1024, rl[:-1]
            try:
                y["ratelimit"] = int(float(rl) * mult)
            except Exception:
                pass
        self._auth(y)

        pps = []
        if o.get("kind") == "audio":
            y["format"] = "ba/b"
            if has_ff:
                codec = str(o.get("acodec", "mp3"))
                pp = {"key": "FFmpegExtractAudio", "preferredcodec": codec}
                if codec not in ("best", "wav", "flac"):
                    pp["preferredquality"] = str(o.get("abr", "192"))
                pps.append(pp)
        else:
            q = str(o.get("quality", "best"))
            hc = "" if q == "best" else "[height<=%s]" % q
            cont = str(o.get("container", "mp4"))
            if not has_ff:
                y["format"] = "b%s[ext=mp4]/b%s/b" % (hc, hc)
            elif cont == "mp4":
                y["format"] = "bv*%s[ext=mp4]+ba[ext=m4a]/bv*%s+ba/b%s/b" % (hc, hc, hc)
                y["merge_output_format"] = "mp4"
                # H.264/AAC מתנגן בכל נגן; בלי זה יוטיוב מגיש AV1
                y["format_sort"] = ["vcodec:h264", "acodec:aac"]
            else:
                y["format"] = "bv*%s+ba/b%s/b" % (hc, hc)
                y["merge_output_format"] = cont
            if o.get("subs"):
                y["writesubtitles"] = True
                y["writeautomaticsub"] = True
                y["subtitleslangs"] = [s.strip() for s in str(o.get("sublangs") or "he,en").split(",") if s.strip()]
                if has_ff:
                    pps.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})

        if has_ff and o.get("sponsorblock"):
            cats = ["sponsor", "selfpromo", "interaction"]
            pps.append({"key": "SponsorBlock", "categories": cats, "when": "after_filter"})
            pps.append({"key": "ModifyChapters", "remove_sponsor_segments": cats})
        if has_ff and o.get("metadata"):
            pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True})
        if has_ff and o.get("thumb"):
            y["writethumbnail"] = True
            pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
        if pps:
            y["postprocessors"] = pps
        return y

    def _hook(self, it, d):
        if d.get("tmpfilename"):
            it.tmpfile = d["tmpfilename"]
        if d.get("filename"):
            it.dlname = d["filename"]
        if it.cancel:
            raise Canceled()
        st = d.get("status")
        if st == "downloading":
            it.status = "downloading"
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            it.downloaded, it.total = done, total
            if total:
                it.percent = min(100.0, done * 100.0 / total)
            else:
                fi, fc = d.get("fragment_index"), d.get("fragment_count")
                if fi and fc:
                    it.percent = min(100.0, fi * 100.0 / fc)
            it.speed = int(d.get("speed") or 0)
            it.eta = int(d.get("eta") or 0)
            info = d.get("info_dict") or {}
            if info.get("playlist_index"):
                it.playlist_index = info.get("playlist_index") or 0
                it.playlist_count = info.get("n_entries") or it.playlist_count
            it.stage = "מוריד"
        elif st == "finished":
            it.percent = 100.0
            it.speed = 0
            it.status = "processing"
            it.stage = "מעבד"
            if d.get("filename"):
                it.filepath = d["filename"]
        elif st == "error":
            it.stage = "שגיאה"

    def _pp_hook(self, it, d):
        if it.cancel:
            raise Canceled()
        names = {"FFmpegExtractAudio": "ממיר אודיו", "EmbedThumbnail": "משבץ תמונה",
                 "FFmpegMetadata": "כותב מטא-דאטה", "Merger": "ממזג וידאו + אודיו",
                 "FFmpegEmbedSubtitle": "משבץ כתוביות", "ModifyChapters": "מסיר חסויות",
                 "SponsorBlock": "בודק SponsorBlock", "MoveFiles": "מסדר קבצים"}
        pp = d.get("postprocessor") or ""
        if d.get("status") == "started":
            it.status = "processing"
            it.stage = names.get(pp, "מעבד")
        elif d.get("status") == "finished":
            info = d.get("info_dict") or {}
            fp = info.get("filepath") or info.get("_filename")
            if fp:
                it.filepath = fp

    def _run(self, it):
        try:
            y = self._build(it)
            with yt_dlp.YoutubeDL(y) as ydl:
                info = ydl.extract_info(it.url, download=True)
            if it.cancel:
                raise Canceled()
            if info:
                it.title = info.get("title") or it.title
                if info.get("_type") != "playlist":
                    rd = info.get("requested_downloads") or []
                    if rd and rd[0].get("filepath"):
                        it.filepath = rd[0]["filepath"]
            it.status = "done"
            it.percent = 100.0
            it.stage = "הושלם"
            it.speed = 0
            it.eta = 0
            it.finished = time.time()
            if it.filepath and os.path.isfile(it.filepath):
                it.total = os.path.getsize(it.filepath)
            log("הורדה: הושלם %s -> %s" % (it.url, it.filepath or "?"))
        except Canceled:
            it.status = "canceled"
            it.stage = "בוטל"
            it.speed = 0
            clean_partials(it)
        except Exception as e:
            if it.cancel:
                it.status = "canceled"
                it.stage = "בוטל"
                clean_partials(it)
            else:
                msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "").strip()
                it.status = "error"
                it.error_raw = msg[:400]
                it.error = friendly_error(msg)[:400] or "שגיאה לא ידועה"
                log("הורדה: כשל %s :: %s" % (it.url, msg[:300]))
                it.stage = "שגיאה"
            it.speed = 0
        finally:
            with self.lock:
                self.active = max(0, self.active - 1)


# ----------------------------------------------------------------------------- api bridge
SETTINGS = load_settings()
apply_ssl_policy(SETTINGS.get("insecureSSL", True))
MGR = Manager(SETTINGS)
UPD = Updater()
NOTE = Notice()


WIN = None          # חלון pywebview. חייב להישאר מחוץ ל-Api: אובייקט שאינו מתודה
                    # על מחלקת ה-js_api גורם ל-pywebview לחשוף אפס פונקציות ל-JS.


class Api:
    """כל התקשורת בין ה-HTML לפייתון עוברת דרך rpc() - בלי HTTP ובלי פורטים.
    אסור להוסיף כאן תכונות שאינן מתודות."""

    def rpc(self, path, body=None):
        try:
            return self._route(path, body or {})
        except Exception as e:
            import traceback
            log("rpc %s נכשל: %s" % (path, traceback.format_exc()[-600:]))
            return {"__error": str(e)}

    def _route(self, path, d):
        if path == "/api/state":
            return MGR.state()

        if path == "/api/clipboard":
            return {"text": read_clipboard()}

        if path == "/api/add":
            entries = d.get("items") or []
            if not entries and d.get("text"):
                base = d.get("opts") or {}
                entries = [{"url": u, "opts": base} for u in extract_urls(d["text"])]
            return {"added": MGR.add(entries)}

        if path == "/api/settings":
            changed = False
            for k, v in (d or {}).items():
                if k not in DEFAULT_SETTINGS:
                    continue
                dv = DEFAULT_SETTINGS[k]
                if k == "concurrency":
                    try:
                        v = max(1, min(8, int(v)))
                    except Exception:
                        v = dv
                elif isinstance(dv, bool):
                    v = bool(v)
                elif isinstance(dv, str):
                    v = str(v if v is not None else "")
                SETTINGS[k] = v
                changed = True
            if "insecureSSL" in (d or {}):
                apply_ssl_policy(SETTINGS.get("insecureSSL", True))
            if "ffmpeg" in (d or {}):
                MGR.ffmpeg = locate_ffmpeg(SETTINGS.get("ffmpeg", ""))
            if "folder" in (d or {}):
                SETTINGS["folderAuto"] = False
            if "folder" in (d or {}) and SETTINGS.get("folder"):
                try:
                    os.makedirs(SETTINGS["folder"], exist_ok=True)
                except Exception:
                    SETTINGS["folder"] = DEFAULT_SETTINGS["folder"]
            if changed:
                save_settings(SETTINGS)
            return {"settings": SETTINGS}

        if path == "/api/folder":
            folder = ""
            try:
                picked = WIN.create_file_dialog(
                    webview.FOLDER_DIALOG, directory=SETTINGS.get("folder") or HOME)
                if picked:
                    folder = picked[0] if isinstance(picked, (list, tuple)) else str(picked)
            except Exception:
                folder = ""
            if folder:
                SETTINGS["folder"] = folder
                SETTINGS["folderAuto"] = False
                save_settings(SETTINGS)
            return {"folder": folder, "settings": SETTINGS}

        if path == "/api/openfolder":
            folder = SETTINGS.get("folder") or DEFAULT_SETTINGS["folder"]
            try:
                os.makedirs(folder, exist_ok=True)
            except Exception:
                pass
            return {"ok": open_path(folder)}

        if path == "/api/update":
            return {"ok": MGR.update_opts(d.get("id"), d.get("opts"))}
        if path == "/api/cancel":
            return {"ok": MGR.cancel(d.get("id"))}
        if path == "/api/retry":
            return {"ok": MGR.retry(d.get("id"))}
        if path == "/api/remove":
            return {"ok": MGR.remove(d.get("id"))}
        if path == "/api/clear":
            MGR.clear(d.get("which") or "done")
            return {"ok": True}
        if path == "/api/cancelall":
            MGR.cancel_all()
            return {"ok": True}

        if path in ("/api/open", "/api/reveal"):
            it = MGR.get(d.get("id"))
            if not it or not it.filepath:
                return {"ok": False}
            if not os.path.isfile(it.filepath):
                return {"ok": open_path(it.opts.get("folder") or SETTINGS["folder"])}
            return {"ok": open_path(it.filepath) if path == "/api/open" else reveal_file(it.filepath)}

        if path == "/api/log":
            log("ממשק: %s" % str(d.get("text") or "")[:400])
            return {"ok": True}

        if path == "/api/log/open":
            if not os.path.isfile(LOG_PATH):
                log("נפתח קובץ לוג ריק")
            return {"ok": open_path(LOG_PATH)}

        if path == "/api/notice/check":
            NOTE.check_async()
            return {"ok": True}

        if path == "/api/notice/dismiss":
            SETTINGS["noticeSeen"] = str(d.get("id") or "")
            save_settings(SETTINGS)
            return {"ok": True}

        if path == "/api/update/check":
            UPD.check_async()
            return {"ok": True}

        if path == "/api/update/run":
            ok, why = UPD.run_async()
            return {"ok": ok, "error": why}

        if path == "/api/openurl":
            url = str(d.get("url") or "")
            if url.startswith("http://") or url.startswith("https://"):
                webbrowser.open(url)
                return {"ok": True}
            return {"ok": False}

        return {"__error": "unknown route " + str(path)}


def close_splash(*_args, **_kw):
    """סוגר את מסך הטעינה של ה-EXE ברגע שהחלון האמיתי מוכן.
    מקבל ארגומנטים חופשיים כי pywebview מעביר את החלון למאזין."""
    try:
        import pyi_splash            # קיים רק בתוך ה-EXE
        if pyi_splash.is_alive():
            pyi_splash.close()
    except Exception:
        pass


def ui_file():
    """כותב עותק של הממשק לתיקיית המשתמש ומחזיר נתיב.
    טעינה מ-file:// אמינה הרבה יותר מהזרקת HTML ישירות - שם הגשר של pywebview
    לא נוצר בחלק מגרסאות WebView2."""
    src = res_path("ui.html")
    dst = os.path.join(APP_DIR, "ui.html")
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(src, "r", encoding="utf-8") as f:
            html = f.read()
        try:
            with open(dst, "r", encoding="utf-8") as f:
                same = f.read() == html
        except Exception:
            same = False
        if not same:
            with open(dst, "w", encoding="utf-8") as f:
                f.write(html)
        return dst
    except Exception as e:
        log("ui: כתיבה לתיקיית המשתמש נכשלה (%s), נופל חזרה למקור" % e)
        return src if os.path.isfile(src) else ""


def bridge_selftest(win, url):
    """ממתין שהגשר ייווצר, מתעד כמה זמן זה לקח, ומרענן רק אם הוא לא הגיע בכלל."""
    probe = "!!(window.pywebview && window.pywebview.api && window.pywebview.api.rpc)"

    def wait(limit):
        t0 = time.time()
        last = None
        while time.time() - t0 < limit:
            time.sleep(1.0)
            try:
                if win.evaluate_js(probe):
                    return time.time() - t0
                last = win.evaluate_js(
                    "[typeof window.pywebview, window.pywebview && window.pywebview.api ? "
                    "Object.keys(window.pywebview.api).join('|') : 'no-api', "
                    "location.href.slice(0,80), document.readyState].join(' ; ')")
            except Exception as e:
                last = "evaluate_js EXC: %s" % e
        if last:
            log("גשר: מצב אחרון -> %s" % last)
        return None

    took = wait(35.0)
    if took is not None:
        log("גשר: מוכן אחרי %.1f שניות" % took)
        return
    log("גשר: לא נוצר תוך 35 שניות, טוען את הממשק מחדש")
    try:
        win.load_url("file:///" + url.replace("\\", "/"))
    except Exception as e:
        log("גשר: טעינה מחדש נכשלה (%s)" % e)
        return
    took = wait(35.0)
    log("גשר: אחרי טעינה מחדש -> %s" %
        ("מוכן אחרי %.1f שניות" % took if took is not None else "עדיין לא זמין"))


def main():
    path = ui_file()
    if not path:
        log("שגיאה: ui.html לא נמצא")
        return

    log("=" * 60)
    log("הפעלה: גרסה %s | frozen=%s | yt-dlp %s" % (APP_VERSION, FROZEN, yt_dlp.version.__version__))
    log("נתיבים: exe=%s | הורדות=%s | ffmpeg=%r | ממשק=%s" % (
        (sys.executable if FROZEN else __file__), SETTINGS.get("folder"),
        MGR.ffmpeg or "בהכנה", path))

    global WIN
    api = Api()
    win = webview.create_window(
        APP_NAME, "file:///" + path.replace("\\", "/"), js_api=api,
        width=1220, height=900, min_size=(900, 620),
        background_color="#06080f", text_select=False,
    )
    WIN = win
    for event in ("shown", "loaded"):               # מה שיקרה קודם סוגר את מסך הטעינה
        try:
            getattr(win.events, event).__iadd__(close_splash)
        except Exception:
            pass
    threading.Timer(20.0, close_splash).start()    # רשת ביטחון אם האירוע לא נורה
    UPD.check_async(delay=4.0)
    NOTE.check_async(delay=2.0)
    threading.Thread(target=bridge_selftest, args=(win, path), daemon=True).start()
    try:
        webview.start(debug=os.environ.get("YTS_DEBUG") == "1")
        log("סגירה: החלון נסגר כרגיל")
    except Exception:
        import traceback
        log("קריסה בחלון הראשי:\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        log("קריסה בהפעלה:\n" + traceback.format_exc())
        raise
