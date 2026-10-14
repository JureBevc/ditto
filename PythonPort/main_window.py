# Main frame: sidebar, tab strip, content area, bottom panel (output / terminal) and status bar.
# (port of main_window.h / main_window.cpp)
import common as C
import win32 as W
from win32 import *  # noqa: F401,F403
from resource import *  # noqa: F401,F403

import app
import git
import editor_view
import terminal as Term
import find_bar as FindBar
import explorer as Explorer
import scm_panel as Scm
import diff_view as DiffView
import log_view as LogView
import main_commands

WA_INACTIVE = 0

# Icons loaded by ditto.py from res\app.ico (there are no exe resources); used for the class and WM_SETICON.
g_appIcon = None
g_appIconSm = None


class Tab:
    Editor, Diff, Log = range(3)

    def __init__(self, kind=0, hwnd=None, ed=None, key=""):
        self.kind = kind
        self.hwnd = hwnd
        self.ed = ed
        self.key = key


class MainState:
    PanelOutput, PanelTerminal = range(2)

    class PanelItem:
        OutputTab, TerminalTab, Group, NewTerm, SplitTerm, KillTerm, ClosePanel = range(7)

        def __init__(self, rc, kind, group):
            self.rc = rc
            self.kind = kind
            self.group = group

    class StatusItem:
        def __init__(self, rc, cmd):
            self.rc = rc
            self.cmd = cmd

    def __init__(self):
        self.hwnd = None
        self.explorer = None
        self.scm = None
        self.findBar = None
        self.output = None
        self.menu = None
        self.accel = None
        self.symFont = None
        self.outputBrush = None

        self.tabs = []
        self.active = -1
        self.untitled = 1
        self.folder = ""
        self.status = git.GitStatus()
        self.gitPending = False
        self.gitQueued = False
        self.statusText = ""
        self.sidebarMode = 0  # 0 explorer, 1 source control

        # geometry (client coordinates)
        self.rcSideHeader = RECT()
        self.rcSideBody = RECT()
        self.rcSplitter = RECT()
        self.rcTabs = RECT()
        self.rcContent = RECT()
        self.rcOutputHeader = RECT()
        self.rcOutput = RECT()
        self.rcStatus = RECT()
        self.rcPanelSplit = RECT()  # drag handle on the top edge of the bottom panel
        self.panelTab = MainState.PanelOutput
        self.panelItems = []
        self.lastFocus = None  # restored when the window is re-activated
        self.tabRects = []  # parallel to tabs (empty rect = not visible)
        self.rcTabOverflow = RECT()
        self.tabFirst = 0
        self.hoverTab = -1
        self.hoverClose = False
        self.draggingSplit = False
        self.draggingPanel = False
        self.tracking = False
        self.statusItems = []
        self.welcomeLinks = []  # list of (RECT, str)


M = MainState()

kIcoClose = ""
kTimerStatus = 1


def ActiveEditor():
    if 0 <= M.active < len(M.tabs) and M.tabs[M.active].kind == Tab.Editor:
        return M.tabs[M.active].ed
    return None


def TabTitle(t):
    if t.kind == Tab.Editor:
        return t.ed.Title()
    if t.kind == Tab.Diff:
        return DiffView.Title(t.hwnd)
    return "History"


def TabModified(t):
    return t.kind == Tab.Editor and t.ed.buf.Modified()


# ---------------- fonts / theme ----------------

def _set_font_cb(h, lp):
    buf = create_unicode_buffer(64)
    GetClassNameW(h, buf, 64)
    cls = buf.value.lower()
    if cls in ("edit", "button", "systreeview32", "syslistview32"):
        SendMessageW(h, WM_SETFONT, C.g_uiFont or 0, True)
    return True


_setFontProc = C.keep(WNDENUMPROC(_set_font_cb))


def RecreateFonts():
    oldUi, oldBold, oldIcon, oldSym = C.g_uiFont, C.g_uiFontBold, C.g_iconFont, M.symFont
    ncm = NONCLIENTMETRICSW()
    ncm.cbSize = sizeof(ncm)
    SystemParametersInfoForDpi(SPI_GETNONCLIENTMETRICS, sizeof(ncm), byref(ncm), 0, C.g_dpi)
    lf = LOGFONTW.from_buffer_copy(ncm.lfMessageFont)
    C.g_uiFont = CreateFontIndirectW(byref(lf))
    lf.lfWeight = FW_BOLD
    C.g_uiFontBold = CreateFontIndirectW(byref(lf))
    li = LOGFONTW()
    li.lfHeight = -C.S(14)
    li.lfCharSet = DEFAULT_CHARSET
    li.lfQuality = CLEARTYPE_QUALITY
    li.lfFaceName = "Segoe MDL2 Assets"
    C.g_iconFont = CreateFontIndirectW(byref(li))
    lf = LOGFONTW.from_buffer_copy(ncm.lfMessageFont)
    lf.lfFaceName = "Segoe UI Symbol"
    M.symFont = CreateFontIndirectW(byref(lf))
    editor_view.CreateEditorFonts()
    if M.hwnd:
        EnumChildWindows(M.hwnd, _setFontProc, 0)
    if oldUi:
        DeleteObject(oldUi)
    if oldBold:
        DeleteObject(oldBold)
    if oldIcon:
        DeleteObject(oldIcon)
    if oldSym:
        DeleteObject(oldSym)


def _explorer_theme():
    return "DarkMode_Explorer" if C.g_settings.dark else "Explorer"


def ApplyThemeAll():
    C.ApplyTheme(C.g_settings.dark)
    dark = BOOL(1 if C.g_settings.dark else 0)
    DwmSetWindowAttribute(M.hwnd, 20, byref(dark), sizeof(dark))  # DWMWA_USE_IMMERSIVE_DARK_MODE
    if M.outputBrush:
        DeleteObject(M.outputBrush)
    M.outputBrush = CreateSolidBrush(C.g_theme.bg)
    SetWindowTheme(M.output, _explorer_theme(), None)
    Explorer.ThemeChanged()
    Scm.ThemeChanged()
    FindBar.ThemeChanged()
    Term.ThemeChanged()
    for t in M.tabs:
        SetWindowTheme(t.hwnd, _explorer_theme(), None)
        if t.kind == Tab.Log:
            LogView.ThemeChanged(t.hwnd)
    DrawMenuBar(M.hwnd)
    RedrawWindow(M.hwnd, None, None, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_FRAME)


