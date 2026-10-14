# Source Control side panel: commit message box, commit button and a custom-drawn change list.
# (port of scm_panel.cpp, namespace Scm in app.h)
import copy

import common as C
import win32 as W
from win32 import *  # noqa: F401,F403
from resource import *  # noqa: F401,F403
import app
import git
import git_commands

# ---- tooltip bits not in win32.py ----
TOOLTIPS_CLASSW = "tooltips_class32"
TTS_ALWAYSTIP = 0x01
TTS_NOPREFIX = 0x02
TTF_IDISHWND = 0x0001
TTF_SUBCLASS = 0x0010
TTM_ADDTOOLW = WM_USER + 50


class TOOLINFOW(Structure):
    _fields_ = [("cbSize", UINT), ("uFlags", UINT), ("hwnd", HWND), ("uId", UINT_PTR), ("rect", RECT),
                ("hinst", HINSTANCE), ("lpszText", LPWSTR), ("lParam", LPARAM), ("lpReserved", c_void_p)]


IDC_MSG = 2001
IDC_COMMIT = 2002
IDC_COMMITMENU = 2003
IDC_QUICK = 2004
IDC_REFRESH = 2005
IDC_MORE = 2006
IDC_INIT = 2007
IDC_CLONE = 2008
IDC_OPENFOLDER = 2009
IDC_PUBLISH = 2010

G_MERGE, G_STAGED, G_CHANGES, G_COUNT = range(4)
kGroupNames = ["MERGE CHANGES", "STAGED CHANGES", "CHANGES"]

# glyphs (Segoe MDL2 Assets)
kIcoAdd = ""
kIcoRemove = ""
kIcoUndo = ""
kIcoOpen = ""
kIcoRefresh = ""
kIcoMore = ""
kIcoDown = ""
kIcoRight = ""


class Row:
    __slots__ = ("header", "group", "file")

    def __init__(self, header, group, file):
        self.header = header
        self.group = group
        self.file = file  # index in st.files


class State:
    def __init__(self):
        self.hwnd = None
        self.msg = self.commit = self.commitMenu = self.quick = self.refresh = self.more = None
        self.init = self.clone = self.openFolder = self.publish = self.list = None
        self.inputBrush = None
        self.st = git.GitStatus()
        self.rows = []
        self.sel = []
        self.focus = -1
        self.anchor = -1
        self.hover = -1
        self.hoverBtn = -1
        self.top = 0
        self.collapsed = [False] * G_COUNT
        self.tracking = False


P = State()


