# ditto - shared declarations (port of common.h / util.cpp)
#
# Conventions for the Python port:
#   std::wstring -> str, std::string (UTF-8 bytes) -> bytes.
#   Globals live on this module; always access them as C.g_xxx (import common as C) so rebinding is seen.
import os
import sys
import threading
import itertools

from win32 import *  # noqa: F401,F403
import win32 as W

# ---- custom window messages ----
WM_APP_RUNUI = WM_APP + 1       # lParam = id of a queued callable (main window runs + drops it)
WM_APP_INDEXED = WM_APP + 2     # TextBuffer -> editor view: background line index finished
WM_APP_FINDDONE = WM_APP + 3    # editor search thread finished; lParam = id of the FindResult
WM_APP_DOCCHANGED = WM_APP + 4  # view -> main: caret/modified/progress changed; wParam = HWND of view
WM_APP_TERMDATA = WM_APP + 5    # terminal reader thread -> terminal view: new output is pending
WM_APP_TERMEXIT = WM_APP + 6    # terminal shell process exited


# ---- UTF-8 helpers ----
def Utf8ToWide(s):
    if not s:
        return ""
    if isinstance(s, str):
        return s
    return bytes(s).decode("utf-8", "replace")


def WideToUtf8(s):
    if not s:
        return b""
    return s.encode("utf-8", "surrogatepass")


def IsUtf8Cont(c):
    return (c & 0xC0) == 0x80


def Utf8Decode(p, i, n):
    """Decodes one code point from bytes p at index i (n = bytes available from i).
    Returns (bytes consumed, code point); invalid input -> (1, U+FFFD)."""
    c = p[i]
    if c < 0x80:
        return 1, c
    if (c & 0xE0) == 0xC0:
        ln, v = 2, c & 0x1F
    elif (c & 0xF0) == 0xE0:
        ln, v = 3, c & 0x0F
    elif (c & 0xF8) == 0xF0:
        ln, v = 4, c & 0x07
    else:
        return 1, 0xFFFD
    if ln > n:
        return 1, 0xFFFD
    for k in range(1, ln):
        b = p[i + k]
        if (b & 0xC0) != 0x80:
            return 1, 0xFFFD
        v = (v << 6) | (b & 0x3F)
    if (ln == 2 and v < 0x80) or (ln == 3 and v < 0x800) or (ln == 4 and (v < 0x10000 or v > 0x10FFFF)) or \
            (0xD800 <= v <= 0xDFFF):
        return 1, 0xFFFD
    return ln, v


def CharColumns(cp):
    """Display cells: 2 for wide East Asian / emoji, else 1."""
    if cp < 0x1100:
        return 1
    if (cp <= 0x115F or (0x2E80 <= cp <= 0xA4CF and cp != 0x303F) or (0xAC00 <= cp <= 0xD7A3) or
            (0xF900 <= cp <= 0xFAFF) or (0xFE30 <= cp <= 0xFE4F) or (0xFF00 <= cp <= 0xFF60) or
            (0xFFE0 <= cp <= 0xFFE6) or (0x1F300 <= cp <= 0x1F64F) or (0x1F900 <= cp <= 0x1F9FF) or
            (0x20000 <= cp <= 0x3FFFD)):
        return 2
    return 1


# ---- theme ----
class Theme:
    __slots__ = ("bg", "fg", "gutterBg", "gutterFg", "gutterFgCur", "curLine", "selBg", "caret",
                 "heading", "bold", "italic", "code", "link", "url", "quote", "listMarker", "rule", "fence", "html",
                 "addBg", "delBg", "addStrong", "delStrong", "emptyBg", "hunkFg",
                 "panelBg", "panelFg", "panelFgDim", "border", "accent", "hoverBg", "activeBg", "inputBg",
                 "buttonBg", "buttonFg", "statusBg", "statusFg")

    def __init__(self):
        for k in self.__slots__:
            setattr(self, k, 0)


g_theme = Theme()