# ---------------- layout ----------------

def ComputeTabRects():
    S = C.S
    M.tabRects = [RECT() for _ in M.tabs]
    M.rcTabOverflow = RECT()
    if not M.tabs:
        return
    dc = GetDC(M.hwnd)
    o = SelectObject(dc, C.g_uiFont)
    w = []
    for tab in M.tabs:
        t = TabTitle(tab)
        sz = text_extent(dc, t)
        w.append(max(S(80), min(S(260), sz.cx + S(48))))
    SelectObject(dc, o)
    ReleaseDC(M.hwnd, dc)
    avail = M.rcTabs.right - M.rcTabs.left - S(30)
    total = sum(w)
    overflow = total > avail + S(30)
    if not overflow:
        M.tabFirst = 0
    if M.active >= 0:
        if M.active < M.tabFirst:
            M.tabFirst = M.active
        while True:
            s = sum(w[M.tabFirst:M.active + 1])
            if s <= avail or M.tabFirst >= M.active:
                break
            M.tabFirst += 1
    M.tabFirst = max(0, min(M.tabFirst, len(M.tabs) - 1))
    x = M.rcTabs.left
    limit = M.rcTabs.right - S(30) if overflow else M.rcTabs.right
    for i in range(M.tabFirst, len(M.tabs)):
        if x + w[i] > limit and i != M.tabFirst:
            break
        M.tabRects[i] = RECT(x, M.rcTabs.top, x + w[i], M.rcTabs.bottom)
        x += w[i]
    if overflow:
        M.rcTabOverflow = RECT(M.rcTabs.right - S(30), M.rcTabs.top, M.rcTabs.right, M.rcTabs.bottom)


def Layout():
    if not M.hwnd:
        return
    S = C.S
    st = C.g_settings
    rc = client_rect(M.hwnd)
    sh = S(24)
    M.rcStatus = RECT(0, rc.bottom - sh, rc.right, rc.bottom)
    bottom = rc.bottom - sh
    sideW = min(S(st.sidebarWidth), rc.right - S(200)) if st.showSidebar else 0
    if sideW < S(120) and st.showSidebar:
        sideW = min(S(120), int(rc.right / 2))
    if st.showSidebar:
        M.rcSideHeader = RECT(0, 0, sideW, S(34))
        M.rcSideBody = RECT(0, S(34), sideW, bottom)
        M.rcSplitter = RECT(sideW, 0, sideW + S(4), bottom)
        show = M.explorer if M.sidebarMode == 0 else M.scm
        hide = M.scm if M.sidebarMode == 0 else M.explorer
        MoveWindow(show, 0, M.rcSideBody.top, sideW, bottom - M.rcSideBody.top, True)
        ShowWindow(show, SW_SHOW)
        ShowWindow(hide, SW_HIDE)
    else:
        M.rcSideHeader = RECT()
        M.rcSideBody = RECT()
        M.rcSplitter = RECT()
        ShowWindow(M.explorer, SW_HIDE)
        ShowWindow(M.scm, SW_HIDE)
    x0 = M.rcSplitter.right if st.showSidebar else 0
    M.rcTabs = RECT(x0, 0, rc.right, S(34))
    y = M.rcTabs.bottom
    fh = FindBar.Height()
    if fh:
        MoveWindow(M.findBar, x0, y, rc.right - x0, fh, True)
        y += fh
    outH = 0
    if st.showOutput:
        avail = bottom - y
        outH = min(avail, max(S(60), min(S(st.panelHeight), avail - S(80))))
    if outH:
        M.rcOutputHeader = RECT(x0, bottom - outH, rc.right, bottom - outH + S(28))
        M.rcOutput = RECT(x0, M.rcOutputHeader.bottom, rc.right, bottom)
        M.rcPanelSplit = RECT(x0, M.rcOutputHeader.top, rc.right, M.rcOutputHeader.top + S(4))
        term = M.panelTab == MainState.PanelTerminal
        if not term:
            MoveWindow(M.output, x0 + S(8), M.rcOutput.top, rc.right - x0 - S(8), bottom - M.rcOutput.top, True)
        ShowWindow(M.output, SW_HIDE if term else SW_SHOW)
        Term.Layout(M.rcOutput, term)
    else:
        M.rcOutputHeader = RECT()
        M.rcOutput = RECT()
        M.rcPanelSplit = RECT()
        ShowWindow(M.output, SW_HIDE)
        Term.Layout(RECT(), False)
    M.rcContent = RECT(x0, y, rc.right, bottom - outH)
    if M.active >= 0:
        h = M.tabs[M.active].hwnd
        MoveWindow(h, M.rcContent.left, M.rcContent.top, M.rcContent.right - M.rcContent.left,
                   M.rcContent.bottom - M.rcContent.top, True)
    ComputeTabRects()
    InvalidateRect(M.hwnd, None, False)


def InvalidateChrome():
    ComputeTabRects()
    InvalidateRect(M.hwnd, byref(M.rcTabs), False)
    InvalidateRect(M.hwnd, byref(M.rcStatus), False)
    InvalidateRect(M.hwnd, byref(M.rcSideHeader), False)


def UpdateTitle():
    t = ""
    if M.active >= 0:
        tab = M.tabs[M.active]
        t = TabTitle(tab)
        if TabModified(tab):
            t = "● " + t
        t += " - "
    if M.folder:
        t += C.FileNameOf(M.folder) + " - "
    t += "ditto"
    SetWindowTextW(M.hwnd, t)


def ActivateTab(i):
    if i < 0 or i >= len(M.tabs):
        return
    if M.active >= 0 and M.active != i and M.active < len(M.tabs):
        ShowWindow(M.tabs[M.active].hwnd, SW_HIDE)
    M.active = i
    h = M.tabs[i].hwnd
    MoveWindow(h, M.rcContent.left, M.rcContent.top, M.rcContent.right - M.rcContent.left,
               M.rcContent.bottom - M.rcContent.top, True)
    ShowWindow(h, SW_SHOW)
    SetFocus(h)
    ed = ActiveEditor()
    FindBar.SetMessage(ed.findMessage if ed else "")
    UpdateTitle()
    InvalidateChrome()
    InvalidateRect(M.hwnd, None, False)


