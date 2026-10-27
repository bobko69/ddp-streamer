"""DDP Streamer - play videos, pictures, text, clocks and scripts on DDP LED
displays such as WLED matrices (DDP on UDP 4048). Works with any DDP receiver
that takes one RGB frame per push.

Run:   python ddp_streamer.py [files...]      (or double-click "DDP Streamer.bat")
Needs: the packages in requirements.txt (tkinter ships with Python).
Sound (optional): av, sounddevice. On Linux sounddevice also needs PortAudio
(e.g. "sudo apt install libportaudio2"). Without them video still streams, silently.
Settings are saved to %APPDATA%\\DDP Streamer\\settings.json (Linux: ~/.config/ddp-streamer,
Mac: ~/Library/Application Support/DDP Streamer).
"""
import collections, concurrent.futures, ctypes, gc, json, os, queue, select, socket, struct, sys, threading, time
import tempfile, traceback, urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Cameras on Windows: Media Foundation opens in ~1.5 s with this off instead of ~8 s
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")
import cv2
import numpy as np

import clock_styles
import datasources
import scripting

APP_NAME = "DDP Streamer"                 # also the settings folder name — keep
APP_VERSION = "1.0.0"                     # release version (build.py reads it for the installer)
WINDOW_TITLE = "Bong 69 DDP Streamer"     # title bar / taskbar
HERE = os.path.dirname(os.path.abspath(__file__))
LOGO_ICO = os.path.join(HERE, "bong69.ico")        # window / taskbar icon (Windows)
LOGO_PNG = os.path.join(HERE, "bong69.png")        # the same for Linux / Mac
CHUNK = 1440                    # 480 RGB pixels per packet (fits a 1500-byte MTU)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
FILE_TYPES = [("Video / image files", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.wmv *.mpg *.mpeg "
                                      "*.ts *.flv *.gif *.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff"),
              ("All files", "*.*")]
SCALE_MODES = ["Fill (crop)", "Fit (letterbox)", "Stretch"]
LOOP_MODES = ["Loop playlist", "Loop current", "Play once"]
OVERLAY_MODES = ["Off", "Small", "Large"]   # stats drawn on the picture: off / 1x / 2x font
OUTPUT_MODES = ["One display", "Mirror", "Span"]   # Mirror: same picture to every bridge;
                                                    # Span: one big picture split across them
CFG_FILE = os.path.join(datasources.DATA_DIR, "settings.json")
SAVE_KEY = ("<Command-s>", "Cmd+S") if sys.platform == "darwin" else ("<Control-s>", "Ctrl+S")
# Player button symbols. Linux fonts (DejaVu) have no ⏮⏭⏹⏸, so plainer ones there
SYM = (dict(prev="⏮", next="⏭", stop="⏹", pause="⏸")
       if os.name == "nt" or sys.platform == "darwin" else
       dict(prev="|◀", next="▶|", stop="■", pause="❚❚"))
DEFAULTS = dict(ip="", port=4048, width=128, height=64, fps=30,
                scale=SCALE_MODES[0], brightness=100, gamma=1.0, image_secs=10,
                loop=LOOP_MODES[0], pace=False, blank_on_stop=True, theme="night",
                audio_on=True, volume=80, audio_device="System default", sound_delay=0,
                outputs=OUTPUT_MODES[0], bridges=[], span_cols=2, span_gap_x=0, span_gap_y=0,
                overlay=OVERLAY_MODES[0], playlist=[], last_dir="", autoplay=False)
# Colours for the few plain-tk widgets and status texts; apply_theme() swaps
# in the dark set when the Sun Valley dark theme is active
COL = dict(ok="#2e7d32", warn="#c62828", muted="#777", faint="#555", playing="#cde8ff",
           tip_bg="#ffffe0", tip_fg="#000", canvas="#111")
COL_DARK = dict(ok="#6fcf73", warn="#ff6b6b", muted="#9a9a9a", faint="#8a8a8a", playing="#2f4f6f",
                tip_bg="#2b2b2b", tip_fg="#e6e6e6", canvas="#111")
VIDEO_LATENCY = 0.02            # s from sending a frame to the board showing it
STALL_SECS = 15                 # playing but nothing sent this long: the watchdog restarts the item
PREVIEW_W, PREVIEW_H = 512, 256      # starting size; the preview grows with the window


def load_settings():
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_settings(cfg):
    try:
        os.makedirs(os.path.dirname(CFG_FILE), exist_ok=True)
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


def wait_until(t):
    """Sleep until perf_counter() reaches t (sleep for the bulk, spin the last ~1.5 ms)."""
    while True:
        d = t - time.perf_counter()
        if d <= 0:
            return
        time.sleep(d - 0.0015 if d > 0.002 else 0)


def fmt_time(s):
    s = max(0, int(s))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# DDP output
# ---------------------------------------------------------------------------
class DdpSender:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
        self.seq = 0

    def send_frame(self, addr, data, spread_s=0.0):
        self.send_frames([(addr, data)], spread_s)

    def send_frames(self, frames, spread_s=0.0):
        """Send one RGB frame to each (addr, data); the last packet of each carries
        the PUSH flag. Those last packets are held back and sent together at the
        end, so several bridges show the new frame at (nearly) the same moment.
        spread_s > 0 spreads the packets (in groups of 8) over that many seconds."""
        pkts, last = [], []
        for addr, data in frames:
            n = len(data)
            mv = memoryview(data)
            for off in range(0, n, CHUNK):
                ln = min(CHUNK, n - off)
                (last if off + ln >= n else pkts).append((addr, mv, off, ln))
        n_last = len(pkts)              # index of the first held-back PUSH packet
        pkts += last
        n_groups = -(-len(pkts) // 8)
        gap = spread_s / n_groups if spread_s > 0 and n_groups > 1 else 0.0
        t = time.perf_counter()
        for i, (addr, mv, off, ln) in enumerate(pkts):
            if gap and i and i % 8 == 0 and i < n_last:
                t += gap
                wait_until(t)
            flags = 0x41 if i >= n_last else 0x40          # version 1, push on last
            self.seq = self.seq % 15 + 1
            hdr = struct.pack(">BBBBIH", flags, self.seq, 0x0B, 1, off, ln)  # 0x0B = RGB 8-bit
            self.sock.sendto(hdr + mv[off:off + ln], addr)


# ---------------------------------------------------------------------------
# Media sources
# ---------------------------------------------------------------------------
def _short_path(path):
    """8.3 path: OpenCV's video backend can't open some non-ASCII paths on Windows."""
    if os.name != "nt":
        return path
    buf = ctypes.create_unicode_buffer(1024)
    return buf.value if ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024) else path


# ---------------------------------------------------------------------------
# Live playlist items: a screen or window, a camera, text with an effect, a clock.
# Stored in the playlist as "screen:desktop", "screen:window:<title>",
# "camera:<json>", "text:<json>", "clock:<json>", "script:<json>"; they play for "Image s" seconds like a picture (or forever
# with Repeat = Loop current).
# ---------------------------------------------------------------------------
LIVE_PREFIXES = ("screen:", "camera:", "text:", "clock:", "script:", "test:")
COLOURS = {"White": (255, 255, 255), "Red": (0, 0, 255), "Green": (0, 220, 0), "Blue": (255, 80, 0),
           "Yellow": (0, 230, 255), "Cyan": (255, 230, 0), "Magenta": (255, 0, 255),
           "Orange": (0, 140, 255), "Rainbow": None}          # BGR
SPEEDS = {"Slow": 0.5, "Normal": 1.0, "Fast": 2.0}             # display heights per second


def is_live(path):
    return path.startswith(LIVE_PREFIXES)


def live_info(path):
    """Settings dict of a text/clock item ({} if unreadable)."""
    try:
        d = json.loads(path.split(":", 1)[1])
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


# Rotate / crop: any playlist entry can carry a view after a tab (not allowed in Windows file
# names; the live items' JSON never has one): '<item>\tview:{"rotate": 90, "crop": [x, y, w, h]}'.
# rotate = degrees clockwise; crop = part of the rotated picture, as fractions of its size.
VIEW_SEP = "\tview:"
ROTATIONS = {0: "None", 90: "90° right", 180: "180°", 270: "90° left"}
_CV_ROTATE = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def split_view(entry):
    """(item, view dict) of a playlist entry."""
    base, sep, js = entry.partition(VIEW_SEP)
    try:
        view = json.loads(js) if sep else {}
    except ValueError:
        view = {}
    return base, view if isinstance(view, dict) else {}


def join_view(base, view):
    view = {k: v for k, v in view.items() if v}
    return base + VIEW_SEP + json.dumps(view) if view else base


def apply_view(img, view):
    """Rotate, then crop a BGR frame per the item's view."""
    r = view.get("rotate", 0)
    if r in _CV_ROTATE:
        img = cv2.rotate(img, _CV_ROTATE[r])
    c = view.get("crop")
    if c and len(c) == 4:
        h, w = img.shape[:2]
        x0, y0 = min(w - 1, max(0, round(c[0] * w))), min(h - 1, max(0, round(c[1] * h)))
        x1, y1 = max(x0 + 1, min(w, round((c[0] + c[2]) * w))), max(y0 + 1, min(h, round((c[1] + c[3]) * h)))
        img = img[y0:y1, x0:x1]
    return img


def item_label(entry):
    """How a playlist entry is listed."""
    base, view = split_view(entry)
    extra = [ROTATIONS[r] for r in [view.get("rotate")] if r in _CV_ROTATE] + (["cropped"] if view.get("crop") else [])
    return _base_label(base) + (f"  [{', '.join(extra)}]" if extra else "")


def _base_label(path):
    if path == "screen:desktop":
        return "Screen: main screen"
    if path.startswith("screen:window:"):
        return "Window: " + path[len("screen:window:"):]
    if path.startswith("camera:"):
        d = live_info(path)
        name = "Camera: " + d["name"] if d.get("name") else f"Camera {d.get('index', 0) + 1}"
        return name + (" (mirrored)" if d.get("mirror") else "")
    if path.startswith("text:"):
        d = live_info(path)
        fx = d.get("effect", "Auto")
        return "Text" + ("" if fx == "Auto" else f" ({fx})") + ": " + d.get("text", "")
    if path.startswith("test:"):
        p = live_info(path).get("pattern", "Cycle")
        return "Test pattern: " + ("cycle (white, greys, red, green, blue)" if p == "Cycle" else p)
    if path.startswith("clock:"):
        d = live_info(path)
        look = d.get("look", "Plain")
        return ("Clock" + ("" if look == "Plain" else f": {look}") + (" + date" if d.get("date") and look == "Plain" else "")
                + f" ({d.get('style', '24 hour')})")
    if path.startswith("script:"):
        return "Script: " + script_title(live_info(path).get("file", ""))
    return os.path.basename(path)


def script_title(file):
    return os.path.splitext(os.path.basename(file))[0].replace("_", " ").capitalize()


if os.name == "nt":
    from ctypes import wintypes
    _u32, _g32 = ctypes.windll.user32, ctypes.windll.gdi32
    _H = ctypes.c_void_p
    for _f, _res, _args in (
            (_u32.GetDC, _H, [_H]), (_u32.ReleaseDC, ctypes.c_int, [_H, _H]),
            (_g32.CreateCompatibleDC, _H, [_H]), (_g32.CreateCompatibleBitmap, _H, [_H, ctypes.c_int, ctypes.c_int]),
            (_g32.SelectObject, _H, [_H, _H]), (_g32.DeleteObject, wintypes.BOOL, [_H]),
            (_g32.DeleteDC, wintypes.BOOL, [_H]),
            (_g32.CreateDIBSection, _H, [_H, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, _H, wintypes.DWORD]),
            (_g32.BitBlt, wintypes.BOOL, [_H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          _H, ctypes.c_int, ctypes.c_int, wintypes.DWORD]),
            (_g32.GetDIBits, ctypes.c_int, [_H, _H, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                            ctypes.c_void_p, wintypes.UINT]),
            (_u32.PrintWindow, wintypes.BOOL, [_H, _H, wintypes.UINT]),
            (_u32.GetClientRect, wintypes.BOOL, [_H, ctypes.POINTER(wintypes.RECT)]),
            (_u32.IsWindow, wintypes.BOOL, [_H]), (_u32.IsIconic, wintypes.BOOL, [_H]),
            (_u32.IsWindowVisible, wintypes.BOOL, [_H]),
            (_u32.GetWindowTextW, ctypes.c_int, [_H, wintypes.LPWSTR, ctypes.c_int]),
            (_u32.GetWindowTextLengthW, ctypes.c_int, [_H])):
        _f.restype, _f.argtypes = _res, _args

    class _BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]


# Linux / Mac: whole screens with mss (no single windows). Wayland doesn't let apps
# read the screen this way (it needs the desktop portal), so there it's off.
_mss = None
SCREEN_ERROR = None             # why screen capture can't work here, or None
if os.name != "nt":
    if sys.platform != "darwin" and (os.environ.get("XDG_SESSION_TYPE") == "wayland"
                                     or os.environ.get("WAYLAND_DISPLAY")):
        SCREEN_ERROR = ("Screen capture doesn't work in a Wayland session yet.\n"
                        "Log out, click the gear icon on the login screen and pick\n"
                        "\"Ubuntu on Xorg\" (or your desktop's X11 session).")
    else:
        try:
            import mss as _mss
        except ImportError:
            SCREEN_ERROR = "Screen capture needs the mss package: pip install mss"


def list_windows():
    """Titles of the visible top-level windows (not this app's), for the screen picker."""
    if os.name != "nt":
        return []
    titles = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, _H, _H)

    def cb(hwnd, _):
        cloaked = wintypes.DWORD(0)             # hidden system windows (DWMWA_CLOAKED)
        try:
            ctypes.windll.dwmapi.DwmGetWindowAttribute(_H(hwnd), 14, ctypes.byref(cloaked), 4)
        except OSError:
            pass
        if _u32.IsWindowVisible(hwnd) and not _u32.IsIconic(hwnd) and not cloaked.value:
            n = _u32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                _u32.GetWindowTextW(hwnd, buf, n + 1)
                t = buf.value.strip()
                if t and t not in titles and t not in (WINDOW_TITLE, "Program Manager"):
                    titles.append(t)
        return True
    _u32.EnumWindows(proto(cb), 0)
    return titles


def _find_window(title):
    """Window with this exact title, else one whose title starts the same way
    (browsers and players change the end of their title)."""
    if os.name != "nt":
        return None
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, _H, _H)

    def cb(hwnd, _):
        n = _u32.GetWindowTextLengthW(hwnd)
        if n and _u32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(n + 1)
            _u32.GetWindowTextW(hwnd, buf, n + 1)
            found.append((hwnd, buf.value))
        return True
    _u32.EnumWindows(proto(cb), 0)
    for hwnd, t in found:
        if t == title:
            return hwnd
    stem = title[:20]
    for hwnd, t in found:
        if stem and t.startswith(stem):
            return hwnd
    return None