def ApplyTheme(dark):
    t = g_theme
    if not dark:
        t.bg = RGB(255, 255, 255); t.fg = RGB(31, 31, 31)
        t.gutterBg = RGB(255, 255, 255); t.gutterFg = RGB(160, 160, 160); t.gutterFgCur = RGB(40, 40, 40)
        t.curLine = RGB(245, 245, 245); t.selBg = RGB(173, 214, 255); t.caret = RGB(0, 0, 0)
        t.heading = RGB(0, 70, 160); t.bold = RGB(0, 0, 0); t.italic = RGB(60, 60, 60)
        t.code = RGB(163, 21, 21); t.link = RGB(0, 102, 204); t.url = RGB(0, 128, 128)
        t.quote = RGB(0, 128, 0); t.listMarker = RGB(4, 81, 165); t.rule = RGB(150, 150, 150)
        t.fence = RGB(128, 60, 0); t.html = RGB(128, 0, 128)
        t.addBg = RGB(220, 245, 220); t.delBg = RGB(255, 225, 225)
        t.addStrong = RGB(170, 230, 170); t.delStrong = RGB(255, 190, 190)
        t.emptyBg = RGB(242, 242, 242); t.hunkFg = RGB(120, 120, 180)
        t.panelBg = RGB(243, 243, 243); t.panelFg = RGB(30, 30, 30); t.panelFgDim = RGB(110, 110, 110)
        t.border = RGB(220, 220, 220); t.accent = RGB(0, 95, 184); t.hoverBg = RGB(228, 228, 228)
        t.activeBg = RGB(255, 255, 255); t.inputBg = RGB(255, 255, 255); t.buttonBg = RGB(0, 95, 184)
        t.buttonFg = RGB(255, 255, 255); t.statusBg = RGB(0, 95, 184); t.statusFg = RGB(255, 255, 255)
    else:
        t.bg = RGB(30, 30, 30); t.fg = RGB(212, 212, 212)
        t.gutterBg = RGB(30, 30, 30); t.gutterFg = RGB(110, 118, 129); t.gutterFgCur = RGB(204, 204, 204)
        t.curLine = RGB(40, 40, 40); t.selBg = RGB(38, 79, 120); t.caret = RGB(230, 230, 230)
        t.heading = RGB(86, 156, 214); t.bold = RGB(240, 240, 240); t.italic = RGB(200, 200, 200)
        t.code = RGB(206, 145, 120); t.link = RGB(78, 201, 176); t.url = RGB(156, 220, 254)
        t.quote = RGB(106, 153, 85); t.listMarker = RGB(103, 150, 230); t.rule = RGB(128, 128, 128)
        t.fence = RGB(215, 186, 125); t.html = RGB(197, 134, 192)
        t.addBg = RGB(35, 60, 35); t.delBg = RGB(75, 35, 35)
        t.addStrong = RGB(45, 90, 45); t.delStrong = RGB(110, 45, 45)
        t.emptyBg = RGB(37, 37, 38); t.hunkFg = RGB(140, 140, 200)
        t.panelBg = RGB(37, 37, 38); t.panelFg = RGB(204, 204, 204); t.panelFgDim = RGB(140, 140, 140)
        t.border = RGB(60, 60, 60); t.accent = RGB(0, 122, 204); t.hoverBg = RGB(55, 55, 58)
        t.activeBg = RGB(30, 30, 30); t.inputBg = RGB(49, 49, 49); t.buttonBg = RGB(62, 70, 84)
        t.buttonFg = RGB(224, 224, 224); t.statusBg = RGB(45, 45, 48); t.statusFg = RGB(190, 190, 190)


# ---- settings (ditto.ini next to ditto.py) ----
class Settings:
    def __init__(self):
        self.fontName = "Consolas"
        self.fontSize = 11
        self.dark = False
        self.wrap = False
        self.tabSize = 4
        self.sidebarWidth = 260
        self.showOutput = False  # bottom panel (output / terminal) visible
        self.panelHeight = 240
        self.showSidebar = True
        self.winX = CW_USEDEFAULT
        self.winY = CW_USEDEFAULT
        self.winW = 1200
        self.winH = 800
        self.winMax = False
        self.recentFolders = []


g_settings = Settings()