def AddTab(t):
    SetWindowTheme(t.hwnd, _explorer_theme(), None)
    M.tabs.append(t)
    ActivateTab(len(M.tabs) - 1)


def CloseTab(i):
    """False if cancelled."""
    if i < 0 or i >= len(M.tabs):
        return True
    t = M.tabs[i]
    if TabModified(t):
        ActivateTab(i)
        r = C.MsgBox(M.hwnd, "Do you want to save the changes you made to " + t.ed.Title() +
                     "?\n\nYour changes will be lost if you don't save them.", MB_YESNOCANCEL | MB_ICONWARNING)
        if r == IDCANCEL:
            return False
        if r == IDYES and not main_commands.SaveTab(i, False):
            return False
    del M.tabs[i]
    DestroyWindow(t.hwnd)
    if not M.tabs:
        M.active = -1
    elif i == M.active:
        M.active = -1
        ActivateTab(min(i, len(M.tabs) - 1))
    elif i < M.active:
        M.active -= 1
    if not M.tabs:
        FindBar.SetMessage("")
    UpdateTitle()
    Layout()
    return True


# ---------------- painting ----------------

def FillC(dc, r, c):
    SetBkColor(dc, c)
    ExtTextOutW(dc, 0, 0, ETO_OPAQUE, byref(r), None, 0, None)


def Text(dc, s, r, c, fmt, f=None):
    SelectObject(dc, f if f else C.g_uiFont)
    SetTextColor(dc, c)
    rr = r.copy()
    draw_text(dc, s, rr, fmt | DT_NOPREFIX)


def FormatSize(n):
    if n >= (1 << 30):
        return "%.2f GB" % (n / 1073741824.0)
    if n >= (1 << 20):
        return "%.1f MB" % (n / 1048576.0)
    if n >= 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%d B" % n


def PaintStatus(dc):
    import text_buffer
    S = C.S
    t = C.g_theme
    r = M.rcStatus
    FillC(dc, r, (RGB(104, 33, 122) if C.g_settings.dark else RGB(104, 33, 122)) if not M.folder else t.statusBg)
    M.statusItems = []
    pad = S(10)
    state = {"x": r.left + S(6), "rx": r.right - S(8)}

    def addLeft(s, cmd, f=None):
        if not s:
            return
        SelectObject(dc, f if f else C.g_uiFont)
        sz = text_extent(dc, s)
        x = state["x"]
        ir = RECT(x, r.top, x + sz.cx + pad, r.bottom)
        Text(dc, s, ir, t.statusFg, DT_CENTER | DT_VCENTER | DT_SINGLELINE, f)
        if cmd:
            M.statusItems.append(MainState.StatusItem(ir, cmd))
        state["x"] = ir.right

    st = M.status
    if st.isRepo:
        dirty = bool(st.files)
        addLeft("⎇ " + st.branch + ("*" if dirty else ""), ID_GIT_CHECKOUT, M.symFont)
        if st.upstream:
            sync = "↓" + str(st.behind) + " ↑" + str(st.ahead)
        elif st.remotes:
            sync = "↑ Publish branch"
        else:
            sync = "☁ Publish to GitHub"
        addLeft(sync, ID_GIT_PUBLISH if not st.remotes else ID_GIT_SYNC, M.symFont)
    if git.BgPending() > 0:
        addLeft("⌛ git...", 0, M.symFont)
    if M.statusText:
        addLeft(M.statusText, 0)

    def addRight(s, cmd):
        if not s:
            return
        SelectObject(dc, C.g_uiFont)
        sz = text_extent(dc, s)
        rx = state["rx"]
        ir = RECT(rx - sz.cx - pad, r.top, rx, r.bottom)
        if ir.left < state["x"]:
            return
        Text(dc, s, ir, t.statusFg, DT_CENTER | DT_VCENTER | DT_SINGLELINE)
        if cmd:
            M.statusItems.append(MainState.StatusItem(ir, cmd))
        state["rx"] = ir.left

    ed = ActiveEditor()
    if ed:
        b = ed.buf
        addRight("Markdown" if ed.markdown else "Plain Text", 0)
        addRight("CRLF" if b.crlf else "LF", 0)
        enc = "UTF-8"
        E = text_buffer.Encoding
        if b.encoding == E.Utf8Bom:
            enc = "UTF-8 with BOM"
        elif b.encoding == E.Utf16LE:
            enc = "UTF-16 LE"
        elif b.encoding == E.Utf16BE:
            enc = "UTF-16 BE"
        addRight(enc, 0)
        if ed.SaveProgress() >= 0:
            addRight("Saving " + str(int(ed.SaveProgress() * 100)) + "%", 0)
        elif not b.Ready():
            addRight("Indexing " + str(int(b.Progress() * 100)) + "%", 0)
        else:
            addRight(FormatSize(b.Length()), 0)
        pos = "Ln " + str(ed.CaretLine() + 1) + ", Col " + str(ed.CaretCol() + 1)
        sel = ed.SelectionLength()
        if sel:
            pos += " (" + str(sel) + " selected)"
        addRight(pos, ID_EDIT_GOTO)