class ScreenGrab:
    """The main screen, or one window's inside (even when other windows cover it).
    A copy of the screen waits for the next display refresh (~17 ms at 60 Hz),
    so a background thread keeps grabbing and latest() never waits.
    Linux / Mac: the main screen only, with mss."""

    def __init__(self, spec):
        if os.name != "nt" and SCREEN_ERROR:
            raise IOError(SCREEN_ERROR)
        self.title = spec[len("window:"):] if spec.startswith("window:") else None
        self.sct = threading.local()    # mss: one per thread (its X connection is per thread)
        self.hwnd = None
        self.dib = None
        self.black = np.zeros((8, 8, 3), np.uint8)
        self.frame = self.black
        self.running = False

    def latest(self):
        if not self.running:
            self.running = True
            self.frame = self.grab()
            threading.Thread(target=self._loop, daemon=True).start()
        return self.frame

    def _loop(self):
        while self.running:
            f = self.grab()
            self.frame = f
            if f is self.black:
                time.sleep(0.1)                 # window closed or minimised: check now and then
        self.close()

    def stop(self):
        self.running = False

    def _bitmap(self, w, h, fill):
        """Run fill(memory_dc, screen_dc) on a w x h bitmap and return it as BGR.
        The DIB section is kept between frames and its pixels read directly."""
        if self.dib is None or self.dib[0] != (w, h):
            self.close()
            screen = _u32.GetDC(None)
            mem = _g32.CreateCompatibleDC(screen)
            _u32.ReleaseDC(None, screen)
            bi = _BIH(ctypes.sizeof(_BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            bits = ctypes.c_void_p()
            bmp = _g32.CreateDIBSection(mem, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
            if not bmp:
                _g32.DeleteDC(mem)
                return None
            old = _g32.SelectObject(mem, bmp)
            view = np.ctypeslib.as_array(ctypes.cast(bits, ctypes.POINTER(ctypes.c_uint8)), shape=(h, w, 4))
            self.dib = ((w, h), mem, bmp, old, view)
        _, mem, _, _, view = self.dib
        screen = _u32.GetDC(None)
        try:
            ok = fill(mem, screen)
        finally:
            _u32.ReleaseDC(None, screen)
        return cv2.cvtColor(view, cv2.COLOR_BGRA2BGR) if ok else None

    def close(self):
        sct = getattr(self.sct, "mss", None)
        if sct is not None:
            sct.close()
            self.sct.mss = None
        if self.dib:
            _, mem, bmp, old, _ = self.dib
            _g32.SelectObject(mem, old)
            _g32.DeleteObject(bmp)
            _g32.DeleteDC(mem)
        self.dib = None

    def grab(self):
        if os.name != "nt":
            if self.title is not None:
                return self.black               # windows: Windows only
            try:
                sct = getattr(self.sct, "mss", None)
                if sct is None:
                    make = getattr(_mss, "MSS", None) or _mss.mss      # MSS: mss 10.2+
                    try:
                        sct = make()
                    except Exception:           # no libxcb-render & co.: the older Xlib way
                        sct = make(backend="xlib")
                    self.sct.mss = sct
                mons = sct.monitors             # [0] = all screens together, [1] = the main one
                shot = sct.grab(mons[1] if len(mons) > 1 else mons[0])
                return cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2BGR)
            except Exception:
                return self.black
        if self.title is None:
            w, h = _u32.GetSystemMetrics(0), _u32.GetSystemMetrics(1)
            img = self._bitmap(w, h, lambda mem, scr: _g32.BitBlt(mem, 0, 0, w, h, scr, 0, 0, 0x00CC0020))
            return img if img is not None else self.black
        if not self.hwnd or not _u32.IsWindow(self.hwnd):
            self.hwnd = _find_window(self.title)
        if not self.hwnd or _u32.IsIconic(self.hwnd):
            return self.black                   # closed or minimised: black until it's back
        r = wintypes.RECT()
        _u32.GetClientRect(self.hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 2 or h < 2:
            return self.black
        # PW_CLIENTONLY | PW_RENDERFULLCONTENT: the inside of the window, drawn by
        # the window itself, so it works when covered (browsers included)
        img = self._bitmap(w, h, lambda mem, scr: _u32.PrintWindow(self.hwnd, mem, 3))
        return img if img is not None else self.black


# Cameras: Media Foundation on Windows (DirectShow virtual cameras such as
# SplitCam crash OpenCV there), the system default elsewhere. Virtual cameras
# that only speak DirectShow (OBS, SplitCam...) are read by name through PyAV's
# FFmpeg instead: camera:{"name": "OBS Virtual Camera"}.
CAM_BACKEND = cv2.CAP_MSMF if os.name == "nt" else cv2.CAP_ANY


def dshow_virtual_cameras():
    """Names of the DirectShow virtual cameras (software devices; the real ones
    show up through Media Foundation). Windows with PyAV only."""
    if os.name != "nt" or av is None or "dshow" not in av.formats_available:
        return []
    import re
    from av import logging as avlog
    old = avlog.get_level()
    avlog.set_level(avlog.INFO)
    try:
        with avlog.Capture() as logs:
            try:
                av.open("dummy", format="dshow", options={"list_devices": "true"}).close()
            except Exception:
                pass                # it always "fails": the list is in the log
    finally:
        avlog.set_level(old)
    text = "".join(l[2] for l in logs if l[1] == "dshow")
    return re.findall(r'"([^"]+)" \(video\)\s*Alternative name "@device_sw_', text)


class DshowCamera:
    """A DirectShow camera by name, with VideoCapture's read() / release()."""

    def __init__(self, name):
        self.c = av.open("video=" + name, format="dshow", options={"rtbufsize": "64M"})
        self.frames = self.c.decode(self.c.streams.video[0])

    def read(self):
        try:
            return True, next(self.frames).to_ndarray(format="bgr24")
        except Exception:
            return False, None

    def release(self):
        self.c.close()


def open_camera(index):
    """An opened camera (cv2.VideoCapture at up to 1280x720 for a number, DshowCamera
    for a name), or None."""
    if isinstance(index, str):
        try:
            return DshowCamera(index)
        except Exception:
            return None
    cap = cv2.VideoCapture(index, CAM_BACKEND)
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    return cap


def list_cameras(limit=8):
    """[(index or name, first frame)] of the cameras that answer; numbers stop after two
    empty ones. Slow (each camera takes about a second to open): call it off the GUI thread."""
    found, misses = [], 0
    for i in range(limit):
        cap = open_camera(i)
        if cap is None:
            misses += 1
            if misses >= 2:
                break
            continue
        ok, f = cap.read()
        cap.release()
        if ok and f is not None:
            found.append((i, f))
    for name in dshow_virtual_cameras():
        cap = open_camera(name)
        if cap is not None:
            ok, f = cap.read()
            cap.release()
            if ok and f is not None:
                found.append((name, f))
    return found


class CameraGrab:
    """A USB or built-in camera. A background thread keeps reading, so latest()
    never waits; while the camera is missing (unplugged, in use) it shows black
    and tries again every 2 s."""

    def __init__(self, d):
        self.index = d.get("name") or int(d.get("index", 0))
        self.mirror = bool(d.get("mirror"))
        self.black = np.zeros((8, 8, 3), np.uint8)
        self.frame = self.black
        self.running = False

    def latest(self):
        if not self.running:
            self.running = True
            threading.Thread(target=self._loop, daemon=True).start()
        return self.frame

    def _loop(self):
        cap = None
        while self.running:
            if cap is None:
                cap = open_camera(self.index)
                if cap is None:
                    self.frame = self.black
                    time.sleep(2)
                    continue
            ok, f = cap.read()                  # waits for the camera's next frame
            if not ok or f is None:
                cap.release()
                cap = None
                self.frame = self.black
                continue
            self.frame = cv2.flip(f, 1) if self.mirror else f
        if cap is not None:
            cap.release()

    def stop(self):
        self.running = False


def _rainbow(img, t):
    """Colour lit pixels with a hue sweep across the width that drifts with time."""
    h, w = img.shape[:2]
    hue = ((np.arange(w) * 180 // max(1, w) + int(t * 40)) % 180).astype(np.uint8)
    hsv = np.empty((1, w, 3), np.uint8)
    hsv[0, :, 0], hsv[0, :, 1], hsv[0, :, 2] = hue, 255, 255
    row = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0]
    lit = img.max(axis=2, keepdims=True).astype(np.uint16)
    return (row[None, :, :].astype(np.uint16) * lit // 255).astype(np.uint8)


# Fonts for text and clock items: free fonts shipped in fonts/ (SIL OFL, DejaVu
# license; see fonts/licenses) that stay readable on a low-resolution LED wall,
# drawn with Pillow so they look the same on every system. name: (file, weight
# for a variable font or None), or None for the built-in OpenCV font.
FONTS = {"Built-in": None, "Archivo Black": ("ArchivoBlack-Regular.ttf", None),
         "Anton": ("Anton-Regular.ttf", None), "Inter Bold": ("Inter-Bold.ttf", 700),
         "DejaVu Sans Bold": ("DejaVuSans-Bold.ttf", None), "Noto Sans Bold": ("NotoSans-Bold.ttf", None),
         "Barlow Semi Condensed": ("BarlowSemiCondensed-SemiBold.ttf", None),
         "JetBrains Mono Bold": ("JetBrainsMono-Bold.ttf", None), "Comic Neue Bold": ("ComicNeue-Bold.ttf", None),
         "Press Start 2P": ("PressStart2P-Regular.ttf", None)}
PIXEL_FONTS = {"Press Start 2P"}         # drawn without smoothing, at whole multiples of its 8 px grid
# Items saved by older versions name Windows fonts: use the closest free font
OLD_FONTS = {"Arial Black": "Archivo Black", "Impact": "Anton", "Segoe UI Bold": "Inter Bold",
             "Verdana Bold": "DejaVu Sans Bold", "Tahoma Bold": "Noto Sans Bold",
             "Bahnschrift": "Barlow Semi Condensed", "Consolas Bold": "JetBrains Mono Bold",
             "Comic Sans Bold": "Comic Neue Bold"}
FONT_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "fonts")
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:                     # without Pillow every font falls back to the built-in one
    ImageFont = None


def font_name(name):
    name = OLD_FONTS.get(name, name)
    return name if name in FONTS else "Built-in"


_font_cache = {}


def _font(name, px):
    """Pillow font `name` at `px` pixels per em (cached), or None."""
    key = (name, px)
    if key not in _font_cache:
        f = None
        spec = FONTS.get(name)
        if spec and ImageFont is not None:
            try:
                f = ImageFont.truetype(os.path.join(FONT_DIR, spec[0]), px)
                if spec[1]:             # variable font: pick the weight axis value
                    axes = f.get_variation_axes()
                    f.set_variation_by_axes([spec[1] if str(a.get("name", "")).strip("b'") == "Weight"
                                             else a.get("default", a["minimum"]) for a in axes])
            except (OSError, ValueError):
                f = None
        _font_cache[key] = f
    return _font_cache[key]


def _font_metrics(name):
    """(cap height, top of tall letters, bottom of descenders) per pixel of em size,
    measured once at 100 px."""
    key = (name, "metrics")
    if key not in _font_cache:
        f = _font(name, 100)
        if f is None:
            _font_cache[key] = None
        else:
            cap = f.getbbox("H", anchor="ls")
            tall = f.getbbox("Hdlkfh", anchor="ls")
            low = f.getbbox("gjpqy", anchor="ls")
            _font_cache[key] = (max(1, -cap[1]) / 100, -tall[1] / 100, low[3] / 100)
    return _font_cache[key]


def _font_block(text, colour, height, name):
    """Text in a bundled font with capital letters `height` px tall, or None to use
    the built-in font. Every line of one size gets the same band from the tops of
    tall letters to the bottoms of descenders, so lines stack and align evenly."""
    name = font_name(name)
    m = _font_metrics(name) if FONTS.get(name) else None
    if m is None or not text.strip():
        return None
    cap, tall, low = m
    px = max(4, round(height / cap))
    pixel = name in PIXEL_FONTS
    if pixel:
        px = max(8, round(px / 8) * 8)
    f = _font(name, px)
    if f is None:
        return None
    x0, y0, x1, y1 = f.getbbox(text, anchor="ls")
    pad = max(1, int(height * 0.06))
    top = min(y0, -round(tall * px)) - pad              # band above the baseline, at least tall letters
    bottom = max(y1, round(low * px)) + pad
    w, h = max(1, x1 - x0 + 2 * pad), max(1, bottom - top)
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)
    if pixel:
        draw.fontmode = "1"
    draw.text((pad - x0, -top), text, font=f, fill=255, anchor="ls")
    mask = np.asarray(img, np.uint16)
    c = np.array(colour or (255, 255, 255), np.uint16)
    return (mask[:, :, None] * c // 255).astype(np.uint8)


def _text_block(text, colour, height, font="Built-in"):
    """Text drawn `height` px tall (cap height), on black, in `colour` (None: white)."""
    block = _font_block(text, colour, height, font)
    if block is not None:
        return block
    font = cv2.FONT_HERSHEY_DUPLEX
    (tw, th), base = cv2.getTextSize(text or " ", font, 1.0, 1)
    scale = height / max(1, th)
    thick = max(1, int(round(scale * 1.3)))
    (tw, th), base = cv2.getTextSize(text or " ", font, scale, thick)
    pad = thick
    img = np.zeros((th + base + 2 * pad, tw + 2 * pad, 3), np.uint8)
    cv2.putText(img, text, (pad, pad + th), font, scale, colour or (255, 255, 255), thick, cv2.LINE_AA)
    return img


def _place(canvas, block, cx, cy):
    """Copy block centred at (cx, cy), clipped to the canvas."""
    H, W = canvas.shape[:2]
    h, w = block.shape[:2]
    x0, y0 = int(cx - w / 2), int(cy - h / 2)
    sx, sy = max(0, -x0), max(0, -y0)
    ex, ey = min(w, W - x0), min(h, H - y0)
    if ex > sx and ey > sy:
        canvas[y0 + sy:y0 + ey, x0 + sx:x0 + ex] = np.maximum(canvas[y0 + sy:y0 + ey, x0 + sx:x0 + ex],
                                                              block[sy:ey, sx:ex])


TEXT_EFFECTS = ["Auto", "Scroll left", "Scroll up", "Fit (still)", "Wave", "Bounce",
                "Typewriter", "Blink", "Pulse", "Zoom"]
TEXT_SIZES = {"Small": 0.28, "Medium": 0.42, "Large": 0.6}     # capital height / display height


def _wrap(text, colour, height, font, max_w):
    """Words of `text` split into lines no wider than max_w (a long word gets a line of its own)."""
    lines, cur = [], ""
    for word in text.split():
        cand = (cur + " " + word).strip()
        if cur and _text_block(cand, colour, height, font).shape[1] > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    return lines + [cur] if cur else lines or [""]


def _layout(text, colour, font, W, H, max_frac):
    """Wrapped lines as big as fits the display, up to max_frac of its height.
    Returns (lines, line blocks, gap between lines)."""
    for frac in (0.8, 0.7, 0.6, 0.5, 0.42, 0.36, 0.3, 0.25, 0.2, 0.16, 0.13, 0.1, 0.07):
        if frac > max_frac + 1e-6:
            continue
        h = max(6, int(H * frac))
        lines = _wrap(text, colour, h, font, W * 0.94)
        blocks = [_text_block(l, colour, h, font) for l in lines]
        gap = max(1, h // 4)
        total = sum(b.shape[0] for b in blocks) + gap * (len(blocks) - 1)
        if max(b.shape[1] for b in blocks) <= W * 0.96 and total <= H * 0.96:
            break
    return lines, blocks, gap, h


def _stack(blocks, gap, width=None):
    """Line blocks centred under each other in one image (`width` wide, or as wide as the widest line)."""
    width = width or max(b.shape[1] for b in blocks)
    img = np.zeros((sum(b.shape[0] for b in blocks) + gap * (len(blocks) - 1), width, 3), np.uint8)
    y = 0
    for b in blocks:
        _place(img, b, width / 2, y + b.shape[0] / 2)
        y += b.shape[0] + gap
    return img


def _pingpong(v, span):
    """0 -> span -> 0 ... as v grows (span <= 0 gives 0)."""
    return 0.0 if span <= 0 else span - abs(v % (2 * span) - span)


class TextGen:
    """A text item with one of TEXT_EFFECTS. Auto: one line, centred if it fits,
    otherwise scrolling right to left."""

    def __init__(self, d, W, H):
        self.W, self.H = W, H
        self.colour = COLOURS.get(d.get("colour", "White"), (255, 255, 255))
        self.rainbow = self.colour is None
        self.effect = e = d.get("effect", "Auto") if d.get("effect") in TEXT_EFFECTS else "Auto"
        self.sp = SPEEDS.get(d.get("speed", "Normal"), 1.0)
        size = TEXT_SIZES.get(d.get("size", "Medium"), 0.42)
        text, font, colour = d.get("text", ""), d.get("font", "Built-in"), self.colour
        if e in ("Auto", "Scroll left", "Wave"):            # one line
            self.block = _text_block(text, colour, max(6, int(H * size)), font)
            bw = self.block.shape[1]
            self.scroll = e == "Scroll left" or bw > W * 0.95
            if self.scroll:                     # strip = text + one screen of gap, wraps round
                strip = np.zeros((self.block.shape[0], bw + W, 3), np.uint8)
                strip[:, :bw] = self.block
                self.strip = strip
            self.speed = self.sp * H            # px per second
        elif e == "Scroll up":                  # lines rolling up like film credits
            h = max(6, int(H * size))
            while True:                         # smaller if a single word is wider than the display
                lines = _wrap(text, colour, h, font, W * 0.94)
                blocks = [_text_block(l, colour, h, font) for l in lines]
                if h <= 6 or max(b.shape[1] for b in blocks) <= W * 0.96:
                    break
                h = max(6, int(h * 0.85))
            img = _stack(blocks, max(1, h // 4), W)
            self.strip = np.vstack([img, np.zeros((H, W, 3), np.uint8)])
            self.speed = self.sp * H * 0.5
        else:
            lines, blocks, gap, h = _layout(text, colour, font, W, H, 0.8 if e == "Fit (still)" else size)
            self.img = _stack(blocks, gap)
            if e == "Typewriter":               # reveal letter by letter: x where each letter ends
                self.lines, self.gap, self.cuts = blocks, gap, []
                for line, b in zip(lines, blocks):
                    n = len(line)
                    if n > 60:                  # long line: estimate instead of rendering every prefix
                        cuts = [b.shape[1] * (k + 1) // n for k in range(n)]
                    else:
                        cuts = []
                        for k in range(n):      # a space ends where the letter before it ends
                            cuts.append(cuts[-1] if line[k] == " " and cuts else
                                        min(b.shape[1], _text_block(line[:k + 1], colour, h, font).shape[1]))
                    self.cuts.append(cuts)
                self.nchars = sum(len(c) for c in self.cuts)

    def frame(self, t):
        W, H, e = self.W, self.H, self.effect
        out = np.zeros((H, W, 3), np.uint8)
        if e in ("Auto", "Scroll left", "Wave"):
            if self.scroll:
                n = self.strip.shape[1]
                cols = (int(t * self.speed) + np.arange(W)) % n
                _place(out, self.strip[:, cols], W / 2, H / 2)
            else:
                _place(out, self.block, W / 2, H / 2)
            if e == "Wave":                     # each column bobs on a travelling sine wave
                dy = (H * 0.12 * np.sin(np.arange(W) * (3 * np.pi / W) - t * 4 * self.sp)).astype(int)
                ys = np.arange(H)[:, None] - dy[None, :]
                ok = (ys >= 0) & (ys < H)
                out = out[np.clip(ys, 0, H - 1), np.arange(W)[None, :]]
                out[~ok] = 0
        elif e == "Scroll up":
            rows = (int(t * self.speed) + np.arange(H) - H) % self.strip.shape[0]   # starts below the screen
            out = self.strip[rows]
        elif e == "Bounce":                     # bounces off the edges; pans if wider than the display
            ih, iw = self.img.shape[:2]
            v = t * self.sp * H * 0.6
            x = _pingpong(v, W - iw) if iw <= W else -_pingpong(v, iw - W)
            y = _pingpong(t * self.sp * H * 0.45, H - ih)
            _place(out, self.img, x + iw / 2, y + ih / 2)
        elif e == "Typewriter":                 # type, hold 3 s, blank, again
            cps = 10 * self.sp
            cycle = self.nchars / cps + 3.5
            p = t % cycle
            if p < cycle - 0.5:
                left = int(p * cps)
                total = sum(b.shape[0] for b in self.lines) + self.gap * (len(self.lines) - 1)
                y = (H - total) / 2
                for b, cuts in zip(self.lines, self.cuts):
                    k = min(max(left, 0), len(cuts))
                    cut = cuts[k - 1] if k else 0
                    part = b.copy()
                    part[:, cut:] = 0
                    if 0 <= left < len(cuts) and (t * 2) % 1 < 0.6:   # blinking cursor
                        part[:, cut + 1:cut + 1 + max(1, b.shape[0] // 8)] = self.colour or (255, 255, 255)
                    _place(out, part, W / 2, y + b.shape[0] / 2)
                    left -= len(cuts)
                    y += b.shape[0] + self.gap
        elif e == "Zoom":                       # grow in, hold, shrink away
            cycle = 4.0 / self.sp
            p = t % cycle
            k = max(0.0, min(1.0, p / 0.5, (cycle - p) / 0.4))
            k = k * k * (3 - 2 * k)             # smooth start and stop
            ih, iw = self.img.shape[:2]
            if k > 0.02:
                z = cv2.resize(self.img, (max(1, int(iw * k)), max(1, int(ih * k))), interpolation=cv2.INTER_AREA)
                _place(out, z, W / 2, H / 2)
        else:                                   # Fit (still), Blink, Pulse
            _place(out, self.img, W / 2, H / 2)
            if e == "Blink" and (t * self.sp) % 1 > 0.65:
                out[:] = 0
        if self.rainbow:
            out = _rainbow(out, t)
        if e == "Pulse":
            k = 0.15 + 0.85 * (0.5 + 0.5 * np.cos(t * np.pi * self.sp))
            out = (out * k).astype(np.uint8)
        return out


CLOCK_LOOKS = ["Plain"] + list(clock_styles.STYLES)
STYLE_COLOURS = "Style colors"     # an animated look's own colors


# Test patterns for matching displays (Displays > Edit... brightness / color balance):
# one plain color over the whole picture, so every display shows the same thing
TEST_COLOURS = {"White": (255, 255, 255), "Grey 50%": (128, 128, 128), "Grey 25%": (64, 64, 64),
                "Red": (0, 0, 255), "Green": (0, 255, 0), "Blue": (255, 0, 0)}          # BGR
TEST_PATTERNS = ("Cycle",) + tuple(TEST_COLOURS)
TEST_CYCLE_SECS = 4


class TestGen:
    """'Cycle' steps through TEST_COLOURS every TEST_CYCLE_SECS; any other
    pattern is that one color, held."""

    def __init__(self, d, W, H):
        self.W, self.H = W, H
        p = d.get("pattern", "Cycle")
        self.pattern = p if p in TEST_PATTERNS else "Cycle"
        self.frames = {name: np.full((H, W, 3), c, np.uint8) for name, c in TEST_COLOURS.items()}

    def frame(self, t):
        if self.pattern != "Cycle":
            return self.frames[self.pattern]
        names = list(TEST_COLOURS)
        return self.frames[names[int(t // TEST_CYCLE_SECS) % len(names)]]


class ClockGen:
    """Time (and optionally the date) centred, as big as fits; or one of the
    animated looks from clock_styles (hours and minutes, animated changes)."""

    def __init__(self, d, W, H):
        self.W, self.H = W, H
        self.twelve = d.get("style", "24 hour") == "12 hour"
        look = d.get("look", "Plain")
        cname = d.get("colour", STYLE_COLOURS if look != "Plain" else "White")
        self.anim = None
        if look in clock_styles.STYLES:
            col = None if cname == STYLE_COLOURS else ("rainbow" if cname == "Rainbow" else COLOURS.get(cname))
            self.anim = clock_styles.STYLES[look](W, H, col)
            self.t0 = time.monotonic()
            return
        self.colour = COLOURS.get(cname, (255, 255, 255))
        self.secs = bool(d.get("seconds"))
        self.date = bool(d.get("date"))
        self.font = d.get("font", "Built-in")
        self.cache = (None, None)

    def _fit(self, text, max_w, max_h):
        b = _text_block(text, self.colour, max(6, int(max_h)), self.font)
        if b.shape[1] > max_w:
            b = _text_block(text, self.colour, max(6, int(max_h * max_w / b.shape[1])), self.font)
        return b

    def frame(self, t):
        now = time.localtime()
        if self.anim is not None:
            hhmm = time.strftime(("%I" if self.twelve else "%H") + ":%M", now)
            if self.twelve and hhmm[0] == "0":
                hhmm = " " + hhmm[1:]
            return self.anim.frame(time.monotonic() - self.t0, hhmm, now.tm_sec)
        fmt = ("%I" if self.twelve else "%H") + ":%M" + (":%S" if self.secs else "")
        text = time.strftime(fmt, now)
        if self.twelve:
            text = text.lstrip("0") + time.strftime(" %p", now)
        key = text + (time.strftime("%a %b %d", now) if self.date else "")
        if key != self.cache[0]:
            out = np.zeros((self.H, self.W, 3), np.uint8)
            if self.date:
                tb = self._fit(text, self.W * 0.92, self.H * 0.42)
                db = self._fit(time.strftime("%a %b %d", now), self.W * 0.92, self.H * 0.2)
                gap = self.H * 0.08
                total = tb.shape[0] + gap + db.shape[0]
                y = (self.H - total) / 2
                _place(out, tb, self.W / 2, y + tb.shape[0] / 2)
                _place(out, db, self.W / 2, y + tb.shape[0] + gap + db.shape[0] / 2)
            else:
                _place(out, self._fit(text, self.W * 0.92, self.H * 0.5), self.W / 2, self.H / 2)
            self.cache = (key, out)
        out = self.cache[1]
        return _rainbow(out, t) if self.colour is None else out


class Source:
    def __init__(self, path, size=(256, 128)):
        self.path = path
        self.image = None
        self.cap = None
        self.video = None       # AvVideo, or cap (OpenCV) without PyAV
        self.last = None
        self.live = None        # live item: called with t, returns a BGR frame
        self.timed = False      # ends after "Image s" seconds (pictures and live items)
        self.pos = 0            # video: index of the next frame grab() will return
        if is_live(path):
            W, H = size
            if path.startswith("screen:"):
                self.grabber = ScreenGrab(path[len("screen:"):])
                self.live = lambda t: self.grabber.latest()
            elif path.startswith("camera:"):
                self.grabber = CameraGrab(live_info(path))
                self.live = lambda t: self.grabber.latest()
            elif path.startswith("text:"):
                self.live = TextGen(live_info(path), W, H).frame
            elif path.startswith("script:"):
                self.gen = scripting.ScriptGen(live_info(path), W, H)
                self.live = self.gen.frame
            elif path.startswith("test:"):
                self.live = TestGen(live_info(path), W, H).frame
                self.endless = True     # stays up until Stop or another item
            else:
                self.live = ClockGen(live_info(path), W, H).frame
            self.timed = True
            self.fps = 0.0
            self.duration = 0.0
            self.last_t = None
            return
        if os.path.splitext(path)[1].lower() in IMAGE_EXT:
            self.timed = True
            img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                raise IOError("can't decode image")
            self.image = img
            self.fps = 0.0
            self.duration = 0.0
            return
        if av is not None:              # same FFmpeg as the sound (the build has no OpenCV FFmpeg)
            try:
                self.video = AvVideo(path)
                self.fps, self.duration = self.video.fps, self.video.duration
                return
            except Exception:
                self.video = None
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            cap = cv2.VideoCapture(_short_path(path))
        if not cap.isOpened():
            raise IOError("can't open video")
        self.cap = cap
        fps = cap.get(cv2.CAP_PROP_FPS)
        self.fps = fps if 1 <= fps <= 240 else 30.0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        self.duration = n / self.fps if n > 0 else 0.0

    def frame_at(self, t):
        """BGR frame for media time t (seconds), or None at end of video."""
        if self.image is not None:
            return self.image
        if self.live is not None:
            if t != self.last_t:                # paused: same t, keep the frame still
                self.last, self.last_t = self.live(t), t
                self.pos += 1                   # new frame: the player re-renders it
            return self.last
        if self.video is not None:
            f = self.video.frame_at(t)
            self.pos = self.video.n
            return f
        idx = max(0, int(t * self.fps + 1e-6))
        # Seek for backwards jumps or big forward jumps; otherwise grab() forward
        if idx < self.pos - 1 or idx > self.pos + 2 * self.fps:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            self.pos = idx
            self.last = None
        need = self.last is None
        while self.pos <= idx:
            if not self.cap.grab():
                return None
            self.pos += 1
            need = True
        if need:
            ok, f = self.cap.retrieve()
            if not ok:
                return None
            self.last = f
        return self.last

    def close(self):
        if self.cap is not None:
            self.cap.release()
        if self.video is not None:
            self.video.close()
        if getattr(self, "grabber", None) is not None:
            self.grabber.stop()


# Stats overlay: a 3x5 pixel font, crisp on an LED wall at 1:1 (rows of 3 bits per glyph)
_GLYPHS = {
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
    "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001001001001",
    "8": "111101111101111", "9": "111101111001111", "A": "010101111101101", "B": "110101110101110",
    "C": "011100100100011", "D": "110101101101110", "E": "111100110100111", "F": "111100110100100",
    "G": "011100101101011", "H": "101101111101101", "I": "111010010010111", "J": "001001001101010",
    "K": "101101110101101", "L": "100100100100111", "M": "101111111101101", "N": "110101101101101",
    "O": "010101101101010", "P": "110101110100100", "Q": "010101101110011", "R": "110101110101101",
    "S": "011100010001110", "T": "111010010010010", "U": "101101101101111", "V": "101101101101010",
    "W": "101101111111101", "X": "101101010101101", "Y": "101101010010010", "Z": "111001010100111",
    " ": "000000000000000", ".": "000000000000010", ":": "000010000010000", "/": "001001010100100",
    "-": "000000111000000", "%": "101001010100101", "+": "000010111010000",
}
_GLYPH_MASKS = {c: np.array([b == "1" for b in g]).reshape(5, 3) for c, g in _GLYPHS.items()}


def stats_mask(lines, scale=1):
    """Boolean mask of `lines` in the 3x5 font (1 px between letters and lines), or None."""
    lines = [l.upper() for l in lines if l]
    if not lines:
        return None
    m = np.zeros((len(lines) * 6 - 1, max(len(l) for l in lines) * 4 - 1), bool)
    for r, line in enumerate(lines):
        for i, ch in enumerate(line):
            g = _GLYPH_MASKS.get(ch)
            if g is not None:
                m[r * 6:r * 6 + 5, i * 4:i * 4 + 3] = g
    return m.repeat(scale, 0).repeat(scale, 1) if scale > 1 else m


def draw_overlay(rgb, mask, lut):
    """Draw the stats mask in the top-left corner of an RGB frame, on a darkened box."""
    H, W = rgb.shape[:2]
    pad = 1 if mask.shape[0] < 20 else 2
    bh, bw = min(H, mask.shape[0] + 2 * pad), min(W, mask.shape[1] + 2 * pad)
    rgb[:bh, :bw] //= 4
    m = mask[:bh - pad, :bw - pad]
    level = int(lut[255]) if lut is not None else 255     # follows the brightness setting
    rgb[pad:pad + m.shape[0], pad:pad + m.shape[1]][m] = max(64, level)


def _resize(img, w, h):
    if img.shape[1] == w and img.shape[0] == h:
        return img
    interp = cv2.INTER_AREA if w < img.shape[1] else cv2.INTER_LINEAR
    return cv2.resize(img, (w, h), interpolation=interp)


def render(img, W, H, mode, lut):
    """Scale a BGR image to W x H per the scaling mode; returns RGB uint8 (H, W, 3)."""
    h, w = img.shape[:2]
    if mode.startswith("Fill"):
        target = W / H
        if w / h > target:
            cw = max(1, round(h * target))
            x0 = (w - cw) // 2
            img = img[:, x0:x0 + cw]
        else:
            ch = max(1, round(w / target))
            y0 = (h - ch) // 2
            img = img[y0:y0 + ch]
        out = _resize(img, W, H)
    elif mode.startswith("Fit"):
        s = min(W / w, H / h)
        nw, nh = max(1, min(W, round(w * s))), max(1, min(H, round(h * s)))
        out = np.zeros((H, W, 3), np.uint8)
        x0, y0 = (W - nw) // 2, (H - nh) // 2
        out[y0:y0 + nh, x0:x0 + nw] = _resize(img, nw, nh)
    else:
        out = _resize(img, W, H)
    if lut is not None:
        out = cv2.LUT(out, lut)
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB)


# ---------------------------------------------------------------------------
# Sound: PyAV decodes, sounddevice plays. The audio clock steers the video
# clock (see Player), so picture and sound stay together over long videos.
# PyAV also decodes the video (AvVideo); without it OpenCV does.
# ---------------------------------------------------------------------------
AUDIO_ERROR = ""
try:
    import av
except Exception as e:
    av = None
    AUDIO_ERROR = f"Sound unavailable: {e}"
def _import_sounddevice():
    """sounddevice finds PortAudio with ctypes.util.find_library, which only looks
    in the system's folders. The Linux build carries its own copy: use that one
    when the system has none."""
    bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "libportaudio.so.2")
    if not (getattr(sys, "frozen", False) and sys.platform.startswith("linux") and os.path.isfile(bundled)):
        import sounddevice
        return sounddevice
    import ctypes.util
    find = ctypes.util.find_library
    ctypes.util.find_library = lambda name: (find(name) or bundled) if name == "portaudio" else find(name)
    try:
        import sounddevice
    finally:
        ctypes.util.find_library = find
    return sounddevice


try:
    sd = _import_sounddevice()
except Exception as e:          # ImportError, or OSError when PortAudio is missing (Linux)
    sd = None
    AUDIO_ERROR = AUDIO_ERROR or f"Sound unavailable: {e}"
if av is None:
    sd = None                   # sound needs both

AUDIO_RATE = 48000


class AvVideo:
    """A video file decoded by PyAV, frames by media time (0 = container start,
    as for the sound). Phone videos are turned upright."""

    def __init__(self, path):
        self.container = av.open(path)
        if not self.container.streams.video:
            self.container.close()
            raise IOError("no video stream")
        self.stream = s = self.container.streams.video[0]
        s.thread_type = "AUTO"
        rate = s.average_rate or s.guessed_rate
        fps = float(rate) if rate else 0.0
        self.fps = fps if 1 <= fps <= 240 else 30.0
        st, d = self.container.start_time, self.container.duration
        self.t_start = st / 1e6 if st else 0.0
        self.duration = d / 1e6 if d and d > 0 else 0.0
        self.n = 0              # bumped for every new picture (the player re-renders it)
        self.img = None
        self._restart(None)

    def _restart(self, t):
        """Decode from the keyframe at or before media time t (None = from the start, no seek)."""
        if t is not None:
            try:
                self.container.seek(int((max(0.0, t) + self.t_start) * 1e6))
            except Exception:
                pass
        self.frames = self.container.decode(self.stream)
        self.cur = self.cur_t = None
        self.nxt = self._next()

    def _next(self):
        """(frame, media time) of the next decoded frame, or None at the end."""
        for _ in range(100):            # skip a run of corrupt packets
            try:
                f = next(self.frames)
            except StopIteration:
                return None
            except Exception:
                continue
            t = f.time - self.t_start if f.time is not None else (self.cur_t or 0.0) + 1 / self.fps
            return f, t
        return None

    def frame_at(self, t):
        """BGR frame for media time t, or None after the last frame."""
        if self.cur_t is not None and (t < self.cur_t - 1.5 / self.fps or t > self.cur_t + 2.0):
            self._restart(t)            # jumped back, or far ahead: seek instead of decoding through
        new = False
        while self.nxt is not None and (self.cur is None or self.nxt[1] <= t + 1e-6):
            self.cur, self.cur_t = self.nxt
            self.nxt = self._next()
            new = True
        if self.cur is None or (self.nxt is None and t >= self.cur_t + self._length(self.cur)):
            return None
        if new:
            img = self.cur.to_ndarray(format="bgr24")
            k = round(getattr(self.cur, "rotation", 0) / 90) % 4
            self.img = np.ascontiguousarray(np.rot90(img, k)) if k else img
            self.n += 1
        return self.img

    def _length(self, f):
        """How long frame f stays up (GIF frames can be long)."""
        try:
            return float(f.duration * f.time_base) or 1 / self.fps
        except Exception:
            return 1 / self.fps

    def close(self):
        self.container.close()
        _free_decoder(self)


def _free_decoder(obj):
    """PyAV's container and stream refer to each other, so a closed file's
    decoder (and its FFmpeg threads, ~9 per video) lingers until a full garbage
    collection, which a long-running app almost never gets: drop our
    references and collect now (~50 ms, once per item)."""
    obj.container = obj.stream = obj.frames = obj.cur = obj.nxt = None
    gc.collect()


def output_devices():
    """[(name, device index or None)]: the default host API's outputs."""
    devs = [("System default", None)]
    if sd is not None:
        try:
            api = sd.default.hostapi
            for i, d in enumerate(sd.query_devices()):
                if d["max_output_channels"] > 0 and d["hostapi"] == api:
                    devs.append((d["name"], i))
        except Exception:
            pass
    return devs


class AudioTrack:
    """Decodes a file's first audio stream into float32 stereo blocks, ~1 s ahead."""
    AHEAD = AUDIO_RATE          # samples to keep buffered

    def __init__(self, path):
        self.container = av.open(path)
        if not self.container.streams.audio:
            self.container.close()
            raise LookupError("no audio stream")
        self.stream = self.container.streams.audio[0]
        self.stream.thread_type = "AUTO"
        st = self.container.start_time
        self.t_start = st / 1e6 if st else 0.0     # media time 0 = container start
        self.lock = threading.Condition()
        self.blocks = collections.deque()
        self.head = 0           # samples already played from blocks[0]
        self.buffered = 0
        self.base_t = 0.0       # media time of the first sample after the last seek
        self.consumed = 0       # samples played since the last seek
        self.gen = 0            # bumped by every seek
        self.eof = False
        self.seek_to = 0.0
        self.quit = False
        threading.Thread(target=self._decode, daemon=True).start()

    def seek(self, t):
        with self.lock:
            self.gen += 1
            self.seek_to = max(0.0, t)
            self.blocks.clear()
            self.head = self.buffered = self.consumed = 0
            self.base_t = self.seek_to
            self.eof = False
            self.lock.notify_all()

    def close(self):
        with self.lock:
            self.quit = True
            self.lock.notify_all()

    def read(self, out):
        """Audio callback: fill `out` (n, 2) from the buffer; returns samples written."""
        n, w = len(out), 0
        with self.lock:
            while w < n and self.blocks:
                b = self.blocks[0]
                take = min(n - w, len(b) - self.head)
                out[w:w + take] = b[self.head:self.head + take]
                w += take
                self.head += take
                if self.head >= len(b):
                    self.blocks.popleft()
                    self.head = 0
            self.buffered -= w
            self.consumed += w
            if w:
                self.lock.notify_all()
        return w

    def _decode(self):
        frames = resampler = None
        gen = -1
        skip_until = None
        while True:
            with self.lock:
                while not self.quit and self.seek_to is None and (self.eof or self.buffered >= self.AHEAD):
                    self.lock.wait(0.2)
                if self.quit:
                    break
                seek, self.seek_to = self.seek_to, None
                if seek is not None:
                    gen = self.gen
            if seek is not None:
                try:
                    self.container.seek(int((seek + self.t_start) * 1e6))   # back to a keyframe
                except Exception:
                    pass
                frames = self.container.decode(self.stream)
                resampler = av.AudioResampler(format="fltp", layout="stereo", rate=AUDIO_RATE)
                skip_until = seek
            try:
                frame = next(frames)
            except StopIteration:
                with self.lock:
                    if gen == self.gen:
                        self.eof = True
                continue
            except Exception:           # corrupt packet: skip it
                continue
            t = frame.time - self.t_start if frame.time is not None else None
            for rf in resampler.resample(frame):
                a = np.ascontiguousarray(rf.to_ndarray().T, dtype=np.float32)
                if skip_until is not None:      # after a seek, drop audio before the target
                    if t is not None and t < skip_until:
                        a = a[int((skip_until - t) * AUDIO_RATE):]
                    skip_until = None
                if len(a):
                    with self.lock:
                        if gen == self.gen:
                            self.blocks.append(a)
                            self.buffered += len(a)
        self.container.close()
        _free_decoder(self)


class AudioOut:
    """One output stream for the app's lifetime; plays whichever AudioTrack is attached."""
    def __init__(self):
        self.stream = None
        self.device = "unset"
        self.track = None
        self.paused = False
        self.volume = 1.0
        self.mark = None        # (perf_counter, media time heard at that moment, track, gen)
        self.error = ""

    def open(self, device):
        if self.stream is not None and device == self.device:
            return
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        self.device = device
        try:
            self.stream = sd.OutputStream(samplerate=AUDIO_RATE, channels=2, dtype="float32",
                                          device=device, callback=self._cb)
            self.stream.start()
            self.error = ""
        except Exception as e:
            self.stream = None
            self.error = f"Sound output error: {e}"

    def _cb(self, out, frames, time_info, status):
        tr = self.track
        n = 0
        if tr is not None and not self.paused:
            n = tr.read(out)
            if n == frames:
                # The first sample of this block is heard `latency` from now
                self.mark = (time.perf_counter(),
                             tr.base_t + (tr.consumed - frames) / AUDIO_RATE - self.stream.latency,
                             tr, tr.gen)
            if n and self.volume != 1.0:
                out[:n] *= self.volume
        out[n:] = 0

    def position(self):
        """Media time being heard right now, or None (no sound playing)."""
        m, tr = self.mark, self.track
        if m is None or tr is None or self.paused or m[2] is not tr or m[3] != tr.gen:
            return None
        now = time.perf_counter()
        if now - m[0] > 0.25:           # callbacks stopped delivering sound (end of track)
            return None
        return m[1] + (now - m[0])


# ---------------------------------------------------------------------------
# Output layout: which bridges get which part of the picture
# ---------------------------------------------------------------------------
def make_layout(mode, ip, port, w, h, bridges, span_cols, gap_x=0, gap_y=0):
    """(kind, canvas_w, canvas_h, tiles) with tiles = [(ip, port, w, h, x0, y0, sw, sh)].
    kind "one": the single display from the main fields. "mirror": every bridge
    gets the whole picture at its own size (x0/y0 unused). "span": the picture
    is rendered at canvas size and bridge i takes the tile at column i % cols,
    row i // cols; a column is as wide as its widest bridge, a row as tall as
    its tallest. Every tile ends with (sw, sh): the size of its piece of the span picture,
    which it is scaled from (= w, h except when laid out by pixel pitch).
    When every span display has a pitch (mm), the span is laid out in real
    size instead: each row's displays side by side, rows centered under the
    widest one, displays centered in their row's height; the picture is drawn
    at the finest pitch and each display's piece is scaled to its pixels.
    In that layout gap_x / gap_y (mm) leave space between displays in a row /
    between rows, and a display's "dx" / "dy" (mm) nudge it right / down."""
    bl = [b for b in bridges if b.get("ip")]
    if mode == "Mirror" and bl:
        tiles = [(b["ip"], int(b.get("port", 4048)), int(b["w"]), int(b["h"]), 0, 0, int(b["w"]), int(b["h"]))
                 for b in bl]
        return "mirror", tiles[0][2], tiles[0][3], tiles
    if mode == "Span" and bl:
        cols = max(1, min(int(span_cols), len(bl)))
        rows = -(-len(bl) // cols)
        if all(bridge_pitch(b) for b in bl):
            fine = min(bridge_pitch(b) for b in bl)
            gx, gy = max(0.0, float(gap_x)) / fine, max(0.0, float(gap_y)) / fine

            def mm(b, k):
                try:
                    return float(b.get(k) or 0) / fine
                except (TypeError, ValueError):
                    return 0.0
            size = [(int(b["w"]) * bridge_pitch(b) / fine, int(b["h"]) * bridge_pitch(b) / fine) for b in bl]
            row_of = [range(r * cols, min(len(bl), (r + 1) * cols)) for r in range(rows)]
            roww = [sum(size[i][0] for i in row_of[r]) + gx * (len(row_of[r]) - 1) for r in range(rows)]
            rowh = [max(size[i][1] for i in row_of[r]) for r in range(rows)]
            boxes = []
            for i, b in enumerate(bl):
                r = i // cols
                x = (max(roww) - roww[r]) / 2 + sum(size[j][0] + gx for j in range(r * cols, i)) + mm(b, "dx")
                y = sum(rowh[:r]) + gy * r + (rowh[r] - size[i][1]) / 2 + mm(b, "dy")
                boxes.append((x, y))
            x0 = min(0.0, min(x for x, _ in boxes))         # a nudge past the edge grows the picture
            y0 = min(0.0, min(y for _, y in boxes))
            cw = round(max(max(x + size[i][0] for i, (x, _) in enumerate(boxes)), max(roww)) - x0)
            ch = round(max(max(y + size[i][1] for i, (_, y) in enumerate(boxes)), sum(rowh) + gy * (rows - 1)) - y0)
            tiles = []
            for i, b in enumerate(bl):
                x, y = boxes[i]
                sw, sh = max(1, round(size[i][0])), max(1, round(size[i][1]))
                tiles.append((b["ip"], int(b.get("port", 4048)), int(b["w"]), int(b["h"]),
                              min(cw - sw, max(0, round(x - x0))), min(ch - sh, max(0, round(y - y0))), sw, sh))
            return "span", cw, ch, tiles
        colw, rowh = [0] * cols, [0] * rows
        for i, b in enumerate(bl):
            colw[i % cols] = max(colw[i % cols], int(b["w"]))
            rowh[i // cols] = max(rowh[i // cols], int(b["h"]))
        tiles = [(b["ip"], int(b.get("port", 4048)), int(b["w"]), int(b["h"]),
                  sum(colw[:i % cols]), sum(rowh[:i // cols]), int(b["w"]), int(b["h"])) for i, b in enumerate(bl)]
        return "span", sum(colw), sum(rowh), tiles
    return "one", w, h, [(ip, port, w, h, 0, 0, w, h)]


PITCHES = (1.25, 1.5, 1.86, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 6.67, 8.0, 10.0)   # common HUB75 pitches (mm)


def bridge_pitch(b):
    """A display's pixel pitch in mm (float), or 0.0 if not set."""
    try:
        p = float(b.get("pitch") or 0)
    except (TypeError, ValueError):
        return 0.0
    return p if 0.1 <= p <= 100 else 0.0


def display_lut(b):
    """cv2.LUT table (1 x 256 x 3, RGB) for a display's brightness and
    red/green/blue balance (percent), or None when they are all 100."""
    try:
        br = min(100, max(0, int(b.get("bright", 100))))
        bal = [min(100, max(0, int(x))) for x in (b.get("rgb") or (100, 100, 100))][:3]
    except (TypeError, ValueError):
        return None
    if br == 100 and bal == [100, 100, 100]:
        return None
    x = np.arange(256, dtype=np.float32)
    return np.stack([np.round(x * br * c / 10000) for c in bal], axis=-1).astype(np.uint8)[None]


def display_luts(layout, bridges):
    """Per tile of the layout: its display's LUT or None (tiles follow the
    displays that have an IP, in order)."""
    if layout[0] == "one":
        return [None]
    return [display_lut(b) for b in bridges if b.get("ip")]


def level_label(b):
    """'100%' / '80%' / '90%, R100 G95 B90' for the Displays list."""
    br = b.get("bright", 100)
    bal = list(b.get("rgb") or (100, 100, 100))
    return f"{br}%" + ("" if bal == [100, 100, 100] else f", R{bal[0]} G{bal[1]} B{bal[2]}")


def pitch_label(p):
    """'P2.5' for 2.5 mm, 'None' when not set."""
    return f"P{p:g}" if p else "None"


def span_piece(rgb, t):
    """Display t's bytes from the span picture: its (sw, sh) piece at (x0, y0),
    scaled to the display's w x h when the layout is by pitch."""
    x, y, w, h, sw, sh = t[4], t[5], t[2], t[3], t[6], t[7]
    piece = rgb[y:y + sh, x:x + sw]
    if (sw, sh) != (w, h):
        piece = cv2.resize(piece, (w, h), interpolation=cv2.INTER_AREA if sw >= w else cv2.INTER_LINEAR)
    return np.ascontiguousarray(piece).tobytes()


# ---------------------------------------------------------------------------
# Show schedule: play between two times of day on chosen days
# ---------------------------------------------------------------------------
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_hhmm(text):
    """Minutes after midnight for 'HH:MM' (or 'H'), or None."""
    try:
        h, _, m = text.strip().partition(":")
        h, m = int(h), int(m or 0)
    except ValueError:
        return None
    return h * 60 + m if 0 <= h <= 24 and 0 <= m < 60 and h * 60 + m <= 24 * 60 else None


def norm_schedule(sc):
    """A complete schedule dict from saved settings (off by default)."""
    sc = sc if isinstance(sc, dict) else {}
    days = sc.get("days")
    days = [bool(d) for d in days][:7] if isinstance(days, list) and len(days) == 7 else [True] * 7
    start = sc.get("start") if parse_hhmm(str(sc.get("start", ""))) is not None else "17:00"
    stop = sc.get("stop") if parse_hhmm(str(sc.get("stop", ""))) is not None else "22:00"
    return {"on": bool(sc.get("on")), "start": start, "stop": stop, "days": days}


def schedule_active(sc, now):
    """True if `now` (a datetime) is inside the schedule's playing time. A
    stop before the start runs past midnight and counts for the start's day;
    start == stop means all day."""
    a, b = parse_hhmm(sc["start"]), parse_hhmm(sc["stop"])
    t, day = now.hour * 60 + now.minute, now.weekday()
    if a == b:
        return sc["days"][day]
    if a < b:
        return sc["days"][day] and a <= t < b
    return (sc["days"][day] and t >= a) or (sc["days"][(day - 1) % 7] and t < b)


# ---------------------------------------------------------------------------
# Event log: player errors and watchdog actions, for long unattended shows
# ---------------------------------------------------------------------------
LOG_FILE = os.path.join(datasources.DATA_DIR, "streamer.log")
_log_lock = threading.Lock()


def log_event(text):
    """Append a timestamped line to streamer.log (kept under ~1 MB, one old copy)."""
    try:
        with _log_lock:
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000:
                os.replace(LOG_FILE, LOG_FILE + ".old")
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text.rstrip() + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Player thread: decode -> scale -> send, paced by its own clock
# ---------------------------------------------------------------------------
class Player:
    def __init__(self):
        self.q = queue.Queue()
        self.settings = None        # dict, replaced wholesale by the GUI
        self.playlist = []          # list of paths, replaced wholesale by the GUI
        self.sender = DdpSender()
        self._addr_cache = {}
        # Published for the GUI (plain attribute reads, no lock needed)
        self.state = "stopped"      # stopped / playing / paused
        self.index = -1
        self.pos = 0.0
        self.duration = 0.0
        self.preview = None
        self.preview_seq = 0
        self.raw = None             # last source frame before rotate / crop
        self.sent_frames = 0
        self.sent_bytes = 0
        self.late = 0
        self.errors = 0
        self.av_diff = 0.0          # last picture-vs-sound correction (s), for testing
        self.overlay = (0, [])      # (seq, lines) of stats to draw on the picture, set by the GUI
        self.message = ""
        self.audio_out = AudioOut() if sd is not None else None
        self.track = None           # AudioTrack of the current item, if it has sound
        self.last_progress = time.monotonic()   # last time the loop reached the send step
        self.stalls = 0             # times the watchdog restarted an item
        self.crashes = 0            # times the loop hit an error and moved on
        self._src = None            # the open Source, for cleanup after an error
        self.testing = None         # test pattern name while one is shown (Displays window)
        self._before_test = ("stopped", -1)
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()

    def _set_track(self, path, s):
        """Swap in the sound track for `path` (None = silence)."""
        out = self.audio_out
        if out is None:
            return
        old, self.track = self.track, None
        out.track = None
        if old is not None:
            old.close()
        if path is None or not s["audio_on"] or is_live(path) or os.path.splitext(path)[1].lower() in IMAGE_EXT:
            return
        try:
            self.track = AudioTrack(path)
        except Exception:
            return                  # no sound in this file
        out.open(s["audio_dev_idx"])
        out.paused = False
        out.track = self.track

    def cmd(self, *c):
        self.q.put(c)

    def _addr(self, ip, port):
        key = (ip, port)
        if key not in self._addr_cache:
            self._addr_cache[key] = (socket.gethostbyname(ip), port)
        return self._addr_cache[key]

    def _open(self, index, step=1):
        """Open playlist item `index`, skipping unplayable files in direction `step`.
        Returns (Source, index)."""
        pl = self.playlist
        self.message = ""
        kind, cw, ch, tiles = self.settings["layout"]
        size = (cw, ch) if kind == "span" else (tiles[0][2], tiles[0][3])   # live items draw at this size
        for k in range(len(pl)):
            i = (index + k * step) % len(pl)
            base, view = split_view(pl[i])
            try:
                # turned 90°: live items draw on a tall canvas that becomes the wall's shape again
                src = Source(base, size[::-1] if view.get("rotate") in (90, 270) else size)
                src.entry, src.view = pl[i], view
                return src, i
            except Exception as e:
                self.message = f"Skipped {item_label(pl[i])}: {e}"
        return None, -1

    def _blank(self, s):
        try:
            frames = [(self._addr(t[0], t[1]), bytes(t[2] * t[3] * 3)) for t in s["layout"][3] if t[0]]
            for _ in range(2):
                self.sender.send_frames(frames)
        except OSError:
            pass

    def _run(self):
        """The playback loop. If it ever hits an error, log it, close the item
        and carry on with the next one instead of silently stopping."""
        while True:
            try:
                self._loop()
            except Exception as e:
                self.crashes += 1
                log_event("Player error, going on with the next item:\n" + traceback.format_exc())
                self.message = f"Skipped an item after an error ({type(e).__name__}: {e})"
                for close in (lambda: self._src and self._src.close(), lambda: self._set_track(None, self.settings)):
                    try:
                        close()
                    except Exception:
                        pass
                self._src = None
                if self.state in ("playing", "paused") and self.playlist:
                    self.q.put(("next",))
                else:
                    self.state = "stopped"
                time.sleep(0.5)

    def _watch(self):
        """Watchdog: while playing, the loop reaches the send step every frame.
        If it hasn't for STALL_SECS, restart the item (the next one if the same
        item stalls twice in a row); if even that doesn't help, say so."""
        asked_at, last_idx = None, None
        while True:
            time.sleep(1.0)
            now = time.monotonic()
            if self.state != "playing":
                self.last_progress, asked_at = now, None
                continue
            stale = now - self.last_progress
            if stale < STALL_SECS:
                if asked_at is not None:
                    asked_at = None
                    self.message = ""
                continue
            if asked_at is None:
                self.stalls += 1
                again = last_idx == self.index
                last_idx = self.index
                log_event(f"Watchdog: nothing sent for {stale:.0f} s while playing item {self.index + 1}; "
                          + ("going to the next item" if again else "restarting it"))
                self.message = f"Nothing sent for {stale:.0f} s: " + ("skipped to the next item" if again
                                                                       else "restarted the item")
                self.q.put(("next",) if again else ("play", self.index))
                asked_at = now
            elif now - asked_at > STALL_SECS and "stuck" not in self.message:
                log_event(f"Watchdog: still nothing sent after restarting ({stale:.0f} s); player stuck")
                self.message = f"Player stuck: nothing sent for {stale:.0f} s. Restart the app."

    def _loop(self):
        src = None
        paused = False
        t0 = paused_t = 0.0
        next_send = time.perf_counter()
        key = data = None
        pending = None          # command the loop issues to itself (end of item)
        audio_on = None
        while True:
            # ---- commands ------------------------------------------------
            c, pending = pending, None
            if c is None:
                try:
                    c = self.q.get(timeout=0.1) if src is None else self.q.get_nowait()
                except queue.Empty:
                    pass
            s = self.settings
            if c and s:
                op = c[0]
                if op in ("play", "next", "prev", "stop"):
                    self.testing = None             # playing or stopping ends a test pattern
                if op == "test" and c[1]:
                    # Test pattern on every display (Displays window); "test", None goes back
                    if not self.testing:
                        self._before_test = (self.state, self.index)
                    if src:
                        src.close()
                    self._set_track(None, s)
                    kind, cw, ch, tiles = s["layout"]
                    src = Source("test:" + json.dumps({"pattern": c[1]}),
                                 (cw, ch) if kind == "span" else (tiles[0][2], tiles[0][3]))
                    src.entry = self.playlist[self.index] if 0 <= self.index < len(self.playlist) else None
                    src.view = {}
                    self._src, self.testing = src, c[1]
                    paused, key = False, None
                    next_send = t0 = time.perf_counter()
                    self.last_progress = time.monotonic()
                    self.duration = 0.0
                    self.state = "playing"
                elif op == "test":
                    if self.testing:
                        self.testing = None
                        if src:
                            src.close()
                            src = self._src = None
                        was, idx = self._before_test
                        if was in ("playing", "paused") and self.playlist and idx >= 0:
                            pending = ("play", idx)     # carry on with what was playing
                        else:
                            self.state = "stopped"
                            self._blank(s)
                elif op in ("play", "next", "prev") and self.playlist:
                    if op == "play":
                        i = c[1] if c[1] is not None else max(0, self.index)
                    else:
                        i = self.index + (1 if op == "next" else -1)
                    if src:
                        src.close()
                    src, self.index = self._open(i % len(self.playlist), -1 if op == "prev" else 1)
                    self._src = src
                    self.last_progress = time.monotonic()
                    paused = False
                    next_send = t0 = time.perf_counter()
                    key = None
                    if src is None:
                        self.state = "stopped"
                        continue
                    self.duration = src.duration or (s["image_secs"] if src.timed else 0)
                    self.state = "playing"
                    self._set_track(src.path, s)
                    audio_on = s["audio_on"]
                elif op == "pause" and src and not paused:
                    paused, paused_t = True, next_send - t0
                    self.state = "paused"
                    if self.audio_out:
                        self.audio_out.paused = True
                elif op == "resume" and src and paused:
                    paused = False
                    next_send = time.perf_counter()
                    t0 = next_send - paused_t
                    self.state = "playing"
                    if self.track:
                        self.track.seek(paused_t)
                    if self.audio_out:
                        self.audio_out.paused = False
                elif op == "seek" and src:
                    if paused:
                        paused_t = c[1]
                    else:
                        t0 = next_send - c[1]
                    if self.track:
                        self.track.seek(c[1])
                elif op == "stop":
                    self._set_track(None, s)
                    if src:
                        src.close()
                        src = self._src = None
                        if s["blank_on_stop"]:
                            self._blank(s)
                    self.state = "stopped"
                    self.pos = 0.0
                    self.preview = None
                    self.preview_seq += 1
                elif op == "blank":
                    self._blank(s)
            if src is None or not s:
                continue
            entry = self.playlist[self.index] if 0 <= self.index < len(self.playlist) else src.entry
            if entry != src.entry:          # rotate / crop changed while playing
                view = split_view(entry)[1]
                if src.live and src.view.get("rotate", 0) % 180 != view.get("rotate", 0) % 180:
                    pending = ("play", self.index)      # live item: redraw at the turned size
                    continue
                src.entry, src.view, key = entry, view, None

            # ---- timing --------------------------------------------------
            period = 1.0 / s["fps"]
            if paused:
                period = max(period, 0.2)   # keep the board's 5 s blackout timer fed
            now = time.perf_counter()
            if now > next_send + period:    # fell behind: skip ahead, stay real-time
                self.late += 1
                next_send = now
            mt = paused_t if paused else next_send - t0

            # ---- sound: follow setting changes, and let the audio clock steer mt
            out = self.audio_out
            if out is not None:
                out.volume = s["volume"] / 100
                if s["audio_on"] != audio_on:
                    audio_on = s["audio_on"]
                    self._set_track(src.path if audio_on else None, s)
                    if self.track:
                        self.track.seek(mt)
                        out.paused = paused
                elif self.track and out.device != s["audio_dev_idx"]:
                    out.open(s["audio_dev_idx"])
                    self.track.seek(mt)
                a = out.position() if self.track and not paused else None
                if a is not None:
                    # picture time that matches the sound when this frame reaches the wall
                    target = a + (next_send - now) + VIDEO_LATENCY - s["sound_delay"] / 1000
                    diff = target - mt
                    t0 -= diff if abs(diff) > 0.25 else diff * 0.05
                    mt = next_send - t0
                    self.av_diff = diff

            # ---- get the frame for time mt --------------------------------
            if src.timed:                   # picture or live item: shown for "Image s"
                frame = src.frame_at(mt)
                if (not paused and mt >= s["image_secs"] and s["loop"] != "Loop current"
                        and not getattr(src, "endless", False)):
                    frame = None
            else:
                frame = src.frame_at(mt)
            gen = getattr(src, "gen", None)
            if gen is not None:             # script item: show its error (or clear it once fixed)
                self.message = "Script error: " + gen.error if gen.error else ""
            if frame is None:               # end of item
                if s["loop"] == "Loop current" or (s["loop"] == "Loop playlist" and len(self.playlist) == 1):
                    t0 = next_send
                    if self.track:
                        self.track.seek(0)
                    continue
                last = self.index + 1 >= len(self.playlist)
                pending = ("stop",) if last and s["loop"] == "Play once" else ("next",)
                continue
            self.pos = mt

            # ---- scale (cached while the source frame and settings are unchanged)
            ov_scale = OVERLAY_MODES.index(s["overlay"]) if s["overlay"] in OVERLAY_MODES else 0
            ov_seq, ov_lines = self.overlay
            k = (id(src), src.pos, s["version"], ov_seq if ov_scale else 0)
            if k != key:
                self.raw = frame            # for the Rotate / crop dialog
                frame = apply_view(frame, src.view)
                kind, cw, ch, tiles = s["layout"]
                mask = stats_mask(ov_lines, ov_scale) if ov_scale else None
                if kind == "span":
                    rgb = render(frame, cw, ch, s["scale"], s["lut"])
                    if mask is not None:
                        draw_overlay(rgb, mask, s["lut"])
                    data = [span_piece(rgb, t) for t in tiles]
                else:                       # one / mirror: each size rendered once
                    done = {}
                    for t in tiles:
                        if (t[2], t[3]) not in done:
                            r = render(frame, t[2], t[3], s["scale"], s["lut"])
                            if mask is not None:
                                draw_overlay(r, mask, s["lut"])
                            done[(t[2], t[3])] = (r, r.tobytes())
                    rgb = done[(tiles[0][2], tiles[0][3])][0]
                    data = [done[(t[2], t[3])][1] for t in tiles]
                luts = s.get("tile_luts")
                if luts and any(lut is not None for lut in luts):     # per-display brightness / balance
                    data = [d if lut is None else
                            cv2.LUT(np.frombuffer(d, np.uint8).reshape(t[3], t[2], 3), lut).tobytes()
                            for d, t, lut in zip(data, tiles, luts)]
                key = k
                self.preview = rgb
                self.preview_seq += 1

            # ---- send ----------------------------------------------------
            wait_until(next_send)
            try:
                out = [(self._addr(t[0], t[1]), d) for t, d in zip(s["layout"][3], data) if t[0]]
                if out:                     # nothing yet if no display is set
                    self.sender.send_frames(out, period * 0.5 if s["pace"] else 0.0)
                    self.sent_frames += 1
                    self.sent_bytes += sum(len(d) for _, d in out)
            except OSError as e:
                self.errors += 1
                self.message = f"Send error: {e}"
            self.last_progress = time.monotonic()   # the loop is alive (even if the network isn't)
            next_send += period


# ---------------------------------------------------------------------------
# DDP discovery: broadcast a status query (ID 251), collect the replies, then
# ask each device for its config (ID 250) to learn the display size.
# ---------------------------------------------------------------------------
DDP_QUERY, DDP_REPLY = 0x02, 0x04
DDP_ID_CONTROL, DDP_ID_CONFIG, DDP_ID_STATUS = 246, 250, 251


def _ddp_query_packet(ident, seq=1):
    return struct.pack(">BBBBIH", 0x40 | DDP_QUERY, seq, 0, ident, 0, 0)


def _ddp_parse_reply(data, ident):
    """JSON body of a DDP reply for `ident`, or None."""
    if len(data) < 10:
        return None
    flags, _, _, rid, _, ln = struct.unpack(">BBBBIH", data[:10])
    if not (flags & DDP_REPLY) or rid != ident or ln == 0:
        return None
    try:
        return json.loads(data[10:10 + ln].decode("utf-8", "replace"))
    except ValueError:
        return None


def ddp_read(ip, ident, key, port=4048, timeout=0.5):
    """Query a JSON ID (status 251 / config 250) and return reply[key] as a dict, or None."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(_ddp_query_packet(ident), (ip, port))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            data, _ = s.recvfrom(1500)
            j = _ddp_parse_reply(data, ident)
            if isinstance(j, dict) and isinstance(j.get(key), dict):
                return j[key]
    except OSError:
        pass
    finally:
        s.close()
    return None


def ddp_config(ip, port=4048, timeout=0.5):
    """The device's DDP config ({"ip":..., "ports":[...], "w":..., "h":..., ...}), or None."""
    return ddp_read(ip, DDP_ID_CONFIG, "config", port, timeout)


def ddp_status(ip, port=4048, timeout=0.5):
    """The device's DDP status ({"man":..., "fps":..., "demo":..., ...}), or None."""
    return ddp_read(ip, DDP_ID_STATUS, "status", port, timeout)


def ddp_control(ip, obj, port=4048):
    """Write a JSON control command to the device (ID 246), e.g. {"control": {"demo": False}}."""
    body = json.dumps(obj).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.sendto(struct.pack(">BBBBIH", 0x40 | 0x01, 1, 0, DDP_ID_CONTROL, 0, len(body)) + body, (ip, port))
    finally:
        s.close()


def ddp_read_many(targets, ident, key, timeout=0.5):
    """Like ddp_read for several (ip, port) at once: {(ip, port): dict or None}."""
    res = {t: None for t in targets}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setblocking(False)
    try:
        for ip, port in targets:
            try:
                s.sendto(_ddp_query_packet(ident), (socket.gethostbyname(ip), port))
            except OSError:
                pass
        by_addr = {}
        for t in targets:
            try:
                by_addr[(socket.gethostbyname(t[0]), t[1])] = t
            except OSError:
                pass
        end = time.monotonic() + timeout
        while any(v is None for v in res.values()) and time.monotonic() < end:
            ready, _, _ = select.select([s], [], [], max(0.0, end - time.monotonic()))
            if not ready:
                break
            try:
                data, addr = s.recvfrom(1500)
            except OSError:
                continue
            j = _ddp_parse_reply(data, ident)
            t = by_addr.get(addr)
            if t is not None and isinstance(j, dict) and isinstance(j.get(key), dict):
                res[t] = j[key]
    finally:
        s.close()
    return res


def _mdns_query(service):
    """mDNS PTR question for e.g. "_wled._tcp.local", sent from a normal port so
    responders answer us directly (RFC 6762 legacy unicast)."""
    name = b"".join(bytes([len(p)]) + p.encode() for p in service.split(".")) + b"\0"
    return struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + name + struct.pack(">HH", 12, 1)


def ddp_discover(timeout=1.2, port=4048):
    """Find displays on every local IPv4 interface: a broadcast DDP status query
    (the bridge) plus an mDNS query for _wled._tcp (WLED, which doesn't answer
    DDP queries). Returns a list of {"ip", "man", "mod", "ver", "mac", "w", "h"}
    (w/h None if the device doesn't say). Displays may wait up to 255 ms before
    answering a broadcast (DDP spec), mDNS responders up to 500 ms. The mDNS
    question goes out four times: ESP8266 boards in Wi-Fi power save often
    miss multicast packets."""
    try:
        local = {ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        local = set()
    local = {a for a in local if not a.startswith("127.")}  # Debian/Ubuntu name the PC 127.0.1.1
    if not local:
        try:    # the address used for the default route (connecting UDP sends nothing)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("192.0.2.1", 9))
                local = {s.getsockname()[0]}
        except OSError:
            pass
    socks, mdns = [], []
    for addr in sorted(local) or ["0.0.0.0"]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind((addr, 0))
            s.setblocking(False)
            s.sendto(_ddp_query_packet(DDP_ID_STATUS), ("255.255.255.255", port))
            socks.append(s)
        except OSError:
            pass
        try:   # mDNS must go out of this interface explicitly (else Windows picks one)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            if addr != "0.0.0.0":
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(addr))
            s.bind((addr, 0))
            s.setblocking(False)
            s.sendto(_mdns_query("_wled._tcp.local"), ("224.0.0.251", 5353))
            socks.append(s)
            mdns.append(s)
        except OSError:
            pass
    try:       # also hear answers sent to the mDNS group (what phones listen to)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):     # Mac/Linux: share the port with mDNSResponder/Avahi
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.bind(("", 5353))
        for addr in sorted(local):
            try:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                             socket.inet_aton("224.0.0.251") + socket.inet_aton(addr))
            except OSError:
                pass
        s.setblocking(False)
        socks.append(s)
    except OSError:
        pass
    found, wled = {}, set()
    start = time.monotonic()
    end, resend = start + timeout, [start + 0.25, start + 0.5, start + 0.8]
    while socks and time.monotonic() < end:
        if resend and time.monotonic() >= resend[0]:
            resend.pop(0)
            for s in mdns:
                try:
                    s.sendto(_mdns_query("_wled._tcp.local"), ("224.0.0.251", 5353))
                except OSError:
                    pass
        wait = min([end] + resend[:1]) - time.monotonic()
        ready, _, _ = select.select(socks, [], [], max(0.0, wait))
        for s in ready:
            try:
                data, (ip, src_port) = s.recvfrom(4096)
            except OSError:
                continue
            if src_port == 5353:
                if len(data) > 3 and data[2] & 0x80 and b"_wled" in data:   # an answer about WLED
                    wled.add(ip)
                continue
            j = _ddp_parse_reply(data, DDP_ID_STATUS)
            st = j.get("status") if isinstance(j, dict) else None
            if isinstance(st, dict) and ip not in found:
                found[ip] = {"ip": ip, "man": st.get("man", "?"), "mod": st.get("mod", "?"),
                             "ver": st.get("ver", ""), "mac": st.get("mac", ""), "w": None, "h": None}
    for s in socks:
        s.close()
    for dev in found.values():
        cfg = ddp_config(dev["ip"], port)
        if cfg and isinstance(cfg.get("w"), int) and isinstance(cfg.get("h"), int):
            dev["w"], dev["h"] = cfg["w"], cfg["h"]
    wled -= set(found)
    if wled:
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            for ip, st in zip(wled, ex.map(wled_status, wled)):
                if st:
                    cfg = wled_config(ip) or {}
                    found[ip] = {"ip": ip, "man": st["man"], "mod": st["mod"], "ver": st["ver"],
                                 "mac": st.get("mac", ""), "w": cfg.get("w"), "h": cfg.get("h")}
    return sorted(found.values(), key=lambda d: tuple(int(x) for x in d["ip"].split(".")))


# ---------------------------------------------------------------------------
# WLED doesn't answer the DDP status/config queries, so read its JSON API
# (HTTP port 80) and shape the answers like the DDP replies.
# ---------------------------------------------------------------------------
WLED_HUB75 = range(64, 72)          # WLED bus types for HUB75 panels (65 half scan, 66 quarter scan)


def _http_json(url, timeout=0.6):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None


def wled_status(ip, timeout=0.6):
    """WLED's state + info as a DDP-like status ({"man": "WLED", "mod", "ver", "fps",
    "live", "override", ...}), or None if there's no WLED at that address."""
    si = _http_json(f"http://{ip}/json/si", timeout)
    if isinstance(si, dict) and isinstance(si.get("info"), dict):
        info, state = si["info"], si.get("state") or {}
    else:                                       # fall back to the info alone
        info, state = _http_json(f"http://{ip}/json/info", timeout), {}
    if not isinstance(info, dict) or info.get("brand") != "WLED":
        return None
    leds = info.get("leds") or {}
    name = info.get("name") or "WLED"
    st = {"man": "WLED", "mod": name if name != "WLED" else info.get("arch", ""), "ver": info.get("ver", ""),
          "mac": info.get("mac", ""), "live": bool(info.get("live")), "lm": info.get("lm", ""), "wled": True,
          "override": bool(state.get("lor"))}     # realtime override: WLED ignores the stream, runs its effects
    if isinstance(leds.get("fps"), (int, float)):
        st["fps"] = leds["fps"]
    return st


def wled_config(ip, timeout=0.6):
    """{"w", "h"} of a WLED display, or None if it can't be worked out: the 2D
    matrix size if one is set up, else the size of a single HUB75 output
    (DDP pixels then go to the panels row by row)."""
    info = _http_json(f"http://{ip}/json/info", timeout)
    if not isinstance(info, dict):
        return None
    leds = info.get("leds") or {}
    m = leds.get("matrix")
    if isinstance(m, dict) and isinstance(m.get("w"), int) and isinstance(m.get("h"), int):
        return {"w": m["w"], "h": m["h"]}
    cfg = _http_json(f"http://{ip}/json/cfg", timeout)
    try:
        buses = cfg["hw"]["led"]["ins"]
        count = int(leds["count"])
    except (TypeError, KeyError, ValueError):
        return None
    if len(buses) != 1 or buses[0].get("type") not in WLED_HUB75:
        return None
    # HUB75 "pins" are panel width, height, chain length, rows, columns (WLED BusHub75Matrix)
    pw, ph, chain, rows, cols = (list(buses[0].get("pin") or []) + [0] * 5)[:5]
    pw, chain = max(16, min(128, pw)), max(1, min(4, chain))
    w = cols * pw if chain > 1 and rows and cols else pw * chain
    if count % w:
        w = pw
    return {"w": w, "h": count // w} if count % w == 0 else None


def device_config(ip, port=4048):
    """Display size from whatever the device speaks: DDP config, else WLED's JSON API."""
    return ddp_config(ip, port) or wled_config(ip)


# ---------------------------------------------------------------------------
# Board status poller: DDP status query once a second, config every 10 s
# ---------------------------------------------------------------------------
class BoardPoller:
    def __init__(self, get_targets):
        self.get_targets = get_targets  # -> [(ip, port)], first = the one shown in detail
        self.status = None              # first target's last DDP status dict, or None (no answer)
        self.config = None              # first target's last DDP config dict, or None
        self.all = {}                   # {(ip, port): (status, config)} for every target
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        last_targets, cfg_t, cfgs = None, 0.0, {}
        wled, probed = set(), {}        # targets known to be WLED; when a silent one was last tried as WLED
        pool = concurrent.futures.ThreadPoolExecutor(8)
        while True:
            t = time.monotonic()
            targets = list(dict.fromkeys(self.get_targets()))
            # The bridge answers DDP queries. A device that doesn't is tried as WLED
            # (every 10 s while unknown), then polled over HTTP until that stops answering.
            st = ddp_read_many([k for k in targets if k not in wled], DDP_ID_STATUS, "status")
            http = [k for k in targets if k in wled or (not st[k] and t - probed.get(k, -99) > 10)]
            for k, s in zip(http, pool.map(lambda k: wled_status(k[0]), http)):
                probed[k] = t
                st[k] = s
                (wled.add if s else wled.discard)(k)
            if targets != last_targets or t - cfg_t > 10 or any(st[k] and not cfgs.get(k) for k in targets):
                ask = [k for k in targets if st[k] and k not in wled]
                cfgs = ddp_read_many(ask, DDP_ID_CONFIG, "config") if ask else {}
                ask = [k for k in targets if st[k] and k in wled]
                cfgs.update(zip(ask, pool.map(lambda k: wled_config(k[0]), ask)))
                last_targets, cfg_t = targets, t
            self.all = {k: (st[k], cfgs.get(k)) for k in targets}
            first = targets[0] if targets else None
            self.status = st.get(first)
            self.config = cfgs.get(first)
            time.sleep(max(0.1, 1.0 - (time.monotonic() - t)))


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class Tooltip:
    """Hover text for a widget (replaces the grey hint labels)."""
    def __init__(self, widget, text):
        self.widget, self.text, self.win, self.job = widget, text, None, None
        widget.bind("<Enter>", lambda e: self._schedule(), add="+")
        widget.bind("<Leave>", lambda e: self._hide(), add="+")
        widget.bind("<ButtonPress>", lambda e: self._hide(), add="+")

    def _schedule(self):
        self._hide()
        self.job = self.widget.after(500, self._show)

    def _show(self):
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.win = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.text, justify="left", background=COL["tip_bg"], foreground=COL["tip_fg"],
                 relief="solid", borderwidth=1, padx=6, pady=3).pack()

    def _hide(self):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.win:
            self.win.destroy()
            self.win = None


class App:
    def __init__(self, root, files, startup=False):
        self.root = root
        self.cfg = load_settings()
        self.schedule = norm_schedule(self.cfg.get("schedule"))
        self._sched_was = None          # last schedule check: True playing time, False not, None off
        self.player = Player()
        self.version = 0
        self.seeking = False
        self.photo = None
        self.shown_seq = -1
        self.last_stats = (time.monotonic(), 0, 0)
        self.rates = (0.0, 0.0)

        root.title(WINDOW_TITLE)
        root.minsize(820, 480)
        self.v = {}
        self.devices = output_devices()
        if self.cfg["audio_device"] not in [n for n, _ in self.devices]:
            self.cfg["audio_device"] = "System default"
        if self.cfg["outputs"] not in OUTPUT_MODES:
            self.cfg["outputs"] = OUTPUT_MODES[0]
        self.cfg["bridges"] = [b for b in self.cfg.get("bridges", []) if isinstance(b, dict) and b.get("ip")]
        self.bridges_win = None
        if self.cfg["overlay"] not in OVERLAY_MODES:
            self.cfg["overlay"] = OVERLAY_MODES[0]
        for name in ("ip", "scale", "loop", "audio_device", "outputs", "overlay"):
            self.v[name] = tk.StringVar(value=self.cfg[name])
        for name in ("port", "width", "height", "fps", "brightness", "image_secs", "volume", "sound_delay", "span_cols",
                     "span_gap_x", "span_gap_y"):
            self.v[name] = tk.StringVar(value=str(self.cfg[name]))
        self.v["gamma"] = tk.StringVar(value=f"{float(self.cfg['gamma']):.1f}")
        self.v["fps"].set(f"{float(self.cfg['fps']):g}")       # 30, not 30.0
        for name in ("pace", "blank_on_stop", "audio_on"):
            self.v[name] = tk.BooleanVar(value=bool(self.cfg[name]))
        self._build()
        for var in self.v.values():
            var.trace_add("write", lambda *a: self.push_settings())
        self.push_settings()

        for p in self.cfg.get("playlist", []):
            if is_live(p) or os.path.exists(split_view(p)[0]):
                self.add_path(p)
        first_new = len(self.paths)
        for p in files:
            self.add_path(os.path.abspath(p))

        self.poller = BoardPoller(lambda: [(t[0], t[1]) for t in self.player.settings["layout"][3] if t[0]])
        root.bind("<space>", lambda e: self.play_pause() if e.widget.winfo_class() not in ("TEntry", "TSpinbox", "TCombobox") else None)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.tick()
        self.root.after(2000, self._check_schedule)
        if files:
            self.play(first_new)        # start with the files given on the command line
        elif self.paths and self.v_autoplay.get():
            self.root.after(500, lambda: self.play(0))
        if not self.player.settings["ip"] and self.player.settings["outputs"] == OUTPUT_MODES[0]:
            self.root.after(300, lambda: self.find_boards(quiet=True))    # first start: look for displays
        if startup:
            root.iconify()              # started with Windows: out of the way

    # ---- layout ----------------------------------------------------------
    def _build(self):
        """Compact layout: paired fields on one row, hints in tooltips, and a
        fixed-height board status so the window never changes size."""
        root = self.root
        left = ttk.Frame(root, padding=(8, 6, 6, 4))
        left.grid(row=0, column=0, sticky="ns")
        right = ttk.Frame(root, padding=(0, 6, 8, 4))
        right.grid(row=0, column=1, sticky="nsew")
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        PAD = dict(pady=1)

        # Display
        d = ttk.LabelFrame(left, text="Display", padding=(8, 2, 8, 6))
        d.grid(row=0, column=0, sticky="ew")
        ttk.Label(d, text="Outputs").grid(row=0, column=0, sticky="w", padx=(0, 6), **PAD)
        ttk.Combobox(d, values=OUTPUT_MODES, textvariable=self.v["outputs"], state="readonly",
                     width=11).grid(row=0, column=1, columnspan=2, sticky="w", **PAD)
        self.bridges_btn = ttk.Button(d, text="Displays...", command=self.open_bridges)
        self.bridges_btn.grid(row=0, column=3, columnspan=2, sticky="e", **PAD)
        ip = ttk.Entry(d, textvariable=self.v["ip"], width=15)
        port = ttk.Entry(d, textvariable=self.v["port"], width=6)
        ttk.Label(d, text="IP").grid(row=1, column=0, sticky="w", **PAD)
        ip.grid(row=1, column=1, columnspan=2, sticky="w", **PAD)
        ttk.Label(d, text="Port").grid(row=1, column=3, sticky="e", padx=(6, 4), **PAD)
        port.grid(row=1, column=4, sticky="w", **PAD)
        wsp = ttk.Spinbox(d, from_=1, to=4096, textvariable=self.v["width"], width=5)
        hsp = ttk.Spinbox(d, from_=1, to=4096, textvariable=self.v["height"], width=5)
        ttk.Label(d, text="Size").grid(row=2, column=0, sticky="w", **PAD)
        wsp.grid(row=2, column=1, sticky="w", **PAD)
        ttk.Label(d, text="x").grid(row=2, column=2, padx=3, **PAD)
        hsp.grid(row=2, column=3, columnspan=2, sticky="w", **PAD)
        self.single_fields = [ip, port, wsp, hsp]
        b = ttk.Frame(d)
        b.grid(row=3, column=0, columnspan=5, sticky="ew", pady=(4, 0))
        self.find_btn = ttk.Button(b, text="Find boards", command=self.find_boards)
        self.find_btn.pack(side="left")
        ttk.Button(b, text="Get size", command=self.detect).pack(side="left", padx=(4, 0))
        self.demo_btn = ttk.Button(b, text="Demo on", width=9, command=self.demo_toggle)
        self.demo_shown = None          # shown only while a display in use has a demo mode
        # Fixed 3 lines: longer status text is cut off instead of growing the window
        self.board_lbl = tk.Label(d, text="", justify="left", anchor="nw", wraplength=270, height=3)
        self.board_lbl.grid(row=4, column=0, columnspan=5, sticky="ew", pady=(4, 0))

        # Output
        o = ttk.LabelFrame(left, text="Output", padding=(8, 2, 8, 6))
        o.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        # Common rates one click away; any 1-120 can still be typed
        fps = ttk.Combobox(o, values=[15, 20, 24, 30, 40, 50, 60], textvariable=self.v["fps"], width=4)
        ttk.Label(o, text="FPS").grid(row=0, column=0, sticky="w", **PAD)
        fps.grid(row=0, column=1, sticky="w", **PAD)
        ttk.Label(o, text="Image s").grid(row=0, column=2, sticky="e", padx=(8, 4), **PAD)
        ttk.Spinbox(o, from_=1, to=3600, textvariable=self.v["image_secs"], width=4).grid(row=0, column=3, sticky="w", **PAD)
        Tooltip(fps, "Match the video for the smoothest motion (often 24, 30 or 60).\n"
                     "Type any rate from 1 to 120. Large matrices may need less.")
        ttk.Label(o, text="Scaling").grid(row=1, column=0, sticky="w", **PAD)
        ttk.Combobox(o, values=SCALE_MODES, textvariable=self.v["scale"], state="readonly",
                     width=14).grid(row=1, column=1, columnspan=3, sticky="w", **PAD)
        ttk.Label(o, text="Repeat").grid(row=2, column=0, sticky="w", **PAD)
        ttk.Combobox(o, values=LOOP_MODES, textvariable=self.v["loop"], state="readonly",
                     width=14).grid(row=2, column=1, columnspan=3, sticky="w", **PAD)
        ttk.Label(o, text="Bright %").grid(row=3, column=0, sticky="w", **PAD)
        self._slider(o, "brightness").grid(row=3, column=1, columnspan=3, sticky="w", padx=(4, 0), **PAD)
        ttk.Label(o, text="Gamma").grid(row=4, column=0, sticky="w", **PAD)
        ttk.Spinbox(o, from_=1.0, to=3.0, increment=0.1, format="%.1f", textvariable=self.v["gamma"],
                    width=3).grid(row=4, column=1, sticky="w", **PAD)
        ttk.Label(o, text="Stats").grid(row=4, column=2, sticky="e", padx=(8, 4), **PAD)
        ov = ttk.Combobox(o, values=OVERLAY_MODES, textvariable=self.v["overlay"], state="readonly", width=5)
        ov.grid(row=4, column=3, sticky="w", **PAD)
        Tooltip(ov, "Draw send rate, board fps and refresh, and play time\nin the top-left corner of the picture.")
        cb = ttk.Frame(o)
        cb.grid(row=5, column=0, columnspan=4, sticky="w", pady=(3, 0))
        sp = ttk.Checkbutton(cb, text="Spread packets", variable=self.v["pace"])
        sp.pack(side="left")
        Tooltip(sp, "Spread each frame's packets over the frame time.\nTurn on if frames drop on a slow switch.")
        ttk.Checkbutton(cb, text="Blank on stop", variable=self.v["blank_on_stop"]).pack(side="left", padx=(10, 0))

        # Sound (played on this computer)
        a = ttk.LabelFrame(left, text="Sound (on this computer)", padding=(8, 2, 8, 6))
        a.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        if AUDIO_ERROR:
            ttk.Label(a, text=AUDIO_ERROR + "\nInstall with: pip install av sounddevice",
                      foreground=COL["warn"], wraplength=270).grid(row=0, column=0, sticky="w")
        else:
            ttk.Checkbutton(a, text="Play", variable=self.v["audio_on"]).grid(row=0, column=0, sticky="w", **PAD)
            self._slider(a, "volume").grid(row=0, column=1, columnspan=2, sticky="w", **PAD)
            ttk.Label(a, text="Output").grid(row=1, column=0, sticky="w", **PAD)
            ttk.Combobox(a, values=[n for n, _ in self.devices], textvariable=self.v["audio_device"],
                         state="readonly", width=24).grid(row=1, column=1, columnspan=2, sticky="w", **PAD)
            ttk.Label(a, text="Delay ms").grid(row=2, column=0, sticky="w", **PAD)
            dl = ttk.Spinbox(a, from_=-1000, to=3000, increment=10, textvariable=self.v["sound_delay"], width=5)
            dl.grid(row=2, column=1, sticky="w", **PAD)
            Tooltip(dl, "Raise it if the picture is ahead of the sound\n(Bluetooth headphones: try 150-250).")

        # Preview: grows and shrinks with the window
        self.canvas = tk.Canvas(right, width=PREVIEW_W, height=PREVIEW_H, bg=COL["canvas"], highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas_img = self.canvas.create_image(PREVIEW_W // 2, PREVIEW_H // 2)
        self.canvas_txt = self.canvas.create_text(PREVIEW_W // 2, PREVIEW_H // 2, fill="#666",
                                                  text="Add video files, then press Play")
        self.canvas.bind("<Configure>", lambda e: (self.canvas.coords(self.canvas_txt, e.width // 2, e.height // 2),
                                                   setattr(self, "shown_seq", -1)))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=3)

        # Transport
        t = ttk.Frame(right)
        t.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        t.columnconfigure(5, weight=1)
        ttk.Button(t, text=SYM["prev"], width=4, command=lambda: self.player.cmd("prev")).grid(row=0, column=0)
        self.play_btn = ttk.Button(t, text="▶ Play", width=9, command=self.play_pause)
        self.play_btn.grid(row=0, column=1, padx=2)
        ttk.Button(t, text=SYM["stop"] + " Stop", width=8, command=lambda: self.player.cmd("stop")).grid(row=0, column=2)
        ttk.Button(t, text=SYM["next"], width=4, command=lambda: self.player.cmd("next")).grid(row=0, column=3, padx=(2, 8))
        self.time_lbl = ttk.Label(t, text="0:00 / 0:00", width=16)
        self.time_lbl.grid(row=0, column=4)
        self.seek = ttk.Scale(t, from_=0, to=1, orient="horizontal")
        self.seek.grid(row=0, column=5, sticky="ew", padx=(4, 0))
        self.seek.bind("<ButtonPress-1>", self._seek_press)
        self.seek.bind("<ButtonRelease-1>", self._seek_release)

        # Playlist (buttons on the right of the list, not under it)
        pf = ttk.LabelFrame(right, text="Playlist (double-click to play)", padding=(6, 2, 6, 6))
        pf.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        right.rowconfigure(2, weight=1)
        pf.columnconfigure(0, weight=1)
        pf.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(pf, height=5, activestyle="none", selectmode="extended", exportselection=False)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(pf, orient="vertical", command=self.listbox.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.bind("<Double-Button-1>", lambda e: self.play(self.listbox.nearest(e.y)))
        self.listbox.bind("<Delete>", lambda e: self.remove())
        self.listbox.bind("<<ListboxSelect>>", self._selected)
        pb = ttk.Frame(pf)
        pb.grid(row=0, column=2, sticky="n", padx=(6, 0))
        add = ttk.Menubutton(pb, text="Add", width=9)
        am = tk.Menu(add, tearoff=0)
        am.add_command(label="Video or image files...", command=self.add_files)
        am.add_command(label="Screen or window...", command=self.add_screen)
        am.add_command(label="Camera...", command=self.add_camera)
        am.add_command(label="Text...", command=lambda: self.edit_item("text"))
        am.add_command(label="Clock...", command=lambda: self.edit_item("clock"))
        am.add_command(label="Script...", command=self.edit_script)
        add["menu"] = am
        add.pack(pady=(0, 2), fill="x")
        for text, fn in (("Remove", self.remove), ("Move up", lambda: self.move(-1)),
                         ("Move down", lambda: self.move(1)), ("Clear", self.clear)):
            ttk.Button(pb, text=text, width=11, command=fn).pack(pady=(0, 2))
        # Right-click an item: play / edit / remove
        self.item_menu = tk.Menu(self.listbox, tearoff=0)
        self.listbox.bind("<Button-3>", self._item_menu)
        if sys.platform == "darwin":    # Mac: right-click is Button-2 before Tk 9, or Ctrl+click
            self.listbox.bind("<Button-2>", self._item_menu)
            self.listbox.bind("<Control-Button-1>", self._item_menu)
        self.paths = []

        # Status bar
        bar = ttk.Frame(root, relief="sunken")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(0, weight=1)
        # width=1: long status text is cut off instead of widening the window
        self.status = ttk.Label(bar, text="", anchor="w", padding=(8, 1), width=1)
        self.status.grid(row=0, column=0, sticky="ew")
        self.v_autoplay = tk.BooleanVar(value=bool(self.cfg.get("autoplay")))
        self.v_startup = tk.BooleanVar(value=startup_registered())
        # A label, not a button: a button would make the status bar (and window) taller
        opt = ttk.Label(bar, text="\u2699", padding=(4, 1), cursor="hand2")
        om = tk.Menu(opt, tearoff=0)
        om.add_checkbutton(label="Play the playlist when opened", variable=self.v_autoplay,
                           command=self.save)
        om.add_command(label="Schedule...", command=lambda: ScheduleDialog(self))
        if os.name == "nt":             # a Run value in the registry: Windows only for now
            om.add_checkbutton(label="Open when Windows starts", variable=self.v_startup,
                               command=self.toggle_startup)
        opt.bind("<Button-1>", lambda e: om.tk_popup(e.x_root, e.y_root))
        opt.grid(row=0, column=1, sticky="e")
        Tooltip(opt, "Options for a wall that runs on its own")
        ttk.Label(bar, text="Bong 69", foreground=COL["muted"], padding=(4, 1, 8, 1)).grid(row=0, column=2, sticky="e")

    def _slider(self, parent, name):
        """0-100 slider bound to self.v[name], with the value shown beside it."""
        f = ttk.Frame(parent)
        sc = ttk.Scale(f, from_=0, to=100, orient="horizontal", length=130)
        sc.set(float(self.v[name].get()))
        sc.config(command=lambda x: self.v[name].set(str(int(float(x)))))
        sc.pack(side="left")
        ttk.Label(f, textvariable=self.v[name], width=4).pack(side="left", padx=(4, 0))
        return f

    @staticmethod
    def _row(parent, r, label, widget):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=2, padx=(0, 8))
        widget.grid(row=r, column=1, sticky="w", pady=2)

    # ---- settings --------------------------------------------------------
    def push_settings(self):
        """Validate the fields and hand a new settings dict to the player. Bad fields keep the old value."""
        old = self.player.settings or {k: self.cfg[k] for k in DEFAULTS if k not in ("playlist", "last_dir")}
        s = dict(old)

        def num(name, cast, lo, hi):
            try:
                x = cast(self.v[name].get())
                if lo <= x <= hi:
                    s[name] = x
            except ValueError:
                pass
        s["ip"] = self.v["ip"].get().strip() or old["ip"]
        num("port", int, 1, 65535)
        num("width", int, 1, 4096)
        num("height", int, 1, 4096)
        num("fps", float, 1, 120)
        num("brightness", int, 0, 100)
        num("gamma", float, 0.5, 4.0)
        num("image_secs", float, 0.5, 86400)
        s["scale"] = self.v["scale"].get()
        s["loop"] = self.v["loop"].get()
        s["pace"] = self.v["pace"].get()
        s["blank_on_stop"] = self.v["blank_on_stop"].get()
        s["overlay"] = self.v["overlay"].get()
        num("volume", int, 0, 100)
        num("sound_delay", int, -1000, 3000)
        s["audio_on"] = self.v["audio_on"].get()
        s["audio_device"] = self.v["audio_device"].get()
        s["audio_dev_idx"] = dict(self.devices).get(s["audio_device"])
        s["outputs"] = self.v["outputs"].get()
        num("span_cols", int, 1, 16)
        num("span_gap_x", int, 0, 1000)
        num("span_gap_y", int, 0, 1000)
        s["bridges"] = [dict(b) for b in self.cfg["bridges"]]
        s["layout"] = make_layout(s["outputs"], s["ip"], s["port"], s["width"], s["height"],
                                  s["bridges"], s["span_cols"], s["span_gap_x"], s["span_gap_y"])
        s["tile_luts"] = display_luts(s["layout"], s["bridges"])
        if hasattr(self, "single_fields"):
            multi = s["outputs"] != OUTPUT_MODES[0]
            for w in self.single_fields:
                w.state(["disabled"] if multi else ["!disabled"])
            self.bridges_btn.state(["!disabled"] if multi else ["disabled"])
        if self.bridges_win is not None:
            self.bridges_win.refresh()
        if s["brightness"] == 100 and abs(s["gamma"] - 1.0) < 1e-6:
            s["lut"] = None
        else:
            x = np.arange(256) / 255.0
            s["lut"] = np.clip(np.round(255 * (x ** s["gamma"]) * s["brightness"] / 100), 0, 255).astype(np.uint8)
        self.version += 1
        s["version"] = self.version
        self.player.settings = s

    def save(self):
        s = self.player.settings
        cfg = {k: s[k] for k in DEFAULTS if k in s}
        cfg["playlist"] = list(self.paths)
        cfg["last_dir"] = self.cfg.get("last_dir", "")
        cfg["autoplay"] = bool(self.v_autoplay.get()) if hasattr(self, "v_autoplay") else False
        cfg["schedule"] = self.schedule
        save_settings(cfg)

    # ---- schedule --------------------------------------------------------
    def _check_schedule(self):
        """Every 15 s: start the playlist when the schedule's playing time
        begins, stop and blank the displays when it ends. Only acts at those
        moments, so the wall can still be played or stopped by hand."""
        import datetime
        sc = self.schedule
        active = schedule_active(sc, datetime.datetime.now()) if sc["on"] else None
        if active is not None and active != self._sched_was:
            if active and self.player.state == "stopped" and self.paths:
                log_event(f"Schedule: playing time started ({sc['start']})")
                self.play(max(0, self.player.index) if self.player.index < len(self.paths) else 0)
            elif not active and self._sched_was is not None:
                log_event(f"Schedule: playing time ended ({sc['stop']})")
                self.player.cmd("stop")
                self.player.cmd("blank")
        self._sched_was = active
        self.root.after(15000, self._check_schedule)

    def schedule_text(self):
        """'Schedule: until 22:00' / 'Schedule: next 17:00' for the status bar, or ''."""
        sc = self.schedule
        if not sc["on"]:
            return ""
        return f"Schedule: on until {sc['stop']}" if self._sched_was else f"Schedule: starts {sc['start']}"

    # ---- playlist --------------------------------------------------------
    def add_path(self, p):
        self.paths.append(p)
        self.listbox.insert("end", item_label(p))
        self.player.playlist = list(self.paths)

    def add_screen(self):
        ScreenPicker(self.root, lambda spec: (self.add_path(spec), self.save()))

    def add_camera(self, index=None):
        """New camera item (index None) or change an existing one."""
        cur = live_info(split_view(self.paths[index])[0]) if index is not None else {}
        CameraPicker(self.root, cur, lambda path: self._item_done(path, index))

    def edit_item(self, kind, index=None):
        """New text/clock item (index None) or change an existing one."""
        cur = live_info(split_view(self.paths[index])[0]) if index is not None else {}
        ItemDialog(self.root, kind, cur, lambda path: self._item_done(path, index))

    def edit_script(self, index=None):
        """New script item (index None) or change an existing one."""
        cur = live_info(split_view(self.paths[index])[0]) if index is not None else {}
        ScriptDialog(self.root, cur, self.display_size(), lambda path: self._item_done(path, index))

    def edit_view(self, index):
        """Rotate / crop item `index`; the wall follows while the dialog is open, and the
        dialog follows the playlist selection."""
        d = getattr(self, "view_dlg", None)
        if d is None or not d.alive:
            d = self.view_dlg = ViewDialog(self.root, self.display_size(), self.save)
        else:
            self.save()             # keep what was done to the item shown so far
            d.win.lift()
        base, view = split_view(self.paths[index])
        p = self.player

        def playing():              # the item's frames, while the player shows it
            return p.raw if p.index == index and p.state != "stopped" else None

        def change(view):
            if index >= len(self.paths) or split_view(self.paths[index])[0] != base:
                return              # the playlist changed meanwhile
            old, self.paths[index] = self.paths[index], join_view(base, view)
            if item_label(old) != item_label(self.paths[index]):
                sel = self.listbox.curselection()
                self.listbox.delete(index)
                self.listbox.insert(index, item_label(self.paths[index]))
                for i in sel:
                    self.listbox.selection_set(i)
            self.player.playlist = list(self.paths)
        d.set_item(base, view, playing, change)

    def _selected(self, e=None):
        """Playlist selection changed: the Rotate / crop window switches to that item."""
        d = getattr(self, "view_dlg", None)
        sel = self.listbox.curselection()
        if d is not None and d.alive and len(sel) == 1 and split_view(self.paths[sel[0]])[0] != d.base:
            self.edit_view(sel[0])

    def display_size(self):
        """Picture size live items are drawn at (the whole span, or one bridge)."""
        try:
            kind, cw, ch, tiles = self.player.settings["layout"]
            return (cw, ch) if kind == "span" else (tiles[0][2], tiles[0][3])
        except (TypeError, KeyError, IndexError, ValueError):
            return (int(self.v["width"].get() or 256), int(self.v["height"].get() or 128))

    def _item_done(self, path, index):
        """Add a new live item (index None) or replace item `index`."""
        if index is None:
            self.add_path(path)
        else:
            path = join_view(path, split_view(self.paths[index])[1])     # keep its rotate / crop
            self.paths[index] = path
            self.listbox.delete(index)
            self.listbox.insert(index, item_label(path))
            self.player.playlist = list(self.paths)
            if self.player.index == index and self.player.state != "stopped":
                self.player.cmd("play", index)          # show the change now
        self.save()

    def _item_menu(self, e):
        i = self.listbox.nearest(e.y)
        if i < 0 or i >= len(self.paths):
            return
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(i)
        m = self.item_menu
        m.delete(0, "end")
        m.add_command(label="Play", command=lambda: self.play(i))
        p = split_view(self.paths[i])[0]
        if p.startswith(("text:", "clock:")):
            m.add_command(label="Edit...", command=lambda: self.edit_item(p.split(":", 1)[0], i))
        elif p.startswith("camera:"):
            m.add_command(label="Edit...", command=lambda: self.add_camera(i))
        elif p.startswith("script:"):
            m.add_command(label="Edit...", command=lambda: self.edit_script(i))
            m.add_command(label="Edit code...", command=lambda: open_script_editor(
                self.root, scripting.resolve(live_info(p).get("file", ""))))
        m.add_command(label="Rotate / crop...", command=lambda: self.edit_view(i))
        m.add_command(label="Remove", command=self.remove)
        m.tk_popup(e.x_root, e.y_root)

    def toggle_startup(self):
        try:
            set_startup(self.v_startup.get())
        except OSError as err:
            self.v_startup.set(startup_registered())
            messagebox.showerror(APP_NAME, f"Couldn't change the Windows start-up setting.\n\n{err}")

    def add_files(self):
        files = filedialog.askopenfilenames(title="Add videos or images", filetypes=FILE_TYPES,
                                            initialdir=self.cfg.get("last_dir") or None)
        for f in files:
            self.add_path(os.path.normpath(f))
        if files:
            self.cfg["last_dir"] = os.path.dirname(files[0])
            self.save()

    def remove(self):
        playing = self.player.index
        for i in sorted(self.listbox.curselection(), reverse=True):
            del self.paths[i]
            self.listbox.delete(i)
            if i == playing:
                self.player.cmd("stop")
            elif i < playing:
                self.player.index -= 1
        self.player.playlist = list(self.paths)

    def move(self, d):
        sel = self.listbox.curselection()
        if len(sel) != 1:
            return
        i, j = sel[0], sel[0] + d
        if not 0 <= j < len(self.paths):
            return
        self.paths[i], self.paths[j] = self.paths[j], self.paths[i]
        for k in (i, j):
            self.listbox.delete(k)
            self.listbox.insert(k, item_label(self.paths[k]))
        self.listbox.selection_set(j)
        if self.player.index in (i, j):
            self.player.index = j if self.player.index == i else i
        self.player.playlist = list(self.paths)

    def clear(self):
        self.player.cmd("stop")
        self.paths.clear()
        self.listbox.delete(0, "end")
        self.player.playlist = []

    # ---- transport -------------------------------------------------------
    def play(self, index=None):
        if not self.paths:
            self.add_files()
            if not self.paths:
                return
        if index is None:
            sel = self.listbox.curselection()
            index = sel[0] if sel else 0
        self.player.cmd("play", index)

    def play_pause(self):
        st = self.player.state
        if st == "playing":
            self.player.cmd("pause")
        elif st == "paused":
            self.player.cmd("resume")
        else:
            self.play()

    def _seek_press(self, e):
        self.seeking = True

    def _seek_release(self, e):
        self.seeking = False
        # ttk.Scale jumps by steps on a trough click; set the value from the click position instead
        frac = min(1.0, max(0.0, e.x / max(1, self.seek.winfo_width())))
        dur = self.player.duration
        if dur > 0:
            self.seek.set(frac * dur)
            self.player.cmd("seek", frac * dur)

    # ---- board -----------------------------------------------------------
    def detect(self):
        """Display size from the board (DDP config reply, or WLED's JSON API)."""
        s = self.player.settings
        if s["outputs"] != OUTPUT_MODES[0]:
            self.open_bridges()
            self.bridges_win.get_sizes()
            return
        if not s["ip"]:
            self.find_boards()
            return
        cfg = device_config(s["ip"], s["port"])
        if cfg and isinstance(cfg.get("w"), int) and isinstance(cfg.get("h"), int):
            self.v["width"].set(str(cfg["w"]))
            self.v["height"].set(str(cfg["h"]))
        else:
            messagebox.showerror(APP_NAME, f"The device at {s['ip']} didn't report its display size. "
                                           "Enter it by hand.")

    def find_boards(self, quiet=False):
        """Look for displays; quiet (first start) skips the "none answered" message."""
        if self.player.settings["outputs"] != OUTPUT_MODES[0]:
            self.open_bridges()
            self.bridges_win.find()
            return
        self.find_btn.state(["disabled"])
        self.find_btn.config(text="Searching...")
        port = self.player.settings["port"]
        result = []
        threading.Thread(target=lambda: (result.extend(ddp_discover(port=port)),
                                         self.root.after(0, lambda: self._found(result, quiet))),
                         daemon=True).start()

    def _use_device(self, dev):
        self.v["ip"].set(dev["ip"])
        if dev["w"] and dev["h"]:
            self.v["width"].set(str(dev["w"]))
            self.v["height"].set(str(dev["h"]))

    @staticmethod
    def _describe(dev):
        size = f"{dev['w']}x{dev['h']}" if dev["w"] else "size unknown"
        ver = f" v{dev['ver']}" if dev["ver"] else ""
        return f"{dev['ip']}   {dev['man']} {dev['mod']}{ver}   {size}"

    def _found(self, devices, quiet=False):
        self.find_btn.state(["!disabled"])
        self.find_btn.config(text="Find boards")
        if not devices and quiet:
            return
        if not devices:
            messagebox.showinfo(APP_NAME, "No displays answered.\n\nCheck the board is powered and on "
                                          "the same network as this computer.")
            return
        if len(devices) == 1:
            self._use_device(devices[0])
            return
        # Several: let the user pick one
        win = tk.Toplevel(self.root)
        place_over(win, self.root)
        win.title("Choose a display")
        win.transient(self.root)
        ttk.Label(win, text=f"{len(devices)} displays found:", padding=(10, 8, 10, 4)).pack(anchor="w")
        lb = tk.Listbox(win, width=60, height=min(10, len(devices)), exportselection=False)
        for dev in devices:
            lb.insert("end", self._describe(dev))
        lb.pack(padx=10, fill="both", expand=True)
        lb.selection_set(0)

        def use(_e=None):
            sel = lb.curselection()
            if sel:
                self._use_device(devices[sel[0]])
            win.destroy()
        lb.bind("<Double-Button-1>", use)
        bf = ttk.Frame(win, padding=10)
        bf.pack(fill="x")
        ttk.Button(bf, text="Use", command=use).pack(side="right")
        ttk.Button(bf, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 6))

    def show_demo(self, shown, on=False):
        """The Demo button, only for displays that have a demo mode (WLED doesn't)."""
        if shown != self.demo_shown:
            if shown:
                self.demo_btn.pack(side="left", padx=(4, 0))
            else:
                self.demo_btn.pack_forget()
            self.demo_shown = shown
        self.demo_btn.config(text="Demo off" if on else "Demo on")

    def open_bridges(self):
        if self.bridges_win is None:
            self.bridges_win = BridgesDialog(self)
        else:
            self.bridges_win.win.lift()

    def bridges_changed(self):
        self.push_settings()
        self.save()

    def _demo_statuses(self):
        """Status dicts of the displays in use that have a demo mode (the bridge; WLED doesn't)."""
        allst = self.poller.all
        sts = [(t[0], t[1], (allst.get((t[0], t[1])) or (None,))[0]) for t in self.player.settings["layout"][3]]
        return [(ip, port, st) for ip, port, st in sts if st and "demo" in st]

    def demo_toggle(self):
        """Turn demo mode on or off (DDP control command) on every display that has one."""
        demo = self._demo_statuses()
        on = not any(st["demo"] for _, _, st in demo)
        for ip, port, st in demo:
            try:
                ddp_control(ip, {"control": {"demo": on}}, port)
            except OSError as e:
                messagebox.showerror(APP_NAME, f"Couldn't reach the board at {ip}\n\n{e}")
                return
            st["demo"] = on              # show the new state right away; the next poll confirms it

    # ---- periodic UI update ----------------------------------------------
    def tick(self):
        p = self.player
        s = p.settings

        # Preview
        if p.preview_seq != self.shown_seq:
            self.shown_seq = p.preview_seq
            img = p.preview
            if img is None:
                self.canvas.itemconfigure(self.canvas_img, image="")
                self.canvas.itemconfigure(self.canvas_txt, state="normal")
            else:
                h, w = img.shape[:2]
                lay = s["layout"]
                if lay[0] == "span" and len(lay[3]) > 1 and (w, h) == (lay[1], lay[2]):
                    img = img.copy()
                    for t in lay[3]:            # dotted lines around each bridge's piece of the picture
                        x0, y0 = t[4], t[5]
                        x1, y1 = min(w - 1, x0 + t[6]), min(h - 1, y0 + t[7])
                        for x in {x0, x1} - {0, w - 1}:
                            img[y0:y1:2, x] = (255, 255, 255)
                        for y in {y0, y1} - {0, h - 1}:
                            img[y, x0:x1:2] = (255, 255, 255)
                cw, ch = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
                k = min(cw / w, ch / h)
                if k >= 1:
                    k = int(k)
                    img = cv2.resize(img, (w * k, h * k), interpolation=cv2.INTER_NEAREST)
                else:
                    img = cv2.resize(img, (max(1, int(w * k)), max(1, int(h * k))), interpolation=cv2.INTER_AREA)
                ppm = b"P6\n%d %d\n255\n" % (img.shape[1], img.shape[0]) + img.tobytes()
                self.photo = tk.PhotoImage(data=ppm, format="PPM")
                self.canvas.coords(self.canvas_img, cw // 2, ch // 2)
                self.canvas.itemconfigure(self.canvas_img, image=self.photo)
                self.canvas.itemconfigure(self.canvas_txt, state="hidden")

        # Transport
        self.play_btn.config(text=SYM["pause"] + " Pause" if p.state == "playing" else "▶ Play")
        dur = p.duration
        self.time_lbl.config(text=f"{fmt_time(p.pos)} / {fmt_time(dur)}" if p.state != "stopped" else "0:00 / 0:00")
        if not self.seeking:
            self.seek.config(to=max(dur, 0.001))
            self.seek.set(min(p.pos, dur) if p.state != "stopped" else 0)
        for i in range(len(self.paths)):
            self.listbox.itemconfig(i, bg=COL["playing"] if i == p.index and p.state != "stopped" else "")

        # Rates (once a second)
        now = time.monotonic()
        t, f, b = self.last_stats
        if now - t >= 1.0:
            self.rates = ((p.sent_frames - f) / (now - t), (p.sent_bytes - b) * 8 / (now - t) / 1e6)
            self.last_stats = (now, p.sent_frames, p.sent_bytes)
            p.overlay = (p.overlay[0] + 1, self._overlay_lines())

        # Board status (DDP status + config replies)
        st, cfg = self.poller.status, self.poller.config
        warn = False
        lay = s["layout"]
        if lay[0] != "one":
            self._multi_status(lay)
        elif st:
            ver = f" v{st['ver']}" if st.get("ver") else ""
            lines = [f"{st.get('man', '?')} {st.get('mod', '')}{ver}".strip()]
            size = (cfg.get("w"), cfg.get("h")) if cfg else (None, None)
            demo = bool(st.get("demo"))
            info = []
            if isinstance(size[0], int):
                info.append(f"{size[0]}x{size[1]}")
            if "fps" in st:
                info.append(f"drawing {st['fps']:.1f} fps" if not demo else "demo mode ON")
            if "hz" in st:
                info.append(f"{st['hz']} Hz refresh")
            if info:
                lines.append(", ".join(info))
            if demo:
                lines.append("Demo mode is on: press 'Demo off' to stream.")
                warn = True
            if st.get("wled") and st.get("override"):
                lines.append("WLED's realtime override is on: turn it off in WLED to stream.")
                warn = True
            elif st.get("wled") and not st.get("live") and p.state == "playing" and self.rates[0] > 0:
                lines.append("WLED isn't showing the stream (another source, or check the IP and port).")
                warn = True
            if isinstance(size[0], int) and size != (s["width"], s["height"]):
                lines.append("Size doesn't match the board: press 'Get size'.")
                warn = True
            self.board_lbl.config(text="\n".join(lines), fg=COL["warn"] if warn else COL["ok"])
            self.show_demo("demo" in st, demo)
        elif not s["ip"]:
            self.board_lbl.config(text="No display set: click Find boards,\nor type its IP address.",
                                  fg=COL["muted"])
            self.show_demo(False)
        else:
            self.board_lbl.config(text=f"No reply from {s['ip']}:{s['port']}", fg=COL["muted"])
            self.show_demo(False)

        # Status bar
        parts = [p.state.capitalize()]
        if p.state != "stopped":
            parts.append(f"sending {self.rates[0]:.1f} fps, {self.rates[1]:.1f} Mbit/s")
            if p.index >= 0 and p.index < len(self.paths):
                parts.insert(1, item_label(self.paths[p.index]))
        if p.late:
            parts.append(f"{p.late} late frames")
        if self.schedule_text():
            parts.append(self.schedule_text())
        if p.crashes or p.stalls:
            n = p.crashes + p.stalls
            parts.append(f"{n} recover{'y' if n == 1 else 'ies'} (see streamer.log)")
        if p.message:
            parts.append(p.message)
        if p.audio_out is not None and p.audio_out.error:
            parts.append(p.audio_out.error)
        self.status.config(text="   |   ".join(parts))

        self.root.after(100, self.tick)

    def _overlay_lines(self):
        """Stats drawn on the picture: what we send, what the board(s) draw, play time."""
        p = self.player
        lines = [f"TX {self.rates[0]:.1f}FPS {self.rates[1]:.1f}MBIT"]
        sts = [v[0] for v in self.poller.all.values() if v[0]]
        fps = [st["fps"] for st in sts if isinstance(st.get("fps"), (int, float))]
        hz = [st["hz"] for st in sts if isinstance(st.get("hz"), (int, float))]
        if fps:
            f = f"{fps[0]:.1f}" if len(fps) == 1 else f"{min(fps):.0f}-{max(fps):.0f}"
            lines.append(f"BOARD {f}FPS" + (f" {min(hz)}HZ" if hz else ""))
        else:
            lines.append("BOARD NO REPLY")
        t = f"{fmt_time(p.pos)}/{fmt_time(p.duration)}" if p.duration else fmt_time(p.pos)
        lines.append(t + (f" LATE {p.late}" if p.late else ""))
        return lines

    def _multi_status(self, lay):
        tiles = lay[3]
        name = lambda t: t[0] if t[1] == 4048 else f"{t[0]}:{t[1]}"
        allst = self.poller.all
        ok = [t for t in tiles if (allst.get((t[0], t[1])) or (None,))[0]]
        lines = [f"{lay[0].capitalize()} to {len(tiles)} display" + ("s" if len(tiles) != 1 else "")
                 + (f", picture {lay[1]}x{lay[2]}" if lay[0] == "span" else "")
                 + f": {len(ok)} answering"]
        warn = len(ok) < len(tiles)
        missing = [name(t) for t in tiles if t not in ok]
        if missing:
            lines.append("No reply: " + ", ".join(missing))
        demo = [name(t) for t in ok if allst[(t[0], t[1])][0].get("demo")]
        if demo:
            lines.append("Demo mode on: " + ", ".join(demo))
            warn = True
        sts = {t: allst[(t[0], t[1])][0] for t in ok}
        over = [name(t) for t in ok if sts[t].get("wled") and sts[t].get("override")]
        if over:
            lines.append("WLED realtime override on (turn it off in WLED): " + ", ".join(over))
            warn = True
        if self.player.state == "playing" and self.rates[0] > 0:
            idle = [name(t) for t in ok if sts[t].get("wled") and not sts[t].get("override") and not sts[t].get("live")]
            if idle:
                lines.append("WLED not showing the stream: " + ", ".join(idle))
                warn = True
        wrong = []
        for t in ok:
            c = allst[(t[0], t[1])][1] or {}
            if isinstance(c.get("w"), int) and (c["w"], c.get("h")) != (t[2], t[3]):
                wrong.append(f"{name(t)} is {c['w']}x{c.get('h')}")
        if wrong:
            lines.append("Wrong size: " + "; ".join(wrong) + " (Displays... > Get sizes)")
            warn = True
        fps = [allst[(t[0], t[1])][0].get("fps") for t in ok]
        fps = [f for f in fps if isinstance(f, (int, float))]
        if fps and not demo:
            lines.append(f"drawing {min(fps):.1f}-{max(fps):.1f} fps")
        self.board_lbl.config(text="\n".join(lines), fg=COL["warn"] if warn else COL["ok"])
        demo = self._demo_statuses()
        self.show_demo(bool(demo), any(st["demo"] for _, _, st in demo))

    def on_close(self):
        self.save()
        self.player.cmd("stop")
        self.root.after(300, self.root.destroy)


# ---------------------------------------------------------------------------
# Start with Windows: a value under HKCU\...\Run (no file, no console window)
# ---------------------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _startup_command():
    if getattr(sys, "frozen", False):           # packaged .exe
        return f'"{sys.executable}" --startup'
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return f'"{w if os.path.exists(w) else exe}" "{os.path.abspath(__file__)}" --startup'


def startup_registered():
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_startup(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _startup_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


def place_over(win, parent, size=None):
    """Open a new window over the middle of `parent` (Windows otherwise puts it in
    the screen's top-left corner). Call right after creating it: it shows once built.
    Returns the function that shows it, for windows that need to be visible sooner."""
    win.withdraw()

    def show():
        if not win.winfo_exists() or win.winfo_viewable():
            return
        win.update_idletasks()
        w, h = size or (win.winfo_reqwidth(), win.winfo_reqheight())
        p = parent.winfo_toplevel()
        x = p.winfo_rootx() + (p.winfo_width() - w) // 2
        y = p.winfo_rooty() + max(0, (p.winfo_height() - h) // 3)
        win.geometry(f"+{x}+{y}")
        win.deiconify()
    win.after_idle(show)
    return show


def _dialog(root, title):
    win = tk.Toplevel(root)
    place_over(win, root)
    win.title(title)
    win.transient(root)
    win.resizable(False, False)
    frm = ttk.Frame(win, padding=12)
    frm.pack(fill="both", expand=True)
    return win, frm


def _buttons(win, frm, row, ok):
    bf = ttk.Frame(frm)
    bf.grid(row=row, column=0, columnspan=2, sticky="e", pady=(10, 0))
    ttk.Button(bf, text="Cancel", command=win.destroy).pack(side="right")
    ttk.Button(bf, text="OK", command=ok).pack(side="right", padx=(0, 6))
    win.bind("<Return>", lambda e: ok())
    win.bind("<Escape>", lambda e: win.destroy())


class ScreenPicker:
    """Choose the main screen or a window to send to the wall."""

    def __init__(self, root, done):
        if os.name != "nt" and SCREEN_ERROR:
            messagebox.showinfo(APP_NAME, SCREEN_ERROR)
            return
        win, frm = _dialog(root, "Add screen or window")
        ttk.Label(frm, text="Send this to the wall:").grid(row=0, column=0, sticky="w")
        lb = tk.Listbox(frm, width=56, height=10, exportselection=False)
        lb.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(4, 0))
        specs = ["screen:desktop"] + ["screen:window:" + t for t in list_windows()]
        for sp in specs:
            lb.insert("end", item_label(sp))
        lb.selection_set(0)
        hint = ("A window is captured even when other windows cover it\n(not while it's minimised)."
                if os.name == "nt" else "The whole main screen (single windows: Windows only for now).")
        ttk.Label(frm, text=hint, foreground=COL["muted"]).grid(row=2, column=0, columnspan=2, sticky="w",
                                                              pady=(6, 0))

        def ok(_e=None):
            sel = lb.curselection()
            if sel:
                done(specs[sel[0]])
            win.destroy()
        lb.bind("<Double-Button-1>", ok)
        _buttons(win, frm, 3, ok)


class CameraPicker:
    """Choose a camera (with a snapshot from each) to send to the wall."""

    def __init__(self, root, cur, done):
        self.win, frm = _dialog(root, "Camera")
        self.frm, self.done, self.cur = frm, done, cur
        self.status = ttk.Label(frm, text="Looking for cameras...")
        self.status.grid(row=0, column=0, columnspan=2, sticky="w")
        self.choice = tk.IntVar(value=0)       # position in the list of cameras found
        self.mirror = tk.BooleanVar(value=bool(cur.get("mirror", True)))
        self.photos = []
        result = []
        threading.Thread(target=lambda: (result.extend(list_cameras()),
                                         root.after(0, lambda: self._show(result))), daemon=True).start()

    def _show(self, cams):
        if not self.win.winfo_exists():
            return
        frm = self.frm
        if not cams:
            self.status.config(text="No camera found. Plug one in, or close other apps\n"
                                    "that are using it, then try again.")
            _buttons(self.win, frm, 3, self.win.destroy)
            return
        self.status.config(text="Send this camera to the wall:")
        grid = ttk.Frame(frm)
        grid.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        now = self.cur.get("name") or int(self.cur.get("index", 0))
        self.keys = [i for i, _ in cams]
        self.choice.set(self.keys.index(now) if now in self.keys else 0)
        if not self.cur:                        # new item: no mirror for a virtual camera
            self.mirror.set(not isinstance(self.keys[self.choice.get()], str))
        for n, (i, f) in enumerate(cams):
            h, w = f.shape[:2]
            th = cv2.resize(f, (192, max(1, round(192 * h / w))), interpolation=cv2.INTER_AREA)
            th = cv2.cvtColor(th, cv2.COLOR_BGR2RGB)
            ppm = b"P6\n%d %d\n255\n" % (th.shape[1], th.shape[0]) + th.tobytes()
            ph = tk.PhotoImage(data=ppm, format="PPM")
            self.photos.append(ph)
            cell = ttk.Frame(grid)
            cell.grid(row=n // 3, column=n % 3, padx=(0, 8), pady=(0, 6), sticky="n")
            tk.Label(cell, image=ph, bd=0).pack()
            name = i if isinstance(i, str) else f"Camera {i + 1}"
            ttk.Radiobutton(cell, text=f"{name}  ({w}x{h})", value=n, variable=self.choice,
                            # a virtual camera (OBS...) sends a picture, not a selfie view
                            command=lambda i=i: self.mirror.set(not isinstance(i, str))
                            ).pack(anchor="w", pady=(2, 0))
        ttk.Checkbutton(frm, text="Mirror (like a selfie: best when the camera faces the wall's viewers)",
                        variable=self.mirror).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        def ok():
            i = self.keys[self.choice.get()]
            d = {"name": i} if isinstance(i, str) else {"index": i}
            self.done("camera:" + json.dumps(dict(d, mirror=self.mirror.get())))
            self.win.destroy()
        _buttons(self.win, frm, 3, ok)


class ItemDialog:
    """Settings for a text or clock item."""

    def __init__(self, root, kind, cur, done):
        win, frm = _dialog(root, "Text" if kind == "text" else "Clock")
        v = {}
        r = 0
        if kind == "text":
            ttk.Label(frm, text="Text").grid(row=r, column=0, sticky="w", pady=2)
            v["text"] = tk.StringVar(value=cur.get("text", ""))
            e = ttk.Entry(frm, textvariable=v["text"], width=34)
            e.grid(row=r, column=1, sticky="w", pady=2)
            e.focus_set()
            r += 1
            ttk.Label(frm, text="Effect").grid(row=r, column=0, sticky="w", pady=2)
            v["effect"] = tk.StringVar(value=cur.get("effect", "Auto"))
            ef = ttk.Combobox(frm, values=TEXT_EFFECTS, textvariable=v["effect"], state="readonly", width=12)
            ef.grid(row=r, column=1, sticky="w", pady=2)
            Tooltip(ef, "Auto: still if it fits, scrolling if it's too long.\n"
                        "Fit (still): wraps onto lines, as big as fits.\n"
                        "Scroll up rolls lines like film credits.")
            r += 1
            ttk.Label(frm, text="Size").grid(row=r, column=0, sticky="w", pady=2)
            sf = ttk.Frame(frm)
            sf.grid(row=r, column=1, sticky="w", pady=2)
            v["size"] = tk.StringVar(value=cur.get("size", "Medium"))
            ttk.Combobox(sf, values=list(TEXT_SIZES), textvariable=v["size"], state="readonly",
                         width=8).pack(side="left")
            ttk.Label(sf, text="Speed").pack(side="left", padx=(10, 4))
            v["speed"] = tk.StringVar(value=cur.get("speed", "Normal"))
            ttk.Combobox(sf, values=list(SPEEDS), textvariable=v["speed"], state="readonly",
                         width=8).pack(side="left")
            r += 1
        else:
            ttk.Label(frm, text="Look").grid(row=r, column=0, sticky="w", pady=2)
            v["look"] = tk.StringVar(value=cur.get("look", "Plain"))
            ttk.Combobox(frm, values=CLOCK_LOOKS, textvariable=v["look"], state="readonly",
                         width=16).grid(row=r, column=1, sticky="w", pady=2)
            r += 1
            ttk.Label(frm, text="Style").grid(row=r, column=0, sticky="w", pady=2)
            v["style"] = tk.StringVar(value=cur.get("style", "24 hour"))
            ttk.Combobox(frm, values=["24 hour", "12 hour"], textvariable=v["style"], state="readonly",
                         width=10).grid(row=r, column=1, sticky="w", pady=2)
            r += 1
            v["seconds"] = tk.BooleanVar(value=bool(cur.get("seconds")))
            v["date"] = tk.BooleanVar(value=bool(cur.get("date")))
            cf = ttk.Frame(frm)
            cf.grid(row=r, column=1, sticky="w", pady=2)
            ttk.Checkbutton(cf, text="Seconds", variable=v["seconds"]).pack(side="left")
            ttk.Checkbutton(cf, text="Date", variable=v["date"]).pack(side="left", padx=(10, 0))
            r += 1
        ttk.Label(frm, text="Font").grid(row=r, column=0, sticky="w", pady=2)
        v["font"] = tk.StringVar(value=font_name(cur.get("font", "Built-in")))
        ttk.Combobox(frm, values=list(FONTS), textvariable=v["font"], state="readonly",
                     width=16).grid(row=r, column=1, sticky="w", pady=2)
        r += 1
        ttk.Label(frm, text="Color").grid(row=r, column=0, sticky="w", pady=2, padx=(0, 8))
        colours = list(COLOURS) if kind == "text" else [STYLE_COLOURS] + list(COLOURS)
        default = "White" if kind == "text" or cur.get("look", "Plain") == "Plain" else STYLE_COLOURS
        v["colour"] = tk.StringVar(value=cur.get("colour", default))
        ttk.Combobox(frm, values=colours, textvariable=v["colour"], state="readonly",
                     width=12).grid(row=r, column=1, sticky="w", pady=2)
        r += 1
        if kind == "clock":
            ttk.Label(frm, text="Animated looks show hours and minutes; seconds, date and font "
                                "apply to Plain.\nStyle colors = the look's own colors.",
                      foreground=COL["muted"]).grid(row=r, column=0, columnspan=2, sticky="w", pady=(4, 0))
            r += 1
        ttk.Label(frm, text="Shown for 'Image s' seconds, or nonstop with Repeat: Loop current.",
                  foreground=COL["muted"]).grid(row=r, column=0, columnspan=2, sticky="w", pady=(6, 0))
        r += 1

        def ok():
            d = {k: x.get() for k, x in v.items()}
            if kind == "text" and not d["text"].strip():
                return
            done(kind + ":" + json.dumps(d))
            win.destroy()
        _buttons(win, frm, r, ok)


class ViewDialog:
    """Rotate an item and/or show only part of it: a box in the wall's shape on a picture of
    the item. Changes go to the wall as they're made; Cancel puts the item's old ones back.
    Selecting another playlist item while it's open switches to that one (set_item)."""
    CW, CH = 480, 300           # picture area

    def __init__(self, root, size, save):
        self.win, frm = _dialog(root, "Rotate / crop")
        self.size, self.save = size, save
        self.aspect = size[0] / size[1]
        self.rot = tk.IntVar(value=0)
        self.v_crop = tk.BooleanVar(value=False)
        self.gen = 0                    # which item the grab thread works for
        self.alive = True
        self.playing = lambda: None
        self.frame = self.shown = None
        self.error = ""

        rf = ttk.Frame(frm)
        rf.grid(row=0, column=0, sticky="w")
        ttk.Label(rf, text="Rotate").pack(side="left", padx=(0, 8))
        for deg, name in ROTATIONS.items():
            ttk.Radiobutton(rf, text=name, value=deg, variable=self.rot,
                            command=self._rotated).pack(side="left", padx=(0, 6))
        ttk.Checkbutton(frm, text="Show only part of the picture", variable=self.v_crop,
                        command=self._crop_toggled).grid(row=1, column=0, sticky="w", pady=(6, 4))
        cv = self.canvas = tk.Canvas(frm, width=self.CW, height=self.CH, bg=COL["canvas"],
                                     highlightthickness=0, cursor="crosshair")
        cv.grid(row=2, column=0)
        self.img_id = cv.create_image(0, 0, anchor="nw")
        self.box_id = cv.create_rectangle(0, 0, 0, 0, outline="#4fc3f7", width=2, state="hidden")
        self.txt_id = cv.create_text(self.CW // 2, self.CH // 2, fill="#888", text="Getting a picture...")
        self.hint = ttk.Label(frm, foreground=COL["muted"],
                              text="Drag the box to move it, drag beside it to draw a new one, scroll to zoom.")
        self.hint.grid(row=3, column=0, sticky="w", pady=(4, 0))
        bf = ttk.Frame(frm)
        bf.grid(row=4, column=0, sticky="e", pady=(10, 0))
        ttk.Button(bf, text="Cancel", command=self.cancel).pack(side="right")
        ttk.Button(bf, text="OK", command=self.ok).pack(side="right", padx=(0, 6))
        self.win.bind("<Return>", lambda e: self.ok())
        self.win.bind("<Escape>", lambda e: self.cancel())
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.bind("<Destroy>", lambda e: setattr(self, "alive", False) if e.widget is self.win else None)
        cv.bind("<ButtonPress-1>", self._press)
        cv.bind("<B1-Motion>", self._motion)
        cv.bind("<ButtonRelease-1>", self._release)
        cv.bind("<MouseWheel>", lambda e: self._zoom(0.9 if e.delta > 0 else 1 / 0.9))
        cv.bind("<Button-4>", lambda e: self._zoom(0.9))         # Linux wheel
        cv.bind("<Button-5>", lambda e: self._zoom(1 / 0.9))
        self._poll()

    def set_item(self, base, view, playing, change):
        """Show playlist item `base` with its current view. playing() returns the player's
        frame while it shows this item; change(view) applies a view to it."""
        self.base, self.playing, self.change = base, playing, change
        self.win.title("Rotate / crop - " + _base_label(base))
        self.orig = dict(view)
        self.rot.set(view.get("rotate", 0) if view.get("rotate") in ROTATIONS else 0)
        c = view.get("crop")
        self.crop = [float(x) for x in c] if isinstance(c, list) and len(c) == 4 else None
        self.v_crop.set(self.crop is not None)
        self.frame = self.shown = None  # source frame (not rotated) / the one drawn
        self.small = None               # rotated picture at canvas size (RGB)
        self.geo = None                 # (x0, y0, w, h) of the picture on the canvas
        self.error = ""
        self.drag = None
        self.odd = self.rot.get() % 180
        self.canvas.itemconfigure(self.img_id, image="")
        self.canvas.itemconfigure(self.box_id, state="hidden")
        self.canvas.itemconfigure(self.txt_id, text="Getting a picture...", state="normal")
        self._crop_toggled(send=False)
        self.gen += 1
        threading.Thread(target=self._grab, args=(self.gen,), daemon=True).start()

    # ---- the picture --------------------------------------------------------
    def _grab(self, gen):
        """Frames of the item while it isn't playing (when it is, the player's are used)."""
        src, odd, t0 = None, None, time.monotonic()
        base, playing = self.base, self.playing
        current = lambda: self.alive and gen == self.gen

        def put(f):
            if current():
                self.frame = f
        try:
            while current():
                if playing() is not None:
                    if src:
                        src.close()
                        src = None
                elif src is None or (src.live and self.odd != odd):
                    if src:
                        src.close()
                    odd = self.odd      # live items draw at the turned size, as on the wall
                    src = Source(base, self.size[::-1] if odd else self.size)
                    if not src.live:    # picture, or a still from 10% into the video
                        f = src.frame_at(min(src.duration * 0.1, 30)) if src.duration else None
                        put(src.frame_at(0) if f is None else f)
                        return
                if src is not None:
                    put(src.frame_at(time.monotonic() - t0))
                time.sleep(0.2)
        except Exception as e:
            if current():
                self.error = f"Can't show this item: {e}"
        finally:
            if src:
                src.close()

    def _poll(self):
        if not self.alive:
            return
        f = self.playing()
        f = self.frame if f is None else f
        if f is not None and f is not self.shown:
            self.shown = f
            self._fit()
            self._draw()
        elif f is None and self.error:
            self.canvas.itemconfigure(self.txt_id, text=self.error)
        self.win.after(150, self._poll)

    def _fit(self):
        """Rotate the frame and scale it into the canvas."""
        img = apply_view(self.shown, {"rotate": self.rot.get()})
        h, w = img.shape[:2]
        k = min(self.CW / w, self.CH / h)
        dw, dh = max(1, round(w * k)), max(1, round(h * k))
        self.small = cv2.cvtColor(cv2.resize(img, (dw, dh), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        self.geo = ((self.CW - dw) // 2, (self.CH - dh) // 2, dw, dh)
        if self.crop is None and self.v_crop.get():
            self.crop = self._default_box(0.7)

    def _draw(self):
        if self.small is None:
            return
        x0, y0, dw, dh = self.geo
        img = self.small
        cv = self.canvas
        if self.crop is not None and self.v_crop.get():
            bx, by, bw, bh = self._box_px()
            img = (img * 0.35).astype(np.uint8)         # dim what the wall won't show
            img[by:by + bh, bx:bx + bw] = self.small[by:by + bh, bx:bx + bw]
            cv.coords(self.box_id, x0 + bx, y0 + by, x0 + bx + bw, y0 + by + bh)
            cv.itemconfigure(self.box_id, state="normal")
        else:
            cv.itemconfigure(self.box_id, state="hidden")
        ppm = b"P6\n%d %d\n255\n" % (dw, dh) + np.ascontiguousarray(img).tobytes()
        self.photo = tk.PhotoImage(data=ppm, format="PPM")
        cv.itemconfigure(self.img_id, image=self.photo)
        cv.coords(self.img_id, x0, y0)
        cv.itemconfigure(self.txt_id, state="hidden")

    # ---- the box (fractions of the rotated picture) ----------------------------
    def _limits(self):
        """Largest box width and height (fractions) in the wall's shape."""
        _, _, dw, dh = self.geo
        if dw / dh > self.aspect:
            return self.aspect * dh / dw, 1.0
        return 1.0, dw / (self.aspect * dh)

    def _default_box(self, k=1.0):
        mw, mh = self._limits()
        return [(1 - mw * k) / 2, (1 - mh * k) / 2, mw * k, mh * k]

    def _box_px(self):
        _, _, dw, dh = self.geo
        x, y, w, h = self.crop
        return round(x * dw), round(y * dh), max(1, round(w * dw)), max(1, round(h * dh))

    def _set_box(self, cx, cy, w):
        """Box of width w (fraction) centered near (cx, cy), kept inside the picture."""
        mw, mh = self._limits()
        w = min(mw, max(mw * 0.05, w))
        h = w * mh / mw
        x = min(1 - w, max(0.0, cx - w / 2))
        y = min(1 - h, max(0.0, cy - h / 2))
        self.crop = [x, y, w, h]

    def _frac(self, e):
        x0, y0, dw, dh = self.geo
        return (e.x - x0) / dw, (e.y - y0) / dh

    def _press(self, e):
        if self.small is None:
            return
        if not self.v_crop.get():
            self.v_crop.set(True)
            self._crop_toggled(send=False)
        fx, fy = self._frac(e)
        x, y, w, h = self.crop
        self.drag = ("move" if x <= fx <= x + w and y <= fy <= y + h else "new", fx, fy, list(self.crop))

    def _motion(self, e):
        if not self.drag:
            return
        mode, fx0, fy0, (x, y, w, h) = self.drag
        fx, fy = self._frac(e)
        if mode == "move":
            self._set_box(x + w / 2 + fx - fx0, y + h / 2 + fy - fy0, w)
        else:                       # draw a box from the press point, in the wall's shape
            mw, mh = self._limits()
            nw = max(abs(fx - fx0), abs(fy - fy0) * mw / mh)
            if nw < 0.02:
                return
            nh = nw * mh / mw
            cx = fx0 + (nw / 2 if fx >= fx0 else -nw / 2)
            cy = fy0 + (nh / 2 if fy >= fy0 else -nh / 2)
            self._set_box(cx, cy, nw)
        self._draw()
        self._send()

    def _release(self, e):
        if self.drag and self.drag[0] == "new" and self.crop == self.drag[3]:
            fx, fy = self._frac(e)          # a click beside the box: center the box there
            self._set_box(fx, fy, self.crop[2])
        self.drag = None
        self._draw()
        self._send()

    def _zoom(self, k):
        if self.small is None or not self.v_crop.get() or self.crop is None:
            return
        x, y, w, h = self.crop
        self._set_box(x + w / 2, y + h / 2, w * k)
        self._draw()
        self._send()

    # ---- changes ----------------------------------------------------------------
    def _rotated(self):
        self.odd = self.rot.get() % 180
        self.crop = None                    # a new picture shape: start the box again
        if self.shown is not None:
            self._fit()
            self._draw()
        self._send()

    def _crop_toggled(self, send=True):
        if self.v_crop.get() and self.crop is None and self.geo:
            self.crop = self._default_box(0.7)
        self.canvas.config(cursor="crosshair" if self.v_crop.get() else "")
        self._draw()
        if send:
            self._send()

    def view(self):
        v = {"rotate": self.rot.get()}
        if self.v_crop.get() and self.crop is not None:
            v["crop"] = [round(c, 4) for c in self.crop]
        return v

    def _send(self):
        self.change(self.view())

    def ok(self):
        self._send()
        self.save()
        self.win.destroy()

    def cancel(self):
        self.change(self.orig)
        self.win.destroy()


def open_manual():
    """The scripting manual in the web browser."""
    import webbrowser, pathlib
    if os.path.isfile(scripting.MANUAL):
        webbrowser.open(pathlib.Path(scripting.MANUAL).as_uri())
    else:
        messagebox.showinfo(APP_NAME, "The manual (docs/scripting.html) is missing.")


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)
    else:
        import subprocess
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


_EDITORS = {}


def open_script_editor(root, path):
    """Code editor window for a script (examples are copied to My scripts first)."""
    if not os.path.isfile(path):
        messagebox.showerror(APP_NAME, f"Script not found:\n{path}")
        return None
    path = scripting.copy_example(path)
    ed = _EDITORS.get(os.path.normcase(path))
    if ed is not None and ed.win.winfo_exists():
        ed.win.deiconify()
        ed.win.lift()
        return ed
    ed = _EDITORS[os.path.normcase(path)] = ScriptEditor(root, path)
    return ed


class ScriptDialog:
    """Pick a script, set its sliders and colors, watch it run in a preview."""
    PREVIEW_W = 360

    def __init__(self, root, cur, size, done):
        self.root, self.done = root, done
        self.W, self.H = max(1, size[0]), max(1, size[1])
        self.win, frm = win, frm = _dialog(root, "Script")
        self.values = dict(cur.get("params") or {})
        self.path = scripting.resolve(cur["file"]) if cur.get("file") else None
        self.gen = None
        self.info = None
        self.info_mtime = None
        self.photo = None

        left = ttk.Frame(frm)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        self.tree = ttk.Treeview(left, show="tree", height=17, selectmode="browse")
        self.tree.column("#0", width=190)
        self.tree.pack(side="left", fill="y")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        sb.pack(side="left", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._picked())
        self.tree.bind("<Double-Button-1>", lambda e: self.edit_code())

        right = ttk.Frame(frm)
        right.grid(row=0, column=1, sticky="n")
        pw = self.PREVIEW_W
        ph = max(40, min(220, round(pw * self.H / self.W)))
        self.canvas = tk.Canvas(right, width=pw, height=ph, bg=COL["canvas"], highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="w")
        self.canvas_img = self.canvas.create_image(pw // 2, ph // 2)
        self.title = ttk.Label(right, text="", font=("Segoe UI", 11, "bold"))
        self.title.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.about = ttk.Label(right, text="", foreground=COL["muted"], wraplength=pw, justify="left")
        self.about.grid(row=2, column=0, sticky="w")
        self.err = ttk.Label(right, text="", foreground=COL["warn"], wraplength=pw, justify="left")
        self.err.grid(row=3, column=0, sticky="w")
        self.pf = ttk.LabelFrame(right, text="Settings", padding=(8, 2, 8, 6))
        self.pf.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.pf.columnconfigure(1, weight=1)
        ttk.Label(right, text=f"Preview at the display size, {self.W} x {self.H}. Shown for 'Image s' "
                              "seconds, or nonstop with Repeat: Loop current.",
                  foreground=COL["faint"], wraplength=pw, justify="left").grid(row=5, column=0, sticky="w", pady=(6, 0))

        bar = ttk.Frame(frm)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(bar, text="New script...", command=self.new_script).pack(side="left")
        ttk.Button(bar, text="Edit code", command=self.edit_code).pack(side="left", padx=(4, 0))
        b = ttk.Button(bar, text="Folder", command=lambda: open_folder(scripting.USER_DIR))
        b.pack(side="left", padx=(4, 0))
        Tooltip(b, "Open the My scripts folder")
        ttk.Button(bar, text="Manual", command=open_manual).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="Cancel", command=self.close).pack(side="right")
        ttk.Button(bar, text="OK", command=self.ok).pack(side="right", padx=(0, 6))
        win.bind("<Escape>", lambda e: self.close())
        win.protocol("WM_DELETE_WINDOW", self.close)

        self.fill_tree()
        self._tick()

    # ---- list of scripts ---------------------------------------------------
    def fill_tree(self, select=None):
        t = self.tree
        t.delete(*t.get_children())
        groups = {}
        for group, path in scripting.list_scripts():
            if group not in groups:
                groups[group] = t.insert("", "end", text=group, open=True)
            t.insert(groups[group], "end", iid=path, text=script_title(path))
        if "My scripts" not in groups:
            groups["My scripts"] = t.insert("", "end", text="My scripts", open=True)
            t.insert(groups["My scripts"], "end", iid="#new", text="(none yet)")
        want = select or self.path
        if want and t.exists(want):
            t.selection_set(want)
            t.see(want)
        else:
            first = scripting.list_scripts()
            if first:
                t.selection_set(first[0][1])

    def _picked(self):
        sel = self.tree.selection()
        if not sel:
            return
        if sel[0] == "#new":
            self.new_script()
            return
        if not os.path.isfile(sel[0]):
            return
        if self.path and os.path.normcase(sel[0]) != os.path.normcase(self.path):
            self.values = {}                    # another script: its own defaults
        self.path = sel[0]
        self.load_info()

    def load_info(self):
        self.info = scripting.info(self.path)
        try:
            self.info_mtime = os.path.getmtime(self.path)
        except OSError:
            self.info_mtime = None
        self.title.configure(text=self.info["title"])
        self.about.configure(text=self.info["about"])
        self.err.configure(text=self.info["error"])
        self.build_params()
        self.restart()

    # ---- settings ------------------------------------------------------------
    def build_params(self):
        for w in self.pf.winfo_children():
            w.destroy()
        params = self.info["params"] if self.info else {}
        if not params:
            ttk.Label(self.pf, text="This script has no settings.", foreground=COL["muted"]).grid(
                row=0, column=0, columnspan=3, sticky="w")
        for r, (name, p) in enumerate(params.items()):
            label = p.label or name.replace("_", " ").capitalize()
            ttk.Label(self.pf, text=label).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=1)
            val = p.value(self.values.get(name))
            self.values[name] = val if p.kind != "color" else scripting._to_hex(val)
            if p.kind == "slider":
                var = tk.DoubleVar(value=val)
                txt = ttk.Label(self.pf, width=6, anchor="e")
                txt.grid(row=r, column=2, sticky="e", pady=1)

                def moved(_=None, n=name, p=p, var=var, txt=txt):
                    v = p.value(var.get())
                    txt.configure(text=str(v) if p.step == 1 else f"{v:.3g}")
                    self.set_value(n, v)
                ttk.Scale(self.pf, from_=p.lo, to=p.hi, variable=var, command=moved, length=190).grid(
                    row=r, column=1, sticky="ew", pady=1)
                moved()
            elif p.kind == "color":
                btn = tk.Button(self.pf, width=8, relief="flat", bg=self.values[name], activebackground=self.values[name])

                def pick(n=name, btn=btn):
                    from tkinter import colorchooser
                    got = colorchooser.askcolor(self.values[n], parent=self.win, title=n)
                    if got and got[1]:
                        btn.configure(bg=got[1], activebackground=got[1])
                        self.set_value(n, got[1])
                btn.configure(command=pick)
                btn.grid(row=r, column=1, sticky="w", pady=1)
            elif p.kind == "dropdown":
                var = tk.StringVar(value=val)
                cb = ttk.Combobox(self.pf, values=p.choices, textvariable=var, state="readonly", width=18)
                cb.grid(row=r, column=1, sticky="w", pady=1)
                cb.bind("<<ComboboxSelected>>", lambda e, n=name, var=var: self.set_value(n, var.get()))
            elif p.kind == "checkbox":
                var = tk.BooleanVar(value=val)
                ttk.Checkbutton(self.pf, variable=var, command=lambda n=name, var=var: self.set_value(n, var.get())
                                ).grid(row=r, column=1, sticky="w", pady=1)
            elif p.kind == "text":
                var = tk.StringVar(value=val)
                e = ttk.Entry(self.pf, textvariable=var, width=26, show="•" if p.secret else "")
                e.grid(row=r, column=1, columnspan=2, sticky="ew", pady=1)
                var.trace_add("write", lambda *a, n=name, var=var: self.set_value(n, var.get()))

    def set_value(self, name, v):
        self.values[name] = v
        g = self.gen
        if g is not None and g.ns is not None and self.info and name in self.info["params"]:
            g.ns[name] = self.info["params"][name].value(v)      # change it live in the preview
            g.values[name] = v

    # ---- preview -------------------------------------------------------------
    def restart(self):
        if self.path:
            self.gen = scripting.ScriptGen({"file": self.path, "params": dict(self.values)}, self.W, self.H)
            self.t0 = time.monotonic()

    def _tick(self):
        if not self.win.winfo_exists():
            return
        g = self.gen
        if g is not None:
            frame = g.frame(time.monotonic() - self.t0)
            self.err.configure(text=g.error or (self.info["error"] if self.info else ""))
            cw, ch = int(self.canvas["width"]), int(self.canvas["height"])
            k = min(cw / self.W, ch / self.H)
            size = (max(1, round(self.W * k)), max(1, round(self.H * k)))
            img = cv2.resize(frame, size, interpolation=cv2.INTER_NEAREST if k >= 1 else cv2.INTER_AREA)
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            ppm = b"P6\n%d %d\n255\n" % (img.shape[1], img.shape[0]) + img.tobytes()
            self.photo = tk.PhotoImage(data=ppm, format="PPM")
            self.canvas.coords(self.canvas_img, cw // 2, ch // 2)
            self.canvas.itemconfigure(self.canvas_img, image=self.photo)
            try:                                 # saved in the editor: new settings may have appeared
                if self.path and os.path.getmtime(self.path) != self.info_mtime:
                    self.info = scripting.info(self.path)
                    self.info_mtime = os.path.getmtime(self.path)
                    self.title.configure(text=self.info["title"])
                    self.about.configure(text=self.info["about"])
                    self.build_params()
            except OSError:
                pass
        self.win.after(33, self._tick)

    # ---- buttons -----------------------------------------------------------------
    def new_script(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("New script", "Name for the new script:", parent=self.win)
        if not name:
            self.fill_tree()
            return
        base = "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower()).strip("_") or "my_script"
        os.makedirs(scripting.USER_DIR, exist_ok=True)
        path = os.path.join(scripting.USER_DIR, base + ".py")
        if os.path.exists(path):
            messagebox.showerror(APP_NAME, f"There is already a script called {base}.py", parent=self.win)
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(scripting.TEMPLATE.replace("My animation", name.strip(), 1))
        self.fill_tree(select=path)
        self._picked()
        open_script_editor(self.root, path)

    def edit_code(self):
        if not self.path:
            return
        mine = scripting.copy_example(self.path)
        if os.path.normcase(mine) != os.path.normcase(self.path):
            self.path = mine
            self.fill_tree(select=mine)
        open_script_editor(self.root, mine)

    def ok(self):
        if not self.path or not os.path.isfile(self.path):
            return
        in_dirs = os.path.normcase(os.path.dirname(self.path)) in (
            os.path.normcase(scripting.USER_DIR), os.path.normcase(scripting.EXAMPLES_DIR))
        d = {"file": os.path.basename(self.path) if in_dirs else self.path, "params": self.values}
        self.done("script:" + json.dumps(d))
        self.close()

    def close(self):
        self.gen = None
        self.win.destroy()


class ScriptEditor:
    """A small code editor: line numbers, colors, auto-indent, Ctrl+S saves
    (the preview and the wall pick up the change), errors shown below."""
    KEYWORDS = ("def", "return", "if", "elif", "else", "for", "while", "in", "not", "and", "or", "import",
                "from", "as", "pass", "break", "continue", "True", "False", "None", "lambda", "global",
                "class", "try", "except", "with", "is", "range", "len", "min", "max", "abs", "int", "float")

    def __init__(self, root, path):
        self.path = path
        self.win = win = tk.Toplevel(root)
        place_over(win, root, (820, 620))
        win.title(f"{os.path.basename(path)} - Script editor")
        win.geometry("820x620")
        bg = ttk.Style().lookup("TEntry", "fieldbackground") or "#1e293b"
        fg = ttk.Style().lookup("TEntry", "foreground") or "#e2e8f0"
        if not bg.startswith("#"):
            bg, fg = "#1e293b", "#e2e8f0"
        dark = int(bg[1:3], 16) < 128
        self.colors = dict(kw="#c792ea", str="#c3e88d", com="#7f8ca3", num="#f78c6c", fn="#82aaff",
                           err="#5a1d2a") if dark else dict(kw="#7a3e9d", str="#2e7d32", com="#888888",
                                                             num="#c05000", fn="#1f5fbf", err="#ffd6d6")
        top = ttk.Frame(win, padding=(8, 6, 8, 4))
        top.pack(fill="x")
        ttk.Button(top, text=f"Save  ({SAVE_KEY[1]})", command=self.save).pack(side="left")
        b = ttk.Button(top, text="Open in other editor", command=self.external)
        b.pack(side="left", padx=(4, 0))
        Tooltip(b, "VS Code if it's installed, otherwise Notepad.\nSaving there updates the wall too.")
        ttk.Button(top, text="Manual", command=open_manual).pack(side="left", padx=(4, 0))
        ttk.Label(top, text=path, foreground=COL["faint"]).pack(side="left", padx=(10, 0))

        mid = ttk.Frame(win)
        mid.pack(fill="both", expand=True, padx=8)
        font = ("Consolas", 11) if os.name == "nt" else ("DejaVu Sans Mono", 11)
        self.gutter = tk.Text(mid, width=4, padx=4, takefocus=0, bd=0, bg=bg, fg=self.colors["com"],
                              font=font, state="disabled", cursor="arrow")
        self.gutter.pack(side="left", fill="y")
        self.text = tk.Text(mid, wrap="none", undo=True, bd=0, bg=bg, fg=fg, insertbackground=fg,
                            selectbackground=COL["playing"], font=font, tabs=("1c",), padx=6)
        self.text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self._yview)
        sb.pack(side="left", fill="y")
        self.text.configure(yscrollcommand=lambda a, b_: (sb.set(a, b_), self.gutter.yview_moveto(a)))
        for tag in ("kw", "str", "com", "num", "fn"):
            self.text.tag_configure(tag, foreground=self.colors[tag])
        self.text.tag_configure("err", background=self.colors["err"])
        self.status = ttk.Label(win, text="", padding=(8, 3), anchor="w")
        self.status.pack(fill="x")

        with open(path, encoding="utf-8") as f:
            self.text.insert("1.0", f.read())
        self.text.edit_reset()
        self.text.edit_modified(False)
        self._saved_mtime = os.path.getmtime(path)
        self._hl_job = None
        self.highlight()
        self.text.bind("<<Modified>>", self._modified)
        self.text.bind(SAVE_KEY[0], lambda e: (self.save(), "break")[1])
        self.text.bind("<Tab>", lambda e: (self.text.insert("insert", "    "), "break")[1])
        self.text.bind("<Return>", self._newline)
        win.bind(SAVE_KEY[0], lambda e: self.save())
        win.protocol("WM_DELETE_WINDOW", self.close)
        self.text.focus_set()
        self._poll()

    def _yview(self, *a):
        self.text.yview(*a)
        self.gutter.yview(*a)

    def _modified(self, _=None):
        if self.text.edit_modified():
            self.win.title(f"*{os.path.basename(self.path)} - Script editor")
        if self._hl_job:
            self.win.after_cancel(self._hl_job)
        self._hl_job = self.win.after(200, self.highlight)

    def _newline(self, e):
        line = self.text.get("insert linestart", "insert")
        indent = line[:len(line) - len(line.lstrip())]
        if line.rstrip().endswith(":"):
            indent += "    "
        self.text.insert("insert", "\n" + indent)
        self.text.see("insert")
        return "break"

    def highlight(self):
        import re, keyword
        self._hl_job = None
        t = self.text
        src = t.get("1.0", "end-1c")
        for tag in ("kw", "str", "com", "num", "fn"):
            t.tag_remove(tag, "1.0", "end")
        n = src.count("\n") + 1
        self.gutter.configure(state="normal")
        self.gutter.delete("1.0", "end")
        self.gutter.insert("1.0", "\n".join(str(i) for i in range(1, n + 1)))
        self.gutter.configure(state="disabled")
        helpers = set(scripting.HELPERS) | {"setup", "frame", "pixel", "params"}
        pat = re.compile(r'(?P<com>#[^\n]*)|(?P<str>"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"[^"\n]*"|\'[^\'\n]*\')'
                         r'|(?P<num>\b\d+\.?\d*\b)|(?P<word>\b[A-Za-z_]\w*\b)')
        for m in pat.finditer(src):
            kind = m.lastgroup
            if kind == "word":
                w = m.group()
                kind = "kw" if keyword.iskeyword(w) or w in self.KEYWORDS else "fn" if w in helpers else None
            if kind:
                t.tag_add(kind, f"1.0+{m.start()}c", f"1.0+{m.end()}c")

    def save(self):
        src = self.text.get("1.0", "end-1c")
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        self._saved_mtime = os.path.getmtime(self.path)
        self.text.edit_modified(False)
        self.win.title(f"{os.path.basename(self.path)} - Script editor")
        try:
            compile(src, self.path, "exec")
            scripting.ERRORS[self.path] = ""
            self.show("Saved. The preview and the wall use it now.", None)
        except SyntaxError as e:
            self.show(f"Saved, but line {e.lineno}: {e.msg}", e.lineno)

    def show(self, msg, line):
        self.text.tag_remove("err", "1.0", "end")
        bad = line is not None
        self.status.configure(text=msg, foreground=COL["warn"] if bad else COL["ok"])
        if bad:
            self.text.tag_add("err", f"{line}.0", f"{line}.end+1c")
            self.text.see(f"{line}.0")

    def _poll(self):
        """Errors from the running script (preview or wall); reload if changed elsewhere."""
        if not self.win.winfo_exists():
            return
        err = scripting.ERRORS.get(self.path)
        if err:
            self.show(err, scripting.error_line(err))
        elif err == "" and str(self.status.cget("foreground")) == COL["warn"]:
            self.show("Running fine.", None)
        try:
            m = os.path.getmtime(self.path)
            if m != self._saved_mtime and not self.text.edit_modified():
                with open(self.path, encoding="utf-8") as f:
                    src = f.read()
                self.text.delete("1.0", "end")
                self.text.insert("1.0", src)
                self.text.edit_modified(False)
                self._saved_mtime = m
                self.highlight()
        except OSError:
            pass
        self.win.after(500, self._poll)

    def external(self):
        import shutil, subprocess
        code = shutil.which("code")
        if code:
            subprocess.Popen([code, self.path])
        elif os.name == "nt":
            subprocess.Popen(["notepad.exe", self.path])
        else:
            subprocess.Popen(["xdg-open", self.path])

    def close(self):
        if self.text.edit_modified():
            ans = messagebox.askyesnocancel(APP_NAME, "Save the changes first?", parent=self.win)
            if ans is None:
                return
            if ans:
                self.save()
        _EDITORS.pop(os.path.normcase(self.path), None)
        self.win.destroy()


class ScheduleDialog:
    """Play the playlist between two times on chosen days (options menu)."""

    def __init__(self, app):
        self.app = app
        sc = app.schedule
        self.win = d = tk.Toplevel(app.root)
        show = place_over(d, app.root)
        d.title("Schedule")
        d.transient(app.root)
        f = ttk.Frame(d, padding=10)
        f.pack()
        self.on = tk.BooleanVar(value=sc["on"])
        ttk.Checkbutton(f, text="Play the playlist on a schedule", variable=self.on).grid(
            row=0, column=0, columnspan=4, sticky="w")
        self.start, self.stop = tk.StringVar(value=sc["start"]), tk.StringVar(value=sc["stop"])
        ttk.Label(f, text="Start").grid(row=1, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(f, textvariable=self.start, width=7).grid(row=1, column=1, sticky="w", pady=(8, 2))
        ttk.Label(f, text="Stop").grid(row=1, column=2, sticky="w", padx=(12, 0), pady=(8, 2))
        ttk.Entry(f, textvariable=self.stop, width=7).grid(row=1, column=3, sticky="w", pady=(8, 2))
        df = ttk.Frame(f)
        df.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.days = [tk.BooleanVar(value=v) for v in sc["days"]]
        for name, var in zip(DAY_NAMES, self.days):
            ttk.Checkbutton(df, text=name, variable=var).pack(side="left", padx=(0, 6))
        ttk.Label(f, text="Times are 24-hour (17:00, 22:30). A stop before the start runs past midnight.\n"
                          "At the start the playlist plays; at the stop it stops and the displays go\n"
                          "dark. In between you can still play or stop by hand.",
                  foreground=COL["faint"]).grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        bf = ttk.Frame(f)
        bf.grid(row=4, column=0, columnspan=4, sticky="e", pady=(10, 0))
        ttk.Button(bf, text="Cancel", command=d.destroy).pack(side="right")
        ttk.Button(bf, text="OK", command=self.ok).pack(side="right", padx=(0, 6))
        show()

    def ok(self):
        start, stop = self.start.get().strip(), self.stop.get().strip()
        if parse_hhmm(start) is None or parse_hhmm(stop) is None:
            messagebox.showerror(APP_NAME, "Enter the times as HH:MM, e.g. 17:00.", parent=self.win)
            return
        fmt = lambda m: f"{m // 60:02d}:{m % 60:02d}"
        self.app.schedule = {"on": self.on.get(), "start": fmt(parse_hhmm(start)), "stop": fmt(parse_hhmm(stop)),
                             "days": [v.get() for v in self.days]}
        self.app._sched_was = None      # act on the new times at the next check
        self.app.save()
        log_event(f"Schedule set: {'on' if self.on.get() else 'off'}, {self.app.schedule['start']}-"
                  f"{self.app.schedule['stop']}")
        self.win.destroy()


class BridgesDialog:
    """Edit the list of displays used by the Mirror and Span outputs."""
    COLS = ("n", "ip", "port", "size", "pitch", "level", "tile", "state")

    def __init__(self, app):
        self.app = app
        self.win = win = tk.Toplevel(app.root)
        place_over(win, app.root)
        win.title("Displays")
        win.transient(app.root)
        win.protocol("WM_DELETE_WINDOW", self.close)
        f = ttk.Frame(win, padding=10)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Mirror sends the whole picture to every display. Span splits one big picture:\n"
                          "displays fill the columns left to right, then the next row (use Up / Down to order).\n"
                          "Give every display its pixel pitch (Edit...) to line up panels of different sizes\n"
                          "by real size: rows are then centered, and each display gets its real-size piece.",
                  foreground=COL["faint"]).grid(row=0, column=0, columnspan=2, sticky="w")
        self.tree = ttk.Treeview(f, columns=self.COLS, show="headings", height=6, selectmode="browse")
        for c, (title, w) in zip(self.COLS, (("#", 30), ("IP address", 120), ("Port", 50), ("Size", 75),
                                             ("Pitch", 55), ("Level", 140), ("Span tile", 80),
                                             ("Status", 160))):
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor="w", stretch=c == "state")
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.tree.bind("<Double-Button-1>", lambda e: self.edit())
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        bf = ttk.Frame(f)
        bf.grid(row=1, column=1, sticky="n", padx=(8, 0), pady=(8, 0))
        for text, fn in (("Add...", self.add), ("Edit...", self.edit), ("Remove", self.remove),
                         ("Up", lambda: self.move(-1)), ("Down", lambda: self.move(1)),
                         ("Find boards", self.find), ("Get sizes", self.get_sizes)):
            ttk.Button(bf, text=text, width=11, command=fn).pack(pady=(0, 4))
        self.find_btn = bf.winfo_children()[5]
        cf = ttk.Frame(f)
        cf.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(cf, text="Span columns").pack(side="left")
        ttk.Spinbox(cf, from_=1, to=16, textvariable=app.v["span_cols"], width=5).pack(side="left", padx=(6, 0))
        ttk.Label(cf, text="Gap (mm) across").pack(side="left", padx=(14, 0))
        ttk.Spinbox(cf, from_=0, to=1000, textvariable=app.v["span_gap_x"], width=5).pack(side="left", padx=(6, 0))
        ttk.Label(cf, text="down").pack(side="left", padx=(8, 0))
        ttk.Spinbox(cf, from_=0, to=1000, textvariable=app.v["span_gap_y"], width=5).pack(side="left", padx=(6, 0))
        Tooltip(cf, "With a pitch on every display: the space between displays in a row (across)\n"
                    "and between rows (down), so the picture continues behind the gaps.")
        self.total_lbl = ttk.Label(cf, text="", foreground=COL["faint"])
        self.total_lbl.pack(side="left", padx=(12, 0))
        self.map = tk.Canvas(f, width=420, height=120, bg=COL["canvas"], highlightthickness=0)
        self.map.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        tf = ttk.Frame(f)
        tf.grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.test_pattern = tk.StringVar(value=TEST_PATTERNS[0])
        ttk.Combobox(tf, textvariable=self.test_pattern, values=TEST_PATTERNS, state="readonly", width=10).pack(
            side="left")
        self.test_btn = ttk.Button(tf, text="Start test pattern", command=self.toggle_test)
        self.test_btn.pack(side="left", padx=(6, 0))
        self.test_pattern.trace_add("write", lambda *a: self.app.player.testing
                                    and self.app.player.cmd("test", self.test_pattern.get()))
        Tooltip(tf, "The same plain color on every display, to match them with Edit... (brightness and\n"
                    f"color balance). Cycle changes every {TEST_CYCLE_SECS} s: white, grey 50%, grey 25%, red, "
                    "green, blue.\nStop (or closing this window) goes back to what was playing.")
        ttk.Button(f, text="Close", command=self.close).grid(row=4, column=1, sticky="e", pady=(8, 0))
        self.refresh()
        self._poll()

    def toggle_test(self):
        p = self.app.player
        p.cmd("test", None if p.testing else self.test_pattern.get())
        self.test_btn.config(text="Start test pattern" if p.testing else "Stop test pattern")

    @property
    def bridges(self):
        return self.app.cfg["bridges"]

    def close(self):
        if self.app.player.testing:
            self.app.player.cmd("test", None)
        self.app.bridges_win = None
        self.win.destroy()

    def changed(self):
        self.app.bridges_changed()          # also calls refresh()

    def refresh(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        s = self.app.player.settings
        lay = make_layout("Span", "", 0, 0, 0, self.bridges, s["span_cols"], s.get("span_gap_x", 0),
                          s.get("span_gap_y", 0))
        cols = max(1, min(s["span_cols"], len(self.bridges) or 1))
        allst = self.app.poller.all if hasattr(self.app, "poller") else {}
        for i, b in enumerate(self.bridges):
            st, cfg = allst.get((b["ip"], int(b.get("port", 4048))), (None, None))
            if st is None:
                state = "no reply" if (b["ip"], int(b.get("port", 4048))) in allst else "-"
            else:
                state = f"{st.get('man', '')} {st.get('mod', '')}".strip() or "answering"
                if st.get("demo"):
                    state += ", demo on"
            self.tree.insert("", "end", iid=str(i), values=(
                i + 1, b["ip"], b.get("port", 4048), f"{b['w']}x{b['h']}", pitch_label(bridge_pitch(b)), level_label(b),
                f"col {i % cols + 1}, row {i // cols + 1}", state))
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])
        # Layout drawing of the span (also shown for mirror, as the order)
        self.map.delete("all")
        if not self.bridges:
            self.map.create_text(210, 60, fill="#777", text="No displays yet: Add... or Find boards")
            self.total_lbl.config(text="")
            return
        _, cw, ch, tiles = lay
        pitches = [bridge_pitch(b) for b in self.bridges if b.get("ip")]
        if all(pitches):
            fine = min(pitches)
            self.total_lbl.config(text=f"Span picture: {cw}x{ch} px, laid out by pitch "
                                       f"({cw * fine:g} x {ch * fine:g} mm)")
        elif any(pitches):
            self.total_lbl.config(text=f"Span picture: {cw}x{ch} px (by pixels: give every display a pitch "
                                       "to lay out by size)")
        else:
            self.total_lbl.config(text=f"Span picture: {cw}x{ch} px")
        mw, mh = int(self.map.winfo_width() or 420), 120
        if mw < 50:
            mw = 420
        k = min((mw - 10) / max(1, cw), (mh - 10) / max(1, ch))
        ox, oy = (mw - cw * k) / 2, (mh - ch * k) / 2
        for i, t in enumerate(tiles):
            x0, y0 = ox + t[4] * k, oy + t[5] * k
            self.map.create_rectangle(x0, y0, x0 + t[6] * k, y0 + t[7] * k, outline="#4fc3f7", fill="#1e3a4a")
            self.map.create_text(x0 + t[6] * k / 2, y0 + t[7] * k / 2, fill="#fff",
                                 text=f"{i + 1}\n{t[0]}", justify="center")

    def _poll(self):
        if self.app.bridges_win is self:
            self.refresh()
            self.test_btn.config(text="Stop test pattern" if self.app.player.testing else "Start test pattern")
            self.win.after(2000, self._poll)

    def _selected(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _edit_dialog(self, title, b, live_index=None):
        """Small form for one display; returns the edited dict or None.
        live_index: the display being edited; brightness / balance changes
        are then shown on the wall while adjusting (Cancel puts them back)."""
        d = tk.Toplevel(self.win)
        show = place_over(d, self.win)
        d.title(title)
        d.transient(self.win)
        f = ttk.Frame(d, padding=10)
        f.pack()
        v = {k: tk.StringVar(value=str(b.get(k, ""))) for k in ("ip", "port", "w", "h")}
        v["pitch"] = tk.StringVar(value=pitch_label(bridge_pitch(b)))
        choices = [pitch_label(p) for p in (0.0,) + PITCHES]
        if v["pitch"].get() not in choices:         # an older free-typed value
            choices.append(v["pitch"].get())
        for r, (k, label) in enumerate((("ip", "IP address"), ("port", "UDP port"),
                                        ("w", "Width (px)"), ("h", "Height (px)"),
                                        ("pitch", "Pixel pitch"))):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=2, padx=(0, 8))
            if k == "pitch":
                ttk.Combobox(f, textvariable=v[k], values=choices, state="readonly", width=7).grid(
                    row=r, column=1, sticky="w")
            else:
                ttk.Entry(f, textvariable=v[k], width=16 if k == "ip" else 8).grid(row=r, column=1, sticky="w")
        ttk.Label(f, text="The P number of the panels (P2.5 = 2.5 mm between pixels).\n"
                          "Span uses it to line up displays of different pixel sizes.",
                  foreground=COL["faint"]).grid(row=5, column=0, columnspan=2, sticky="w", pady=(2, 0))
        bal0 = list(b.get("rgb") or (100, 100, 100))
        lv = {"bright": tk.StringVar(value=str(b.get("bright", 100))),
              "r": tk.StringVar(value=str(bal0[0])), "g": tk.StringVar(value=str(bal0[1])),
              "b": tk.StringVar(value=str(bal0[2]))}
        ttk.Label(f, text="Brightness (%)").grid(row=6, column=0, sticky="w", pady=(8, 2), padx=(0, 8))
        ttk.Spinbox(f, from_=1, to=100, textvariable=lv["bright"], width=6).grid(row=6, column=1, sticky="w",
                                                                              pady=(8, 2))
        ttk.Label(f, text="Color balance (%)").grid(row=7, column=0, sticky="w", pady=2, padx=(0, 8))
        cb = ttk.Frame(f)
        cb.grid(row=7, column=1, sticky="w")
        for k, name in (("r", "R"), ("g", "G"), ("b", "B")):
            ttk.Label(cb, text=name).pack(side="left", padx=(0 if k == "r" else 6, 2))
            ttk.Spinbox(cb, from_=0, to=100, textvariable=lv[k], width=4).pack(side="left")
        ttk.Label(f, text="To match displays with different panels: lower the brightness of the\n"
                          "brighter one, or the color that looks too strong."
                          + (" Changes show on the wall\nwhile you adjust." if live_index is not None else ""),
                  foreground=COL["faint"]).grid(row=8, column=0, columnspan=2, sticky="w", pady=(2, 0))
        nv = {k: tk.StringVar(value=str(b.get(k, 0))) for k in ("dx", "dy")}
        ttk.Label(f, text="Nudge (mm)").grid(row=9, column=0, sticky="w", pady=(8, 2), padx=(0, 8))
        nf = ttk.Frame(f)
        nf.grid(row=9, column=1, sticky="w", pady=(8, 2))
        for k, name in (("dx", "right"), ("dy", "down")):
            ttk.Label(nf, text=name).pack(side="left", padx=(0 if k == "dx" else 6, 2))
            ttk.Spinbox(nf, from_=-1000, to=1000, textvariable=nv[k], width=5).pack(side="left")
        ttk.Label(f, text="With a pitch on every display: moves this display's piece of the picture\n"
                          "(negative = left / up), to match how the displays are hung.",
                  foreground=COL["faint"]).grid(row=10, column=0, columnspan=2, sticky="w", pady=(2, 0))
        result = []
        original = dict(b)

        def levels():
            """{'bright': n, 'rgb': [r, g, b]} from the fields, or None if not numbers."""
            try:
                br = min(100, max(1, int(lv["bright"].get())))
                bal = [min(100, max(0, int(lv[k].get()))) for k in ("r", "g", "b")]
            except ValueError:
                return None
            return {"bright": br, "rgb": bal}

        def live(*_):
            lvl = levels()
            if live_index is None or lvl is None or live_index >= len(self.bridges):
                return
            try:
                lvl.update({k: min(1000, max(-1000, int(nv[k].get() or 0))) for k in ("dx", "dy")})
            except ValueError:
                return
            self.bridges[live_index] = dict(self.bridges[live_index], **lvl)
            self.changed()
        if live_index is not None:
            for var in list(lv.values()) + list(nv.values()):
                var.trace_add("write", live)

        def cancel():
            if live_index is not None and live_index < len(self.bridges):
                self.bridges[live_index] = original
                self.changed()
            d.destroy()
        d.protocol("WM_DELETE_WINDOW", cancel)

        def get_size():
            try:
                cfg = device_config(v["ip"].get().strip(), int(v["port"].get()))
            except (OSError, ValueError):
                cfg = None
            if cfg and isinstance(cfg.get("w"), int):
                v["w"].set(str(cfg["w"]))
                v["h"].set(str(cfg["h"]))
            else:
                messagebox.showerror(APP_NAME, "No size reply from that address.", parent=d)

        def ok():
            try:
                nb = {"ip": v["ip"].get().strip(), "port": int(v["port"].get()),
                      "w": int(v["w"].get()), "h": int(v["h"].get())}
            except ValueError:
                messagebox.showerror(APP_NAME, "Port, width and height must be numbers.", parent=d)
                return
            if not nb["ip"] or not (1 <= nb["port"] <= 65535 and 1 <= nb["w"] <= 4096 and 1 <= nb["h"] <= 4096):
                messagebox.showerror(APP_NAME, "Enter an IP address, a port and a size.", parent=d)
                return
            p = bridge_pitch({"pitch": v["pitch"].get().strip().upper().lstrip("P")})
            if p:
                nb["pitch"] = p
            lvl = levels()
            if lvl is None:
                messagebox.showerror(APP_NAME, "Brightness and color balance must be numbers (percent).", parent=d)
                return
            if lvl["bright"] != 100:
                nb["bright"] = lvl["bright"]
            if lvl["rgb"] != [100, 100, 100]:
                nb["rgb"] = lvl["rgb"]
            for k in ("dx", "dy"):
                try:
                    n = min(1000, max(-1000, int(nv[k].get() or 0)))
                except ValueError:
                    messagebox.showerror(APP_NAME, "Nudge must be whole millimeters.", parent=d)
                    return
                if n:
                    nb[k] = n
            result.append(nb)
            d.destroy()
        bf = ttk.Frame(f)
        bf.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(bf, text="Get size", command=get_size).pack(side="left")
        ttk.Button(bf, text="OK", command=ok).pack(side="right")
        ttk.Button(bf, text="Cancel", command=cancel).pack(side="right", padx=(0, 6))
        show()
        d.grab_set()                # needs the window on screen
        d.wait_window()
        return result[0] if result else None

    def add(self):
        s = self.app.player.settings
        last = self.bridges[-1] if self.bridges else {"ip": "", "port": s["port"], "w": s["width"], "h": s["height"]}
        nb = self._edit_dialog("Add display", {"ip": "", "port": last.get("port", 4048), "w": last["w"], "h": last["h"]})
        if nb:
            self.bridges.append(nb)
            self.changed()

    def edit(self):
        i = self._selected()
        if i is None:
            return
        nb = self._edit_dialog("Edit display", self.bridges[i], live_index=i)
        if nb:
            self.bridges[i] = nb
            self.changed()

    def remove(self):
        i = self._selected()
        if i is not None:
            del self.bridges[i]
            self.changed()

    def move(self, d):
        i = self._selected()
        if i is None or not 0 <= i + d < len(self.bridges):
            return
        b = self.bridges
        b[i], b[i + d] = b[i + d], b[i]
        self.changed()
        self.tree.selection_set(str(i + d))

    def find(self):
        self.find_btn.state(["disabled"])
        port = self.app.player.settings["port"]
        result = []

        def done():
            if self.app.bridges_win is not self:
                return
            self.find_btn.state(["!disabled"])
            have = {(b["ip"], int(b.get("port", 4048))) for b in self.bridges}
            added, skipped = 0, []
            for dev in result:
                if (dev["ip"], port) in have:
                    continue
                if dev["w"]:
                    self.bridges.append({"ip": dev["ip"], "port": port, "w": dev["w"], "h": dev["h"]})
                    added += 1
                else:
                    skipped.append(dev["ip"])
            if added:
                self.changed()
            if skipped:                         # e.g. WLED LED strips: no width x height to split a picture on
                messagebox.showinfo(APP_NAME, "Also found, but not added: " + ", ".join(skipped)
                                    + "\n\nThey don't report a picture size (e.g. LED strips, or WLED without a "
                                      "2D matrix set up). Add them with Add... if you want them to show the "
                                      "picture too.", parent=self.win)
            elif not added:
                messagebox.showinfo(APP_NAME, f"{len(result)} display(s) answered, none new.", parent=self.win)
        threading.Thread(target=lambda: (result.extend(ddp_discover(port=port)),
                                         self.app.root.after(0, done)), daemon=True).start()

    def get_sizes(self):
        targets = [(b["ip"], int(b.get("port", 4048))) for b in self.bridges]
        cfgs = ddp_read_many(targets, DDP_ID_CONFIG, "config") if targets else {}
        for k in targets:
            if not cfgs.get(k):
                cfgs[k] = wled_config(k[0])
        missing = []
        for b in self.bridges:
            c = cfgs.get((b["ip"], int(b.get("port", 4048))))
            if c and isinstance(c.get("w"), int) and isinstance(c.get("h"), int):
                b["w"], b["h"] = c["w"], c["h"]
            else:
                missing.append(b["ip"])
        self.changed()
        if missing:
            messagebox.showwarning(APP_NAME, "No size reply from: " + ", ".join(missing), parent=self.win)


# "night": the board's web pages use daisyUI's night theme; this recolors Sun
# Valley dark to the same navy palette and accent
NIGHT = dict(bg="#0f172a", field="#1e293b", fg="#c9cbd0", accent="#3abdf7", sel="#1e3a5f")
COL_NIGHT = dict(ok="#2fd4bf", warn="#fb7085", muted="#94a3b8", faint="#64748b", playing="#1e3a5f",
                 tip_bg="#1e293b", tip_fg="#e2e8f0", canvas="#0a1120")


def _night_sprites(root):
    """Repaint Sun Valley dark's sprite images (buttons, fields, checkboxes...)
    in the night palette: greys become navy/slate tones, the blue accent
    becomes night's light blue."""
    import sv_ttk
    sheet = os.path.join(os.path.dirname(sv_ttk.__file__), "theme", "spritesheet_dark.png")
    img = cv2.imread(sheet, cv2.IMREAD_UNCHANGED).astype(np.float32)      # BGRA
    bgr, alpha = img[..., :3], img[..., 3:]
    grey = bgr.max(axis=2) - bgr.min(axis=2) < 14
    lum = bgr.mean(axis=2)
    # grey level -> slate/navy (RGB), piecewise linear
    xs = [0, 28, 47, 70, 158, 210, 255]
    ramp = np.array([(2, 6, 23), (15, 23, 42), (30, 41, 59), (51, 65, 85), (148, 163, 184),
                     (203, 213, 225), (241, 245, 249)], np.float32)
    out = bgr.copy()
    for c in range(3):                                   # c: B, G, R
        out[..., c] = np.where(grey, np.interp(lum, xs, ramp[:, 2 - c]), out[..., c])
    acc = np.array([0xf7, 0xbd, 0x3a], np.float32)       # night accent, BGR
    blue = ~grey & (bgr[..., 0] > bgr[..., 2] + 40)
    scale = (lum / 180.7)[..., None]                     # 180.7 = mean of Sun Valley's (87,200,255)
    out = np.where(blue[..., None], np.clip(acc * scale, 0, 255), out)
    path = os.path.join(tempfile.gettempdir(), "ddp_streamer_night.png")
    cv2.imwrite(path, np.concatenate([out, alpha], axis=2).astype(np.uint8))
    tcl = root.tk
    tcl.call("image", "create", "photo", "night_sheet", "-file", path, "-format", "png")
    info = tcl.splitlist(tcl.eval("set ::spriteinfo"))
    for i in range(0, len(info), 5):
        name, x, y, w, h = info[i], *map(int, info[i + 1:i + 5])
        im = tcl.eval(f"set ::ttk::theme::sv_dark::I({name})")
        tcl.call(im, "copy", "night_sheet", "-from", x, y, x + w, y + h, "-to", 0, 0,
                 "-compositingrule", "set")


def apply_theme(root, theme):
    """theme: "night" (default: Sun Valley dark recolored to match the board's
    web pages), "dark" / "light" (Sun Valley, Windows 11 style; needs the sv-ttk
    package) or "classic" (the stock Windows look). Falls back to classic if
    sv-ttk isn't installed."""
    if theme in ("night", "dark", "light"):
        try:
            import sv_ttk
            sv_ttk.set_theme("light" if theme == "light" else "dark")
            if theme == "night":
                _night_sprites(root)
            # Slimmer than Sun Valley's defaults so the window stays short
            st = ttk.Style()
            st.configure("TButton", padding=(8, 1))
            st.configure("TEntry", padding=(6, 0))
            st.configure("TSpinbox", padding=(6, 0))
            st.configure("TCombobox", padding=(6, 0))
            st.configure("TCheckbutton", padding=(0, 0))
            if theme in ("night", "dark"):
                COL.update(COL_NIGHT if theme == "night" else COL_DARK)
                bg, field, fg, sel = ((NIGHT["bg"], NIGHT["field"], NIGHT["fg"], NIGHT["sel"]) if theme == "night"
                                      else ("#1c1c1c", "#2b2b2b", "#e6e6e6", "#3d6a99"))
                if theme == "night":
                    # Sun Valley's own palette step (tk_setPalette) puts its grey into the
                    # option database, which plain labels read: give it night's colors and rerun it
                    for k, v in (("-bg", bg), ("-fg", fg), ("-selbg", sel)):
                        root.tk.eval(f"set ::ttk::theme::sv_dark::colors({k}) {v}")
                    root.tk.call("configure_colors")
                    st.configure(".", background=bg, foreground=fg, troughcolor=field,
                                 selectbackground=sel, insertcolor=fg)
                    st.map(".", background=[("disabled", bg)])
                    for s in ("TLabelframe", "TLabelframe.Label", "TFrame", "TLabel", "TCheckbutton",
                              "TRadiobutton", "Card.TFrame"):
                        st.configure(s, background=bg)
                    st.configure("TEntry", fieldbackground=field, foreground=fg)
                    st.configure("TCombobox", fieldbackground=field, foreground=fg, selectbackground=sel)
                    st.configure("TSpinbox", fieldbackground=field, foreground=fg)
                    root.option_add("*TCombobox*Listbox.background", field)
                    root.option_add("*TCombobox*Listbox.foreground", fg)
                    root.option_add("*TCombobox*Listbox.selectBackground", sel)
                    root.option_add("*Menu.background", field)
                    root.option_add("*Menu.foreground", fg)
                    root.option_add("*Menu.activeBackground", sel)
                root.configure(bg=bg)
                for w in ("Toplevel", "Frame"):
                    root.option_add(f"*{w}.background", bg)
                root.option_add("*Label.background", bg)
                root.option_add("*Listbox.background", field)
                root.option_add("*Listbox.foreground", fg)
                root.option_add("*Listbox.selectBackground", sel)
                root.option_add("*Listbox.selectForeground", "#ffffff")
                root.option_add("*Listbox.highlightThickness", 0)
            return
        except Exception:
            pass                        # not installed: stock look below
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass


def appimage_menu_entry():
    """Linux AppImage: put DDP Streamer in the applications menu (and so its icon
    in the dock; GNOME shows a gear for windows without a menu entry). Rewritten
    when the AppImage has moved. The .tar.gz build does this with install.sh."""
    import shutil, subprocess
    image = os.environ.get("APPIMAGE")
    entry_src = os.path.join(HERE, "ddp-streamer.desktop")
    if not image or not os.path.isfile(entry_src):
        return
    share = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    apps, icons = os.path.join(share, "applications"), os.path.join(share, "icons", "hicolor", "256x256", "apps")
    try:
        with open(entry_src, encoding="utf-8") as f:
            text = "".join(f'Exec="{image}" %F\n' if line.startswith("Exec=") else line for line in f)
        dst = os.path.join(apps, "ddp-streamer.desktop")
        try:
            with open(dst, encoding="utf-8") as f:
                if f.read() == text:
                    return
        except OSError:
            pass
        os.makedirs(apps, exist_ok=True)
        os.makedirs(icons, exist_ok=True)
        shutil.copy(os.path.join(HERE, "ddp-streamer.png"), os.path.join(icons, "ddp-streamer.png"))
        with open(dst, "w", encoding="utf-8") as f:
            f.write(text)
        # Without a fresh icon cache the dock shows an empty icon (seen on Ubuntu 24.04)
        for cmd in (["gtk-update-icon-cache", "-q", "-t", os.path.dirname(os.path.dirname(icons))],
                    ["update-desktop-database", "-q", apps]):
            if shutil.which(cmd[0]):
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def main():
    if sys.platform.startswith("linux"):
        appimage_menu_entry()
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)     # crisp text on scaled displays
        except Exception:
            pass
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)             # 1 ms sleep resolution
        except Exception:
            pass
    try:
        # Own taskbar entry (and icon) instead of being grouped under pythonw
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Bong69.DDPStreamer")
    except Exception:
        pass
    root = tk.Tk(className="ddp-streamer")    # Linux docks match it to ddp-streamer.desktop
    try:                                                   # this and every dialog window
        if os.name == "nt":
            root.iconbitmap(default=LOGO_ICO)
        else:
            root.iconphoto(True, tk.PhotoImage(file=LOGO_PNG))
    except tk.TclError:
        pass
    apply_theme(root, load_settings().get("theme", "dark"))
    args = sys.argv[1:]
    App(root, [a for a in args if a != "--startup"], startup="--startup" in args)
    root.mainloop()


if __name__ == "__main__":
    main()