_kernel32 = W.kernel32
_GetPrivateProfileStringW = _kernel32.GetPrivateProfileStringW
_GetPrivateProfileStringW.restype = DWORD
_GetPrivateProfileStringW.argtypes = [LPCWSTR, LPCWSTR, LPCWSTR, c_void_p, DWORD, LPCWSTR]
_WritePrivateProfileStringW = _kernel32.WritePrivateProfileStringW
_WritePrivateProfileStringW.restype = BOOL
_WritePrivateProfileStringW.argtypes = [LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR]


def _IniPath():
    return JoinPath(ExeDir(), "ditto.ini")


def _IniInt(key, default):
    # GetPrivateProfileInt can't read negative numbers (window positions on left monitors, CW_USEDEFAULT)
    buf = create_unicode_buffer(64)
    _GetPrivateProfileStringW("ditto", key, "", buf, 64, _IniPath())
    if not buf.value:
        return default
    s = buf.value.strip()
    n = 0
    i = 0
    neg = False
    if s[:1] in "+-":
        neg = s[0] == "-"
        i = 1
    while i < len(s) and s[i].isdigit():
        n = n * 10 + int(s[i])
        i += 1
    n = -n if neg else n
    return max(-0x80000000, min(0x7FFFFFFF, n))  # wcstol clamps, then truncated to int


def _IniStr(key, default):
    buf = create_unicode_buffer(1024)
    _GetPrivateProfileStringW("ditto", key, default, buf, 1024, _IniPath())
    return buf.value


def _IniSet(key, v):
    _WritePrivateProfileStringW("ditto", key, v, _IniPath())


def LoadSettings():
    s = g_settings
    s.fontName = _IniStr("FontName", "Consolas")
    s.fontSize = max(6, min(72, _IniInt("FontSize", 11)))
    s.dark = _IniInt("Dark", 0) != 0
    s.wrap = _IniInt("WordWrap", 0) != 0
    s.tabSize = max(1, min(16, _IniInt("TabSize", 4)))
    s.sidebarWidth = max(120, min(1200, _IniInt("SidebarWidth", 260)))
    s.showOutput = _IniInt("ShowOutput", 0) != 0
    s.panelHeight = max(60, min(2000, _IniInt("PanelHeight", 240)))
    s.showSidebar = _IniInt("ShowSidebar", 1) != 0
    s.winX = _IniInt("WinX", CW_USEDEFAULT)
    s.winY = _IniInt("WinY", CW_USEDEFAULT)
    s.winW = _IniInt("WinW", 1200)
    s.winH = _IniInt("WinH", 800)
    s.winMax = _IniInt("WinMax", 0) != 0
    s.recentFolders = []
    for i in range(10):
        v = _IniStr("Recent" + str(i), "")
        if v:
            s.recentFolders.append(v)


def SaveSettings():
    s = g_settings
    _IniSet("FontName", s.fontName)
    _IniSet("FontSize", str(s.fontSize))
    _IniSet("Dark", "1" if s.dark else "0")
    _IniSet("WordWrap", "1" if s.wrap else "0")
    _IniSet("TabSize", str(s.tabSize))
    _IniSet("SidebarWidth", str(s.sidebarWidth))
    _IniSet("ShowOutput", "1" if s.showOutput else "0")
    _IniSet("PanelHeight", str(s.panelHeight))
    _IniSet("ShowSidebar", "1" if s.showSidebar else "0")
    _IniSet("WinX", str(s.winX))
    _IniSet("WinY", str(s.winY))
    _IniSet("WinW", str(s.winW))
    _IniSet("WinH", str(s.winH))
    _IniSet("WinMax", "1" if s.winMax else "0")
    for i in range(10):
        _IniSet("Recent" + str(i), s.recentFolders[i] if i < len(s.recentFolders) else "")


def AddRecentFolder(path):
    r = [x for x in g_settings.recentFolders if not PathEqualsI(x, path)]
    r.insert(0, path)
    g_settings.recentFolders = r[:10]
    SaveSettings()


# ---- globals ----
g_hinst = None
g_mainWnd = None
g_uiFont = None
g_uiFontBold = None
g_iconFont = None  # Segoe MDL2 Assets (glyphs in the U+E700 private range)
g_dpi = 96


def S(v):
    return MulDiv(v, g_dpi, 96)