def PaintTabs(dc):
    S = C.S
    t = C.g_theme
    stripBg = RGB(37, 37, 38) if C.g_settings.dark else RGB(236, 236, 236)
    FillC(dc, M.rcTabs, stripBg)
    for i, tab in enumerate(M.tabs):
        r = M.tabRects[i]
        if r.right <= r.left:
            continue
        active = i == M.active
        hover = i == M.hoverTab
        FillC(dc, r, t.bg if active else (t.hoverBg if hover else stripBg))
        if active:
            FillC(dc, RECT(r.left, r.top, r.right, r.top + S(2)), t.accent)
        FillC(dc, RECT(r.right - 1, r.top, r.right, r.bottom), t.border)
        tr = RECT(r.left + S(12), r.top, r.right - S(30), r.bottom)
        Text(dc, TabTitle(tab), tr, t.fg if active else t.panelFgDim,
             DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS)
        cr = RECT(r.right - S(28), r.top + S(7), r.right - S(8), r.bottom - S(7))
        closeHover = hover and M.hoverClose
        if closeHover:
            FillC(dc, cr, RGB(70, 70, 74) if C.g_settings.dark else RGB(210, 210, 210))
        if TabModified(tab) and not closeHover:
            Text(dc, "●", cr, t.fg if active else t.panelFgDim, DT_CENTER | DT_VCENTER | DT_SINGLELINE)
        elif active or hover:
            Text(dc, kIcoClose, cr, t.fg if active else t.panelFgDim, DT_CENTER | DT_VCENTER | DT_SINGLELINE,
                 C.g_iconFont)
    if M.rcTabOverflow.right > M.rcTabOverflow.left:
        Text(dc, "", M.rcTabOverflow, t.panelFg, DT_CENTER | DT_VCENTER | DT_SINGLELINE, C.g_iconFont)
    line = RECT(M.rcTabs.left, M.rcTabs.bottom - 1, M.rcTabs.right, M.rcTabs.bottom)
    if not M.tabs:
        FillC(dc, line, t.border)


def PaintSideHeader(dc):
    S = C.S
    t = C.g_theme
    r = M.rcSideHeader
    if r.right <= r.left:
        return
    FillC(dc, r, t.panelBg)
    half = (r.right - r.left) // 2
    a = RECT(r.left, r.top, r.left + half, r.bottom)
    b = RECT(r.left + half, r.top, r.right, r.bottom)
    changes = len(M.status.files)
    scmLabel = "SOURCE CONTROL"
    if changes:
        scmLabel += " (" + str(changes) + ")"
    Text(dc, "EXPLORER", a, t.panelFg if M.sidebarMode == 0 else t.panelFgDim,
         DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS,
         C.g_uiFontBold if M.sidebarMode == 0 else C.g_uiFont)
    Text(dc, scmLabel, b, t.panelFg if M.sidebarMode == 1 else t.panelFgDim,
         DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS,
         C.g_uiFontBold if M.sidebarMode == 1 else C.g_uiFont)
    u = a if M.sidebarMode == 0 else b
    FillC(dc, RECT(u.left + S(12), u.bottom - S(3), u.right - S(12), u.bottom - S(1)), t.accent)


_welcomeLines = [
    "Ctrl+N\tNew file", "Ctrl+O\tOpen file", "Ctrl+Shift+O\tOpen folder",
    "Ctrl+R\tOpen recent folder",
    "Ctrl+Shift+E\tExplorer", "Ctrl+Shift+G\tSource control", "Ctrl+F / Ctrl+H\tFind / replace",
    "Ctrl+G\tGo to line", "Alt+Z\tToggle word wrap", "Ctrl+`\tToggle terminal",
]


def PaintWelcome(dc):
    S = C.S
    t = C.g_theme
    FillC(dc, M.rcContent, t.bg)
    M.welcomeLinks = []
    x = M.rcContent.left + S(48)
    y = M.rcContent.top + S(40)
    r = RECT(x, y, M.rcContent.right - S(16), y + S(40))
    lf = LOGFONTW()
    GetObjectW(C.g_uiFont, sizeof(lf), byref(lf))
    lf.lfHeight = lf.lfHeight * 2
    lf.lfWeight = FW_LIGHT
    big = CreateFontIndirectW(byref(lf))
    Text(dc, "ditto", r, t.fg, DT_LEFT | DT_TOP | DT_SINGLELINE, big)
    SelectObject(dc, C.g_uiFont)
    DeleteObject(big)
    y += S(56)
    for s in _welcomeLines:
        tab = s.find("\t")
        k = RECT(x, y, x + S(130), y + S(22))
        v = RECT(x + S(140), y, M.rcContent.right - S(16), y + S(22))
        Text(dc, s[:tab], k, t.accent, DT_LEFT | DT_VCENTER | DT_SINGLELINE)
        Text(dc, s[tab + 1:], v, t.panelFgDim, DT_LEFT | DT_VCENTER | DT_SINGLELINE)
        y += S(22)
    if C.g_settings.recentFolders:
        y += S(18)
        h = RECT(x, y, M.rcContent.right - S(16), y + S(24))
        Text(dc, "Recent folders", h, t.fg, DT_LEFT | DT_VCENTER | DT_SINGLELINE, C.g_uiFontBold)
        y += S(26)
        for f in C.g_settings.recentFolders:
            lr = RECT(x, y, M.rcContent.right - S(16), y + S(22))
            Text(dc, f, lr, t.link, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_PATH_ELLIPSIS)
            M.welcomeLinks.append((lr, f))
            y += S(22)


# Bottom panel header: OUTPUT / TERMINAL tabs, terminal groups, and the terminal actions on the right.
def PaintPanelHeader(dc):
    PI = MainState.PanelItem
    S = C.S
    t = C.g_theme
    M.panelItems = []
    h = M.rcOutputHeader
    if h.bottom <= h.top:
        return
    o = RECT(h.left, h.top, M.rcOutput.right, M.rcOutput.bottom)
    FillC(dc, o, t.bg)
    FillC(dc, RECT(o.left, o.top, o.right, o.top + 1), t.border)
    term = M.panelTab == MainState.PanelTerminal
    state = {"x": h.left + S(8), "rx": h.right - S(4)}

    def label(s, active, kind):
        SelectObject(dc, C.g_uiFont)
        sz = text_extent(dc, s)
        x = state["x"]
        r = RECT(x, h.top, x + sz.cx + S(16), h.bottom)
        Text(dc, s, r, t.panelFg if active else t.panelFgDim, DT_CENTER | DT_VCENTER | DT_SINGLELINE)
        if active:
            FillC(dc, RECT(r.left + S(6), r.bottom - S(3), r.right - S(6), r.bottom - S(1)), t.accent)
        M.panelItems.append(PI(r, kind, -1))
        state["x"] = r.right

    label("OUTPUT", not term, PI.OutputTab)
    label("TERMINAL", term, PI.TerminalTab)

    def icon(glyph, kind):
        rx = state["rx"]
        r = RECT(rx - S(28), h.top, rx, h.bottom)
        Text(dc, glyph, r, t.panelFg, DT_CENTER | DT_VCENTER | DT_SINGLELINE, C.g_iconFont)
        M.panelItems.append(PI(r, kind, -1))
        state["rx"] = r.left

    icon(kIcoClose, PI.ClosePanel)
    if not term:
        return
    icon("", PI.KillTerm)   # Delete
    icon("", PI.SplitTerm)  # TwoPage
    icon("", PI.NewTerm)    # Add
    x = state["x"] + S(16)
    rx = state["rx"]
    i = 0
    while i < Term.GroupCount() and x < rx - S(48):
        s = Term.GroupTitle(i)
        SelectObject(dc, C.g_uiFont)
        sz = text_extent(dc, s)
        r = RECT(x, h.top + S(4), x + min(sz.cx + S(16), rx - S(8) - x), h.bottom - S(4))
        active = i == Term.ActiveGroup()
        if active:
            FillC(dc, r, t.activeBg)
        tr = RECT(r.left + S(8), r.top, r.right - S(8), r.bottom)
        Text(dc, s, tr, t.panelFg if active else t.panelFgDim, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS)
        M.panelItems.append(PI(r, PI.Group, i))
        x = r.right + S(4)
        i += 1


