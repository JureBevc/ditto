# Search side panel (Ctrl+Shift+F): find text in every file of the open folder.
# Files are listed with `git ls-files` (so .gitignore is honoured) or a directory walk, then searched on a worker
# thread. Results stream into a custom-drawn list while the search runs, so the UI never waits on it.
# (port of search_panel.cpp, namespace Search in app.h)
import os
import re
import stat
import threading

import common as C
import win32 as W
from win32 import *  # noqa: F401,F403
from resource import *  # noqa: F401,F403
import app
import git
import main_window as MW

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


IDC_QUERY = 4001
IDC_INCLUDE = 4002
IDC_EXCLUDE = 4003
IDC_CASE = 4004
IDC_WORD = 4005
IDC_RERUN = 4006
IDC_CLEAR = 4007

kMaxResults = 20000
kMaxFileSize = 64 << 20  # larger files are skipped
kTimerDebounce = 1
kTimerDrain = 2

kIcoDown = ""
kIcoRight = ""


class Match:
    __slots__ = ("line", "col", "len", "preview", "hlStart", "hlLen")

    def __init__(self):
        self.line = self.col = self.len = 0  # 0-based line; byte column and length within the line (UTF-8)
        self.preview = ""
        self.hlStart = self.hlLen = 0  # highlighted range in preview


class FileHits:
    __slots__ = ("order", "rel", "matches", "collapsed")

    def __init__(self, order, rel):
        self.order = order  # position in the sorted file list
        self.rel = rel
        self.matches = []
        self.collapsed = False


class Query:
    def __init__(self):
        self.needle = b""  # UTF-8
        self.matchCase = False
        self.wholeWord = False
        self.include = []
        self.exclude = []


class Job:
    """One search run, shared between the UI and its worker thread."""

    def __init__(self):
        self.cancel = False
        self.results = 0
        self.searched = 0
        self.fileCount = 0
        self.listed = False
        self.limited = False
        self.mu = threading.Lock()
        self.pending = []  # found but not yet taken by the UI


class Row:
    __slots__ = ("file", "match")

    def __init__(self, file, match):
        self.file = file
        self.match = match  # -1 = file header


class State:
    def __init__(self):
        self.hwnd = None
        self.query = self.include = self.exclude = self.caseBtn = self.wordBtn = self.rerun = self.clear = None
        self.list = None
        self.inputBrush = None
        self.matchCase = False
        self.wholeWord = False
        self.job = None
        self.searching = False
        self.root = ""     # folder the results belong to
        self.files = []    # FileHits sorted by order
        self.total = 0     # matches in files
        self.rows = []
        self.sel = -1
        self.top = 0
        self.hover = -1
        self.selRel = ""   # file of the selected row (rows are rebuilt as results stream in)
        self.tracking = False
        self.status = ""


P = State()


