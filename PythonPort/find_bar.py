# Find / replace bar shown above the editor (port of find_bar.cpp, FindBar:: in main_window.h).
import common as C
import win32 as W
from win32 import *  # noqa: F401,F403
import resource as R
import main_window as MW

# ---- Win32 bits win32.py lacks ----
TOOLTIPS_CLASSW = "tooltips_class32"
TTS_ALWAYSTIP = 0x01
TTS_NOPREFIX = 0x02
TTF_IDISHWND = 0x0001
TTF_SUBCLASS = 0x0010
TTM_ADDTOOLW = WM_USER + 50


class TOOLINFOW(Structure):
    _fields_ = [("cbSize", UINT), ("uFlags", UINT), ("hwnd", HWND), ("uId", UINT_PTR), ("rect", RECT),
                ("hinst", HINSTANCE), ("lpszText", LPWSTR), ("lParam", LPARAM), ("lpReserved", c_void_p)]


IDC_FIND = 3001
IDC_REPL = 3002
g_bar = g_find = g_repl = g_case = g_word = g_prev = g_next = g_close = g_replOne = g_replAll = None
g_replace = False
g_visible = False
g_matchCase = False
g_wholeWord = False
g_msg = ""
g_inputBrush = None
kTimerIncr = 1


def GetText(h):
    return text_of(h)


def RowH():
    return C.S(34)