def Paint(hdc, clip):
    rc = client_rect(M.hwnd)
    dc = CreateCompatibleDC(hdc)
    bmp = CreateCompatibleBitmap(hdc, max(1, rc.right), max(1, rc.bottom))
    ob = SelectObject(dc, bmp)
    of = SelectObject(dc, C.g_uiFont)
    SetBkMode(dc, TRANSPARENT)
    PaintSideHeader(dc)
    if M.rcSplitter.right > M.rcSplitter.left:
        FillC(dc, M.rcSplitter, C.g_theme.panelBg)
        FillC(dc, RECT(M.rcSplitter.left, M.rcSplitter.top, M.rcSplitter.left + 1, M.rcSplitter.bottom),
              C.g_theme.border)
    PaintTabs(dc)
    if not M.tabs:
        PaintWelcome(dc)
    PaintPanelHeader(dc)
    PaintStatus(dc)
    BitBlt(hdc, clip.left, clip.top, clip.right - clip.left, clip.bottom - clip.top, dc, clip.left, clip.top, SRCCOPY)
    SelectObject(dc, of)
    SelectObject(dc, ob)
    DeleteObject(bmp)
    DeleteDC(dc)


# ---------------- mouse ----------------

def TabAt(pt):
    """Returns (index, onClose)."""
    S = C.S
    for i, r in enumerate(M.tabRects):
        if r.right > r.left and PtInRect(r, pt):
            cr = RECT(r.right - S(30), r.top, r.right - S(4), r.bottom)
            return i, PtInRect(cr, pt)
    return -1, False


def TabMenu(i, screen):
    m = CreatePopupMenu()
    AppendMenuW(m, MF_STRING, 1, "Close")
    AppendMenuW(m, MF_STRING, 2, "Close Others")
    AppendMenuW(m, MF_STRING, 3, "Close All")
    t = M.tabs[i]
    hasPath = t.kind == Tab.Editor and bool(t.ed.buf.path)
    if hasPath:
        AppendMenuW(m, MF_SEPARATOR, 0, None)
        AppendMenuW(m, MF_STRING, 4, "Copy Path")
        AppendMenuW(m, MF_STRING, 5, "Reveal in File Explorer\tAlt+Shift+R")
    cmd = TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTBUTTON, screen.x, screen.y, 0, M.hwnd, None)
    DestroyMenu(m)
    keep = t.hwnd
    path = t.ed.buf.path if hasPath else ""
    if cmd == 1:
        CloseTab(i)
    elif cmd == 2 or cmd == 3:
        for k in range(len(M.tabs) - 1, -1, -1):
            if cmd == 2 and M.tabs[k].hwnd == keep:
                continue
            if not CloseTab(k):
                break
    elif cmd == 4:
        C.SetClipboardText(M.hwnd, path)
    elif cmd == 5:
        C.RevealInExplorer(path)


def OverflowMenu():
    m = CreatePopupMenu()
    for i, tab in enumerate(M.tabs):
        AppendMenuW(m, MF_STRING | (MF_CHECKED if i == M.active else 0), i + 1, TabTitle(tab))
    pt = POINT(M.rcTabOverflow.left, M.rcTabOverflow.bottom)
    ClientToScreen(M.hwnd, byref(pt))
    cmd = TrackPopupMenu(m, TPM_RETURNCMD, pt.x, pt.y, 0, M.hwnd, None)
    DestroyMenu(m)
    if cmd > 0:
        ActivateTab(cmd - 1)


# ---------------- dark menu bar ----------------
# The native menu bar ignores DWM dark mode and stays white, so in dark mode it is painted by hand.
# WM_UAHDRAWMENU / WM_UAHDRAWMENUITEM are the undocumented messages Windows uses for that bar.
kWmUahDrawMenu = 0x0091
kWmUahDrawMenuItem = 0x0092


class UahMenu(Structure):
    _fields_ = [("hmenu", HMENU), ("hdc", HDC), ("dwFlags", DWORD)]


class UahDrawMenuItem(Structure):
    _fields_ = [("dis", DRAWITEMSTRUCT), ("um", UahMenu), ("iPosition", c_int)]


def PaintMenuBar(h, um):
    mbi = MENUBARINFO()
    mbi.cbSize = sizeof(mbi)
    if not GetMenuBarInfo(h, OBJID_MENU, 0, byref(mbi)):
        return
    win = window_rect(h)
    r = mbi.rcBar.copy()  # screen coordinates, the DC is window-relative
    OffsetRect(r, -win.left, -win.top)
    FillC(um.hdc, r, C.g_theme.panelBg)


def PaintMenuItem(d):
    text = create_unicode_buffer(256)
    GetMenuStringW(d.um.hmenu, d.iPosition, text, 256, MF_BYPOSITION)
    dc = d.dis.hDC
    r = d.dis.rcItem.copy()
    hot = (d.dis.itemState & (ODS_HOTLIGHT | ODS_SELECTED)) != 0
    FillC(dc, r, C.g_theme.hoverBg if hot else C.g_theme.panelBg)
    old = SelectObject(dc, C.g_uiFont)
    SetBkMode(dc, TRANSPARENT)
    SetTextColor(dc, C.g_theme.panelFgDim if (d.dis.itemState & ODS_GRAYED) else C.g_theme.panelFg)
    DrawTextW(dc, text.value, -1, byref(r), DT_CENTER | DT_VCENTER | DT_SINGLELINE)
    SelectObject(dc, old)


