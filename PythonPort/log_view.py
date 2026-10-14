# Commit history view: commit list on top, files changed by the selected commit below.
# (port of log_view.cpp)
from ctypes import Structure, sizeof, byref, memmove, c_int, c_void_p

import common as C
import win32 as W
import app
import git

# ---- ListView bits win32.py doesn't carry ----
WC_LISTVIEWW = "SysListView32"
LVM_FIRST = 0x1000
LVM_SETBKCOLOR = LVM_FIRST + 1
LVM_GETNEXTITEM = LVM_FIRST + 12
LVM_SETCOLUMNWIDTH = LVM_FIRST + 30
LVM_GETHEADER = LVM_FIRST + 31
LVM_SETTEXTCOLOR = LVM_FIRST + 36
LVM_SETTEXTBKCOLOR = LVM_FIRST + 38
LVM_SETITEMSTATE = LVM_FIRST + 43
LVM_SETITEMCOUNT = LVM_FIRST + 47
LVM_SETEXTENDEDLISTVIEWSTYLE = LVM_FIRST + 54
LVM_INSERTCOLUMNW = LVM_FIRST + 97
LVS_REPORT = 0x0001
LVS_SINGLESEL = 0x0004
LVS_SHOWSELALWAYS = 0x0008
LVS_OWNERDATA = 0x1000
LVS_EX_FULLROWSELECT = 0x00000020
LVS_EX_DOUBLEBUFFER = 0x00010000
LVIS_FOCUSED = 0x0001
LVIS_SELECTED = 0x0002
LVIF_TEXT = 0x0001
LVCF_WIDTH = 0x0002
LVCF_TEXT = 0x0004
LVNI_SELECTED = 0x0002
_U = 0xFFFFFFFF
NM_DBLCLK = (-3) & _U
NM_RETURN = (-4) & _U
NM_RCLICK = (-5) & _U
LVN_ITEMCHANGED = (-101) & _U
LVN_GETDISPINFOW = (-177) & _U


class LVITEMW(Structure):
    _fields_ = [("mask", W.UINT), ("iItem", c_int), ("iSubItem", c_int), ("state", W.UINT), ("stateMask", W.UINT),
                ("pszText", c_void_p), ("cchTextMax", c_int), ("iImage", c_int), ("lParam", W.LPARAM),
                ("iIndent", c_int), ("iGroupId", c_int), ("cColumns", W.UINT), ("puColumns", c_void_p),
                ("piColFmt", c_void_p), ("iGroup", c_int)]


class LVCOLUMNW(Structure):
    _fields_ = [("mask", W.UINT), ("fmt", c_int), ("cx", c_int), ("pszText", W.LPWSTR), ("cchTextMax", c_int),
                ("iSubItem", c_int), ("iImage", c_int), ("iOrder", c_int), ("cxMin", c_int), ("cxDefault", c_int),
                ("cxIdeal", c_int)]


class NMLVDISPINFOW(Structure):
    _fields_ = [("hdr", W.NMHDR), ("item", LVITEMW)]


class NMLISTVIEW(Structure):
    _fields_ = [("hdr", W.NMHDR), ("iItem", c_int), ("iSubItem", c_int), ("uNewState", W.UINT),
                ("uOldState", W.UINT), ("uChanged", W.UINT), ("ptAction", W.POINT), ("lParam", W.LPARAM)]


class NMITEMACTIVATE(Structure):
    _fields_ = [("hdr", W.NMHDR), ("iItem", c_int), ("iSubItem", c_int), ("uNewState", W.UINT),
                ("uOldState", W.UINT), ("uChanged", W.UINT), ("ptAction", W.POINT), ("lParam", W.LPARAM),
                ("uKeyFlags", W.UINT)]


def ListView_SetBkColor(lv, c): return W.SendMessageW(lv, LVM_SETBKCOLOR, 0, c)
def ListView_SetTextBkColor(lv, c): return W.SendMessageW(lv, LVM_SETTEXTBKCOLOR, 0, c)
def ListView_SetTextColor(lv, c): return W.SendMessageW(lv, LVM_SETTEXTCOLOR, 0, c)
def ListView_GetHeader(lv): return W.SendMessageW(lv, LVM_GETHEADER, 0, 0)
def ListView_SetItemCountEx(lv, n, flags): return W.SendMessageW(lv, LVM_SETITEMCOUNT, n, flags)
def ListView_SetColumnWidth(lv, i, cx): return W.SendMessageW(lv, LVM_SETCOLUMNWIDTH, i, cx)
def ListView_SetExtendedListViewStyle(lv, s): return W.SendMessageW(lv, LVM_SETEXTENDEDLISTVIEWSTYLE, 0, s)
def ListView_GetNextItem(lv, i, flags): return W.SendMessageW(lv, LVM_GETNEXTITEM, i & ((1 << 64) - 1), flags)