# ---- misc helpers ----
def ExeDir():
    """Directory holding ditto.py (the C++ build uses the exe's directory)."""
    return os.path.dirname(os.path.abspath(sys.modules["__main__"].__file__)) \
        if hasattr(sys.modules.get("__main__"), "__file__") else os.path.dirname(os.path.abspath(__file__))


def FileNameOf(path):
    p = max(path.rfind("\\"), path.rfind("/"))
    return path if p < 0 else path[p + 1:]


def DirOf(path):
    p = max(path.rfind("\\"), path.rfind("/"))
    return "" if p < 0 else path[:p]


def JoinPath(a, b):
    if not a:
        return b
    if a[-1] in "\\/":
        return a + b
    return a + "\\" + b


def ToBackslashes(s):
    return s.replace("/", "\\")


def ToForwardSlashes(s):
    return s.replace("\\", "/")


def IsDirectory(p):
    a = GetFileAttributesW(p)
    return a != INVALID_FILE_ATTRIBUTES and bool(a & FILE_ATTRIBUTE_DIRECTORY)


def FileExists(p):
    a = GetFileAttributesW(p)
    return a != INVALID_FILE_ATTRIBUTES and not (a & FILE_ATTRIBUTE_DIRECTORY)


def PathEqualsI(a, b):
    return CompareStringOrdinal(ToBackslashes(a), -1, ToBackslashes(b), -1, True) == CSTR_EQUAL


def Trim(s):
    return s.strip(" \t\r\n")


def TrimA(s):
    return s.strip(b" \t\r\n")


def LowerW(s):
    if not s:
        return s
    buf = create_unicode_buffer(s, len(s) + 1)
    n = wstr_len(s)
    CharLowerBuffW(buf, n)
    return buf.value


def FormatError(err):
    buf = c_void_p()
    FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, None,
                   err, 0, byref(buf), 0, None)
    if buf.value:
        s = Trim(wstring_at(buf.value))
        LocalFree(buf)
        return s
    return "Error " + str(err)


def MsgBox(owner, text, flags=MB_OK | MB_ICONINFORMATION):
    return MessageBoxW(owner or g_mainWnd, text, "ditto", flags)


_ui_lock = threading.Lock()
_ui_calls = {}
_ui_ids = itertools.count(1)


def RunOnUi(fn):
    """Queues fn to run on the UI thread (the main window processes WM_APP_RUNUI)."""
    with _ui_lock:
        k = next(_ui_ids)
        _ui_calls[k] = fn
    if not g_mainWnd or not PostMessageW(g_mainWnd, WM_APP_RUNUI, 0, k):
        with _ui_lock:
            _ui_calls.pop(k, None)


def TakeUiCall(k):
    with _ui_lock:
        return _ui_calls.pop(k, None)


# Generic id -> object handoff for messages that carry a pointer in C++ (e.g. WM_APP_FINDDONE).
_handoff = {}
_handoff_ids = itertools.count(1)


def PutHandoff(obj):
    with _ui_lock:
        k = next(_handoff_ids)
        _handoff[k] = obj
        return k


def TakeHandoff(k):
    with _ui_lock:
        return _handoff.pop(k, None)


def SetClipboardText(owner, text):
    if not OpenClipboard(owner):
        return False
    EmptyClipboard()
    data = text.encode("utf-16-le", "surrogatepass") + b"\0\0"
    h = GlobalAlloc(GMEM_MOVEABLE, len(data))
    ok = False
    if h:
        p = GlobalLock(h)
        memmove(p, data, len(data))
        GlobalUnlock(h)
        ok = SetClipboardData(CF_UNICODETEXT, h) is not None
        if not ok:
            GlobalFree(h)
    CloseClipboard()
    return ok


def GetClipboardText(owner):
    """Returns the clipboard text, or None if unavailable."""
    if not IsClipboardFormatAvailable(CF_UNICODETEXT) or not OpenClipboard(owner):
        return None
    text = None
    h = GetClipboardData(CF_UNICODETEXT)
    if h:
        p = GlobalLock(h)
        if p:
            mx = GlobalSize(h) // 2
            raw = string_at(p, mx * 2)
            n = 0
            while n < mx and raw[2 * n:2 * n + 2] != b"\0\0":
                n += 1
            text = raw[:2 * n].decode("utf-16-le", "surrogatepass")
            GlobalUnlock(h)
    CloseClipboard()
    return text