_lastPending = -1


def MainProc(h, m, w, l):
    global _lastPending
    S = C.S
    if m == WM_CREATE:
        M.hwnd = h
        C.g_mainWnd = h
        return 0
    elif m == WM_SIZE:
        Layout()
        return 0
    elif m == WM_ERASEBKGND:
        return 1
    elif m == kWmUahDrawMenu:
        if C.g_settings.dark:
            PaintMenuBar(h, UahMenu.from_address(l))
            return 0
    elif m == kWmUahDrawMenuItem:
        if C.g_settings.dark:
            PaintMenuItem(UahDrawMenuItem.from_address(l))
            return 0
    elif m == WM_PAINT:
        ps = PAINTSTRUCT()
        dc = BeginPaint(h, byref(ps))
        Paint(dc, ps.rcPaint)
        EndPaint(h, byref(ps))
        return 0
    elif m == C.WM_APP_RUNUI:
        fn = C.TakeUiCall(l)
        if fn:
            fn()
        return 0
    elif m == C.WM_APP_DOCCHANGED:
        if M.active >= 0 and M.tabs[M.active].hwnd == w:
            ed = ActiveEditor()
            if ed:
                FindBar.SetMessage(ed.findMessage)
            UpdateTitle()
        InvalidateChrome()
        return 0
    elif m == WM_TIMER:
        if w == kTimerStatus:
            p = git.BgPending()
            ed = ActiveEditor()
            if p != _lastPending or (ed and not ed.buf.Ready()):
                _lastPending = p
                InvalidateRect(h, byref(M.rcStatus), False)
        return 0
    elif m == WM_COMMAND:
        main_commands.OnCommand(LOWORD(w))
        return 0
    elif m == WM_INITMENUPOPUP:
        main_commands.OnInitMenu(w)
        return 0
    elif m == WM_ACTIVATE:
        if LOWORD(w) == WA_INACTIVE:
            M.lastFocus = GetFocus()
        elif M.lastFocus and IsChild(h, M.lastFocus) and IsWindowVisible(M.lastFocus):
            SetFocus(M.lastFocus)
            return 0
    elif m == WM_SETFOCUS:
        # (bounds check: CloseTab can re-enter here before M.active is updated; C++ reads past the vector)
        if 0 <= M.active < len(M.tabs):
            SetFocus(M.tabs[M.active].hwnd)
        return 0
    elif m == WM_ACTIVATEAPP:
        if w:
            App_ReloadUnmodifiedDocs()
            App_RefreshGit()
            Explorer.Refresh()
        return 0
    elif m == WM_DROPFILES:
        drop = w
        n = DragQueryFileW(drop, 0xFFFFFFFF, None, 0)
        for i in range(n):
            ln = DragQueryFileW(drop, i, None, 0)
            buf = create_unicode_buffer(ln + 1)
            DragQueryFileW(drop, i, buf, ln + 1)
            p = buf.value
            if C.IsDirectory(p):
                App_OpenFolder(p)
            else:
                App_OpenFile(p)
        DragFinish(drop)
        return 0
    elif m == WM_CTLCOLORSTATIC or m == WM_CTLCOLOREDIT:
        if M.output and l == M.output:
            SetTextColor(w, C.g_theme.fg)
            SetBkColor(w, C.g_theme.bg)
            return M.outputBrush or 0
    elif m == WM_SETCURSOR:
        if LOWORD(l) == HTCLIENT:
            pt = cursor_pos()
            ScreenToClient(h, byref(pt))
            sp = M.rcSplitter.copy()
            InflateRect(sp, S(2), 0)
            if PtInRect(sp, pt) or M.draggingSplit:
                SetCursor(LoadCursorW(None, IDC_SIZEWE))
                return 1
            if PtInRect(M.rcPanelSplit, pt) or M.draggingPanel:
                SetCursor(LoadCursorW(None, IDC_SIZENS))
                return 1
            link = False
            for it in M.statusItems:
                if PtInRect(it.rc, pt):
                    link = True
            if not M.tabs:
                for wl in M.welcomeLinks:
                    if PtInRect(wl[0], pt):
                        link = True
            if link:
                SetCursor(LoadCursorW(None, IDC_HAND))
                return 1
            SetCursor(LoadCursorW(None, IDC_ARROW))
            return 1
    elif m == WM_LBUTTONDOWN:
        pt = POINT(GET_X_LPARAM(l), GET_Y_LPARAM(l))
        sp = M.rcSplitter.copy()
        InflateRect(sp, S(2), 0)
        if PtInRect(sp, pt):
            M.draggingSplit = True
            SetCapture(h)
            return 0
        if PtInRect(M.rcPanelSplit, pt):
            M.draggingPanel = True
            SetCapture(h)
            return 0
        PI = MainState.PanelItem
        for it in list(M.panelItems):
            if not PtInRect(it.rc, pt):
                continue
            if it.kind == PI.OutputTab:
                ShowPanel(MainState.PanelOutput)
            elif it.kind == PI.TerminalTab:
                ShowPanel(MainState.PanelTerminal)
                if not Term.Focus():
                    Term.New()
            elif it.kind == PI.Group:
                Term.SelectGroup(it.group)
            elif it.kind == PI.NewTerm:
                main_commands.OnCommand(ID_TERM_NEW)
            elif it.kind == PI.SplitTerm:
                main_commands.OnCommand(ID_TERM_SPLIT)
            elif it.kind == PI.KillTerm:
                main_commands.OnCommand(ID_TERM_KILL)
            elif it.kind == PI.ClosePanel:
                HidePanel()
            return 0
        if PtInRect(M.rcSideHeader, pt):
            mode = 0 if pt.x < (M.rcSideHeader.left + M.rcSideHeader.right) // 2 else 1
            main_commands.OnCommand(ID_VIEW_EXPLORER if mode == 0 else ID_VIEW_SCM)
            return 0
        if PtInRect(M.rcTabOverflow, pt):
            OverflowMenu()
            return 0
        ti, onClose = TabAt(pt)
        if ti >= 0:
            if onClose:
                CloseTab(ti)
            else:
                ActivateTab(ti)
            return 0
        for it in list(M.statusItems):
            if PtInRect(it.rc, pt):
                main_commands.OnCommand(it.cmd)
                return 0
        if not M.tabs:
            for wl in list(M.welcomeLinks):
                if PtInRect(wl[0], pt):
                    f = wl[1]
                    App_OpenFolder(f)
                    return 0
        return 0
    elif m == WM_LBUTTONDBLCLK:
        pt = POINT(GET_X_LPARAM(l), GET_Y_LPARAM(l))
        if PtInRect(M.rcTabs, pt) and TabAt(pt)[0] < 0:
            main_commands.OnCommand(ID_FILE_NEW)
        return 0
    elif m == WM_MOUSEMOVE:
        pt = POINT(GET_X_LPARAM(l), GET_Y_LPARAM(l))
        if M.draggingSplit:
            C.g_settings.sidebarWidth = max(120, min(1200, MulDiv(pt.x, 96, C.g_dpi)))
            Layout()
            return 0
        if M.draggingPanel:
            C.g_settings.panelHeight = max(60, min(2000, MulDiv(M.rcStatus.top - pt.y, 96, C.g_dpi)))
            Layout()
            return 0
        ti, onClose = TabAt(pt)
        if ti != M.hoverTab or onClose != M.hoverClose:
            M.hoverTab = ti
            M.hoverClose = onClose
            InvalidateRect(h, byref(M.rcTabs), False)
        if not M.tracking:
            tme = TRACKMOUSEEVENT()
            tme.cbSize = sizeof(tme)
            tme.dwFlags = TME_LEAVE
            tme.hwndTrack = h
            tme.dwHoverTime = 0
            TrackMouseEvent(byref(tme))
            M.tracking = True
        return 0
    elif m == WM_MOUSELEAVE:
        M.tracking = False
        if M.hoverTab != -1:
            M.hoverTab = -1
            InvalidateRect(h, byref(M.rcTabs), False)
        return 0
    elif m == WM_LBUTTONUP:
        if M.draggingSplit or M.draggingPanel:
            M.draggingSplit = M.draggingPanel = False
            ReleaseCapture()
            C.SaveSettings()
        return 0
    elif m == WM_MBUTTONUP:
        pt = POINT(GET_X_LPARAM(l), GET_Y_LPARAM(l))
        ti = TabAt(pt)[0]
        if ti >= 0:
            CloseTab(ti)
        for it in list(M.panelItems):
            if it.kind == MainState.PanelItem.Group and PtInRect(it.rc, pt):
                Term.KillGroup(it.group)
                break
        return 0
    elif m == WM_RBUTTONUP:
        pt = POINT(GET_X_LPARAM(l), GET_Y_LPARAM(l))
        ti = TabAt(pt)[0]
        if ti >= 0:
            ClientToScreen(h, byref(pt))
            TabMenu(ti, pt)
        return 0
    elif m == WM_MOUSEWHEEL:
        pt = POINT(GET_X_LPARAM(l), GET_Y_LPARAM(l))
        ScreenToClient(h, byref(pt))
        if PtInRect(M.rcTabs, pt) and M.tabs:
            d = -1 if GET_WHEEL_DELTA_WPARAM(w) > 0 else 1
            n = max(0, min(len(M.tabs) - 1, M.active + d))
            ActivateTab(n)
        return 0
    elif m == WM_DPICHANGED:
        C.g_dpi = HIWORD(w)
        RecreateFonts()
        r = RECT.from_address(l)
        SetWindowPos(h, None, r.left, r.top, r.right - r.left, r.bottom - r.top, SWP_NOZORDER | SWP_NOACTIVATE)
        for t in M.tabs:
            if t.kind == Tab.Editor:
                t.ed.FontChanged()
        Explorer.ThemeChanged()
        Layout()
        Term.FontChanged()
        RedrawWindow(h, None, None, RDW_INVALIDATE | RDW_ALLCHILDREN)
        return 0
    elif m == WM_CLOSE:
        if not main_commands.ConfirmCloseAll():
            return 0
        wp = WINDOWPLACEMENT()
        wp.length = sizeof(wp)
        GetWindowPlacement(h, byref(wp))
        st = C.g_settings
        st.winMax = wp.showCmd == SW_SHOWMAXIMIZED
        st.winX = wp.rcNormalPosition.left
        st.winY = wp.rcNormalPosition.top
        st.winW = wp.rcNormalPosition.right - wp.rcNormalPosition.left
        st.winH = wp.rcNormalPosition.bottom - wp.rcNormalPosition.top
        C.SaveSettings()
        Term.CloseAll()
        DestroyWindow(h)
        return 0
    elif m == WM_DESTROY:
        PostQuitMessage(0)
        return 0
    return DefWindowProcW(h, m, w, l)