def _idiv(a, b):
    """C++ integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def GetText(h):
    return text_of(h)


# ---------------- file filtering ----------------

def SplitPatterns(s):
    out = []
    for cur in re.split("[,;]", s):
        p = C.LowerW(C.ToBackslashes(C.Trim(cur)))
        while p.startswith(".\\"):
            p = p[2:]
        while p.startswith("**\\"):
            p = p[3:]
        p = p.lstrip("\\").rstrip("\\")
        if p:
            out.append(p)
    return out


def PatternMatches(lowerRel, p):
    """pattern without a backslash: matches any path component ("*.cpp", "build", "node_modules");
    with one: matches the path from the root ("src\\ui", "src\\*.h"), including everything below a matching folder."""
    if "\\" not in p:
        wild = "*" in p or "?" in p
        for part in lowerRel.split("\\"):
            if (PathMatchSpecW(part, p) if wild else part == p):
                return True
        return False
    if len(lowerRel) > len(p) and lowerRel.startswith(p) and lowerRel[len(p)] == "\\":
        return True
    return bool(PathMatchSpecW(lowerRel, p)) or bool(PathMatchSpecW(lowerRel, p + "\\*"))


def Included(rel, q):
    if not q.include and not q.exclude:
        return True
    lower = C.LowerW(rel)
    if q.include and not any(PatternMatches(lower, p) for p in q.include):
        return False
    for p in q.exclude:
        if PatternMatches(lower, p):
            return False
    return True


# ---------------- listing ----------------

def InGitRepo(d):
    while True:
        if GetFileAttributesW(C.JoinPath(d, ".git")) != INVALID_FILE_ATTRIBUTES:
            return True
        up = C.DirOf(d)
        if not up or len(up) >= len(d):
            return False
        d = up


def GitList(root, out):
    if not InGitRepo(root):
        return False
    r = git.Git(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"], None, False)
    if r.code != 0:
        return False
    for s in git.SplitNul(r.out):
        if not s:
            continue
        rel = C.ToBackslashes(C.Utf8ToWide(s))
        if ".~mye-" not in C.FileNameOf(rel):
            out.append(rel)
    return True


def WalkList(root, job, out):
    stack = [""]
    while stack and not job.cancel:
        rel = stack.pop()
        try:
            it = os.scandir(C.JoinPath(root, rel) if rel else root)
        except OSError:
            continue
        with it:
            for e in it:
                name = e.name
                r = name if not rel else rel + "\\" + name
                try:
                    attrs = e.stat(follow_symlinks=False).st_file_attributes
                except OSError:
                    continue
                if attrs & stat.FILE_ATTRIBUTE_DIRECTORY:
                    if name == ".git" or name == "node_modules" or (attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                        continue
                    stack.append(r)
                elif ".~mye-" not in name:
                    out.append(r)


# ---------------- searching ----------------

_WORD = rb"0-9A-Za-z_\x80-\xff"


def ReadWholeFile(path):
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            if size <= 0 or size > kMaxFileSize:
                return None
            # read the head first so binary files (no BOM, NUL bytes) are skipped without reading them whole
            data = f.read(8192)
            if data[:2] not in (b"\xff\xfe", b"\xfe\xff") and b"\0" in data:
                return None
            if len(data) == 8192:
                data += f.read()
    except OSError:
        return None
    return data or None


def TextOf(data):
    """Text bytes of a file as UTF-8 (what the editor shows); None for binary files."""
    if data[:2] == b"\xff\xfe" or data[:2] == b"\xfe\xff":
        n = (len(data) - 2) // 2 * 2
        return data[2:2 + n].decode("utf-16-be" if data[0] == 0xFE else "utf-16-le", "surrogatepass").encode(
            "utf-8", "replace")
    skip = 3 if data[:3] == b"\xef\xbb\xbf" else 0
    if b"\0" in data[skip:skip + 8192]:
        return None
    return data[skip:] if skip else data


def _IsCont(b):
    return (b & 0xC0) == 0x80


def MakePreview(p, ls, le, at, me, m):
    a = ls
    while a < at and p[a] in (0x20, 0x09):
        a += 1
    lead = False
    if at - a > 60:
        a = at - 30
        while a < at and _IsCont(p[a]):
            a += 1
        lead = True
    b = le
    if b - me > 250:
        b = me + 250
        while b > me and _IsCont(p[b]):
            b -= 1
    pre = C.Utf8ToWide(p[a:at])
    mid = C.Utf8ToWide(p[at:me])
    post = C.Utf8ToWide(p[me:b])
    s = ("…" if lead else "") + pre + mid + post
    m.preview = "".join(" " if (c == "\t" or c < " ") else c for c in s)
    m.hlStart = len(pre) + (1 if lead else 0)
    m.hlLen = len(mid)


def CompilePattern(q):
    pat = re.escape(q.needle)
    if q.wholeWord:
        pat = b"(?<![" + _WORD + b"])" + pat + b"(?![" + _WORD + b"])"
    return re.compile(pat, 0 if q.matchCase else re.IGNORECASE)


def SearchText(p, q, pat, job, out):
    L = len(q.needle)
    n = len(p)
    if L == 0 or L > n:
        return
    counted = 0
    line = 0
    ls = 0
    for mt in pat.finditer(p):
        if job.cancel:
            return
        at = mt.start()
        k = p.count(b"\n", counted, at)
        if k:
            line += k
            ls = p.rfind(b"\n", counted, at) + 1
        counted = at
        le = p.find(b"\n", at)
        if le < 0:
            le = n
        if le > ls and p[le - 1] == 0x0D:
            le -= 1
        job.results += 1
        if job.results > kMaxResults:
            job.limited = True
            job.cancel = True
            return
        m = Match()
        m.line = line
        m.col = at - ls
        m.len = L
        MakePreview(p, ls, le, at, min(at + L, le), m)
        out.matches.append(m)


def RunJob(job, root, q):
    all_ = []
    if not GitList(root, all_):
        WalkList(root, job, all_)
    if job.cancel:
        return
    import palette
    files = sorted({f for f in all_ if Included(f, q)}, key=palette._SortKey)  # ls-files lists conflicts per stage
    job.fileCount = len(files)
    job.listed = True
    pat = CompilePattern(q)
    for i, rel in enumerate(files):
        if job.cancel:
            return
        fh = FileHits(i, rel)
        data = ReadWholeFile(C.JoinPath(root, rel))
        if data is not None:
            text = TextOf(data)
            if text is not None:
                SearchText(text, q, pat, job, fh)
        job.searched += 1
        if fh.matches:
            with job.mu:
                job.pending.append(fh)


# ---------------- results list ----------------

def RowH():
    return C.S(22)


def VisibleRows():
    return max(1, _idiv(client_rect(P.list).bottom, RowH()))


def UpdateListScroll():
    vis = VisibleRows()
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
    selRow = P.rows[P.sel] if 0 <= P.sel < len(P.rows) else None
    selRel = P.selRel if selRow is not None else ""
    P.rows = []
    P.sel = -1
    for f, fh in enumerate(P.files):
        selFile = selRow is not None and fh.rel == selRel
        if selFile and selRow.match < 0:
            P.sel = len(P.rows)
        P.rows.append(Row(f, -1))
        if fh.collapsed:
            continue
        for m in range(len(fh.matches)):
            if selFile and selRow.match == m:
                P.sel = len(P.rows)
            P.rows.append(Row(f, m))
    UpdateListScroll()
    InvalidateRect(P.list, None, False)


def Plural(n, one, many):
    return str(n) + " " + (one if n == 1 else many)


def StatusRect():
    rc = client_rect(P.hwnd)
    lr = window_rect(P.list)
    MapWindowPoints(None, P.hwnd, byref(lr), 2)
    return RECT(C.S(10), lr.top - C.S(26), rc.right - C.S(8), lr.top - C.S(2))


def UpdateStatus():
    s = ""
    if not P.root:
        s = "Open a folder to search in files."
    elif P.searching:
        j = P.job
        s = "Searching"
        if j.listed:
            s += " " + str(j.searched) + "/" + str(j.fileCount) + " files"
        s += "... " + Plural(P.total, "result", "results")
    elif P.job:
        if P.total == 0:
            s = "No results found."
        else:
            s = Plural(P.total, "result", "results") + " in " + Plural(len(P.files), "file", "files")
        if P.job.limited:
            s += " (limited to " + str(kMaxResults) + ")"
    if s == P.status:
        return
    P.status = s
    InvalidateRect(P.hwnd, byref(StatusRect()), True)


def Drain():
    """Moves results found by the worker into the list."""
    if not P.job:
        return
    with P.job.mu:
        got = P.job.pending
        P.job.pending = []
    orders = [f.order for f in P.files]
    for f in got:
        P.total += len(f.matches)
        k = _upper_bound(orders, f.order)
        orders.insert(k, f.order)
        P.files.insert(k, f)
    if got:
        RebuildRows()
    UpdateStatus()


def _upper_bound(a, x):
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if x < a[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo


def ClearResults():
    if P.job:
        P.job.cancel = True
    P.job = None
    P.searching = False
    KillTimer(P.hwnd, kTimerDrain)
    KillTimer(P.hwnd, kTimerDebounce)
    P.files = []
    P.rows = []
    P.total = 0
    P.sel = -1
    P.top = 0
    P.hover = -1
    UpdateListScroll()
    InvalidateRect(P.list, None, False)
    UpdateStatus()


def StartSearch():
    ClearResults()
    text = GetText(P.query)
    P.root = app.Folder()
    if not P.root or not text:
        UpdateStatus()
        return
    q = Query()
    q.needle = C.WideToUtf8(text)
    q.matchCase = P.matchCase
    q.wholeWord = P.wholeWord
    q.include = SplitPatterns(GetText(P.include))
    q.exclude = SplitPatterns(GetText(P.exclude))
    job = Job()
    P.job = job
    P.searching = True
    SetTimer(P.hwnd, kTimerDrain, 100, None)
    root = P.root

    def worker():
        try:
            RunJob(job, root, q)
        finally:
            def done():
                if job is not P.job:
                    return
                P.searching = False
                KillTimer(P.hwnd, kTimerDrain)
                Drain()
                UpdateStatus()
            C.RunOnUi(done)

    threading.Thread(target=worker, daemon=True).start()
    UpdateStatus()


def OpenMatch(row):
    if row < 0 or row >= len(P.rows):
        return
    r = P.rows[row]
    f = P.files[r.file]
    full = C.JoinPath(P.root, f.rel)
    if r.match < 0:
        app.OpenFile(full)
        return
    m = f.matches[r.match]
    line, col, len_ = m.line, m.col, m.len
    app.OpenFile(full)  # may pump messages (error box), so nothing from P is used after this
    ed = MW.ActiveEditor()
    if ed and C.PathEqualsI(ed.buf.path, full):
        ed.SelectInLine(line, col, len_)


def Select(i):
    if not P.rows:
        return
    P.sel = max(0, min(i, len(P.rows) - 1))
    P.selRel = P.files[P.rows[P.sel].file].rel
    vis = VisibleRows()
    if P.sel < P.top:
        P.top = P.sel
    elif P.sel >= P.top + vis:
        P.top = P.sel - vis + 1
    UpdateListScroll()
    InvalidateRect(P.list, None, False)


def ToggleCollapse(fileIdx):
    P.files[fileIdx].collapsed = not P.files[fileIdx].collapsed
    RebuildRows()


def SetAllCollapsed(c):
    for f in P.files:
        f.collapsed = c
    RebuildRows()


def MatchBg():
    return RGB(110, 76, 34) if C.g_settings.dark else RGB(250, 214, 158)


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
        f = P.files[r.file]
        row = RECT(0, y, rc.right, y + rh)
        if i == P.sel or i == P.hover:
            fill_solid(dc, row, t.selBg if (i == P.sel and focused) else t.hoverBg)
        if r.match < 0:
            cr = RECT(S(4), y, S(20), y + rh)
            SelectObject(dc, C.g_iconFont)
            SetTextColor(dc, t.panelFg)
            draw_text(dc, kIcoRight if f.collapsed else kIcoDown, cr, DT_CENTER | DT_VCENTER | DT_SINGLELINE)
            cnt = str(len(f.matches))
            SelectObject(dc, C.g_uiFont)
            sz = text_extent(dc, cnt)
            bw = max(sz.cx + S(10), S(18))
            br = RECT(rc.right - S(8) - bw, y + S(3), rc.right - S(8), y + rh - S(3))
            bb = CreateSolidBrush(RGB(77, 77, 77) if C.g_settings.dark else RGB(196, 196, 196))
            obr = SelectObject(dc, bb)
            op = SelectObject(dc, GetStockObject(NULL_PEN))
            RoundRect(dc, br.left, br.top, br.right, br.bottom, S(12), S(12))
            SelectObject(dc, op)
            SelectObject(dc, obr)
            DeleteObject(bb)
            draw_text(dc, cnt, br, DT_CENTER | DT_VCENTER | DT_SINGLELINE)
            sl = f.rel.rfind("\\")
            name = f.rel if sl < 0 else f.rel[sl + 1:]
            dir = "" if sl < 0 else f.rel[:sl]
            right = br.left - S(6)
            nr = RECT(S(22), y, right, y + rh)
            SetTextColor(dc, t.panelFg)
            nsz = text_extent(dc, name)
            draw_text(dc, name, nr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX)
            if dir and S(22) + nsz.cx + S(8) < right:
                dr = RECT(S(22) + nsz.cx + S(6), y, right, y + rh)
                SetTextColor(dc, t.panelFgDim)
                draw_text(dc, dir, dr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX)
        else:
            m = f.matches[r.match]
            SelectObject(dc, C.g_uiFont)
            tm = TEXTMETRICW()
            GetTextMetricsW(dc, byref(tm))
            clip = RECT(S(30), y, rc.right - S(4), y + rh)
            x = clip.left
            ty = y + _idiv(rh - tm.tmHeight, 2)
            s = m.preview
            parts = (s[:m.hlStart], s[m.hlStart:m.hlStart + m.hlLen], s[m.hlStart + m.hlLen:])
            for k in range(3):
                if x >= clip.right:
                    break
                part = parts[k]
                if not part:
                    continue
                sz = text_extent(dc, part)
                if k == 1:
                    fill_solid(dc, RECT(x, y + S(2), min(x + sz.cx, clip.right), y + rh - S(2)), MatchBg())
                SetTextColor(dc, t.panelFg)
                ext_text_out(dc, x, ty, ETO_CLIPPED, clip, part)
                x += sz.cx
    BitBlt(hdc, 0, 0, rc.right, rc.bottom, dc, 0, 0, SRCCOPY)
    SelectObject(dc, ob)
    DeleteObject(bmp)
    DeleteDC(dc)


def ListContextMenu(row, x, y):
    m = CreatePopupMenu()
    if row >= 0:
        AppendMenuW(m, MF_STRING, 1, "Open")
        AppendMenuW(m, MF_STRING, 2, "Copy Path")
        AppendMenuW(m, MF_STRING, 3, "Reveal in File Explorer")
        AppendMenuW(m, MF_SEPARATOR, 0, None)
    AppendMenuW(m, MF_STRING, 4, "Collapse All")
    AppendMenuW(m, MF_STRING, 5, "Expand All")
    cmd = TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, 0, P.list, None)
    DestroyMenu(m)
    if cmd == 4 or cmd == 5:
        SetAllCollapsed(cmd == 4)
        return
    if row < 0 or row >= len(P.rows):
        return
    full = C.JoinPath(P.root, P.files[P.rows[row].file].rel)
    if cmd == 1:
        OpenMatch(row)
    elif cmd == 2:
        C.SetClipboardText(P.list, full)
    elif cmd == 3:
        C.RevealInExplorer(full)


def FocusEditor():
    ed = MW.ActiveEditor()
    if ed:
        SetFocus(ed.hwnd)


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
    if m == WM_SETFOCUS:
        if P.sel < 0 and P.rows:
            Select(0)
        InvalidateRect(h, None, False)
        return 0
    if m == WM_KILLFOCUS:
        InvalidateRect(h, None, False)
        return 0
    if m == WM_GETDLGCODE:
        return DLGC_WANTARROWS | DLGC_WANTCHARS
    if m == WM_MOUSEMOVE:
        i = P.top + _idiv(GET_Y_LPARAM(l), RowH())
        if i >= len(P.rows):
            i = -1
        if i != P.hover:
            P.hover = i
            InvalidateRect(h, None, False)
        if not P.tracking:
            tme = TRACKMOUSEEVENT(sizeof(TRACKMOUSEEVENT), TME_LEAVE, h, 0)
            TrackMouseEvent(byref(tme))
            P.tracking = True
        return 0
    if m == WM_MOUSELEAVE:
        P.tracking = False
        P.hover = -1
        InvalidateRect(h, None, False)
        return 0
    if m == WM_LBUTTONDOWN:
        i = P.top + _idiv(GET_Y_LPARAM(l), RowH())
        if i >= len(P.rows):
            SetFocus(h)
            return 0
        Select(i)
        SetFocus(h)
        if P.rows[i].match < 0:
            ToggleCollapse(P.rows[i].file)
        else:
            OpenMatch(i)
        return 0
    if m == WM_RBUTTONDOWN:
        i = P.top + _idiv(GET_Y_LPARAM(l), RowH())
        if i < len(P.rows):
            Select(i)
        SetFocus(h)
        return 0
    if m == WM_CONTEXTMENU:
        x, y = GET_X_LPARAM(l), GET_Y_LPARAM(l)
        if x == -1 and y == -1:
            pt = POINT(C.S(30), (max(P.sel, P.top) - P.top + 1) * RowH())
            ClientToScreen(h, byref(pt))
            x, y = pt.x, pt.y
        ListContextMenu(P.sel, x, y)
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
        i = P.sel
        if w == VK_DOWN:
            Select(i + 1)
        elif w == VK_UP:
            if i <= 0:
                SetFocus(P.query)
            else:
                Select(i - 1)
        elif w == VK_NEXT:
            Select(i + VisibleRows())
        elif w == VK_PRIOR:
            Select(i - VisibleRows())
        elif w == VK_HOME:
            Select(0)
        elif w == VK_END:
            Select(n - 1)
        elif w == VK_RETURN:
            if 0 <= i < n:
                if P.rows[i].match < 0:
                    ToggleCollapse(P.rows[i].file)
                else:
                    OpenMatch(i)
        elif w == VK_LEFT:
            if 0 <= i < n:
                r = P.rows[i]
                if r.match >= 0:
                    while i > 0 and P.rows[i].match >= 0:
                        i -= 1
                    Select(i)
                elif not P.files[r.file].collapsed:
                    ToggleCollapse(r.file)
        elif w == VK_RIGHT:
            if 0 <= i < n and P.rows[i].match < 0 and P.files[P.rows[i].file].collapsed:
                ToggleCollapse(P.rows[i].file)
        elif w == VK_TAB:
            SetFocus(P.exclude if GetKeyState(VK_SHIFT) < 0 else P.query)
        elif w == VK_ESCAPE:
            FocusEditor()
        return 0
    if m == WM_CHAR:
        return 0
    return DefWindowProcW(h, m, w, l)


# ---------------- panel ----------------

def EditSub(h, m, w, l, uid, ref):
    if m == WM_KEYDOWN:
        if w == VK_RETURN:
            StartSearch()
            return 0
        if w == VK_ESCAPE:
            FocusEditor()
            return 0
        if w == VK_DOWN and h == P.query and P.rows:
            SetFocus(P.list)
            return 0
        if w == VK_TAB:
            back = GetKeyState(VK_SHIFT) < 0
            order = (P.query, P.include, P.exclude, P.list)
            k = 0 if h == P.query else 1 if h == P.include else 2
            SetFocus(order[(k + (3 if back else 1)) % 4])
            return 0
        if w == ord('A') and GetKeyState(VK_CONTROL) < 0:
            SendMessageW(h, EM_SETSEL, 0, -1)
            return 0
    if m == WM_CHAR and w in (VK_RETURN, VK_ESCAPE, VK_TAB, 1):
        return 0
    return DefSubclassProc(h, m, w, l)


def LayoutPanel():
    rc = client_rect(P.hwnd)
    S = C.S
    W_ = rc.right
    pad, bw, eh = S(8), S(26), S(24)
    MoveWindow(P.rerun, W_ - pad - 2 * bw, S(3), bw, bw - S(2), True)
    MoveWindow(P.clear, W_ - pad - bw, S(3), bw, bw - S(2), True)
    y = S(30)
    ew = max(S(40), W_ - 2 * pad - 2 * bw - S(4))
    MoveWindow(P.query, pad, y, ew, eh, True)
    MoveWindow(P.caseBtn, pad + ew + S(4), y, bw, eh, True)
    MoveWindow(P.wordBtn, pad + ew + S(4) + bw, y, bw, eh, True)
    y += eh + S(6)
    MoveWindow(P.include, pad, y, max(S(40), W_ - 2 * pad), eh, True)
    y += eh + S(6)
    MoveWindow(P.exclude, pad, y, max(S(40), W_ - 2 * pad), eh, True)
    y += eh + S(28)
    MoveWindow(P.list, 0, y, W_, max(0, rc.bottom - y), True)
    InvalidateRect(P.hwnd, None, True)


def PanelProc(h, m, w, l):
    if m == WM_SIZE:
        LayoutPanel()
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
        draw_text(dc, "SEARCH", tr, DT_LEFT | DT_VCENTER | DT_SINGLELINE)
        sr = StatusRect()
        draw_text(dc, P.status, sr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX)
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
        toggled = (dis.hwndItem == P.caseBtn and P.matchCase) or (dis.hwndItem == P.wordBtn and P.wholeWord)
        C.DrawFlatButton(dis, False, toggled)
        return 1
    if m == WM_TIMER:
        if w == kTimerDebounce:
            KillTimer(h, kTimerDebounce)
            StartSearch()
        elif w == kTimerDrain:
            Drain()
        return 0
    if m == WM_COMMAND:
        id = LOWORD(w)
        if id in (IDC_QUERY, IDC_INCLUDE, IDC_EXCLUDE):
            if HIWORD(w) == EN_CHANGE:
                SetTimer(h, kTimerDebounce, 300, None)
        elif id == IDC_CASE:
            P.matchCase = not P.matchCase
            InvalidateRect(P.caseBtn, None, False)
            StartSearch()
        elif id == IDC_WORD:
            P.wholeWord = not P.wholeWord
            InvalidateRect(P.wordBtn, None, False)
            StartSearch()
        elif id == IDC_RERUN:
            StartSearch()
        elif id == IDC_CLEAR:
            SetWindowTextW(P.query, "")
            ClearResults()
            SetFocus(P.query)
        return 0
    return DefWindowProcW(h, m, w, l)


def MakeEdit(id, cue):
    e = CreateWindowExW(0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL | WS_BORDER, 0, 0, 0, 0,
                        P.hwnd, id, C.g_hinst, None)
    SendMessageW(e, WM_SETFONT, C.g_uiFont or 0, False)
    buf = create_unicode_buffer(cue)
    SendMessageW(e, EM_SETCUEBANNER, True, addressof(buf))
    C.Subclass(e, EditSub, 1, 0)
    return e


def AddTip(tip, ctl, text):
    ti = TOOLINFOW()
    ti.cbSize = sizeof(TOOLINFOW)
    ti.uFlags = TTF_IDISHWND | TTF_SUBCLASS
    ti.hwnd = P.hwnd
    ti.uId = ctl
    ti.lpszText = text
    SendMessageW(tip, TTM_ADDTOOLW, 0, addressof(ti))


# ---- Search namespace ----
def Create(parent):
    C.RegisterWindowClass("MySearchPanel", PanelProc, cursor=IDC_ARROW)
    C.RegisterWindowClass("MySearchList", ListProc, cursor=IDC_ARROW)

    P.inputBrush = CreateSolidBrush(C.g_theme.inputBg)
    P.hwnd = CreateWindowExW(0, "MySearchPanel", "", WS_CHILD | WS_CLIPCHILDREN, 0, 0, 0, 0, parent, None,
                             C.g_hinst, None)
    P.query = MakeEdit(IDC_QUERY, "Search")
    P.include = MakeEdit(IDC_INCLUDE, "Files to include (e.g. *.cpp, src)")
    P.exclude = MakeEdit(IDC_EXCLUDE, "Files to exclude")
    P.caseBtn = C.MakeButton(P.hwnd, IDC_CASE, "Aa")
    P.wordBtn = C.MakeButton(P.hwnd, IDC_WORD, "ab|")
    P.rerun = C.MakeButton(P.hwnd, IDC_RERUN, "")
    P.clear = C.MakeButton(P.hwnd, IDC_CLEAR, "")
    P.list = CreateWindowExW(0, "MySearchList", "", WS_CHILD | WS_VISIBLE | WS_VSCROLL | WS_TABSTOP, 0, 0, 0, 0,
                             P.hwnd, None, C.g_hinst, None)
    tip = CreateWindowExW(WS_EX_TOPMOST, TOOLTIPS_CLASSW, None, WS_POPUP | TTS_ALWAYSTIP | TTS_NOPREFIX, 0, 0, 0, 0,
                          P.hwnd, None, C.g_hinst, None)
    AddTip(tip, P.caseBtn, "Match Case")
    AddTip(tip, P.wordBtn, "Match Whole Word")
    AddTip(tip, P.rerun, "Search Again")
    AddTip(tip, P.clear, "Clear Search Results")
    LayoutPanel()
    UpdateStatus()
    return P.hwnd


def Focus(seed):
    """Focus the query box; a non-empty seed replaces the query and searches."""
    if seed and seed != GetText(P.query):
        SetWindowTextW(P.query, seed)
        StartSearch()
    elif not P.job and GetWindowTextLengthW(P.query) > 0:
        StartSearch()
    SetFocus(P.query)
    SendMessageW(P.query, EM_SETSEL, 0, -1)


def SetRoot(folder):
    if folder == P.root and P.job:
        return
    ClearResults()
    P.root = folder
    UpdateStatus()


def ThemeChanged():
    if P.inputBrush:
        DeleteObject(P.inputBrush)
    P.inputBrush = CreateSolidBrush(C.g_theme.inputBg)
    for e in (P.query, P.include, P.exclude):
        SetWindowTheme(e, "DarkMode_CFD" if C.g_settings.dark else "Explorer", None)
    SetWindowTheme(P.list, "DarkMode_Explorer" if C.g_settings.dark else "Explorer", None)
    LayoutPanel()
    RedrawWindow(P.hwnd, None, None, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_FRAME)