def RevealInExplorer(path):
    """Opens Explorer with `path` selected."""
    if not path:
        return
    ShellExecuteW(None, "open", "explorer.exe", '/select,"' + path + '"', None, SW_SHOWNORMAL)


def Base64(data):
    import base64
    return base64.b64encode(data).decode("ascii")


def DrawFlatButton(dis, primary, toggled=False):
    """Flat owner-drawn button painting (used for BS_OWNERDRAW buttons in panels). dis: DRAWITEMSTRUCT."""
    t = g_theme
    dc = dis.hDC
    r = dis.rcItem.copy()
    pressed = (dis.itemState & ODS_SELECTED) != 0
    disabled = (dis.itemState & ODS_DISABLED) != 0
    bg = t.buttonBg if primary else (t.hoverBg if toggled else t.panelBg)
    fg = t.buttonFg if primary else t.panelFg
    if pressed:
        bg = RGB(GetRValue(bg) * 4 // 5, GetGValue(bg) * 4 // 5, GetBValue(bg) * 4 // 5) if primary else t.hoverBg
    if disabled:
        fg = t.panelFgDim
    fill_solid(dc, r, bg)
    if not primary or toggled:
        frame_solid(dc, r, t.accent if toggled else t.border)
    text = text_of(dis.hwndItem)[:255]
    SetBkMode(dc, TRANSPARENT)
    SetTextColor(dc, fg)
    use_icon = bool(text) and 0xE000 <= ord(text[0]) < 0xF900 and g_iconFont
    old = SelectObject(dc, g_iconFont if use_icon else g_uiFont)
    draw_text(dc, text, r, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS)
    SelectObject(dc, old)
    if dis.itemState & ODS_FOCUS:
        f = r.copy()
        InflateRect(f, -2, -2)
        DrawFocusRect(dc, byref(f))


def MakeButton(parent, id, text):
    h = CreateWindowExW(0, "BUTTON", text, WS_CHILD | WS_VISIBLE | BS_OWNERDRAW, 0, 0, 10, 10, parent,
                        id, g_hinst, None)
    SendMessageW(h, WM_SETFONT, g_uiFont or 0, False)
    return h


# ---- window-class helper ----
# ctypes callbacks must stay referenced for as long as Windows may call them.
_keepalive = []


def keep(obj):
    _keepalive.append(obj)
    return obj


def RegisterWindowClass(name, wndproc, style=0, cursor=IDC_ARROW, background=None, icon=None, icon_sm=None):
    """Registers a window class whose Python wndproc(hwnd, msg, wp, lp) -> int is wrapped and kept alive."""
    def _wrap(h, m, wp, lp):
        try:
            r = wndproc(h, m, wp, lp)
            return 0 if r is None else int(r)
        except Exception:
            ReportException()
            return DefWindowProcW(h, m, wp, lp)

    proc = keep(WNDPROC(_wrap))
    wc = WNDCLASSEXW()
    wc.cbSize = sizeof(WNDCLASSEXW)
    wc.style = style
    wc.lpfnWndProc = proc
    wc.hInstance = g_hinst
    wc.hCursor = LoadCursorW(None, cursor) if cursor else None
    wc.hbrBackground = background
    wc.lpszClassName = name
    wc.hIcon = icon
    wc.hIconSm = icon_sm
    return RegisterClassExW(byref(wc))


def Subclass(hwnd, proc, uid=1, ref=0):
    """SetWindowSubclass with a Python proc(hwnd, msg, wp, lp, uid, ref) -> int kept alive."""
    def _wrap(h, m, wp, lp, i, d):
        try:
            r = proc(h, m, wp, lp, i, d)
            return 0 if r is None else int(r)
        except Exception:
            ReportException()
            return DefSubclassProc(h, m, wp, lp)

    cb = keep(SUBCLASSPROC(_wrap))
    SetWindowSubclass(hwnd, cb, uid, ref)
    return cb


def ReportException():
    import traceback
    traceback.print_exc()
    try:
        sys.stderr.flush()
    except Exception:
        pass
