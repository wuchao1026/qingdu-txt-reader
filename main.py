# -*- coding: utf-8 -*-
"""轻读 — 极简 Windows 桌面 TXT 阅读器"""

from __future__ import annotations

import json
import os
import sys
import ctypes
import threading
import time
import winreg
from ctypes import wintypes
from pathlib import Path

import webview

APP_NAME = "轻读"
CONFIG_PATH = Path(os.path.expanduser("~")) / ".mimo_txt_reader.json"
RADIUS = 18
TITLEBAR_H = 42
MIN_WIDTH = 360
MAX_WIDTH = 920
DEFAULT_WIDTH = 520
DEFAULT_FONT = 18

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
gdiplus = ctypes.windll.gdiplus
_gdiplus_token = None


class GdiplusStartupInput(ctypes.Structure):
    _fields_ = [
        ("GdiplusVersion", ctypes.c_uint32),
        ("DebugEventCallback", ctypes.c_void_p),
        ("SuppressBackgroundThread", ctypes.c_int32),
        ("SuppressExternalCodecs", ctypes.c_int32),
    ]


def _ensure_gdiplus() -> None:
    global _gdiplus_token
    if _gdiplus_token is not None:
        return
    token = ctypes.c_ulong()
    inp = GdiplusStartupInput(1, None, 0, 0)
    gdiplus.GdiplusStartup(ctypes.byref(token), ctypes.byref(inp), None)
    _gdiplus_token = token

    # x64-safe signatures (angles are REAL/float)
    gdiplus.GdipCreatePath.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    gdiplus.GdipCreatePath.restype = ctypes.c_int
    gdiplus.GdipAddPathArcI.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_float,
        ctypes.c_float,
    ]
    gdiplus.GdipAddPathArcI.restype = ctypes.c_int
    gdiplus.GdipClosePathFigure.argtypes = [ctypes.c_void_p]
    gdiplus.GdipClosePathFigure.restype = ctypes.c_int
    gdiplus.GdipCreateRegionPath.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    gdiplus.GdipCreateRegionPath.restype = ctypes.c_int
    gdiplus.GdipGetRegionHRgn.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    gdiplus.GdipGetRegionHRgn.restype = ctypes.c_int
    gdiplus.GdipDeletePath.argtypes = [ctypes.c_void_p]
    gdiplus.GdipDeletePath.restype = ctypes.c_int
    gdiplus.GdipDeleteRegion.argtypes = [ctypes.c_void_p]
    gdiplus.GdipDeleteRegion.restype = ctypes.c_int

# DWMWA_WINDOW_CORNER_PREFERENCE = 33
# DWMWCP_DONOTROUND = 1  — keep one CSS/Region radius only
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DONOTROUND = 1
# Win11 system backdrop (true blur of whatever is behind the window)
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_NONE = 1
DWMSBT_TRANSIENTWINDOW = 3  # acrylic


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def _work_area() -> tuple[int, int, int, int]:
    rect = RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    return rect.left, rect.top, rect.right, rect.bottom


def _screen_size() -> tuple[int, int]:
    left, top, right, bottom = _work_area()
    return right - left, bottom - top


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "fontSize": DEFAULT_FONT,
    "prefWidth": DEFAULT_WIDTH,
    "zhFont": "自动",
    "enFont": "自动",
    "bgMode": "paper",
    "indentFirstLine": True,
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.is_file():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in DEFAULT_CONFIG:
                    if key in data:
                        cfg[key] = data[key]
    except Exception:
        pass
    try:
        cfg["fontSize"] = max(12, min(36, int(cfg["fontSize"])))
    except Exception:
        cfg["fontSize"] = DEFAULT_FONT
    try:
        cfg["prefWidth"] = max(MIN_WIDTH, min(MAX_WIDTH, int(cfg["prefWidth"])))
    except Exception:
        cfg["prefWidth"] = DEFAULT_WIDTH
    if cfg["bgMode"] not in ("paper", "dark"):
        cfg["bgMode"] = "paper"
    cfg["indentFirstLine"] = bool(cfg.get("indentFirstLine", True))
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# File encoding
# ---------------------------------------------------------------------------

def read_text_file(path: str) -> str:
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gb18030")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("big5")
    except UnicodeDecodeError:
        pass
    return raw.decode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# File association (HKCU)
# ---------------------------------------------------------------------------

def _exe_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    main_py = os.path.abspath(__file__)
    py = sys.executable
    return f'"{py}" "{main_py}" "%1"'