# ---------------- bottom panel ----------------

def ShowPanel(tab):
    M.panelTab = tab
    if not C.g_settings.showOutput:
        C.g_settings.showOutput = True
        C.SaveSettings()
    Layout()


def HidePanel():
    hadFocus = Term.HasFocus() or (M.output and GetFocus() == M.output)
    C.g_settings.showOutput = False
    Layout()
    C.SaveSettings()
    if hadFocus:
        SetFocus(M.hwnd)


def TerminalsChanged():
    """terminal.py -> main: groups/panes changed, re-layout."""
    if Term.GroupCount() == 0 and M.panelTab == MainState.PanelTerminal and C.g_settings.showOutput:
        C.g_settings.showOutput = False
        C.SaveSettings()
    Layout()


# ---------------- App API ----------------

def App_Main():
    return M.hwnd


def App_Folder():
    return M.folder


def App_Status():
    return M.status


def FullPath(p):
    buf = create_unicode_buffer(32768)
    n = GetFullPathNameW(p, 32768, buf, None)
    return buf.value if n else p


def App_OpenFile(path, line=-1):
    full = FullPath(path)
    key = "file:" + C.LowerW(full)
    for i, t in enumerate(M.tabs):
        if t.key == key:
            ActivateTab(i)
            if line >= 0:
                t.ed.GotoLine(line)
            return
    old = SetCursor(LoadCursorW(None, IDC_WAIT))
    ed, err = editor_view.EditorView.Create(M.hwnd, full)
    SetCursor(old)
    if not ed:
        C.MsgBox(M.hwnd, "Could not open " + full + "\n\n" + err, MB_ICONERROR)
        return
    AddTab(Tab(Tab.Editor, ed.hwnd, ed, key))
    if line >= 0:
        ed.GotoLine(line)


