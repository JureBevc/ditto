# ditto - ctypes bindings for the Win32 API surface the app uses.
# Struct pointer parameters are declared as c_void_p so callers pass byref(struct) (or None).
import ctypes
from ctypes import (
    POINTER, Structure, Union, WINFUNCTYPE, byref, sizeof, cast, c_void_p, c_int, c_uint, c_long, c_ulong,
    c_short, c_ushort, c_ubyte, c_byte, c_char, c_wchar, c_char_p, c_wchar_p, c_size_t, c_ssize_t, c_int64,
    c_uint64, c_uint32, c_int32, c_uint16, c_bool, create_unicode_buffer, create_string_buffer, addressof,
    memmove, string_at, wstring_at,
)
from ctypes import wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
comdlg32 = ctypes.WinDLL("comdlg32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
winhttp = ctypes.WinDLL("winhttp", use_last_error=True)
imm32 = ctypes.WinDLL("imm32", use_last_error=True)
uxtheme = ctypes.WinDLL("uxtheme", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
shlwapi = ctypes.WinDLL("shlwapi", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)

# ---- basic types ----
BOOL = wt.BOOL
BYTE = wt.BYTE
WORD = wt.WORD
DWORD = wt.DWORD
UINT = wt.UINT
INT = c_int
LONG = wt.LONG
HANDLE = c_void_p
HWND = c_void_p
HDC = c_void_p
HMENU = c_void_p
HFONT = c_void_p
HBRUSH = c_void_p
HPEN = c_void_p
HBITMAP = c_void_p
HGDIOBJ = c_void_p
HICON = c_void_p
HCURSOR = c_void_p
HINSTANCE = c_void_p
HMODULE = c_void_p
HACCEL = c_void_p
HGLOBAL = c_void_p
HIMC = c_void_p
HMONITOR = c_void_p
HRESULT = c_long
COLORREF = DWORD
WPARAM = c_size_t
LPARAM = c_ssize_t
LRESULT = c_ssize_t
UINT_PTR = c_size_t
DWORD_PTR = c_size_t
LONG_PTR = c_ssize_t
ULONG_PTR = c_size_t
SIZE_T = c_size_t
LPCWSTR = c_wchar_p
LPWSTR = c_wchar_p
LPVOID = c_void_p
PVOID = c_void_p


class LPCWSTR_OR_INT:
    """Parameter type accepting a str, None, or an integer resource/atom id (MAKEINTRESOURCE)."""

    @classmethod
    def from_param(cls, v):
        if v is None:
            return None
        if isinstance(v, int):
            return c_void_p(v)
        if isinstance(v, str):
            return c_wchar_p(v)
        return v


def _f(dll, name, restype, *argtypes):
    fn = getattr(dll, name)
    fn.restype = restype
    fn.argtypes = list(argtypes)
    globals()[name] = fn
    return fn


def GetLastError():
    return ctypes.get_last_error()


# ---- structures ----
class POINT(Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class RECT(Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]

    def __init__(self, left=0, top=0, right=0, bottom=0):
        super().__init__(left, top, right, bottom)

    def copy(self):
        return RECT(self.left, self.top, self.right, self.bottom)

    def __repr__(self):
        return f"RECT({self.left},{self.top},{self.right},{self.bottom})"


class SIZE(Structure):
    _fields_ = [("cx", LONG), ("cy", LONG)]


class MSG(Structure):
    _fields_ = [("hwnd", HWND), ("message", UINT), ("wParam", WPARAM), ("lParam", LPARAM), ("time", DWORD),
                ("pt", POINT), ("lPrivate", DWORD)]


WNDPROC = WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)
DLGPROC = WINFUNCTYPE(c_ssize_t, HWND, UINT, WPARAM, LPARAM)
SUBCLASSPROC = WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM, UINT_PTR, DWORD_PTR)
WNDENUMPROC = WINFUNCTYPE(BOOL, HWND, LPARAM)
TIMERPROC = WINFUNCTYPE(None, HWND, UINT, UINT_PTR, DWORD)


class WNDCLASSEXW(Structure):
    _fields_ = [("cbSize", UINT), ("style", UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", c_int),
                ("cbWndExtra", c_int), ("hInstance", HINSTANCE), ("hIcon", HICON), ("hCursor", HCURSOR),
                ("hbrBackground", HBRUSH), ("lpszMenuName", LPCWSTR), ("lpszClassName", LPCWSTR),
                ("hIconSm", HICON)]


class PAINTSTRUCT(Structure):
    _fields_ = [("hdc", HDC), ("fErase", BOOL), ("rcPaint", RECT), ("fRestore", BOOL), ("fIncUpdate", BOOL),
                ("rgbReserved", BYTE * 32)]


class LOGFONTW(Structure):
    _fields_ = [("lfHeight", LONG), ("lfWidth", LONG), ("lfEscapement", LONG), ("lfOrientation", LONG),
                ("lfWeight", LONG), ("lfItalic", BYTE), ("lfUnderline", BYTE), ("lfStrikeOut", BYTE),
                ("lfCharSet", BYTE), ("lfOutPrecision", BYTE), ("lfClipPrecision", BYTE), ("lfQuality", BYTE),
                ("lfPitchAndFamily", BYTE), ("lfFaceName", c_wchar * 32)]


class TEXTMETRICW(Structure):
    _fields_ = [("tmHeight", LONG), ("tmAscent", LONG), ("tmDescent", LONG), ("tmInternalLeading", LONG),
                ("tmExternalLeading", LONG), ("tmAveCharWidth", LONG), ("tmMaxCharWidth", LONG),
                ("tmWeight", LONG), ("tmOverhang", LONG), ("tmDigitizedAspectX", LONG),
                ("tmDigitizedAspectY", LONG), ("tmFirstChar", c_wchar), ("tmLastChar", c_wchar),
                ("tmDefaultChar", c_wchar), ("tmBreakChar", c_wchar), ("tmItalic", BYTE), ("tmUnderlined", BYTE),
                ("tmStruckOut", BYTE), ("tmPitchAndFamily", BYTE), ("tmCharSet", BYTE)]


class NONCLIENTMETRICSW(Structure):
    _fields_ = [("cbSize", UINT), ("iBorderWidth", c_int), ("iScrollWidth", c_int), ("iScrollHeight", c_int),
                ("iCaptionWidth", c_int), ("iCaptionHeight", c_int), ("lfCaptionFont", LOGFONTW),
                ("iSmCaptionWidth", c_int), ("iSmCaptionHeight", c_int), ("lfSmCaptionFont", LOGFONTW),
                ("iMenuWidth", c_int), ("iMenuHeight", c_int), ("lfMenuFont", LOGFONTW),
                ("lfStatusFont", LOGFONTW), ("lfMessageFont", LOGFONTW), ("iPaddedBorderWidth", c_int)]


class SCROLLINFO(Structure):
    _fields_ = [("cbSize", UINT), ("fMask", UINT), ("nMin", c_int), ("nMax", c_int), ("nPage", UINT),
                ("nPos", c_int), ("nTrackPos", c_int)]


class DRAWITEMSTRUCT(Structure):
    _fields_ = [("CtlType", UINT), ("CtlID", UINT), ("itemID", UINT), ("itemAction", UINT), ("itemState", UINT),
                ("hwndItem", HWND), ("hDC", HDC), ("rcItem", RECT), ("itemData", ULONG_PTR)]


class NMHDR(Structure):
    _fields_ = [("hwndFrom", HWND), ("idFrom", UINT_PTR), ("code", UINT)]


class TRACKMOUSEEVENT(Structure):
    _fields_ = [("cbSize", DWORD), ("dwFlags", DWORD), ("hwndTrack", HWND), ("dwHoverTime", DWORD)]


class ACCEL(Structure):
    _fields_ = [("fVirt", BYTE), ("key", WORD), ("cmd", WORD)]


class WINDOWPLACEMENT(Structure):
    _fields_ = [("length", UINT), ("flags", UINT), ("showCmd", UINT), ("ptMinPosition", POINT),
                ("ptMaxPosition", POINT), ("rcNormalPosition", RECT), ("rcDevice", RECT)]


class MENUBARINFO(Structure):
    _fields_ = [("cbSize", DWORD), ("rcBar", RECT), ("hMenu", HMENU), ("hwndMenu", HWND), ("fFlags", DWORD)]


class COMPOSITIONFORM(Structure):
    _fields_ = [("dwStyle", DWORD), ("ptCurrentPos", POINT), ("rcArea", RECT)]


class INITCOMMONCONTROLSEX(Structure):
    _fields_ = [("dwSize", DWORD), ("dwICC", DWORD)]


class FILETIME(Structure):
    _fields_ = [("dwLowDateTime", DWORD), ("dwHighDateTime", DWORD)]

    def value(self):
        return (self.dwHighDateTime << 32) | self.dwLowDateTime


class WIN32_FILE_ATTRIBUTE_DATA(Structure):
    _fields_ = [("dwFileAttributes", DWORD), ("ftCreationTime", FILETIME), ("ftLastAccessTime", FILETIME),
                ("ftLastWriteTime", FILETIME), ("nFileSizeHigh", DWORD), ("nFileSizeLow", DWORD)]


class WIN32_FIND_DATAW(Structure):
    _fields_ = [("dwFileAttributes", DWORD), ("ftCreationTime", FILETIME), ("ftLastAccessTime", FILETIME),
                ("ftLastWriteTime", FILETIME), ("nFileSizeHigh", DWORD), ("nFileSizeLow", DWORD),
                ("dwReserved0", DWORD), ("dwReserved1", DWORD), ("cFileName", c_wchar * 260),
                ("cAlternateFileName", c_wchar * 14), ("dwFileType", DWORD), ("dwCreatorType", DWORD),
                ("wFinderFlags", WORD)]


class SECURITY_ATTRIBUTES(Structure):
    _fields_ = [("nLength", DWORD), ("lpSecurityDescriptor", LPVOID), ("bInheritHandle", BOOL)]


class STARTUPINFOW(Structure):
    _fields_ = [("cb", DWORD), ("lpReserved", LPWSTR), ("lpDesktop", LPWSTR), ("lpTitle", LPWSTR),
                ("dwX", DWORD), ("dwY", DWORD), ("dwXSize", DWORD), ("dwYSize", DWORD),
                ("dwXCountChars", DWORD), ("dwYCountChars", DWORD), ("dwFillAttribute", DWORD),
                ("dwFlags", DWORD), ("wShowWindow", WORD), ("cbReserved2", WORD), ("lpReserved2", LPVOID),
                ("hStdInput", HANDLE), ("hStdOutput", HANDLE), ("hStdError", HANDLE)]


class STARTUPINFOEXW(Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", LPVOID)]


class PROCESS_INFORMATION(Structure):
    _fields_ = [("hProcess", HANDLE), ("hThread", HANDLE), ("dwProcessId", DWORD), ("dwThreadId", DWORD)]


class COORD(Structure):
    _fields_ = [("X", c_short), ("Y", c_short)]


class SHFILEOPSTRUCTW(Structure):
    _fields_ = [("hwnd", HWND), ("wFunc", UINT), ("pFrom", c_void_p), ("pTo", c_void_p), ("fFlags", WORD),
                ("fAnyOperationsAborted", BOOL), ("hNameMappings", LPVOID), ("lpszProgressTitle", LPCWSTR)]


class SHFILEINFOW(Structure):
    _fields_ = [("hIcon", HICON), ("iIcon", c_int), ("dwAttributes", DWORD), ("szDisplayName", c_wchar * 260),
                ("szTypeName", c_wchar * 80)]


class OPENFILENAMEW(Structure):
    _fields_ = [("lStructSize", DWORD), ("hwndOwner", HWND), ("hInstance", HINSTANCE), ("lpstrFilter", LPCWSTR),
                ("lpstrCustomFilter", LPWSTR), ("nMaxCustFilter", DWORD), ("nFilterIndex", DWORD),
                ("lpstrFile", c_void_p), ("nMaxFile", DWORD), ("lpstrFileTitle", LPWSTR),
                ("nMaxFileTitle", DWORD), ("lpstrInitialDir", LPCWSTR), ("lpstrTitle", LPCWSTR),
                ("Flags", DWORD), ("nFileOffset", WORD), ("nFileExtension", WORD), ("lpstrDefExt", LPCWSTR),
                ("lCustData", LPARAM), ("lpfnHook", LPVOID), ("lpTemplateName", LPCWSTR), ("pvReserved", LPVOID),
                ("dwReserved", DWORD), ("FlagsEx", DWORD)]


class CHOOSEFONTW(Structure):
    _fields_ = [("lStructSize", DWORD), ("hwndOwner", HWND), ("hDC", HDC), ("lpLogFont", c_void_p),
                ("iPointSize", c_int), ("Flags", DWORD), ("rgbColors", COLORREF), ("lCustData", LPARAM),
                ("lpfnHook", LPVOID), ("lpTemplateName", LPCWSTR), ("hInstance", HINSTANCE),
                ("lpszStyle", LPWSTR), ("nFontType", WORD), ("___MISSING_ALIGNMENT__", WORD),
                ("nSizeMin", c_int), ("nSizeMax", c_int)]


class ACTCTXW(Structure):
    _fields_ = [("cbSize", c_ulong), ("dwFlags", DWORD), ("lpSource", LPCWSTR),
                ("wProcessorArchitecture", WORD), ("wLangId", WORD), ("lpAssemblyDirectory", LPCWSTR),
                ("lpResourceName", LPCWSTR), ("lpApplicationName", LPCWSTR), ("hModule", HMODULE)]


class GUID(Structure):
    _fields_ = [("Data1", DWORD), ("Data2", WORD), ("Data3", WORD), ("Data4", BYTE * 8)]

    def __init__(self, s=None):
        super().__init__()
        if s:
            import uuid
            u = uuid.UUID(s)
            memmove(byref(self), u.bytes_le, 16)


class DLGTEMPLATE(Structure):
    _pack_ = 2
    _fields_ = [("style", DWORD), ("dwExtendedStyle", DWORD), ("cdit", WORD), ("x", c_short), ("y", c_short),
                ("cx", c_short), ("cy", c_short)]


# ---- constants ----
CW_USEDEFAULT = -0x80000000
MAX_PATH = 260
INFINITE = 0xFFFFFFFF
INVALID_HANDLE_VALUE = c_void_p(-1).value
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
SIZE_MAX = (1 << 64) - 1
UINT64_MAX = (1 << 64) - 1

WM_NULL = 0x0000
WM_CREATE = 0x0001
WM_DESTROY = 0x0002
WM_MOVE = 0x0003
WM_SIZE = 0x0005
WM_ACTIVATE = 0x0006
WM_SETFOCUS = 0x0007
WM_KILLFOCUS = 0x0008
WM_ENABLE = 0x000A
WM_SETREDRAW = 0x000B
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_PAINT = 0x000F
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_ERASEBKGND = 0x0014
WM_SHOWWINDOW = 0x0018
WM_ACTIVATEAPP = 0x001C
WM_SETCURSOR = 0x0020
WM_MOUSEACTIVATE = 0x0021
WM_GETMINMAXINFO = 0x0024
WM_DRAWITEM = 0x002B
WM_MEASUREITEM = 0x002C
WM_SETFONT = 0x0030
WM_GETFONT = 0x0031
WM_WINDOWPOSCHANGED = 0x0047
WM_NOTIFY = 0x004E
WM_CONTEXTMENU = 0x007B
WM_GETICON = 0x007F
WM_SETICON = 0x0080
WM_NCCREATE = 0x0081
WM_NCDESTROY = 0x0082
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCPAINT = 0x0085
WM_NCACTIVATE = 0x0086
WM_GETDLGCODE = 0x0087
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_DEADCHAR = 0x0103
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_SYSCHAR = 0x0106
WM_IME_STARTCOMPOSITION = 0x010D
WM_IME_ENDCOMPOSITION = 0x010E
WM_IME_COMPOSITION = 0x010F
WM_INITDIALOG = 0x0110
WM_COMMAND = 0x0111
WM_SYSCOMMAND = 0x0112
WM_TIMER = 0x0113
WM_HSCROLL = 0x0114
WM_VSCROLL = 0x0115
WM_INITMENU = 0x0116
WM_INITMENUPOPUP = 0x0117
WM_MENUSELECT = 0x011F
WM_ENTERIDLE = 0x0121
WM_UNINITMENUPOPUP = 0x0125
WM_CTLCOLORMSGBOX = 0x0132
WM_CTLCOLOREDIT = 0x0133
WM_CTLCOLORLISTBOX = 0x0134
WM_CTLCOLORBTN = 0x0135
WM_CTLCOLORDLG = 0x0136
WM_CTLCOLORSCROLLBAR = 0x0137
WM_CTLCOLORSTATIC = 0x0138
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_RBUTTONDBLCLK = 0x0206
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MBUTTONDBLCLK = 0x0209
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WM_ENTERMENULOOP = 0x0211
WM_EXITMENULOOP = 0x0212
WM_CAPTURECHANGED = 0x0215
WM_ENTERSIZEMOVE = 0x0231
WM_EXITSIZEMOVE = 0x0232
WM_DROPFILES = 0x0233
WM_IME_SETCONTEXT = 0x0281
WM_IME_NOTIFY = 0x0282
WM_MOUSELEAVE = 0x02A3
WM_DPICHANGED = 0x02E0
WM_CUT = 0x0300
WM_COPY = 0x0301
WM_PASTE = 0x0302
WM_CLEAR = 0x0303
WM_UNDO = 0x0304
WM_THEMECHANGED = 0x031A
WM_SETTINGCHANGE = 0x001A
WM_USER = 0x0400
WM_APP = 0x8000
WM_UAHDRAWMENU = 0x0091
WM_UAHDRAWMENUITEM = 0x0092

WS_OVERLAPPED = 0x00000000
WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_MINIMIZE = 0x20000000
WS_VISIBLE = 0x10000000
WS_DISABLED = 0x08000000
WS_CLIPSIBLINGS = 0x04000000
WS_CLIPCHILDREN = 0x02000000
WS_MAXIMIZE = 0x01000000
WS_CAPTION = 0x00C00000
WS_BORDER = 0x00800000
WS_DLGFRAME = 0x00400000
WS_VSCROLL = 0x00200000
WS_HSCROLL = 0x00100000
WS_SYSMENU = 0x00080000
WS_THICKFRAME = 0x00040000
WS_GROUP = 0x00020000
WS_TABSTOP = 0x00010000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_OVERLAPPEDWINDOW = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
WS_EX_DLGMODALFRAME = 0x00000001
WS_EX_TOPMOST = 0x00000008
WS_EX_ACCEPTFILES = 0x00000010
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_WINDOWEDGE = 0x00000100
WS_EX_CLIENTEDGE = 0x00000200
WS_EX_CONTROLPARENT = 0x00010000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_COMPOSITED = 0x02000000

CS_VREDRAW = 0x0001
CS_HREDRAW = 0x0002
CS_DBLCLKS = 0x0008
CS_DROPSHADOW = 0x00020000

ES_LEFT = 0x0000
ES_MULTILINE = 0x0004
ES_PASSWORD = 0x0020
ES_AUTOVSCROLL = 0x0040
ES_AUTOHSCROLL = 0x0080
ES_NOHIDESEL = 0x0100
ES_READONLY = 0x0800
ES_WANTRETURN = 0x1000

BS_PUSHBUTTON = 0x0
BS_DEFPUSHBUTTON = 0x1
BS_AUTOCHECKBOX = 0x3
BS_AUTORADIOBUTTON = 0x9
BS_OWNERDRAW = 0xB
BST_UNCHECKED = 0
BST_CHECKED = 1
BM_GETCHECK = 0x00F0
BM_SETCHECK = 0x00F1
BN_CLICKED = 0

SS_LEFT = 0x0
SS_NOPREFIX = 0x80
SS_LEFTNOWORDWRAP = 0xC

LBS_NOTIFY = 0x0001
LBS_NOINTEGRALHEIGHT = 0x0100
LB_ADDSTRING = 0x0180
LB_RESETCONTENT = 0x0184
LB_SETCURSEL = 0x0186
LB_GETCURSEL = 0x0188
LB_GETCOUNT = 0x018B
LBN_SELCHANGE = 1
LBN_DBLCLK = 2

EM_GETSEL = 0x00B0
EM_SETSEL = 0x00B1
EM_GETRECT = 0x00B2
EM_SCROLLCARET = 0x00B7
EM_REPLACESEL = 0x00C2
EM_SETLIMITTEXT = 0x00C5
EM_SETPASSWORDCHAR = 0x00CC
EM_SETMARGINS = 0x00D3
EM_SETCUEBANNER = 0x1501
EC_LEFTMARGIN = 0x1
EC_RIGHTMARGIN = 0x2
EN_SETFOCUS = 0x0100
EN_KILLFOCUS = 0x0200
EN_CHANGE = 0x0300
EN_UPDATE = 0x0400

DS_SETFONT = 0x40
DS_MODALFRAME = 0x80
DS_CENTER = 0x0800
DS_SHELLFONT = DS_SETFONT | 0x0008

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_SHOWMAXIMIZED = 3
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
SW_MINIMIZE = 6
SW_SHOWNA = 8
SW_RESTORE = 9
SW_SHOWDEFAULT = 10

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOREDRAW = 0x0008
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080
HWND_TOP = 0
HWND_TOPMOST = -1

MF_STRING = 0x0000
MF_ENABLED = 0x0000
MF_GRAYED = 0x0001
MF_DISABLED = 0x0002
MF_UNCHECKED = 0x0000
MF_CHECKED = 0x0008
MF_POPUP = 0x0010
MF_BYCOMMAND = 0x0000
MF_BYPOSITION = 0x0400
MF_SEPARATOR = 0x0800
MF_OWNERDRAW = 0x0100
TPM_LEFTALIGN = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_CENTERALIGN = 0x0004
TPM_RIGHTALIGN = 0x0008
TPM_TOPALIGN = 0x0000
TPM_BOTTOMALIGN = 0x0020
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

DT_TOP = 0x0
DT_LEFT = 0x0
DT_CENTER = 0x1
DT_RIGHT = 0x2
DT_VCENTER = 0x4
DT_BOTTOM = 0x8
DT_WORDBREAK = 0x10
DT_SINGLELINE = 0x20
DT_EXPANDTABS = 0x40
DT_NOCLIP = 0x100
DT_CALCRECT = 0x400
DT_NOPREFIX = 0x800
DT_PATH_ELLIPSIS = 0x4000
DT_END_ELLIPSIS = 0x8000

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_CANCEL = 0x03
VK_MBUTTON = 0x04
VK_BACK = 0x08
VK_TAB = 0x09
VK_CLEAR = 0x0C
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_PAUSE = 0x13
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_APPS = 0x5D
VK_NUMPAD0 = 0x60
VK_MULTIPLY = 0x6A
VK_ADD = 0x6B
VK_SEPARATOR = 0x6C
VK_SUBTRACT = 0x6D
VK_DECIMAL = 0x6E
VK_DIVIDE = 0x6F
VK_F1 = 0x70
VK_F2 = 0x71
VK_F3 = 0x72
VK_F4 = 0x73
VK_F5 = 0x74
VK_F6 = 0x75
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_F11 = 0x7A
VK_F12 = 0x7B
VK_OEM_1 = 0xBA
VK_OEM_PLUS = 0xBB
VK_OEM_COMMA = 0xBC
VK_OEM_MINUS = 0xBD
VK_OEM_PERIOD = 0xBE
VK_OEM_2 = 0xBF
VK_OEM_3 = 0xC0
VK_OEM_4 = 0xDB
VK_OEM_5 = 0xDC
VK_OEM_6 = 0xDD
VK_OEM_7 = 0xDE

FVIRTKEY = 0x01
FNOINVERT = 0x02
FSHIFT = 0x04
FCONTROL = 0x08
FALT = 0x10

SB_HORZ = 0
SB_VERT = 1
SB_CTL = 2
SB_BOTH = 3
SB_LINEUP = 0
SB_LINELEFT = 0
SB_LINEDOWN = 1
SB_LINERIGHT = 1
SB_PAGEUP = 2
SB_PAGELEFT = 2
SB_PAGEDOWN = 3
SB_PAGERIGHT = 3
SB_THUMBPOSITION = 4
SB_THUMBTRACK = 5
SB_TOP = 6
SB_LEFT = 6
SB_BOTTOM = 7
SB_RIGHT = 7
SB_ENDSCROLL = 8
SIF_RANGE = 0x1
SIF_PAGE = 0x2
SIF_POS = 0x4
SIF_DISABLENOSCROLL = 0x8
SIF_TRACKPOS = 0x10
SIF_ALL = SIF_RANGE | SIF_PAGE | SIF_POS | SIF_TRACKPOS

MB_OK = 0x0
MB_OKCANCEL = 0x1
MB_YESNOCANCEL = 0x3
MB_YESNO = 0x4
MB_ICONERROR = 0x10
MB_ICONQUESTION = 0x20
MB_ICONWARNING = 0x30
MB_ICONINFORMATION = 0x40
MB_DEFBUTTON2 = 0x100
IDOK = 1
IDCANCEL = 2
IDABORT = 3
IDRETRY = 4
IDIGNORE = 5
IDYES = 6
IDNO = 7

IDC_ARROW = 32512
IDC_IBEAM = 32513
IDC_WAIT = 32514
IDC_CROSS = 32515
IDC_SIZEWE = 32644
IDC_SIZENS = 32645
IDC_SIZEALL = 32646
IDC_HAND = 32649
IDC_APPSTARTING = 32650
IDI_APPLICATION = 32512

GWL_STYLE = -16
GWL_EXSTYLE = -20
GWLP_WNDPROC = -4
GWLP_HINSTANCE = -6
GWLP_ID = -12
GWLP_USERDATA = -21
GA_PARENT = 1
GA_ROOT = 2
GA_ROOTOWNER = 3

CF_TEXT = 1
CF_UNICODETEXT = 13
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
FILE_SHARE_DELETE = 0x4
CREATE_NEW = 1
CREATE_ALWAYS = 2
OPEN_EXISTING = 3
OPEN_ALWAYS = 4
TRUNCATE_EXISTING = 5
FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_ARCHIVE = 0x20
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
FILE_BEGIN = 0
FILE_CURRENT = 1
FILE_END = 2
PAGE_READONLY = 0x02
FILE_MAP_READ = 0x0004
MOVEFILE_REPLACE_EXISTING = 0x1
MOVEFILE_COPY_ALLOWED = 0x2
MOVEFILE_WRITE_THROUGH = 0x8
FindExInfoBasic = 1
FindExSearchNameMatch = 0
FIND_FIRST_EX_LARGE_FETCH = 2
GetFileExInfoStandard = 0

ERROR_SUCCESS = 0
ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_BROKEN_PIPE = 109
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_ALREADY_EXISTS = 183
ERROR_NO_MORE_FILES = 18
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
WAIT_FAILED = 0xFFFFFFFF
STILL_ACTIVE = 259

STARTF_USESHOWWINDOW = 0x1
STARTF_USESTDHANDLES = 0x100
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x400
EXTENDED_STARTUPINFO_PRESENT = 0x80000
CREATE_NEW_PROCESS_GROUP = 0x200
HANDLE_FLAG_INHERIT = 0x1
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016

FORMAT_MESSAGE_ALLOCATE_BUFFER = 0x100
FORMAT_MESSAGE_IGNORE_INSERTS = 0x200
FORMAT_MESSAGE_FROM_SYSTEM = 0x1000

DEFAULT_CHARSET = 1
OUT_DEFAULT_PRECIS = 0
OUT_TT_PRECIS = 4
CLIP_DEFAULT_PRECIS = 0
DEFAULT_QUALITY = 0
ANTIALIASED_QUALITY = 4
CLEARTYPE_QUALITY = 5
DEFAULT_PITCH = 0
FIXED_PITCH = 1
VARIABLE_PITCH = 2
FF_DONTCARE = 0
FF_MODERN = 0x30
FW_DONTCARE = 0
FW_THIN = 100
FW_LIGHT = 300
FW_NORMAL = 400
FW_MEDIUM = 500
FW_SEMIBOLD = 600
FW_BOLD = 700
LOGPIXELSX = 88
LOGPIXELSY = 90
ETO_OPAQUE = 0x2
ETO_CLIPPED = 0x4
SRCCOPY = 0x00CC0020
TRANSPARENT = 1
OPAQUE = 2
PS_SOLID = 0
PS_DOT = 2
NULL_BRUSH = 5
NULL_PEN = 8
WHITE_BRUSH = 0
BLACK_BRUSH = 4
DEFAULT_GUI_FONT = 17
DC_BRUSH = 18
DC_PEN = 19

TME_HOVER = 0x1
TME_LEAVE = 0x2
TME_CANCEL = 0x80000000
HOVER_DEFAULT = 0xFFFFFFFF
RDW_INVALIDATE = 0x1
RDW_INTERNALPAINT = 0x2
RDW_ERASE = 0x4
RDW_ALLCHILDREN = 0x80
RDW_UPDATENOW = 0x100
RDW_FRAME = 0x400
SPI_GETWHEELSCROLLLINES = 0x0068
SPI_GETNONCLIENTMETRICS = 0x0029
WHEEL_DELTA = 120
WHEEL_PAGESCROLL = 0xFFFFFFFF
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_CXVSCROLL = 2
SM_CYHSCROLL = 3
SM_CXICON = 11
SM_CYICON = 12
SM_CXSMICON = 49
SM_CYSMICON = 50
SM_CXDOUBLECLK = 36
SM_CYDOUBLECLK = 37
MK_LBUTTON = 0x1
MK_RBUTTON = 0x2
MK_SHIFT = 0x4
MK_CONTROL = 0x8
MK_MBUTTON = 0x10

ODT_MENU = 1
ODT_BUTTON = 4
ODA_DRAWENTIRE = 1
ODA_SELECT = 2
ODA_FOCUS = 4
ODS_SELECTED = 0x1
ODS_GRAYED = 0x2
ODS_DISABLED = 0x4
ODS_CHECKED = 0x8
ODS_FOCUS = 0x10
ODS_HOTLIGHT = 0x40
ODS_INACTIVE = 0x80
ODS_NOACCEL = 0x100

DLGC_WANTARROWS = 0x1
DLGC_WANTTAB = 0x2
DLGC_WANTALLKEYS = 0x4
DLGC_WANTMESSAGE = 0x4
DLGC_HASSETSEL = 0x8
DLGC_WANTCHARS = 0x80

FO_MOVE = 1
FO_COPY = 2
FO_DELETE = 3
FO_RENAME = 4
FOF_SILENT = 0x4
FOF_NOCONFIRMATION = 0x10
FOF_ALLOWUNDO = 0x40
FOF_NOERRORUI = 0x400
SHGFI_ICON = 0x100
SHGFI_SYSICONINDEX = 0x4000
SHGFI_SMALLICON = 0x1
SHGFI_OPENICON = 0x2
SHGFI_USEFILEATTRIBUTES = 0x10

OFN_OVERWRITEPROMPT = 0x2
OFN_HIDEREADONLY = 0x4
OFN_NOCHANGEDIR = 0x8
OFN_PATHMUSTEXIST = 0x800
OFN_FILEMUSTEXIST = 0x1000
OFN_EXPLORER = 0x80000
CF_SCREENFONTS = 0x1
CF_INITTOLOGFONTSTRUCT = 0x40
CF_FIXEDPITCHONLY = 0x4000
CF_NOVERTFONTS = 0x01000000

IMAGE_BITMAP = 0
IMAGE_ICON = 1
IMAGE_CURSOR = 2
LR_DEFAULTSIZE = 0x40
LR_LOADFROMFILE = 0x10
LR_SHARED = 0x8000
ICON_SMALL = 0
ICON_BIG = 1

CSTR_LESS_THAN = 1
CSTR_EQUAL = 2
CSTR_GREATER_THAN = 3

MONITOR_DEFAULTTONULL = 0
MONITOR_DEFAULTTOPRIMARY = 1
MONITOR_DEFAULTTONEAREST = 2
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
ICC_LISTVIEW_CLASSES = 0x1
ICC_TREEVIEW_CLASSES = 0x2
ICC_BAR_CLASSES = 0x4
ICC_STANDARD_CLASSES = 0x4000
COINIT_APARTMENTTHREADED = 0x2
COINIT_DISABLE_OLE1DDE = 0x4
CLSCTX_INPROC_SERVER = 0x1
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
OBJID_MENU = -3
PM_NOREMOVE = 0
PM_REMOVE = 1
QS_ALLINPUT = 0x04FF
MWMO_INPUTAVAILABLE = 0x4
CFS_POINT = 0x2
CFS_RECT = 0x1
GCS_RESULTSTR = 0x800
HTCLIENT = 1
SC_KEYMENU = 0xF100

FOS_PICKFOLDERS = 0x20
FOS_FORCEFILESYSTEM = 0x40
SIGDN_FILESYSPATH = 0x80058000 - (1 << 32)  # signed int32

ACTCTX_FLAG_RESOURCE_NAME_VALID = 0x8

WINHTTP_ACCESS_TYPE_DEFAULT_PROXY = 0
WINHTTP_ACCESS_TYPE_NO_PROXY = 1
WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY = 4
WINHTTP_FLAG_SECURE = 0x00800000
WINHTTP_QUERY_STATUS_CODE = 19
WINHTTP_QUERY_FLAG_NUMBER = 0x20000000
WINHTTP_NO_HEADER_INDEX = None
WINHTTP_HEADER_NAME_BY_INDEX = None
INTERNET_DEFAULT_HTTPS_PORT = 443

# ---- macros ----
def RGB(r, g, b):
    return (r & 0xFF) | ((g & 0xFF) << 8) | ((b & 0xFF) << 16)


def GetRValue(c):
    return c & 0xFF


def GetGValue(c):
    return (c >> 8) & 0xFF


def GetBValue(c):
    return (c >> 16) & 0xFF


def LOWORD(v):
    return v & 0xFFFF


def HIWORD(v):
    return (v >> 16) & 0xFFFF


def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def GET_X_LPARAM(lp):
    return _s16(lp)


def GET_Y_LPARAM(lp):
    return _s16(lp >> 16)


def GET_WHEEL_DELTA_WPARAM(wp):
    return _s16(wp >> 16)


def GET_KEYSTATE_WPARAM(wp):
    return wp & 0xFFFF


def MAKELPARAM(lo, hi):
    return ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)


MAKEWPARAM = MAKELPARAM
MAKELONG = MAKELPARAM


def MAKEINTRESOURCEW(i):
    return i


def IS_HIGH_SURROGATE(c):
    return 0xD800 <= c <= 0xDBFF


def IS_LOW_SURROGATE(c):
    return 0xDC00 <= c <= 0xDFFF


def SUCCEEDED(hr):
    return hr >= 0


def FAILED(hr):
    return hr < 0


def PtInRect(r, pt):
    x, y = (pt.x, pt.y) if hasattr(pt, "x") else pt
    return r.left <= x < r.right and r.top <= y < r.bottom


def InflateRect(r, dx, dy):
    r.left -= dx
    r.top -= dy
    r.right += dx
    r.bottom += dy


def OffsetRect(r, dx, dy):
    r.left += dx
    r.right += dx
    r.top += dy
    r.bottom += dy


def IsRectEmpty(r):
    return r.right <= r.left or r.bottom <= r.top


def MulDiv(a, b, c):
    if c == 0:
        return -1
    n = a * b
    # round half away from zero, like Win32 MulDiv
    q, rem = divmod(abs(n), abs(c))
    if rem * 2 >= abs(c):
        q += 1
    return q if (n >= 0) == (c > 0) else -q


# ---- user32 ----
_f(user32, "RegisterClassExW", wt.ATOM, c_void_p)
_f(user32, "UnregisterClassW", BOOL, LPCWSTR_OR_INT, HINSTANCE)
_f(user32, "CreateWindowExW", HWND, DWORD, LPCWSTR_OR_INT, LPCWSTR, DWORD, c_int, c_int, c_int, c_int, HWND, HMENU,
   HINSTANCE, LPVOID)
_f(user32, "DestroyWindow", BOOL, HWND)
_f(user32, "DefWindowProcW", LRESULT, HWND, UINT, WPARAM, LPARAM)
_f(user32, "CallWindowProcW", LRESULT, c_void_p, HWND, UINT, WPARAM, LPARAM)
_f(user32, "ShowWindow", BOOL, HWND, c_int)
_f(user32, "UpdateWindow", BOOL, HWND)
_f(user32, "MoveWindow", BOOL, HWND, c_int, c_int, c_int, c_int, BOOL)
_f(user32, "SetWindowPos", BOOL, HWND, HWND, c_int, c_int, c_int, c_int, UINT)
_f(user32, "GetClientRect", BOOL, HWND, c_void_p)
_f(user32, "GetWindowRect", BOOL, HWND, c_void_p)
_f(user32, "InvalidateRect", BOOL, HWND, c_void_p, BOOL)
_f(user32, "ValidateRect", BOOL, HWND, c_void_p)
_f(user32, "RedrawWindow", BOOL, HWND, c_void_p, c_void_p, UINT)
_f(user32, "BeginPaint", HDC, HWND, c_void_p)
_f(user32, "EndPaint", BOOL, HWND, c_void_p)
_f(user32, "GetDC", HDC, HWND)
_f(user32, "ReleaseDC", c_int, HWND, HDC)
_f(user32, "FillRect", c_int, HDC, c_void_p, HBRUSH)
_f(user32, "FrameRect", c_int, HDC, c_void_p, HBRUSH)
_f(user32, "DrawFocusRect", BOOL, HDC, c_void_p)
_f(user32, "DrawTextW", c_int, HDC, LPCWSTR, c_int, c_void_p, UINT)
_f(user32, "SendMessageW", LRESULT, HWND, UINT, WPARAM, LPARAM)
_f(user32, "PostMessageW", BOOL, HWND, UINT, WPARAM, LPARAM)
_f(user32, "PostQuitMessage", None, c_int)
_f(user32, "GetMessageW", BOOL, c_void_p, HWND, UINT, UINT)
_f(user32, "PeekMessageW", BOOL, c_void_p, HWND, UINT, UINT, UINT)
_f(user32, "TranslateMessage", BOOL, c_void_p)
_f(user32, "DispatchMessageW", LRESULT, c_void_p)
_f(user32, "IsDialogMessageW", BOOL, HWND, c_void_p)
_f(user32, "MsgWaitForMultipleObjects", DWORD, DWORD, c_void_p, BOOL, DWORD, DWORD)
_f(user32, "TranslateAcceleratorW", c_int, HWND, HACCEL, c_void_p)
_f(user32, "CreateAcceleratorTableW", HACCEL, c_void_p, c_int)
_f(user32, "DestroyAcceleratorTable", BOOL, HACCEL)
_f(user32, "SetFocus", HWND, HWND)
_f(user32, "GetFocus", HWND)
_f(user32, "SetActiveWindow", HWND, HWND)
_f(user32, "SetForegroundWindow", BOOL, HWND)
_f(user32, "GetForegroundWindow", HWND)
_f(user32, "GetActiveWindow", HWND)
_f(user32, "SetCapture", HWND, HWND)
_f(user32, "ReleaseCapture", BOOL)
_f(user32, "GetCapture", HWND)
_f(user32, "LoadCursorW", HCURSOR, HINSTANCE, LPCWSTR_OR_INT)
_f(user32, "LoadIconW", HICON, HINSTANCE, LPCWSTR_OR_INT)
_f(user32, "LoadImageW", HANDLE, HINSTANCE, LPCWSTR_OR_INT, UINT, c_int, c_int, UINT)
_f(user32, "DestroyIcon", BOOL, HICON)
_f(user32, "SetCursor", HCURSOR, HCURSOR)
_f(user32, "GetCursorPos", BOOL, c_void_p)
_f(user32, "SetCursorPos", BOOL, c_int, c_int)
_f(user32, "ScreenToClient", BOOL, HWND, c_void_p)
_f(user32, "ClientToScreen", BOOL, HWND, c_void_p)
_f(user32, "MapWindowPoints", c_int, HWND, HWND, c_void_p, UINT)
_f(user32, "GetKeyState", c_short, c_int)
_f(user32, "GetAsyncKeyState", c_short, c_int)
_f(user32, "SetWindowLongPtrW", LONG_PTR, HWND, c_int, LONG_PTR)
_f(user32, "GetWindowLongPtrW", LONG_PTR, HWND, c_int)
_f(user32, "SetWindowTextW", BOOL, HWND, LPCWSTR)
_f(user32, "GetWindowTextW", c_int, HWND, c_void_p, c_int)
_f(user32, "GetWindowTextLengthW", c_int, HWND)
_f(user32, "GetClassNameW", c_int, HWND, c_void_p, c_int)
_f(user32, "GetParent", HWND, HWND)
_f(user32, "SetParent", HWND, HWND, HWND)
_f(user32, "GetAncestor", HWND, HWND, UINT)
_f(user32, "GetWindow", HWND, HWND, UINT)
_f(user32, "IsWindow", BOOL, HWND)
_f(user32, "IsWindowVisible", BOOL, HWND)
_f(user32, "IsWindowEnabled", BOOL, HWND)
_f(user32, "IsChild", BOOL, HWND, HWND)
_f(user32, "IsIconic", BOOL, HWND)
_f(user32, "IsZoomed", BOOL, HWND)
_f(user32, "EnableWindow", BOOL, HWND, BOOL)
_f(user32, "EnumChildWindows", BOOL, HWND, WNDENUMPROC, LPARAM)
_f(user32, "GetDlgItem", HWND, HWND, c_int)
_f(user32, "GetDlgCtrlID", c_int, HWND)
_f(user32, "SetDlgItemTextW", BOOL, HWND, c_int, LPCWSTR)
_f(user32, "GetDlgItemTextW", UINT, HWND, c_int, c_void_p, c_int)
_f(user32, "SendDlgItemMessageW", LRESULT, HWND, c_int, UINT, WPARAM, LPARAM)
_f(user32, "CheckRadioButton", BOOL, HWND, c_int, c_int, c_int)
_f(user32, "IsDlgButtonChecked", UINT, HWND, c_int)
_f(user32, "CheckDlgButton", BOOL, HWND, c_int, UINT)
_f(user32, "DialogBoxParamW", c_ssize_t, HINSTANCE, LPCWSTR_OR_INT, HWND, DLGPROC, LPARAM)
_f(user32, "DialogBoxIndirectParamW", c_ssize_t, HINSTANCE, c_void_p, HWND, DLGPROC, LPARAM)
_f(user32, "EndDialog", BOOL, HWND, c_ssize_t)
_f(user32, "MapDialogRect", BOOL, HWND, c_void_p)
_f(user32, "GetDialogBaseUnits", LONG)
_f(user32, "MessageBoxW", c_int, HWND, LPCWSTR, LPCWSTR, UINT)
_f(user32, "MessageBeep", BOOL, UINT)
_f(user32, "SetTimer", UINT_PTR, HWND, UINT_PTR, UINT, c_void_p)
_f(user32, "KillTimer", BOOL, HWND, UINT_PTR)
_f(user32, "TrackMouseEvent", BOOL, c_void_p)
_f(user32, "SetScrollInfo", c_int, HWND, c_int, c_void_p, BOOL)
_f(user32, "GetScrollInfo", BOOL, HWND, c_int, c_void_p)
_f(user32, "GetScrollPos", c_int, HWND, c_int)
_f(user32, "SetScrollPos", c_int, HWND, c_int, c_int, BOOL)
_f(user32, "ShowScrollBar", BOOL, HWND, c_int, BOOL)
_f(user32, "CreateMenu", HMENU)
_f(user32, "CreatePopupMenu", HMENU)
_f(user32, "DestroyMenu", BOOL, HMENU)
_f(user32, "AppendMenuW", BOOL, HMENU, UINT, UINT_PTR, LPCWSTR_OR_INT)
_f(user32, "InsertMenuW", BOOL, HMENU, UINT, UINT, UINT_PTR, LPCWSTR_OR_INT)
_f(user32, "DeleteMenu", BOOL, HMENU, UINT, UINT)
_f(user32, "RemoveMenu", BOOL, HMENU, UINT, UINT)
_f(user32, "EnableMenuItem", BOOL, HMENU, UINT, UINT)
_f(user32, "CheckMenuItem", DWORD, HMENU, UINT, UINT)
_f(user32, "GetMenuItemCount", c_int, HMENU)
_f(user32, "GetMenuItemID", UINT, HMENU, c_int)
_f(user32, "GetSubMenu", HMENU, HMENU, c_int)
_f(user32, "GetMenuStringW", c_int, HMENU, UINT, c_void_p, c_int, UINT)
_f(user32, "GetMenuBarInfo", BOOL, HWND, LONG, LONG, c_void_p)
_f(user32, "GetMenu", HMENU, HWND)
_f(user32, "SetMenu", BOOL, HWND, HMENU)
_f(user32, "DrawMenuBar", BOOL, HWND)
_f(user32, "TrackPopupMenu", BOOL, HMENU, UINT, c_int, c_int, c_int, HWND, c_void_p)
_f(user32, "TrackPopupMenuEx", BOOL, HMENU, UINT, c_int, c_int, HWND, c_void_p)
_f(user32, "EndMenu", BOOL)
_f(user32, "CreateCaret", BOOL, HWND, HBITMAP, c_int, c_int)
_f(user32, "DestroyCaret", BOOL)
_f(user32, "ShowCaret", BOOL, HWND)
_f(user32, "HideCaret", BOOL, HWND)
_f(user32, "SetCaretPos", BOOL, c_int, c_int)
_f(user32, "GetCaretPos", BOOL, c_void_p)
_f(user32, "OpenClipboard", BOOL, HWND)
_f(user32, "CloseClipboard", BOOL)
_f(user32, "EmptyClipboard", BOOL)
_f(user32, "SetClipboardData", HANDLE, UINT, HANDLE)
_f(user32, "GetClipboardData", HANDLE, UINT)
_f(user32, "IsClipboardFormatAvailable", BOOL, UINT)
_f(user32, "GetSystemMetrics", c_int, c_int)
_f(user32, "SystemParametersInfoW", BOOL, UINT, UINT, c_void_p, UINT)
_f(user32, "GetWindowPlacement", BOOL, HWND, c_void_p)
_f(user32, "SetWindowPlacement", BOOL, HWND, c_void_p)
_f(user32, "MonitorFromRect", HMONITOR, c_void_p, DWORD)
_f(user32, "MonitorFromWindow", HMONITOR, HWND, DWORD)
_f(user32, "CharLowerBuffW", DWORD, c_void_p, DWORD)
_f(user32, "CharUpperBuffW", DWORD, c_void_p, DWORD)
_f(user32, "GetDoubleClickTime", UINT)
_f(user32, "ScrollWindowEx", c_int, HWND, c_int, c_int, c_void_p, c_void_p, c_void_p, c_void_p, UINT)
_f(user32, "WindowFromPoint", HWND, POINT)
_f(user32, "GetSysColor", DWORD, c_int)
_f(user32, "GetSysColorBrush", HBRUSH, c_int)
_f(user32, "DrawIconEx", BOOL, HDC, c_int, c_int, HICON, c_int, c_int, UINT, HBRUSH, UINT)
_f(user32, "LockWindowUpdate", BOOL, HWND)
_f(user32, "SetPropW", BOOL, HWND, LPCWSTR, HANDLE)
_f(user32, "GetPropW", HANDLE, HWND, LPCWSTR)
_f(user32, "RemovePropW", HANDLE, HWND, LPCWSTR)
_f(user32, "ToUnicode", c_int, UINT, UINT, c_void_p, c_void_p, c_int, UINT)
_f(user32, "MapVirtualKeyW", UINT, UINT, UINT)
_f(user32, "GetKeyboardState", BOOL, c_void_p)
_f(user32, "FlashWindow", BOOL, HWND, BOOL)
_f(user32, "SetProcessDpiAwarenessContext", BOOL, c_void_p)
_f(user32, "GetDpiForWindow", UINT, HWND)
_f(user32, "GetDpiForSystem", UINT)
_f(user32, "GetSystemMetricsForDpi", c_int, c_int, UINT)
_f(user32, "SystemParametersInfoForDpi", BOOL, UINT, UINT, c_void_p, UINT, UINT)
_f(user32, "AdjustWindowRectExForDpi", BOOL, c_void_p, DWORD, BOOL, DWORD, UINT)

# ---- gdi32 ----
_f(gdi32, "SelectObject", HGDIOBJ, HDC, HGDIOBJ)
_f(gdi32, "DeleteObject", BOOL, HGDIOBJ)
_f(gdi32, "GetStockObject", HGDIOBJ, c_int)
_f(gdi32, "GetObjectW", c_int, HGDIOBJ, c_int, c_void_p)
_f(gdi32, "CreateSolidBrush", HBRUSH, COLORREF)
_f(gdi32, "CreatePen", HPEN, c_int, c_int, COLORREF)
_f(gdi32, "CreateFontIndirectW", HFONT, c_void_p)
_f(gdi32, "CreateFontW", HFONT, c_int, c_int, c_int, c_int, c_int, DWORD, DWORD, DWORD, DWORD, DWORD, DWORD,
   DWORD, DWORD, LPCWSTR)
_f(gdi32, "SetTextColor", COLORREF, HDC, COLORREF)
_f(gdi32, "SetBkColor", COLORREF, HDC, COLORREF)
_f(gdi32, "SetBkMode", c_int, HDC, c_int)
_f(gdi32, "SetDCBrushColor", COLORREF, HDC, COLORREF)
_f(gdi32, "SetDCPenColor", COLORREF, HDC, COLORREF)
_f(gdi32, "TextOutW", BOOL, HDC, c_int, c_int, LPCWSTR, c_int)
_f(gdi32, "ExtTextOutW", BOOL, HDC, c_int, c_int, UINT, c_void_p, LPCWSTR, UINT, c_void_p)
_f(gdi32, "GetTextExtentPoint32W", BOOL, HDC, LPCWSTR, c_int, c_void_p)
_f(gdi32, "GetTextExtentExPointW", BOOL, HDC, LPCWSTR, c_int, c_int, c_void_p, c_void_p, c_void_p)
_f(gdi32, "GetTextMetricsW", BOOL, HDC, c_void_p)
_f(gdi32, "GetDeviceCaps", c_int, HDC, c_int)
_f(gdi32, "CreateCompatibleDC", HDC, HDC)
_f(gdi32, "CreateCompatibleBitmap", HBITMAP, HDC, c_int, c_int)
_f(gdi32, "DeleteDC", BOOL, HDC)
_f(gdi32, "BitBlt", BOOL, HDC, c_int, c_int, c_int, c_int, HDC, c_int, c_int, DWORD)
_f(gdi32, "Rectangle", BOOL, HDC, c_int, c_int, c_int, c_int)
_f(gdi32, "RoundRect", BOOL, HDC, c_int, c_int, c_int, c_int, c_int, c_int)
_f(gdi32, "Ellipse", BOOL, HDC, c_int, c_int, c_int, c_int)
_f(gdi32, "MoveToEx", BOOL, HDC, c_int, c_int, c_void_p)
_f(gdi32, "LineTo", BOOL, HDC, c_int, c_int)
_f(gdi32, "Polygon", BOOL, HDC, c_void_p, c_int)
_f(gdi32, "Polyline", BOOL, HDC, c_void_p, c_int)
_f(gdi32, "IntersectClipRect", c_int, HDC, c_int, c_int, c_int, c_int)
_f(gdi32, "ExcludeClipRect", c_int, HDC, c_int, c_int, c_int, c_int)
_f(gdi32, "SelectClipRgn", c_int, HDC, c_void_p)
_f(gdi32, "SaveDC", c_int, HDC)
_f(gdi32, "RestoreDC", BOOL, HDC, c_int)
_f(gdi32, "CreateRectRgn", c_void_p, c_int, c_int, c_int, c_int)
_f(gdi32, "PatBlt", BOOL, HDC, c_int, c_int, c_int, c_int, DWORD)

# ---- kernel32 ----
_f(kernel32, "GetModuleHandleW", HMODULE, LPCWSTR)
_f(kernel32, "GetModuleFileNameW", DWORD, HMODULE, c_void_p, DWORD)
_f(kernel32, "GetProcAddress", c_void_p, HMODULE, c_char_p)
_f(kernel32, "LoadLibraryW", HMODULE, LPCWSTR)
_f(kernel32, "CloseHandle", BOOL, HANDLE)
_f(kernel32, "CreateFileW", HANDLE, LPCWSTR, DWORD, DWORD, c_void_p, DWORD, DWORD, HANDLE)
_f(kernel32, "ReadFile", BOOL, HANDLE, c_void_p, DWORD, c_void_p, c_void_p)
_f(kernel32, "WriteFile", BOOL, HANDLE, c_void_p, DWORD, c_void_p, c_void_p)
_f(kernel32, "FlushFileBuffers", BOOL, HANDLE)
_f(kernel32, "SetEndOfFile", BOOL, HANDLE)
_f(kernel32, "SetFilePointerEx", BOOL, HANDLE, c_int64, c_void_p, DWORD)
_f(kernel32, "GetFileSizeEx", BOOL, HANDLE, c_void_p)
_f(kernel32, "GetFileTime", BOOL, HANDLE, c_void_p, c_void_p, c_void_p)
_f(kernel32, "CompareFileTime", LONG, c_void_p, c_void_p)
_f(kernel32, "GetFileAttributesW", DWORD, LPCWSTR)
_f(kernel32, "SetFileAttributesW", BOOL, LPCWSTR, DWORD)
_f(kernel32, "GetFileAttributesExW", BOOL, LPCWSTR, c_int, c_void_p)
_f(kernel32, "DeleteFileW", BOOL, LPCWSTR)
_f(kernel32, "MoveFileExW", BOOL, LPCWSTR, LPCWSTR, DWORD)
_f(kernel32, "CopyFileW", BOOL, LPCWSTR, LPCWSTR, BOOL)
_f(kernel32, "CreateDirectoryW", BOOL, LPCWSTR, c_void_p)
_f(kernel32, "RemoveDirectoryW", BOOL, LPCWSTR)
_f(kernel32, "FindFirstFileExW", HANDLE, LPCWSTR, c_int, c_void_p, c_int, c_void_p, DWORD)
_f(kernel32, "FindFirstFileW", HANDLE, LPCWSTR, c_void_p)
_f(kernel32, "FindNextFileW", BOOL, HANDLE, c_void_p)
_f(kernel32, "FindClose", BOOL, HANDLE)
_f(kernel32, "GetFullPathNameW", DWORD, LPCWSTR, DWORD, c_void_p, c_void_p)
_f(kernel32, "GetTempPathW", DWORD, DWORD, c_void_p)
_f(kernel32, "GetTempFileNameW", UINT, LPCWSTR, LPCWSTR, UINT, c_void_p)
_f(kernel32, "SearchPathW", DWORD, LPCWSTR, LPCWSTR, LPCWSTR, DWORD, c_void_p, c_void_p)
_f(kernel32, "GetSystemDirectoryW", UINT, c_void_p, UINT)
_f(kernel32, "GetWindowsDirectoryW", UINT, c_void_p, UINT)
_f(kernel32, "GetCurrentDirectoryW", DWORD, DWORD, c_void_p)
_f(kernel32, "SetCurrentDirectoryW", BOOL, LPCWSTR)
_f(kernel32, "CreateFileMappingW", HANDLE, HANDLE, c_void_p, DWORD, DWORD, DWORD, LPCWSTR)
_f(kernel32, "MapViewOfFile", c_void_p, HANDLE, DWORD, DWORD, DWORD, SIZE_T)
_f(kernel32, "UnmapViewOfFile", BOOL, c_void_p)
_f(kernel32, "CreatePipe", BOOL, c_void_p, c_void_p, c_void_p, DWORD)
_f(kernel32, "PeekNamedPipe", BOOL, HANDLE, c_void_p, DWORD, c_void_p, c_void_p, c_void_p)
_f(kernel32, "SetHandleInformation", BOOL, HANDLE, DWORD, DWORD)
_f(kernel32, "CreateProcessW", BOOL, LPCWSTR, c_void_p, c_void_p, c_void_p, BOOL, DWORD, c_void_p, LPCWSTR,
   c_void_p, c_void_p)
_f(kernel32, "TerminateProcess", BOOL, HANDLE, UINT)
_f(kernel32, "GetExitCodeProcess", BOOL, HANDLE, c_void_p)
_f(kernel32, "WaitForSingleObject", DWORD, HANDLE, DWORD)
_f(kernel32, "WaitForMultipleObjects", DWORD, DWORD, c_void_p, BOOL, DWORD)
_f(kernel32, "CreateEventW", HANDLE, c_void_p, BOOL, BOOL, LPCWSTR)
_f(kernel32, "SetEvent", BOOL, HANDLE)
_f(kernel32, "ResetEvent", BOOL, HANDLE)
_f(kernel32, "GetCurrentProcessId", DWORD)
_f(kernel32, "GetCurrentThreadId", DWORD)
_f(kernel32, "GetEnvironmentVariableW", DWORD, LPCWSTR, c_void_p, DWORD)
_f(kernel32, "SetEnvironmentVariableW", BOOL, LPCWSTR, LPCWSTR)
_f(kernel32, "GetEnvironmentStringsW", c_void_p)
_f(kernel32, "FreeEnvironmentStringsW", BOOL, c_void_p)
_f(kernel32, "ExpandEnvironmentStringsW", DWORD, LPCWSTR, c_void_p, DWORD)
_f(kernel32, "GetCommandLineW", c_void_p)
_f(kernel32, "FormatMessageW", DWORD, DWORD, c_void_p, DWORD, DWORD, c_void_p, DWORD, c_void_p)
_f(kernel32, "LocalFree", c_void_p, c_void_p)
_f(kernel32, "GlobalAlloc", HGLOBAL, UINT, SIZE_T)
_f(kernel32, "GlobalFree", HGLOBAL, HGLOBAL)
_f(kernel32, "GlobalLock", c_void_p, HGLOBAL)
_f(kernel32, "GlobalUnlock", BOOL, HGLOBAL)
_f(kernel32, "GlobalSize", SIZE_T, HGLOBAL)
_f(kernel32, "MultiByteToWideChar", c_int, UINT, DWORD, c_void_p, c_int, c_void_p, c_int)
_f(kernel32, "WideCharToMultiByte", c_int, UINT, DWORD, c_void_p, c_int, c_void_p, c_int, c_void_p, c_void_p)
_f(kernel32, "CompareStringOrdinal", c_int, LPCWSTR, c_int, LPCWSTR, c_int, BOOL)
_f(kernel32, "GetTickCount64", c_uint64)
_f(kernel32, "GetTickCount", DWORD)
_f(kernel32, "Sleep", None, DWORD)
_f(kernel32, "InitializeProcThreadAttributeList", BOOL, c_void_p, DWORD, DWORD, c_void_p)
_f(kernel32, "UpdateProcThreadAttribute", BOOL, c_void_p, DWORD, SIZE_T, c_void_p, SIZE_T, c_void_p, c_void_p)
_f(kernel32, "DeleteProcThreadAttributeList", None, c_void_p)
_f(kernel32, "CreateActCtxW", HANDLE, c_void_p)
_f(kernel32, "ActivateActCtx", BOOL, HANDLE, c_void_p)
_f(kernel32, "DeactivateActCtx", BOOL, DWORD, ULONG_PTR)
_f(kernel32, "GetConsoleWindow", HWND)
_f(kernel32, "FreeConsole", BOOL)
_f(kernel32, "GetUserDefaultUILanguage", WORD)

# ---- comctl32 ----
_f(comctl32, "InitCommonControlsEx", BOOL, c_void_p)
_f(comctl32, "SetWindowSubclass", BOOL, HWND, SUBCLASSPROC, UINT_PTR, DWORD_PTR)
_f(comctl32, "RemoveWindowSubclass", BOOL, HWND, SUBCLASSPROC, UINT_PTR)
_f(comctl32, "DefSubclassProc", LRESULT, HWND, UINT, WPARAM, LPARAM)
_f(comctl32, "ImageList_Draw", BOOL, c_void_p, c_int, HDC, c_int, c_int, UINT)
_f(comctl32, "ImageList_GetIconSize", BOOL, c_void_p, c_void_p, c_void_p)

# ---- shell32 ----
_f(shell32, "ShellExecuteW", HINSTANCE, HWND, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR, c_int)
_f(shell32, "CommandLineToArgvW", c_void_p, c_void_p, c_void_p)
_f(shell32, "DragAcceptFiles", None, HWND, BOOL)
_f(shell32, "DragQueryFileW", UINT, HANDLE, UINT, c_void_p, UINT)
_f(shell32, "DragFinish", None, HANDLE)
_f(shell32, "SHFileOperationW", c_int, c_void_p)
_f(shell32, "SHGetFileInfoW", DWORD_PTR, LPCWSTR, DWORD, c_void_p, UINT, UINT)
_f(shell32, "SHCreateDirectoryExW", c_int, HWND, LPCWSTR, c_void_p)
_f(shell32, "SHGetKnownFolderPath", HRESULT, c_void_p, DWORD, HANDLE, c_void_p)
_f(shell32, "SHCreateItemFromParsingName", HRESULT, LPCWSTR, c_void_p, c_void_p, c_void_p)

# ---- comdlg32 ----
_f(comdlg32, "GetOpenFileNameW", BOOL, c_void_p)
_f(comdlg32, "GetSaveFileNameW", BOOL, c_void_p)
_f(comdlg32, "ChooseFontW", BOOL, c_void_p)
_f(comdlg32, "CommDlgExtendedError", DWORD)

# ---- ole32 ----
_f(ole32, "CoInitializeEx", HRESULT, c_void_p, DWORD)
_f(ole32, "CoUninitialize", None)
_f(ole32, "CoCreateInstance", HRESULT, c_void_p, c_void_p, DWORD, c_void_p, c_void_p)
_f(ole32, "CoTaskMemFree", None, c_void_p)

# ---- dwmapi / uxtheme / imm32 / shlwapi ----
_f(dwmapi, "DwmSetWindowAttribute", HRESULT, HWND, DWORD, c_void_p, DWORD)
_f(uxtheme, "SetWindowTheme", HRESULT, HWND, LPCWSTR, LPCWSTR)
_f(imm32, "ImmGetContext", HIMC, HWND)
_f(imm32, "ImmReleaseContext", BOOL, HWND, HIMC)
_f(imm32, "ImmSetCompositionWindow", BOOL, HIMC, c_void_p)
_f(imm32, "ImmSetCompositionFontW", BOOL, HIMC, c_void_p)
_f(imm32, "ImmGetCompositionStringW", LONG, HIMC, DWORD, c_void_p, DWORD)
_f(shlwapi, "StrCmpLogicalW", c_int, LPCWSTR, LPCWSTR)

# ---- winhttp ----
_f(winhttp, "WinHttpOpen", HANDLE, LPCWSTR, DWORD, LPCWSTR, LPCWSTR, DWORD)
_f(winhttp, "WinHttpConnect", HANDLE, HANDLE, LPCWSTR, WORD, DWORD)
_f(winhttp, "WinHttpOpenRequest", HANDLE, HANDLE, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR, c_void_p, DWORD)
_f(winhttp, "WinHttpSendRequest", BOOL, HANDLE, LPCWSTR, DWORD, c_void_p, DWORD, DWORD, DWORD_PTR)
_f(winhttp, "WinHttpReceiveResponse", BOOL, HANDLE, c_void_p)
_f(winhttp, "WinHttpQueryHeaders", BOOL, HANDLE, DWORD, LPCWSTR, c_void_p, c_void_p, c_void_p)
_f(winhttp, "WinHttpQueryDataAvailable", BOOL, HANDLE, c_void_p)
_f(winhttp, "WinHttpReadData", BOOL, HANDLE, c_void_p, DWORD, c_void_p)
_f(winhttp, "WinHttpCloseHandle", BOOL, HANDLE)
_f(winhttp, "WinHttpSetTimeouts", BOOL, HANDLE, c_int, c_int, c_int, c_int)

# ---- ConPTY (Windows 10 1809+); resolved lazily since older systems lack it ----
HPCON = c_void_p


def LoadConPty():
    """Returns (CreatePseudoConsole, ResizePseudoConsole, ClosePseudoConsole) or None."""
    try:
        c = kernel32.CreatePseudoConsole
        r = kernel32.ResizePseudoConsole
        cl = kernel32.ClosePseudoConsole
    except AttributeError:
        return None
    c.restype = HRESULT
    c.argtypes = [COORD, HANDLE, HANDLE, DWORD, c_void_p]
    r.restype = HRESULT
    r.argtypes = [HPCON, COORD]
    cl.restype = None
    cl.argtypes = [HPCON]
    return c, r, cl


# ---- small helpers ----
def text_of(hwnd):
    """GetWindowTextW as a Python str."""
    n = GetWindowTextLengthW(hwnd)
    buf = create_unicode_buffer(n + 1)
    GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def client_rect(hwnd):
    r = RECT()
    GetClientRect(hwnd, byref(r))
    return r


def window_rect(hwnd):
    r = RECT()
    GetWindowRect(hwnd, byref(r))
    return r


def cursor_pos():
    p = POINT()
    GetCursorPos(byref(p))
    return p


def key_down(vk):
    return GetKeyState(vk) < 0


def wstr_len(s):
    """Length of a Python str in UTF-16 code units (what Win32 counts expect)."""
    n = len(s)
    for ch in s:
        if ord(ch) > 0xFFFF:
            n += 1
    return n


def draw_text(dc, s, rc, flags):
    """DrawTextW wrapper; rc is a RECT (modified in place with DT_CALCRECT)."""
    return DrawTextW(dc, s, wstr_len(s), byref(rc), flags)


def text_extent(dc, s):
    sz = SIZE()
    GetTextExtentPoint32W(dc, s, wstr_len(s), byref(sz))
    return sz


def ext_text_out(dc, x, y, opts, rc, s, dx=None):
    return ExtTextOutW(dc, x, y, opts, byref(rc) if rc is not None else None, s, wstr_len(s), dx)


def fill_solid(dc, rc, color):
    br = CreateSolidBrush(color)
    FillRect(dc, byref(rc), br)
    DeleteObject(br)


def frame_solid(dc, rc, color):
    br = CreateSolidBrush(color)
    FrameRect(dc, byref(rc), br)
    DeleteObject(br)


def lparam_struct(lp, cls):
    """Views the struct an LPARAM points at (no copy)."""
    return cls.from_address(lp)


def utf16_units_to_index(s, units):
    """Converts a UTF-16 offset into a Python str index."""
    if units <= 0:
        return 0
    i = 0
    u = 0
    for i, ch in enumerate(s):
        if u >= units:
            return i
        u += 2 if ord(ch) > 0xFFFF else 1
    return len(s)