def register_file_association() -> bool:
    cmd = _exe_command()
    try:
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, r"Software\Classes\MimoTxtReader.txt"
        ) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "文本文档")
            icon_path = (
                sys.executable
                if getattr(sys, "frozen", False)
                else os.path.abspath(__file__)
            )
            with winreg.CreateKey(k, r"DefaultIcon") as icon_key:
                winreg.SetValueEx(icon_key, "", 0, winreg.REG_SZ, f'"{icon_path}",0')
            with winreg.CreateKey(k, r"shell\open\command") as c:
                winreg.SetValueEx(c, "", 0, winreg.REG_SZ, cmd)

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.txt") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "MimoTxtReader.txt")

        for progid in ("txtfile", "txtfilelegacy"):
            try:
                with winreg.CreateKey(
                    winreg.HKEY_CURRENT_USER,
                    rf"Software\Classes\{progid}\shell\open\command",
                ) as c:
                    winreg.SetValueEx(c, "", 0, winreg.REG_SZ, cmd)
            except OSError:
                pass

        try:
            winreg.DeleteKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.txt\UserChoice",
            )
        except OSError:
            pass
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# HWND helpers (never touch window.native from JS-API thread for walking)
# ---------------------------------------------------------------------------

def _enum_process_hwnd() -> int:
    pid = os.getpid()
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        lpdw = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(lpdw))
        if lpdw.value != pid:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        # skip zero-size helper windows
        r = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return True
        if r.right - r.left < 50 or r.bottom - r.top < 50:
            return True
        found.append(int(hwnd))
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else 0