def App_OpenDiff(spec):
    key = DiffView.Key(spec)
    for i, t in enumerate(M.tabs):
        if t.key == key:
            ActivateTab(i)
            DiffView.Reload(t.hwnd)
            return
    h = DiffView.Create(M.hwnd, spec)
    if not h:
        return
    AddTab(Tab(Tab.Diff, h, None, key))


def App_OpenLog():
    if not M.status.isRepo:
        return
    key = "log:" + C.LowerW(M.status.root)
    for i, t in enumerate(M.tabs):
        if t.key == key:
            ActivateTab(i)
            LogView.Reload(t.hwnd)
            return
    h = LogView.Create(M.hwnd, M.status.root)
    if not h:
        return
    AddTab(Tab(Tab.Log, h, None, key))


def App_OpenFolder(folder):
    f = FullPath(folder)
    while len(f) > 3 and f[-1] in "\\/":
        f = f[:-1]
    if not C.IsDirectory(f):
        C.MsgBox(M.hwnd, "Folder not found:\n" + f, MB_ICONWARNING)
        C.g_settings.recentFolders = [x for x in C.g_settings.recentFolders if not C.PathEqualsI(x, f)]
        C.SaveSettings()
        InvalidateRect(M.hwnd, None, False)
        return
    M.folder = f
    M.status = git.GitStatus()
    Explorer.SetRoot(f)
    C.AddRecentFolder(f)
    Scm.Update(M.status)
    App_RefreshGit()
    UpdateTitle()
    if not C.g_settings.showSidebar:
        C.g_settings.showSidebar = True
        Layout()
    InvalidateRect(M.hwnd, None, False)


def App_RefreshGit():
    if M.gitPending:
        M.gitQueued = True
        return
    M.gitPending = True
    folder = M.folder

    def job():
        st = git.QueryGitStatus(folder)

        def ui():
            M.gitPending = False
            if folder == M.folder:
                App_ReloadUnmodifiedDocs()  # pick up files changed by git or external tools
                M.status = st
                Scm.Update(M.status)
                for t in M.tabs:
                    if t.kind == Tab.Diff:
                        DiffView.Reload(t.hwnd)
                InvalidateChrome()
            if M.gitQueued:
                M.gitQueued = False
                App_RefreshGit()

        C.RunOnUi(ui)

    git.BgRun(job)


def App_SetStatusText(s):
    M.statusText = s
    InvalidateRect(M.hwnd, byref(M.rcStatus), False)


def App_AppendOutput(s):
    if not M.output:
        return
    ln = GetWindowTextLengthW(M.output)
    if ln > 400000:
        SendMessageW(M.output, EM_SETSEL, 0, ln // 2)
        empty = create_unicode_buffer("")
        SendMessageW(M.output, EM_REPLACESEL, False, addressof(empty))
        ln = GetWindowTextLengthW(M.output)
    SendMessageW(M.output, EM_SETSEL, ln, ln)
    buf = create_unicode_buffer(s)
    SendMessageW(M.output, EM_REPLACESEL, False, addressof(buf))
    SendMessageW(M.output, EM_SCROLLCARET, 0, 0)


def App_ShowOutput():
    if not C.g_settings.showOutput:
        ShowPanel(MainState.PanelOutput)  # don't take the panel away from a visible terminal


def App_ReloadUnmodifiedDocs():
    for t in M.tabs:
        if t.kind != Tab.Editor:
            continue
        b = t.ed.buf
        if b.Modified() or not b.path or not b.ChangedOnDisk():
            continue
        if not C.FileExists(b.path):
            continue
        t.ed.Reload()
    InvalidateChrome()


def App_ShowScm():
    if M.sidebarMode != 1 or not C.g_settings.showSidebar:
        main_commands.OnCommand(ID_VIEW_SCM)


# ---------------- creation ----------------

def CreateMainWindow():
    icon = g_appIcon
    iconSm = g_appIconSm
    C.RegisterWindowClass("DittoMain", MainProc, style=CS_DBLCLKS, cursor=IDC_ARROW, icon=icon, icon_sm=iconSm)
    editor_view.EditorView.Register()
    DiffView.Register()
    LogView.Register()

    M.menu = main_commands.BuildMenu()
    M.accel = main_commands.BuildAccel()
    st = C.g_settings
    h = CreateWindowExW(WS_EX_ACCEPTFILES, "DittoMain", "ditto", WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
                        st.winX, st.winY, st.winW, st.winH, None, M.menu, C.g_hinst, None)
    if not h:
        return None
    if icon:
        SendMessageW(h, WM_SETICON, ICON_BIG, icon)
    if iconSm:
        SendMessageW(h, WM_SETICON, ICON_SMALL, iconSm)
    C.g_dpi = GetDpiForWindow(h)
    RecreateFonts()
    C.ApplyTheme(st.dark)
    M.explorer = Explorer.Create(h)
    M.scm = Scm.Create(h)
    M.findBar = FindBar.Create(h)
    M.output = CreateWindowExW(0, "EDIT", "",
                               WS_CHILD | WS_VSCROLL | ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | ES_NOHIDESEL,
                               0, 0, 0, 0, h, None, C.g_hinst, None)
    SendMessageW(M.output, WM_SETFONT, C.g_uiFont or 0, False)
    SendMessageW(M.output, EM_SETLIMITTEXT, 0, 0)

    def logger(s):
        copy = s
        C.RunOnUi(lambda: App_AppendOutput(copy))

    git.SetGitLogger(logger)
    ApplyThemeAll()
    SetTimer(h, kTimerStatus, 500, None)
    Layout()
    UpdateTitle()
    return h