def LayoutBar():
    rc = client_rect(g_bar)
    pad, h, bw = C.S(6), C.S(24), C.S(26)
    y = (RowH() - h) // 2
    ew = min(C.S(320), max(C.S(120), rc.right // 2))
    MoveWindow(g_find, pad, y, ew, h, True)
    x = pad + ew + C.S(4)
    MoveWindow(g_case, x, y, bw, h, True); x += bw + C.S(2)
    MoveWindow(g_word, x, y, bw, h, True); x += bw + C.S(8)
    MoveWindow(g_prev, x, y, bw, h, True); x += bw + C.S(2)
    MoveWindow(g_next, x, y, bw, h, True)
    MoveWindow(g_close, rc.right - pad - bw, y, bw, h, True)
    y2 = RowH() + y - C.S(4)
    MoveWindow(g_repl, pad, y2, ew, h, True)
    MoveWindow(g_replOne, pad + ew + C.S(4), y2, C.S(70), h, True)
    MoveWindow(g_replAll, pad + ew + C.S(4) + C.S(74), y2, C.S(86), h, True)
    show = SW_SHOW if g_replace else SW_HIDE
    ShowWindow(g_repl, show)
    ShowWindow(g_replOne, show)
    ShowWindow(g_replAll, show)
    InvalidateRect(g_bar, None, True)


def RunFind(forward, incremental):
    ed = MW.ActiveEditor()
    if not ed:
        return
    q = Query()
    if not q.needle:
        SetMessage("")
        return
    ed.Find(q, forward, incremental)


def EditSub(h, m, w, l, uid, ref):
    if m == WM_KEYDOWN:
        if w == VK_ESCAPE:
            SendMessageW(MW.M.hwnd, WM_COMMAND, R.ID_FIND_CLOSE, 0)
            return 0
        if w == VK_RETURN:
            if h == g_repl:
                if GetKeyState(VK_CONTROL) < 0:
                    SendMessageW(MW.M.hwnd, WM_COMMAND, R.ID_FIND_REPLACEALL, 0)
                else:
                    SendMessageW(MW.M.hwnd, WM_COMMAND, R.ID_FIND_REPLACEONE, 0)
            else:
                RunFind(GetKeyState(VK_SHIFT) >= 0, False)
            return 0
        if w == VK_TAB:
            SetFocus(g_repl if (h == g_find and g_replace) else g_find)
            return 0
        if w == ord('A') and GetKeyState(VK_CONTROL) < 0:
            SendMessageW(h, EM_SETSEL, 0, -1)
            return 0
    if m == WM_CHAR and w in (VK_RETURN, VK_ESCAPE, VK_TAB, 1):
        return 0
    return DefSubclassProc(h, m, w, l)


def BarProc(h, m, w, l):
    global g_matchCase, g_wholeWord
    if m == WM_SIZE:
        LayoutBar()
        return 0
    if m == WM_ERASEBKGND:
        rc = client_rect(h)
        fill_solid(w, rc, C.g_theme.panelBg)
        line = RECT(0, rc.bottom - 1, rc.right, rc.bottom)
        fill_solid(w, line, C.g_theme.border)
        return 1
    if m == WM_PAINT:
        ps = PAINTSTRUCT()
        dc = BeginPaint(h, byref(ps))
        nr = window_rect(g_next)
        MapWindowPoints(None, h, byref(nr), 2)
        cr = window_rect(g_close)
        MapWindowPoints(None, h, byref(cr), 2)
        tr = RECT(nr.right + C.S(10), 0, cr.left - C.S(6), RowH())
        SetBkMode(dc, TRANSPARENT)
        SetTextColor(dc, RGB(220, 80, 80) if g_msg == "No results" else C.g_theme.panelFgDim)
        o = SelectObject(dc, C.g_uiFont)
        draw_text(dc, g_msg, tr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS)
        SelectObject(dc, o)
        EndPaint(h, byref(ps))
        return 0
    if m == WM_CTLCOLOREDIT:
        SetTextColor(w, C.g_theme.panelFg)
        SetBkColor(w, C.g_theme.inputBg)
        return g_inputBrush or 0
    if m == WM_DRAWITEM:
        dis = DRAWITEMSTRUCT.from_address(l)
        toggled = (dis.hwndItem == g_case and g_matchCase) or (dis.hwndItem == g_word and g_wholeWord)
        C.DrawFlatButton(dis, False, toggled)
        return 1
    if m == WM_TIMER:
        if w == kTimerIncr:
            KillTimer(h, kTimerIncr)
            RunFind(True, True)
        return 0
    if m == WM_COMMAND:
        c = l or None
        if LOWORD(w) == IDC_FIND and HIWORD(w) == EN_CHANGE:
            SetTimer(h, kTimerIncr, 250, None)
            return 0
        if c == g_case:
            g_matchCase = not g_matchCase
            InvalidateRect(g_case, None, False)
            RunFind(True, True)
        elif c == g_word:
            g_wholeWord = not g_wholeWord
            InvalidateRect(g_word, None, False)
            RunFind(True, True)
        elif c == g_prev:
            RunFind(False, False)
        elif c == g_next:
            RunFind(True, False)
        elif c == g_close:
            SendMessageW(MW.M.hwnd, WM_COMMAND, R.ID_FIND_CLOSE, 0)
        elif c == g_replOne:
            SendMessageW(MW.M.hwnd, WM_COMMAND, R.ID_FIND_REPLACEONE, 0)
        elif c == g_replAll:
            SendMessageW(MW.M.hwnd, WM_COMMAND, R.ID_FIND_REPLACEALL, 0)
        return 0
    return DefWindowProcW(h, m, w, l)


def MakeEdit(id, cue):
    e = CreateWindowExW(0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL | WS_BORDER, 0, 0, 0, 0,
                        g_bar, id, C.g_hinst, None)
    SendMessageW(e, WM_SETFONT, C.g_uiFont or 0, False)
    buf = create_unicode_buffer(cue)
    SendMessageW(e, EM_SETCUEBANNER, True, addressof(buf))
    C.Subclass(e, EditSub, 1, 0)
    return e


def AddTip(tip, ctl, text):
    ti = TOOLINFOW()
    ti.cbSize = sizeof(ti)
    ti.uFlags = TTF_IDISHWND | TTF_SUBCLASS
    ti.hwnd = g_bar
    ti.uId = ctl
    ti.lpszText = text
    SendMessageW(tip, TTM_ADDTOOLW, 0, addressof(ti))


# ---- FindBar:: ----
def Create(parent):
    global g_inputBrush, g_bar, g_find, g_repl, g_case, g_word, g_prev, g_next, g_close, g_replOne, g_replAll
    C.RegisterWindowClass("MyFindBar", BarProc, cursor=IDC_ARROW)
    g_inputBrush = CreateSolidBrush(C.g_theme.inputBg)
    g_bar = CreateWindowExW(0, "MyFindBar", "", WS_CHILD | WS_CLIPCHILDREN, 0, 0, 0, 0, parent, None, C.g_hinst, None)
    g_find = MakeEdit(IDC_FIND, "Find")
    g_repl = MakeEdit(IDC_REPL, "Replace")
    g_case = C.MakeButton(g_bar, 0, "Aa")
    g_word = C.MakeButton(g_bar, 0, "ab|")
    g_prev = C.MakeButton(g_bar, 0, "")
    g_next = C.MakeButton(g_bar, 0, "")
    g_close = C.MakeButton(g_bar, 0, "")
    g_replOne = C.MakeButton(g_bar, 0, "Replace")
    g_replAll = C.MakeButton(g_bar, 0, "Replace All")
    tip = CreateWindowExW(WS_EX_TOPMOST, TOOLTIPS_CLASSW, None, WS_POPUP | TTS_ALWAYSTIP | TTS_NOPREFIX, 0, 0, 0, 0,
                          g_bar, None, C.g_hinst, None)
    AddTip(tip, g_case, "Match Case")
    AddTip(tip, g_word, "Match Whole Word")
    AddTip(tip, g_prev, "Previous Match (Shift+Enter / Shift+F3)")
    AddTip(tip, g_next, "Next Match (Enter / F3)")
    AddTip(tip, g_close, "Close (Escape)")
    AddTip(tip, g_replOne, "Replace (Enter)")
    AddTip(tip, g_replAll, "Replace All (Ctrl+Enter)")
    return g_bar


def Show(replace):
    global g_replace, g_visible
    g_replace = replace
    g_visible = True
    ed = MW.ActiveEditor()
    if ed:
        sel = ed.SelectionForFind()
        if sel:
            SetWindowTextW(g_find, C.Utf8ToWide(sel))
    ShowWindow(g_bar, SW_SHOW)
    MW.Layout()
    LayoutBar()
    SetFocus(g_repl if (replace and GetWindowTextLengthW(g_find)) else g_find)
    SendMessageW(g_find, EM_SETSEL, 0, -1)


def Hide():
    global g_visible
    if not g_visible:
        return
    g_visible = False
    ShowWindow(g_bar, SW_HIDE)
    MW.Layout()
    ed = MW.ActiveEditor()
    if ed:
        SetFocus(ed.hwnd)


def Visible():
    return g_visible


def Height():
    return (RowH() * 2 - C.S(6) if g_replace else RowH()) if g_visible else 0


def Query():
    import editor_view
    q = editor_view.FindQuery()
    q.needle = C.WideToUtf8(GetText(g_find))
    q.matchCase = g_matchCase
    q.wholeWord = g_wholeWord
    return q


def Replacement():
    return C.WideToUtf8(GetText(g_repl))


def SetMessage(m):
    global g_msg
    if m == g_msg:
        return
    g_msg = m
    if g_bar:
        InvalidateRect(g_bar, None, True)


def FocusFind():
    SetFocus(g_find)


def ThemeChanged():
    global g_inputBrush
    if g_inputBrush:
        DeleteObject(g_inputBrush)
    g_inputBrush = CreateSolidBrush(C.g_theme.inputBg)
    for e in (g_find, g_repl):
        SetWindowTheme(e, "DarkMode_CFD" if C.g_settings.dark else "Explorer", None)
    RedrawWindow(g_bar, None, None, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_FRAME)