def _apply_round_region(hwnd: int, w: int | None = None, h: int | None = None) -> None:
    """Clip the OS window with a GDI+ rounded path.

    CreateRoundRectRgn leaves a notch at the window origin (top-left only).
    Building the four corner arcs through GDI+ makes every corner identical
    and matches CSS border-radius more closely.
    """
    if not hwnd:
        return
    try:
        if w is None or h is None:
            r = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return
            w = int(r.right - r.left)
            h = int(r.bottom - r.top)
        w = int(w)
        h = int(h)
        if w < 4 or h < 4:
            return
        try:
            dpi = int(user32.GetDpiForWindow(hwnd))
        except Exception:
            dpi = 96
        if dpi <= 0:
            dpi = 96
        # CSS 18px → device pixels; this is the arc *radius*
        radius = int(round(RADIUS * dpi / 96.0))
        radius = max(6, min(radius, w // 2, h // 2))

        _ensure_gdiplus()
        path = ctypes.c_void_p()
        if gdiplus.GdipCreatePath(0, ctypes.byref(path)) != 0 or not path:
            return
        d = radius * 2
        try:
            # four identical 90° arcs: TL → TR → BR → BL
            gdiplus.GdipAddPathArcI(path, 0, 0, d, d, 180.0, 90.0)
            gdiplus.GdipAddPathArcI(path, w - d, 0, d, d, 270.0, 90.0)
            gdiplus.GdipAddPathArcI(path, w - d, h - d, d, d, 0.0, 90.0)
            gdiplus.GdipAddPathArcI(path, 0, h - d, d, d, 90.0, 90.0)
            gdiplus.GdipClosePathFigure(path)

            region = ctypes.c_void_p()
            if gdiplus.GdipCreateRegionPath(path, ctypes.byref(region)) != 0 or not region:
                return
            hrgn = ctypes.c_void_p()
            # GdipGetRegionHRgn(region, graphics, hrgn) — graphics may be NULL
            if gdiplus.GdipGetRegionHRgn(region, None, ctypes.byref(hrgn)) != 0 or not hrgn:
                gdiplus.GdipDeleteRegion(region)
                return
            # SetWindowRgn owns hrgn
            user32.SetWindowRgn(hwnd, hrgn, True)
            gdiplus.GdipDeleteRegion(region)
        finally:
            gdiplus.GdipDeletePath(path)
    except Exception:
        pass


def _disable_dwm_round(hwnd: int) -> None:
    if not hwnd:
        return
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(ctypes.c_int(DWMWCP_DONOTROUND)),
            ctypes.sizeof(ctypes.c_int),
        )
    except Exception:
        pass


def _set_dwm_backdrop(hwnd: int, glass: bool) -> None:
    if not hwnd:
        return
    try:
        val = DWMSBT_TRANSIENTWINDOW if glass else DWMSBT_NONE
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(ctypes.c_int(val)),
            ctypes.sizeof(ctypes.c_int),
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Bridge API
# NOTE: every attribute stored on this object is walked by pywebview when it
# builds the JS bridge. Anything not meant for JS MUST be underscore-prefixed.
# ---------------------------------------------------------------------------

class Api:
    def __init__(self) -> None:
        self._window = None
        self._hwnd = 0
        self._last_size = (0, 0)
        self._maximized = False
        self._config = load_config()
        self._initial_path: str | None = None
        self._initial_data: dict | None = None

    def bind(self, window) -> None:
        self._window = window

    def debug_log(self, message: str) -> str:
        """JS → file, for diagnosing startup. Safe no-op in production."""
        try:
            with open(os.path.join(os.environ.get("TEMP", "."), "qingdu_js.log"), "a", encoding="utf-8") as f:
                f.write(str(message) + "\n")
        except Exception:
            pass
        return "ok"

    # ---- private helpers ----

    def _get_hwnd(self) -> int:
        if self._hwnd:
            return self._hwnd
        # 1) cached / native form handle
        win = self._window
        if win is not None:
            try:
                native = win.native  # BrowserForm
                if native is not None:
                    handle = native.Handle
                    self._hwnd = int(handle.ToInt32() if hasattr(handle, "ToInt32") else handle)
                    return self._hwnd
            except Exception:
                pass
        # 2) enumerate this process's visible top-level window
        self._hwnd = _enum_process_hwnd()
        return self._hwnd

    def _refresh_region(self) -> None:
        hwnd = self._get_hwnd()
        if not hwnd:
            return
        _disable_dwm_round(hwnd)
        _set_dwm_backdrop(hwnd, False)
        # always clip from the real (physical) window rect
        _apply_round_region(hwnd)

    def set_bg_mode(self, mode: str) -> bool:
        if mode not in ("paper", "dark"):
            return False
        self._config["bgMode"] = mode
        save_config(self._config)
        try:
            hwnd = self._get_hwnd()
            _set_dwm_backdrop(hwnd, False)
            self._refresh_region()
        except Exception:
            pass
        return True

    # ---- window chrome (JS) ----

    def resize_window(self, width: int, height: int) -> bool:
        if not self._window or self._maximized:
            return False
        try:
            w = int(width)
            h = int(height)
        except Exception:
            return False
        w = max(MIN_WIDTH, min(MAX_WIDTH, w))
        if h < 120:
            h = 120
        _sw, sh = _screen_size()
        max_h = int(sh * 0.9)
        if h > max_h:
            h = max_h
        if (w, h) == self._last_size:
            return False
        self._last_size = (w, h)
        try:
            self._window.resize(w, h)
            self._refresh_region()
            return True
        except Exception:
            return False

    def start_drag(self) -> None:
        hwnd = self._get_hwnd()
        if hwnd:
            try:
                user32.ReleaseCapture()
                # WM_NCLBUTTONDOWN = 0x00A1, HTCAPTION = 2
                user32.SendMessageW(hwnd, 0x00A1, 2, 0)
            except Exception:
                pass

    def get_window_pos(self) -> dict:
        win = self._window
        if not win:
            return {"x": 0, "y": 0}
        try:
            x = win.x
            y = win.y
            if isinstance(x, (tuple, list)):
                x = x[0]
            if isinstance(y, (tuple, list)):
                y = y[0]
            return {"x": int(x), "y": int(y)}
        except Exception:
            try:
                hwnd = self._get_hwnd()
                r = RECT()
                if hwnd and user32.GetWindowRect(hwnd, ctypes.byref(r)):
                    return {"x": int(r.left), "y": int(r.top)}
            except Exception:
                pass
            return {"x": 0, "y": 0}

    def move_window(self, x: int, y: int) -> bool:
        if not self._window or self._maximized:
            return False
        try:
            self._window.move(int(x), int(y))
            return True
        except Exception:
            try:
                hwnd = self._get_hwnd()
                if hwnd:
                    w = int(self._last_size[0] or 520)
                    h = int(self._last_size[1] or 220)
                    user32.SetWindowPos(hwnd, 0, int(x), int(y), w, h, 0x0004 | 0x0010)
                    return True
            except Exception:
                pass
            return False

    def minimize(self) -> None:
        if self._window:
            try:
                self._window.minimize()
            except Exception:
                pass

    def toggle_maximize(self) -> bool:
        if not self._window:
            return self._maximized
        try:
            if self._maximized:
                self._window.restore()
                self._maximized = False
                self._last_size = (0, 0)
            else:
                self._window.maximize()
                self._maximized = True
            self._refresh_region()
            return self._maximized
        except Exception:
            return self._maximized

    def is_maximized(self) -> bool:
        return self._maximized

    def close(self) -> None:
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                os._exit(0)

    # ---- files (JS) ----

    def open_file_dialog(self) -> dict | None:
        if not self._window:
            return None
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                file_types=("文本文件 (*.txt;*.text;*.log;*.md)", "所有文件 (*.*)"),
            )
        except Exception:
            result = None
        if not result:
            return None
        path = result[0] if isinstance(result, (list, tuple)) else str(result)
        return self.load_file(path)

    def load_file(self, path: str) -> dict:
        try:
            p = Path(path)
            if not p.is_file():
                return {"ok": False, "error": "文件不存在"}
            text = read_text_file(str(p))
            title = p.name
            self._initial_path = str(p)
            self._initial_data = {
                "ok": True,
                "title": title,
                "name": p.name,
                "path": str(p),
                "text": text,
            }
            if self._window:
                try:
                    self._window.set_title(title)
                except Exception:
                    pass
            return self._initial_data
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ---- config (JS) ----

    def get_config(self) -> dict:
        return dict(self._config)

    def get_initial_data(self) -> dict:
        if self._initial_data:
            return self._initial_data
        return {
            "ok": True,
            "title": APP_NAME,
            "name": "",
            "path": "",
            "text": "",
        }

    def save_prefs(self, data: dict) -> bool:
        if not isinstance(data, dict):
            return False
        for key in DEFAULT_CONFIG:
            if key in data:
                self._config[key] = data[key]
        try:
            self._config["fontSize"] = max(12, min(36, int(self._config["fontSize"])))
        except Exception:
            self._config["fontSize"] = DEFAULT_FONT
        try:
            self._config["prefWidth"] = max(
                MIN_WIDTH, min(MAX_WIDTH, int(self._config["prefWidth"]))
            )
        except Exception:
            self._config["prefWidth"] = DEFAULT_WIDTH
        if self._config["bgMode"] not in ("paper", "dark"):
            self._config["bgMode"] = "paper"
        self._config["indentFirstLine"] = bool(self._config.get("indentFirstLine", True))
        save_config(self._config)
        try:
            hwnd = self._get_hwnd()
            _set_dwm_backdrop(hwnd, False)
        except Exception:
            pass
        return True

    def register_association(self) -> bool:
        return register_file_association()

    def screen_size(self) -> dict:
        w, h = _screen_size()
        return {"width": w, "height": h}

    def window_state(self) -> dict:
        return {
            "maximized": self._maximized,
            "width": self._last_size[0],
            "height": self._last_size[1],
        }


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def _extract_cli_path(argv: list[str]) -> str | None:
    for arg in argv[1:]:
        if arg.startswith("-"):
            continue
        p = Path(arg)
        if p.is_file():
            return str(p.resolve())
    return None