def _idiv(a, b):
    """C++ integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def IsConflict(f):
    x, y = f.x, f.y
    return (x == 'U' or y == 'U') or (x == 'A' and y == 'A') or (x == 'D' and y == 'D')


def Letter(f, g):
    if g == G_MERGE:
        return '!'
    c = f.x if g == G_STAGED else f.y
    return 'U' if c == '?' else c


def LetterColor(c):
    d = C.g_settings.dark
    if c == 'M':
        return RGB(226, 192, 141) if d else RGB(137, 92, 20)
    if c in ('A', 'U'):
        return RGB(115, 201, 145) if d else RGB(56, 130, 70)
    if c == 'D':
        return RGB(199, 78, 57) if d else RGB(173, 15, 15)
    if c in ('R', 'C'):
        return RGB(115, 201, 145) if d else RGB(56, 130, 70)
    if c == '!':
        return RGB(230, 100, 100) if d else RGB(200, 30, 30)
    return C.g_theme.panelFg


def RowH():
    return C.S(22)


def GroupFiles(g):
    v = []
    for i, f in enumerate(P.st.files):
        conflict = IsConflict(f)
        if g == G_MERGE and conflict:
            v.append(i)
        if g == G_STAGED and not conflict and f.x != ' ' and f.x != '?':
            v.append(i)
        if g == G_CHANGES and not conflict and (f.y != ' ' or f.x == '?'):
            v.append(i)
    return v


def RowKey(r):
    return str(r.group) + ("#" if r.header else ":" + P.st.files[r.file].path)


def UpdateListScroll():
    rc = client_rect(P.list)
    vis = max(1, _idiv(rc.bottom, RowH()))
    maxTop = max(0, len(P.rows) - vis)
    P.top = max(0, min(P.top, maxTop))
    si = SCROLLINFO()
    si.cbSize = sizeof(SCROLLINFO)
    si.fMask = SIF_RANGE | SIF_PAGE | SIF_POS
    si.nMax = len(P.rows) - 1
    si.nPage = vis
    si.nPos = P.top
    SetScrollInfo(P.list, SB_VERT, byref(si), True)


def RebuildRows():
    selKeys = []
    focusKey = ""
    for i in range(len(P.rows)):
        if i < len(P.sel) and P.sel[i]:
            selKeys.append(RowKey(P.rows[i]))
        if i == P.focus:
            focusKey = RowKey(P.rows[i])
    P.rows = []
    for g in range(G_COUNT):
        files = GroupFiles(g)
        if not files and g != G_CHANGES:
            continue
        P.rows.append(Row(True, g, -1))
        if P.collapsed[g]:
            continue
        for f in files:
            P.rows.append(Row(False, g, f))
    P.sel = [False] * len(P.rows)
    P.focus = -1
    for i in range(len(P.rows)):
        k = RowKey(P.rows[i])
        if k in selKeys:
            P.sel[i] = True
        if k == focusKey:
            P.focus = i
    UpdateListScroll()
    InvalidateRect(P.list, None, False)


# action buttons for a row, right to left (excluding the status letter)
def RowActions(r):
    # 1 = stage, 2 = unstage, 3 = discard, 4 = open file
    if r.header:
        if r.group == G_STAGED:
            return [2]
        if r.group == G_CHANGES:
            return [1, 3]
        return [1]
    if r.group == G_STAGED:
        return [2, 4]
    if r.group == G_CHANGES:
        return [1, 3, 4]
    return [1, 4]


def ActionGlyph(a):
    if a == 1:
        return kIcoAdd
    if a == 2:
        return kIcoRemove
    if a == 3:
        return kIcoUndo
    return kIcoOpen


def ActionTip(a):
    if a == 1:
        return "Stage"
    if a == 2:
        return "Unstage"
    if a == 3:
        return "Discard Changes"
    return "Open File"


def ActionRect(row, idx, header):
    bw = C.S(22)
    right = row.right - (C.S(34) if header else C.S(22)) - idx * bw
    return RECT(right - bw, row.top, right, row.bottom)


def HitAction(rowIdx, x, y):
    if rowIdx < 0 or rowIdx >= len(P.rows):
        return -1
    rc = client_rect(P.list)
    row = RECT(0, (rowIdx - P.top) * RowH(), rc.right, (rowIdx - P.top + 1) * RowH())
    acts = RowActions(P.rows[rowIdx])
    for i in range(len(acts)):
        ar = ActionRect(row, i, P.rows[rowIdx].header)
        if PtInRect(ar, POINT(x, y)):
            return acts[i]
    return -1


def SelectedFileRows():
    return [i for i in range(len(P.rows)) if P.sel[i] and not P.rows[i].header]


def OpenRow(i, file):
    r = P.rows[i]
    if r.header:
        return
    f = P.st.files[r.file]
    full = C.JoinPath(P.st.root, C.ToBackslashes(f.path))
    if file or r.group == G_MERGE:
        if f.x == 'D' and r.group == G_STAGED:
            return
        app.OpenFile(full)
        return
    d = app.DiffSpec()
    d.root = P.st.root
    d.path = f.path
    d.origPath = f.origPath
    if r.group == G_STAGED:
        d.kind = app.DiffSpec.Staged
    else:
        d.kind = app.DiffSpec.Untracked if f.x == '?' else app.DiffSpec.Unstaged
    app.OpenDiff(d)


def DoAction(action, rowIdx):
    paths = []
    files = []
    for i in rowIdx:
        r = P.rows[i]
        if r.header:
            fl = GroupFiles(r.group)
        else:
            fl = [r.file]
        for fi in fl:
            f = P.st.files[fi]
            if f.path not in paths:
                paths.append(f.path)
                files.append(f)
                if action == 2 and f.origPath:
                    paths.append(f.origPath)
    if not paths:
        return
    if action == 1:
        git_commands.GitStage(paths, True)
    elif action == 2:
        git_commands.GitStage(paths, False)
    elif action == 3:
        git_commands.GitDiscard(files)
    elif action == 4:
        for i in rowIdx:
            OpenRow(i, True)


def ListContextMenu(x, y):
    rows = SelectedFileRows()
    if not rows:
        return
    anyStaged = anyChange = False
    for i in rows:
        if P.rows[i].group == G_STAGED:
            anyStaged = True
        else:
            anyChange = True
    m = CreatePopupMenu()
    AppendMenuW(m, MF_STRING, 10, "Open Changes")
    AppendMenuW(m, MF_STRING, 4, "Open File")
    AppendMenuW(m, MF_SEPARATOR, 0, None)
    if anyChange:
        AppendMenuW(m, MF_STRING, 1, "Stage Changes")
    if anyStaged:
        AppendMenuW(m, MF_STRING, 2, "Unstage Changes")
    if anyChange:
        AppendMenuW(m, MF_STRING, 3, "Discard Changes")
    AppendMenuW(m, MF_SEPARATOR, 0, None)
    AppendMenuW(m, MF_STRING, 11, "Copy Path")
    AppendMenuW(m, MF_STRING, 12, "Reveal in File Explorer\tAlt+Shift+R")
    cmd = TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, 0, P.list, None)
    DestroyMenu(m)
    if cmd == 10:
        for i in rows:
            OpenRow(i, False)
    elif cmd == 11 or cmd == 12:
        f = P.st.files[P.rows[rows[0]].file]
        full = C.JoinPath(P.st.root, C.ToBackslashes(f.path))
        if cmd == 11:
            C.SetClipboardText(P.list, full)
        else:
            C.RevealInExplorer(full)
    elif 1 <= cmd <= 4:
        filtered = []
        for i in rows:
            g = P.rows[i].group
            if (cmd == 1 or cmd == 3) and g == G_STAGED:
                continue
            if cmd == 2 and g != G_STAGED:
                continue
            filtered.append(i)
        DoAction(cmd, filtered)


def PaintList(hdc):
    rc = client_rect(P.list)
    dc = CreateCompatibleDC(hdc)
    bmp = CreateCompatibleBitmap(hdc, max(1, rc.right), max(1, rc.bottom))
    ob = SelectObject(dc, bmp)
    t = C.g_theme
    fill_solid(dc, rc, t.panelBg)
    SetBkMode(dc, TRANSPARENT)
    rh = RowH()
    focused = GetFocus() == P.list
    S = C.S
    for i in range(P.top, len(P.rows)):
        y = (i - P.top) * rh
        if y > rc.bottom:
            break
        r = P.rows[i]
        row = RECT(0, y, rc.right, y + rh)
        if P.sel[i] or i == P.hover:
            c = (t.selBg if focused else t.hoverBg) if P.sel[i] else t.hoverBg
            fill_solid(dc, row, c)
        if r.header:
            cr = RECT(S(4), y, S(20), y + rh)
            SelectObject(dc, C.g_iconFont)
            SetTextColor(dc, t.panelFg)
            draw_text(dc, kIcoRight if P.collapsed[r.group] else kIcoDown, cr,
                      DT_CENTER | DT_VCENTER | DT_SINGLELINE)
            tr = RECT(S(22), y, rc.right - S(30), y + rh)
            SelectObject(dc, C.g_uiFontBold)
            draw_text(dc, kGroupNames[r.group], tr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS)
            # count badge
            cnt = str(len(GroupFiles(r.group)))
            SelectObject(dc, C.g_uiFont)
            sz = text_extent(dc, cnt)
            bw = max(sz.cx + S(10), S(18))
            br = RECT(rc.right - S(8) - bw, y + S(3), rc.right - S(8), y + rh - S(3))
            bb = CreateSolidBrush(t.border if t.hoverBg == t.panelBg else
                                  (RGB(77, 77, 77) if C.g_settings.dark else RGB(196, 196, 196)))
            obr = SelectObject(dc, bb)
            op = SelectObject(dc, GetStockObject(NULL_PEN))
            RoundRect(dc, br.left, br.top, br.right, br.bottom, S(12), S(12))
            SelectObject(dc, op)
            SelectObject(dc, obr)
            DeleteObject(bb)
            draw_text(dc, cnt, br, DT_CENTER | DT_VCENTER | DT_SINGLELINE)
        else:
            f = P.st.files[r.file]
            path = f.path
            sl = path.rfind('/')
            name = path if sl < 0 else path[sl + 1:]
            dir = "" if sl < 0 else path[:sl]
            letter = Letter(f, r.group)
            right = rc.right - S(26)
            if i == P.hover or P.sel[i]:
                right -= len(RowActions(r)) * S(22)
            nr = RECT(S(24), y, right, y + rh)
            SelectObject(dc, C.g_uiFont)
            SetTextColor(dc, t.panelFgDim if letter == 'D' else t.panelFg)
            nsz = text_extent(dc, name)
            draw_text(dc, name, nr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX)
            if dir and S(24) + nsz.cx + S(8) < right:
                dr = RECT(S(24) + nsz.cx + S(6), y, right, y + rh)
                SetTextColor(dc, t.panelFgDim)
                draw_text(dc, dir, dr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX)
            ls = letter[:1]
            lr = RECT(rc.right - S(22), y, rc.right - S(6), y + rh)
            SetTextColor(dc, LetterColor(letter))
            SelectObject(dc, C.g_uiFontBold)
            draw_text(dc, ls, lr, DT_CENTER | DT_VCENTER | DT_SINGLELINE)
        if i == P.hover or (not r.header and P.sel[i]):
            acts = RowActions(r)
            SelectObject(dc, C.g_iconFont)
            for a in range(len(acts)):
                ar = ActionRect(row, a, r.header)
                if i == P.hover and acts[a] == P.hoverBtn:
                    hr = RECT(ar.left + S(2), ar.top + S(2), ar.right - S(2), ar.bottom - S(2))
                    fill_solid(dc, hr, RGB(80, 80, 84) if C.g_settings.dark else RGB(205, 205, 205))
                SetTextColor(dc, t.panelFg)
                draw_text(dc, ActionGlyph(acts[a]), ar, DT_CENTER | DT_VCENTER | DT_SINGLELINE)
    if len(P.rows) <= 1 and P.st.isRepo and not P.st.files:
        mr = RECT(S(24), rh + S(6), rc.right - S(8), rh * 3)
        SelectObject(dc, C.g_uiFont)
        SetTextColor(dc, t.panelFgDim)
        draw_text(dc, "No changes.", mr, DT_LEFT | DT_TOP | DT_WORDBREAK)
    BitBlt(hdc, 0, 0, rc.right, rc.bottom, dc, 0, 0, SRCCOPY)
    SelectObject(dc, ob)
    DeleteObject(bmp)
    DeleteDC(dc)


def SetSingleSel(i):
    P.sel = [False] * len(P.sel)
    if 0 <= i < len(P.sel):
        P.sel[i] = True
    P.focus = P.anchor = i


def EnsureVisible(i):
    rc = client_rect(P.list)
    vis = max(1, _idiv(rc.bottom, RowH()))
    if i < P.top:
        P.top = i
    elif i >= P.top + vis:
        P.top = i - vis + 1
    UpdateListScroll()


def _SelectRange(i):
    P.sel = [False] * len(P.sel)
    for k in range(min(P.anchor, i), max(P.anchor, i) + 1):
        if not P.rows[k].header:
            P.sel[k] = True
    P.focus = i


def ListProc(h, m, w, l):
    if m == WM_PAINT:
        ps = PAINTSTRUCT()
        dc = BeginPaint(h, byref(ps))
        PaintList(dc)
        EndPaint(h, byref(ps))
        return 0
    if m == WM_ERASEBKGND:
        return 1
    if m == WM_SIZE:
        UpdateListScroll()
        InvalidateRect(h, None, False)
        return 0
    if m == WM_SETFOCUS or m == WM_KILLFOCUS:
        InvalidateRect(h, None, False)
        return 0
    if m == WM_GETDLGCODE:
        return DLGC_WANTARROWS | DLGC_WANTCHARS
    if m == WM_MOUSEMOVE:
        x, y = GET_X_LPARAM(l), GET_Y_LPARAM(l)
        i = P.top + _idiv(y, RowH())
        if i >= len(P.rows):
            i = -1
        btn = HitAction(i, x, y)
        if i != P.hover or btn != P.hoverBtn:
            P.hover = i
            P.hoverBtn = btn
            InvalidateRect(h, None, False)
        if not P.tracking:
            tme = TRACKMOUSEEVENT(sizeof(TRACKMOUSEEVENT), TME_LEAVE, h, 0)
            TrackMouseEvent(byref(tme))
            P.tracking = True
        return 0
    if m == WM_MOUSELEAVE:
        P.tracking = False
        P.hover = P.hoverBtn = -1
        InvalidateRect(h, None, False)
        return 0
    if m == WM_LBUTTONDOWN or m == WM_RBUTTONDOWN:
        SetFocus(h)
        x, y = GET_X_LPARAM(l), GET_Y_LPARAM(l)
        i = P.top + _idiv(y, RowH())
        if i >= len(P.rows):
            return 0
        if m == WM_LBUTTONDOWN:
            act = HitAction(i, x, y)
            if act > 0:
                target = []
                if not P.rows[i].header and P.sel[i]:
                    for s in SelectedFileRows():
                        if P.rows[s].group == P.rows[i].group:
                            target.append(s)
                else:
                    target.append(i)
                DoAction(act, target)
                return 0
            if P.rows[i].header:
                P.collapsed[P.rows[i].group] = not P.collapsed[P.rows[i].group]
                RebuildRows()
                return 0
            if w & MK_CONTROL:
                P.sel[i] = not P.sel[i]
                P.focus = P.anchor = i
            elif (w & MK_SHIFT) and P.anchor >= 0:
                _SelectRange(i)
            else:
                SetSingleSel(i)
                OpenRow(i, False)
        elif not P.sel[i]:
            SetSingleSel(i)
        InvalidateRect(h, None, False)
        return 0
    if m == WM_LBUTTONDBLCLK:
        i = P.top + _idiv(GET_Y_LPARAM(l), RowH())
        if i < len(P.rows) and not P.rows[i].header and HitAction(i, GET_X_LPARAM(l), GET_Y_LPARAM(l)) < 0:
            OpenRow(i, True)
        return 0
    if m == WM_CONTEXTMENU:
        x, y = GET_X_LPARAM(l), GET_Y_LPARAM(l)
        if x == -1 and y == -1:
            pt = POINT(C.S(30), (P.focus - P.top + 1) * RowH())
            ClientToScreen(h, byref(pt))
            x, y = pt.x, pt.y
        ListContextMenu(x, y)
        return 0
    if m == WM_MOUSEWHEEL:
        d = GET_WHEEL_DELTA_WPARAM(w)
        P.top -= _idiv(d, WHEEL_DELTA) * 3
        UpdateListScroll()
        InvalidateRect(h, None, False)
        return 0
    if m == WM_VSCROLL:
        si = SCROLLINFO()
        si.cbSize = sizeof(SCROLLINFO)
        si.fMask = SIF_ALL
        GetScrollInfo(h, SB_VERT, byref(si))
        code = LOWORD(w)
        if code == SB_LINEUP:
            P.top -= 1
        elif code == SB_LINEDOWN:
            P.top += 1
        elif code == SB_PAGEUP:
            P.top -= si.nPage
        elif code == SB_PAGEDOWN:
            P.top += si.nPage
        elif code == SB_THUMBTRACK or code == SB_THUMBPOSITION:
            P.top = si.nTrackPos
        UpdateListScroll()
        InvalidateRect(h, None, False)
        return 0
    if m == WM_KEYDOWN:
        n = len(P.rows)
        if not n:
            return 0
        i = P.focus
        if w == VK_DOWN:
            i = min(n - 1, i + 1)
        elif w == VK_UP:
            i = max(0, i - 1)
        elif w == VK_HOME:
            i = 0
        elif w == VK_END:
            i = n - 1
        elif w == VK_RETURN and i >= 0:
            OpenRow(i, False)
            return 0
        elif w == VK_SPACE and i >= 0 and not P.rows[i].header:
            DoAction(2 if P.rows[i].group == G_STAGED else 1, [i])
            return 0
        elif w == ord('A') and GetKeyState(VK_CONTROL) < 0:
            for k in range(n):
                P.sel[k] = not P.rows[k].header
            InvalidateRect(h, None, False)
            return 0
        else:
            return 0
        if GetKeyState(VK_SHIFT) < 0 and P.anchor >= 0:
            _SelectRange(i)
        else:
            SetSingleSel(i)
        EnsureVisible(i)
        InvalidateRect(h, None, False)
        return 0
    return DefWindowProcW(h, m, w, l)


def MsgEditSub(h, m, w, l, uid, ref):
    if m == WM_KEYDOWN and w == VK_RETURN and GetKeyState(VK_CONTROL) < 0:
        git_commands.GitCommand(ID_GIT_COMMIT)
        return 0
    if m == WM_KEYDOWN and w == ord('A') and GetKeyState(VK_CONTROL) < 0:
        SendMessageW(h, EM_SETSEL, 0, -1)
        return 0
    if m == WM_CHAR and (w == 10 or w == 1) and GetKeyState(VK_CONTROL) < 0:
        return 0
    r = DefSubclassProc(h, m, w, l)
    if m == WM_PAINT and GetWindowTextLengthW(h) == 0:
        dc = GetDC(h)
        rc = RECT()
        SendMessageW(h, EM_GETRECT, 0, addressof(rc))
        o = SelectObject(dc, C.g_uiFont)
        SetBkMode(dc, TRANSPARENT)
        SetTextColor(dc, C.g_theme.panelFgDim)
        ph = "Message (Ctrl+Enter to commit"
        if P.st.branch:
            ph += " on '" + P.st.branch + "'"
        ph += ")"
        draw_text(dc, ph, rc, DT_LEFT | DT_TOP | DT_WORDBREAK | DT_NOPREFIX)
        SelectObject(dc, o)
        ReleaseDC(h, dc)
    if m == WM_SETFOCUS or m == WM_KILLFOCUS or m == WM_CHAR or m == WM_KEYUP:
        InvalidateRect(h, None, False)
    return r


def Layout():
    rc = client_rect(P.hwnd)
    S = C.S
    Wd, pad, hh, bw = rc.right, S(8), S(30), S(26)
    MoveWindow(P.refresh, Wd - pad - 2 * bw, S(3), bw, bw - S(2), True)
    MoveWindow(P.more, Wd - pad - bw, S(3), bw, bw - S(2), True)
    repo = P.st.isRepo
    folder = bool(app.Folder())
    y = hh
    ShowWindow(P.msg, SW_SHOW if repo else SW_HIDE)
    ShowWindow(P.commit, SW_SHOW if repo else SW_HIDE)
    ShowWindow(P.quick, SW_SHOW if repo else SW_HIDE)
    ShowWindow(P.list, SW_SHOW if repo else SW_HIDE)
    ShowWindow(P.init, SW_SHOW if not repo and folder else SW_HIDE)
    ShowWindow(P.publish, SW_SHOW if not repo and folder else SW_HIDE)
    ShowWindow(P.openFolder, SW_SHOW if not folder else SW_HIDE)
    ShowWindow(P.clone, SW_SHOW if not repo else SW_HIDE)
    if repo:
        mh = S(66)
        MoveWindow(P.msg, pad, y, Wd - 2 * pad, mh, True)
        y += mh + S(6)
        ch, mw = S(28), S(28)
        MoveWindow(P.commit, pad, y, Wd - 2 * pad - mw - S(2), ch, True)
        MoveWindow(P.commitMenu, Wd - pad - mw, y, mw, ch, True)
        y += ch + S(8)
        MoveWindow(P.quick, pad, y, Wd - 2 * pad, ch, True)
        y += ch + S(8)
        MoveWindow(P.list, 0, y, Wd, max(0, rc.bottom - y), True)
    else:
        y += S(64)
        bh = S(28)
        for b in (P.openFolder, P.init, P.publish, P.clone):
            if not IsWindowVisible(b):
                continue
            MoveWindow(b, pad, y, Wd - 2 * pad, bh, True)
            y += bh + S(8)
    InvalidateRect(P.hwnd, None, True)


def ShowMenu(anchor, commitMenu):
    m = CreatePopupMenu()
    if commitMenu:
        AppendMenuW(m, MF_STRING, ID_GIT_COMMIT, "Commit")
        AppendMenuW(m, MF_STRING, ID_GIT_COMMIT_STAGED, "Commit Staged")
        AppendMenuW(m, MF_STRING, ID_GIT_COMMIT_ALL, "Commit All")
        AppendMenuW(m, MF_STRING, ID_GIT_COMMIT_AMEND, "Commit (Amend)")
        AppendMenuW(m, MF_SEPARATOR, 0, None)
        AppendMenuW(m, MF_STRING, ID_GIT_UNDO_COMMIT, "Undo Last Commit")
        AppendMenuW(m, MF_SEPARATOR, 0, None)
        AppendMenuW(m, MF_STRING, ID_GIT_PUSH, "Push")
        AppendMenuW(m, MF_STRING, ID_GIT_SYNC, "Sync (Pull + Push)")
    else:
        AppendMenuW(m, MF_STRING, ID_GIT_PULL, "Pull")
        AppendMenuW(m, MF_STRING, ID_GIT_PUSH, "Push")
        AppendMenuW(m, MF_STRING, ID_GIT_SYNC, "Sync")
        AppendMenuW(m, MF_STRING, ID_GIT_FETCH, "Fetch")
        AppendMenuW(m, MF_STRING, ID_GIT_PUSH_FORCE, "Force Push (with lease)...")
        AppendMenuW(m, MF_SEPARATOR, 0, None)
        AppendMenuW(m, MF_STRING, ID_GIT_CHECKOUT, "Checkout to...")
        AppendMenuW(m, MF_STRING, ID_GIT_BRANCH_CREATE, "Create Branch...")
        AppendMenuW(m, MF_STRING, ID_GIT_BRANCH_DELETE, "Delete Branch...")
        AppendMenuW(m, MF_STRING, ID_GIT_MERGE, "Merge Branch...")
        AppendMenuW(m, MF_SEPARATOR, 0, None)
        AppendMenuW(m, MF_STRING, ID_GIT_STAGE_ALL, "Stage All Changes")
        AppendMenuW(m, MF_STRING, ID_GIT_UNSTAGE_ALL, "Unstage All Changes")
        AppendMenuW(m, MF_STRING, ID_GIT_DISCARD_ALL, "Discard All Changes...")
        AppendMenuW(m, MF_SEPARATOR, 0, None)
        AppendMenuW(m, MF_STRING, ID_GIT_STASH, "Stash...")
        AppendMenuW(m, MF_STRING, ID_GIT_STASH_POP, "Pop Stash...")
        AppendMenuW(m, MF_SEPARATOR, 0, None)
        AppendMenuW(m, MF_STRING, ID_GIT_REMOTE_ADD, "Add Remote...")
        AppendMenuW(m, MF_STRING, ID_GIT_REMOTE_REMOVE, "Remove Remote...")
        AppendMenuW(m, MF_STRING, ID_GIT_PUBLISH, "Publish to GitHub...")
        AppendMenuW(m, MF_SEPARATOR, 0, None)
        AppendMenuW(m, MF_STRING, ID_GIT_LOG, "View History")
        AppendMenuW(m, MF_STRING, ID_VIEW_OUTPUT, "Show Git Output")
    r = window_rect(anchor)
    cmd = TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTALIGN, r.right, r.bottom, 0, P.hwnd, None)
    DestroyMenu(m)
    if cmd == ID_VIEW_OUTPUT:
        app.ShowOutput()
    elif cmd:
        git_commands.GitCommand(cmd)


def PanelProc(h, m, w, l):
    if m == WM_SIZE:
        Layout()
        return 0
    if m == WM_ERASEBKGND:
        fill_solid(w, client_rect(h), C.g_theme.panelBg)
        return 1
    if m == WM_PAINT:
        ps = PAINTSTRUCT()
        dc = BeginPaint(h, byref(ps))
        rc = client_rect(h)
        S = C.S
        SetBkMode(dc, TRANSPARENT)
        SetTextColor(dc, C.g_theme.panelFgDim)
        o = SelectObject(dc, C.g_uiFont)
        tr = RECT(S(10), 0, rc.right - S(70), S(30))
        draw_text(dc, "SOURCE CONTROL", tr, DT_LEFT | DT_VCENTER | DT_SINGLELINE)
        if not P.st.isRepo:
            mr = RECT(S(10), S(36), rc.right - S(10), S(36) + S(60))
            SetTextColor(dc, C.g_theme.panelFg)
            msg = ("Open a folder to use git features, or clone a repository." if not app.Folder() else
                   "The current folder is not a git repository. Initialize a repository or publish it directly "
                   "to GitHub.")
            draw_text(dc, msg, mr, DT_LEFT | DT_TOP | DT_WORDBREAK)
        SelectObject(dc, o)
        EndPaint(h, byref(ps))
        return 0
    if m == WM_CTLCOLOREDIT:
        dc = w
        SetTextColor(dc, C.g_theme.panelFg)
        SetBkColor(dc, C.g_theme.inputBg)
        return P.inputBrush or 0
    if m == WM_DRAWITEM:
        dis = DRAWITEMSTRUCT.from_address(l)
        C.DrawFlatButton(dis, dis.CtlID in (IDC_COMMIT, IDC_COMMITMENU, IDC_QUICK, IDC_INIT, IDC_OPENFOLDER,
                                            IDC_PUBLISH, IDC_CLONE))
        return 1
    if m == WM_COMMAND:
        id = LOWORD(w)
        if id == IDC_COMMIT:
            git_commands.GitCommand(ID_GIT_COMMIT)
        elif id == IDC_COMMITMENU:
            ShowMenu(P.commitMenu, True)
        elif id == IDC_QUICK:
            git_commands.GitCommand(ID_GIT_QUICK_COMMIT)
        elif id == IDC_MORE:
            ShowMenu(P.more, False)
        elif id == IDC_REFRESH:
            app.RefreshGit()
        elif id == IDC_INIT:
            git_commands.GitCommand(ID_GIT_INIT)
        elif id == IDC_CLONE:
            git_commands.GitCommand(ID_GIT_CLONE)
        elif id == IDC_PUBLISH:
            git_commands.GitCommand(ID_GIT_PUBLISH)
        elif id == IDC_OPENFOLDER:
            SendMessageW(app.Main(), WM_COMMAND, ID_FILE_OPENFOLDER, 0)
        return 0
    return DefWindowProcW(h, m, w, l)


def AddTip(tip, ctl, text):
    ti = TOOLINFOW()
    ti.cbSize = sizeof(TOOLINFOW)
    ti.uFlags = TTF_IDISHWND | TTF_SUBCLASS
    ti.hwnd = P.hwnd
    ti.uId = ctl
    ti.lpszText = text
    SendMessageW(tip, TTM_ADDTOOLW, 0, addressof(ti))


# ---- Scm namespace ----
def Create(parent):
    C.RegisterWindowClass("MyScmPanel", PanelProc, cursor=IDC_ARROW)
    C.RegisterWindowClass("MyScmList", ListProc, style=CS_DBLCLKS, cursor=IDC_ARROW)

    S = C.S
    P.inputBrush = CreateSolidBrush(C.g_theme.inputBg)
    P.hwnd = CreateWindowExW(0, "MyScmPanel", "", WS_CHILD | WS_CLIPCHILDREN, 0, 0, 0, 0, parent, None,
                             C.g_hinst, None)
    P.msg = CreateWindowExW(0, "EDIT", "", WS_CHILD | ES_MULTILINE | ES_AUTOVSCROLL | ES_WANTRETURN | WS_TABSTOP,
                            0, 0, 0, 0, P.hwnd, IDC_MSG, C.g_hinst, None)
    SendMessageW(P.msg, WM_SETFONT, C.g_uiFont or 0, False)
    SendMessageW(P.msg, EM_SETMARGINS, EC_LEFTMARGIN | EC_RIGHTMARGIN, MAKELPARAM(S(4), S(4)))
    C.Subclass(P.msg, MsgEditSub, 1, 0)
    P.commit = C.MakeButton(P.hwnd, IDC_COMMIT, "✓  Commit")
    P.commitMenu = C.MakeButton(P.hwnd, IDC_COMMITMENU, kIcoDown)
    P.quick = C.MakeButton(P.hwnd, IDC_QUICK, "Quick Commit")
    P.refresh = C.MakeButton(P.hwnd, IDC_REFRESH, kIcoRefresh)
    P.more = C.MakeButton(P.hwnd, IDC_MORE, kIcoMore)
    P.init = C.MakeButton(P.hwnd, IDC_INIT, "Initialize Repository")
    P.publish = C.MakeButton(P.hwnd, IDC_PUBLISH, "Publish to GitHub")
    P.clone = C.MakeButton(P.hwnd, IDC_CLONE, "Clone Repository")
    P.openFolder = C.MakeButton(P.hwnd, IDC_OPENFOLDER, "Open Folder")
    P.list = CreateWindowExW(0, "MyScmList", "", WS_CHILD | WS_VSCROLL | WS_TABSTOP, 0, 0, 0, 0, P.hwnd, None,
                             C.g_hinst, None)
    tip = CreateWindowExW(WS_EX_TOPMOST, TOOLTIPS_CLASSW, None, WS_POPUP | TTS_ALWAYSTIP | TTS_NOPREFIX, 0, 0, 0, 0,
                          P.hwnd, None, C.g_hinst, None)
    AddTip(tip, P.refresh, "Refresh")
    AddTip(tip, P.more, "More Actions...")
    AddTip(tip, P.commitMenu, "More Commit Actions...")
    AddTip(tip, P.quick, "Commit all changes as \"Update\", or push when there is nothing to commit")
    Layout()
    return P.hwnd


def Update(s):
    wasRepo = P.st.isRepo
    st = copy.copy(s)
    st.files = list(s.files)
    st.remotes = list(s.remotes)
    P.st = st
    SetWindowTextW(P.commit, "✓  Commit")
    SetWindowTextW(P.quick, "Push" if not s.files else "Quick Commit")
    RebuildRows()
    if wasRepo != s.isRepo or True:
        Layout()
    InvalidateRect(P.msg, None, False)


def Message():
    return text_of(P.msg)


def SetMessage(m):
    SetWindowTextW(P.msg, m)
    InvalidateRect(P.msg, None, True)


def FocusMessage():
    SetFocus(P.msg)


def ThemeChanged():
    if P.inputBrush:
        DeleteObject(P.inputBrush)
    P.inputBrush = CreateSolidBrush(C.g_theme.inputBg)
    SetWindowTheme(P.list, "DarkMode_Explorer" if C.g_settings.dark else "Explorer", None)
    SetWindowTheme(P.msg, "DarkMode_Explorer" if C.g_settings.dark else "Explorer", None)
    RedrawWindow(P.hwnd, None, None, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_FRAME)
