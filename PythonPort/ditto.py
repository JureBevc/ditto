# ditto entry point (port of main.cpp).  Usage: python ditto.py [files...] [folder]
import os
import sys
import ctypes
from ctypes import wintypes

_HERE = os.path.dirname(os.path.abspath(__file__))

# Under pythonw there is no console: keep tracebacks in ditto.log instead of losing them.
if sys.stderr is None or sys.stdout is None:
    try:
        _log = open(os.path.join(_HERE, "ditto.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = _log
        sys.stderr = _log
    except OSError:
        pass


def _activate_manifest():
    """Creates and activates an activation context from res\\app.manifest so Common Controls v6 (visual styles)
    is used; python.exe's own manifest doesn't request it. Done before win32.py loads comctl32 so the v6 DLL is
    the one bound. The context stays active for the life of the process."""
    class ACTCTXW(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("dwFlags", wintypes.DWORD), ("lpSource", wintypes.LPCWSTR),
                    ("wProcessorArchitecture", wintypes.WORD), ("wLangId", wintypes.WORD),
                    ("lpAssemblyDirectory", wintypes.LPCWSTR), ("lpResourceName", wintypes.LPCWSTR),
                    ("lpApplicationName", wintypes.LPCWSTR), ("hModule", wintypes.HMODULE)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateActCtxW.restype = ctypes.c_void_p
    k32.CreateActCtxW.argtypes = [ctypes.c_void_p]
    k32.ActivateActCtx.restype = wintypes.BOOL
    k32.ActivateActCtx.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    manifest = os.path.join(_HERE, "res", "app.manifest")
    if not os.path.isfile(manifest):
        return None
    ac = ACTCTXW()
    ac.cbSize = ctypes.sizeof(ac)
    ac.lpSource = manifest
    h = k32.CreateActCtxW(ctypes.byref(ac))
    if not h or h == ctypes.c_void_p(-1).value:
        print("ditto: CreateActCtxW failed (%d)" % ctypes.get_last_error(), file=sys.stderr)
        return None
    cookie = ctypes.c_size_t(0)
    if not k32.ActivateActCtx(h, ctypes.byref(cookie)):
        print("ditto: ActivateActCtx failed (%d)" % ctypes.get_last_error(), file=sys.stderr)
        return None
    return h, cookie


_actctx = _activate_manifest()  # kept referenced; never deactivated

import common as C  # noqa: E402
import win32 as W  # noqa: E402
from win32 import *  # noqa: E402,F401,F403
import app  # noqa: E402
import terminal as Term  # noqa: E402
import main_window as MW  # noqa: E402

_SetAppId = W.shell32.SetCurrentProcessExplicitAppUserModelID
_SetAppId.restype = W.HRESULT
_SetAppId.argtypes = [W.LPCWSTR]

APP_USER_MODEL_ID = "ditto.Editor.1"


def _load_icons():
    ico = os.path.join(_HERE, "res", "app.ico")
    if not os.path.isfile(ico):
        return None, None
    big = LoadImageW(None, ico, IMAGE_ICON, GetSystemMetrics(SM_CXICON), GetSystemMetrics(SM_CYICON),
                     LR_LOADFROMFILE)
    small = LoadImageW(None, ico, IMAGE_ICON, GetSystemMetrics(SM_CXSMICON), GetSystemMetrics(SM_CYSMICON),
                       LR_LOADFROMFILE)
    return big, small


def main():
    C.g_hinst = GetModuleHandleW(None)
    show = SW_SHOWDEFAULT
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    try:
        _SetAppId(APP_USER_MODEL_ID)  # taskbar groups ditto separately from other python windows
    except OSError:
        pass
    CoInitializeEx(None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE)
    icc = INITCOMMONCONTROLSEX()
    icc.dwSize = sizeof(icc)
    icc.dwICC = ICC_STANDARD_CLASSES | ICC_TREEVIEW_CLASSES | ICC_LISTVIEW_CLASSES | ICC_BAR_CLASSES
    InitCommonControlsEx(byref(icc))
    # git must never block waiting for terminal input (there is no console)
    SetEnvironmentVariableW("GIT_TERMINAL_PROMPT", "0")
    os.environ["GIT_TERMINAL_PROMPT"] = "0"

    C.LoadSettings()
    s = C.g_settings
    if s.winX != CW_USEDEFAULT:
        r = RECT(s.winX, s.winY, s.winX + s.winW, s.winY + s.winH)
        if not MonitorFromRect(byref(r), MONITOR_DEFAULTTONULL) or s.winW < 200 or s.winH < 150:
            s.winX = s.winY = CW_USEDEFAULT
            s.winW = 1200
            s.winH = 800
    C.ApplyTheme(s.dark)

    MW.g_appIcon, MW.g_appIconSm = _load_icons()
    wnd = MW.CreateMainWindow()
    if not wnd:
        return 1
    ShowWindow(wnd, SW_SHOWMAXIMIZED if s.winMax else show)
    UpdateWindow(wnd)

    # command line: files and/or a folder
    for a in sys.argv[1:]:
        if C.IsDirectory(a):
            app.OpenFolder(a)
        else:
            app.OpenFile(a)

    msg = MSG()
    while GetMessageW(byref(msg), None, 0, 0) > 0:
        if Term.WantsKey(msg) or not TranslateAcceleratorW(wnd, MW.M.accel, byref(msg)):
            TranslateMessage(byref(msg))
            DispatchMessageW(byref(msg))
    CoUninitialize()
    return int(msg.wParam)


if __name__ == "__main__":
    sys.exit(main())