def ListView_InsertColumn(lv, i, col):
    return W.SendMessageW(lv, LVM_INSERTCOLUMNW, i, W.addressof(col))


def ListView_SetItemState(lv, i, data, mask):
    it = LVITEMW()
    it.stateMask = mask
    it.state = data
    return W.SendMessageW(lv, LVM_SETITEMSTATE, i & ((1 << 64) - 1), W.addressof(it))


def _tdiv(a, b):
    """C integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


class Commit:
    def __init__(self):
        self.hash = ""; self.shortHash = ""; self.author = ""; self.date = ""; self.refs = ""; self.subject = ""
        self.parents = []


class FileChange:
    def __init__(self):
        self.status = ""; self.path = ""; self.origPath = ""


class Data:
    def __init__(self):
        self.root = ""
        self.commits = None
        self.files = None
        self.list = []
        self.changes = []
        self.selected = -1
        self.gen = 0
        self.message = "Loading history..."


_data = {}       # hwnd -> Data
_pending = None  # Data handed to WM_NCCREATE (lpCreateParams in C++)


def From(h):
    return _data.get(h)


def ApplyColors(d):
    for lv in (d.commits, d.files):
        ListView_SetBkColor(lv, C.g_theme.bg)
        ListView_SetTextBkColor(lv, C.g_theme.bg)
        ListView_SetTextColor(lv, C.g_theme.fg)
        W.SetWindowTheme(lv, "DarkMode_Explorer" if C.g_settings.dark else "Explorer", None)
        hdr = ListView_GetHeader(lv)
        if hdr:
            W.SetWindowTheme(hdr, "DarkMode_ItemsView" if C.g_settings.dark else "Explorer", None)
        W.InvalidateRect(lv, None, True)


def LoadFiles(h, idx):
    d = From(h)
    if idx < 0 or idx >= len(d.list):
        return
    d.selected = idx
    c = d.list[idx]
    root = d.root
    d.gen += 1
    gen = d.gen

    def job():
        args = ["diff-tree", "-r", "--no-commit-id", "--name-status", "-M", "-z"]
        if not c.parents:
            args.append("--root"); args.append(c.hash)
        else:
            args.append(c.parents[0]); args.append(c.hash)
        r = git.Git(root, args, None, False)
        changes = []
        f = git.SplitNul(r.out)
        i = 0
        while i < len(f):
            if not f[i]:
                i += 1
                continue
            fc = FileChange()
            fc.status = C.Utf8ToWide(f[i][:1])
            if (f[i][0] == 0x52 or f[i][0] == 0x43) and i + 2 < len(f):  # 'R' / 'C'
                fc.origPath = C.Utf8ToWide(f[i + 1])
                fc.path = C.Utf8ToWide(f[i + 2])
                i += 2
            elif i + 1 < len(f):
                fc.path = C.Utf8ToWide(f[i + 1])
                i += 1
            changes.append(fc)
            i += 1

        def done():
            if not W.IsWindow(h):
                return
            d = From(h)
            if not d or d.gen != gen:
                return
            d.changes = changes
            ListView_SetItemCountEx(d.files, len(d.changes), 0)
            W.InvalidateRect(d.files, None, True)

        C.RunOnUi(done)

    git.BgRun(job)


def LoadCommits(h):
    d = From(h)
    root = d.root
    d.gen += 1
    gen = d.gen

    def job():
        r = git.Git(root, ["log", "-n", "5000", "--date=format:%Y-%m-%d %H:%M",
                           "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%P%x1f%D%x1f%s%x1e"], None, False)
        lst = []
        msg = ""
        if r.code != 0:
            msg = git.GitErrorText(r)
            if "does not have any commits" in msg:
                msg = "No commits yet."
        p = 0
        o = r.out
        while p < len(o):
            e = o.find(b"\x1e", p)
            if e < 0:
                break
            rec = o[p:e]
            p = e + 1
            rec = rec.lstrip(b"\r\n")
            parts = rec.split(b"\x1f")
            if len(parts) < 7:
                continue
            c = Commit()
            c.hash = C.Utf8ToWide(parts[0])
            c.shortHash = C.Utf8ToWide(parts[1])
            c.author = C.Utf8ToWide(parts[2])
            c.date = C.Utf8ToWide(parts[3])
            c.parents = [C.Utf8ToWide(x) for x in parts[4].split(b" ") if x]
            c.refs = C.Utf8ToWide(parts[5])
            c.subject = C.Utf8ToWide(parts[6])
            lst.append(c)

        def done():
            if not W.IsWindow(h):
                return
            d = From(h)
            if not d or d.gen != gen:
                return
            d.list = lst
            d.message = msg
            d.changes = []
            ListView_SetItemCountEx(d.files, 0, 0)
            ListView_SetItemCountEx(d.commits, len(d.list), 0)
            W.InvalidateRect(d.commits, None, True)
            W.InvalidateRect(h, None, True)
            if d.list:
                ListView_SetItemState(d.commits, 0, LVIS_SELECTED | LVIS_FOCUSED, LVIS_SELECTED | LVIS_FOCUSED)

        C.RunOnUi(done)

    git.BgRun(job)


def OpenChange(h, fi):
    d = From(h)
    if d.selected < 0 or fi < 0 or fi >= len(d.changes):
        return
    c = d.list[d.selected]
    s = app.DiffSpec()
    s.kind = app.DiffSpec.Commit
    s.root = d.root
    s.commit = c.hash
    s.parent = "" if not c.parents else c.parents[0]
    s.path = d.changes[fi].path
    s.origPath = d.changes[fi].origPath
    app.OpenDiff(s)


def CommitMenu(h, idx, pt):
    d = From(h)
    if idx < 0 or idx >= len(d.list):
        return
    c = d.list[idx]
    m = W.CreatePopupMenu()
    W.AppendMenuW(m, W.MF_STRING, 1, "Checkout (detached)")
    W.AppendMenuW(m, W.MF_STRING, 2, "Create Branch Here...")
    W.AppendMenuW(m, W.MF_STRING, 3, "Revert Commit")
    W.AppendMenuW(m, W.MF_SEPARATOR, 0, None)
    W.AppendMenuW(m, W.MF_STRING, 4, "Copy Commit Hash")
    W.AppendMenuW(m, W.MF_STRING, 5, "Copy Commit Message")
    W.AppendMenuW(m, W.MF_SEPARATOR, 0, None)
    W.AppendMenuW(m, W.MF_STRING, 6, "Refresh")
    cmd = W.TrackPopupMenu(m, W.TPM_RETURNCMD | W.TPM_RIGHTBUTTON, pt.x, pt.y, 0, h, None)
    W.DestroyMenu(m)

    def after(ok, last):
        if W.IsWindow(h):
            LoadCommits(h)

    import git_commands
    if cmd == 1:
        git_commands.RunGitOp("Checkout " + c.shortHash, d.root, [["checkout", c.hash]], True, after)
    elif cmd == 2:
        import dialogs
        ok, name = dialogs.InputBox(h, "Create Branch", "New branch name (from " + c.shortHash + "):", "")
        if ok and C.Trim(name):
            git_commands.RunGitOp("Create branch", d.root, [["checkout", "-b", C.Trim(name), c.hash]], True, after)
    elif cmd == 3:
        if C.MsgBox(h, "Revert commit " + c.shortHash + " \"" + c.subject +
                    "\"?\n\nThis creates a new commit that undoes its changes.",
                    W.MB_YESNO | W.MB_ICONQUESTION) == W.IDYES:
            git_commands.RunGitOp("Revert", d.root, [["revert", "--no-edit", c.hash]], True, after)
    elif cmd == 4:
        C.SetClipboardText(h, c.hash)
    elif cmd == 5:
        C.SetClipboardText(h, c.subject)
    elif cmd == 6:
        LoadCommits(h)


def Layout(h):
    d = From(h)
    rc = W.client_rect(h)
    S = C.S
    top = S(26)
    split = top + _tdiv((rc.bottom - top) * 62, 100)
    W.MoveWindow(d.commits, 0, top, rc.right, split - top, True)
    W.MoveWindow(d.files, 0, split + S(2), rc.right, rc.bottom - split - S(2), True)
    w = rc.right - W.GetSystemMetrics(W.SM_CXVSCROLL) - S(4)
    ListView_SetColumnWidth(d.commits, 0, S(80))
    ListView_SetColumnWidth(d.commits, 2, S(150))
    ListView_SetColumnWidth(d.commits, 3, S(120))
    ListView_SetColumnWidth(d.commits, 1, max(S(100), w - S(80) - S(150) - S(120)))
    ListView_SetColumnWidth(d.files, 0, S(60))
    ListView_SetColumnWidth(d.files, 1, max(S(100), w - S(60)))


def MakeList(parent, cols):
    lv = W.CreateWindowExW(0, WC_LISTVIEWW, "",
                           W.WS_CHILD | W.WS_VISIBLE | LVS_REPORT | LVS_SINGLESEL | LVS_SHOWSELALWAYS | LVS_OWNERDATA,
                           0, 0, 0, 0, parent, None, C.g_hinst, None)
    ListView_SetExtendedListViewStyle(lv, LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER)
    W.SendMessageW(lv, W.WM_SETFONT, C.g_uiFont or 0, False)
    for i, (text, width) in enumerate(cols):
        c = LVCOLUMNW()
        c.mask = LVCF_TEXT | LVCF_WIDTH
        c.pszText = text
        c.cx = C.S(width)
        ListView_InsertColumn(lv, i, c)
    return lv


def _CopyText(item, text):
    """wcsncpy_s(item.pszText, item.cchTextMax, text, _TRUNCATE)."""
    if not item.pszText or item.cchTextMax <= 0:
        return
    data = text.encode("utf-16-le", "surrogatepass")
    mx = (item.cchTextMax - 1) * 2
    if len(data) > mx:
        data = data[:mx]
    data += b"\0\0"
    memmove(item.pszText, data, len(data))


def Proc(h, m, w, l):
    global _pending
    if m == W.WM_NCCREATE and _pending is not None:
        _data[h] = _pending
        _pending = None
    d = From(h)
    if not d:
        return W.DefWindowProcW(h, m, w, l)
    if m == W.WM_CREATE:
        d.commits = MakeList(h, [("Commit", 80), ("Message", 400), ("Author", 150), ("Date", 120)])
        d.files = MakeList(h, [("Status", 60), ("File", 500)])
        ApplyColors(d)
        LoadCommits(h)
        return 0
    if m == W.WM_NCDESTROY:
        _data.pop(h, None)
        return W.DefWindowProcW(h, m, w, l)
    if m == W.WM_SIZE:
        Layout(h)
        return 0
    if m == W.WM_ERASEBKGND:
        rc = W.client_rect(h)
        W.fill_solid(w, rc, C.g_theme.panelBg)
        return 1
    if m == W.WM_PAINT:
        ps = W.PAINTSTRUCT()
        dc = W.BeginPaint(h, byref(ps))
        rc = W.client_rect(h)
        rc.bottom = C.S(26)
        rc.left += C.S(8)
        W.SetBkMode(dc, W.TRANSPARENT)
        W.SetTextColor(dc, C.g_theme.panelFgDim)
        o = W.SelectObject(dc, C.g_uiFont)
        t = "History of " + d.root + "   —   double-click a file to view its diff, right-click a commit for actions"
        if d.message:
            t = d.message
        W.draw_text(dc, t, rc, W.DT_LEFT | W.DT_VCENTER | W.DT_SINGLELINE | W.DT_END_ELLIPSIS | W.DT_NOPREFIX)
        W.SelectObject(dc, o)
        W.EndPaint(h, byref(ps))
        return 0
    if m == W.WM_NOTIFY:
        nm = W.NMHDR.from_address(l)
        code = nm.code
        frm = nm.hwndFrom
        if code == LVN_GETDISPINFOW:
            di = NMLVDISPINFOW.from_address(l)
            if not (di.item.mask & LVIF_TEXT):
                return 0
            tmp = ""
            ii = di.item.iItem
            if frm == d.commits and ii < len(d.list):
                c = d.list[ii]
                sub = di.item.iSubItem
                if sub == 0:
                    tmp = c.shortHash
                elif sub == 1:
                    tmp = c.subject if not c.refs else "[" + c.refs + "]  " + c.subject
                elif sub == 2:
                    tmp = c.author
                elif sub == 3:
                    tmp = c.date
            elif frm == d.files and ii < len(d.changes):
                f = d.changes[ii]
                tmp = f.status if di.item.iSubItem == 0 else \
                    (f.path if not f.origPath else f.origPath + "  →  " + f.path)
            _CopyText(di.item, tmp)
            return 0
        if code == LVN_ITEMCHANGED and frm == d.commits:
            iv = NMLISTVIEW.from_address(l)
            if (iv.uNewState & LVIS_SELECTED) and not (iv.uOldState & LVIS_SELECTED):
                LoadFiles(h, iv.iItem)
            return 0
        if (code == NM_DBLCLK or code == NM_RETURN) and frm == d.files:
            OpenChange(h, ListView_GetNextItem(d.files, -1, LVNI_SELECTED))
            return 0
        if code == NM_RCLICK and frm == d.commits:
            ia = NMITEMACTIVATE.from_address(l)
            pt = W.POINT(ia.ptAction.x, ia.ptAction.y)
            W.ClientToScreen(d.commits, byref(pt))
            CommitMenu(h, ia.iItem, pt)
            return 0
    return W.DefWindowProcW(h, m, w, l)


# ---- LogView namespace ----

def Register():
    C.RegisterWindowClass("MyLogView", Proc, cursor=W.IDC_ARROW)


def Create(parent, root):
    global _pending
    d = Data()
    d.root = root
    _pending = d
    h = W.CreateWindowExW(0, "MyLogView", "", W.WS_CHILD | W.WS_CLIPCHILDREN | W.WS_CLIPSIBLINGS, 0, 0, 0, 0,
                          parent, None, C.g_hinst, None)
    _pending = None
    return h or None


def Reload(h):
    if From(h):
        LoadCommits(h)


def ThemeChanged(h):
    if From(h):
        ApplyColors(From(h))
        W.InvalidateRect(h, None, True)