def main() -> None:
    api = Api()
    cli_path = _extract_cli_path(sys.argv)
    if cli_path:
        api.load_file(cli_path)

    cfg = api.get_config()
    width = int(cfg.get("prefWidth", DEFAULT_WIDTH))
    height = 220

    if getattr(sys, "frozen", False):
        html_path = os.path.join(sys._MEIPASS, "ui", "index.html")
    else:
        html_path = str(Path(__file__).resolve().parent / "ui" / "index.html")

    window = webview.create_window(
        api.get_initial_data().get("title", APP_NAME),
        url=html_path,
        js_api=api,
        width=width,
        height=height,
        min_size=(MIN_WIDTH, 160),
        frameless=True,
        easy_drag=False,
        transparent=True,
        resizable=True,
        on_top=False,
        shadow=False,
        text_select=True,
        background_color="#FAF9F6",
    )
    api.bind(window)

    def _on_loaded(*_args, **_kwargs) -> None:
        try:
            api._refresh_region()
        except Exception:
            pass
        # second pass once the native surface has its final size
        def _later_region():
            try:
                time.sleep(0.25)
                api._refresh_region()
            except Exception:
                pass

        threading.Thread(target=_later_region, daemon=True).start()
        # Push content via evaluate_js — more reliable than return-value bridge
        try:
            data = api.get_initial_data()
            name_js = json.dumps(data.get("name") or "", ensure_ascii=False)
            text_js = json.dumps(data.get("text") or "", ensure_ascii=False)
            title_js = json.dumps(data.get("title") or APP_NAME, ensure_ascii=False)
            window.evaluate_js(
                f"window.__qingduPush({name_js}, {text_js}, {title_js});"
            )
        except Exception:
            pass
        try:
            register_file_association()
        except Exception:
            pass

    try:
        window.events.loaded += _on_loaded
    except Exception:
        pass

    webview.start(
        gui="edgechromium",
        debug=bool(os.environ.get("QINGDU_DEBUG")),
    )


if __name__ == "__main__":
    main()
