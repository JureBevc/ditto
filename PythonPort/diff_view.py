# Side-by-side diff viewer. Uses `git diff` with unlimited context so the whole file is shown.
# (port of diff_view.cpp)
import itertools
from array import array

import common as C
import win32 as W
import app
import git
import editor_view

K_CTX, K_CHG, K_DEL, K_ADD, K_SEP = range(5)


class Row:
    __slots__ = ("lno", "rno", "kind", "lt", "ll", "rt", "rl", "lhs", "lhe", "rhs", "rhe")

    def __init__(self):
        self.lno = 0; self.rno = 0  # 1-based line numbers, 0 = none
        self.kind = K_CTX
        self.lt = 0; self.ll = 0; self.rt = 0; self.rl = 0  # offsets into pool (UTF-16 units)
        self.lhs = 0; self.lhe = 0; self.rhs = 0; self.rhe = 0  # intraline highlight ranges (relative)


class Result:
    # pool is a str of UTF-16 code units (non-BMP characters are stored as surrogate pairs) so offsets/lengths
    # match the C++ std::wstring. While parsing, text is collected in _parts and joined by _Finish().
    def __init__(self):
        self.pool = ""
        self.message = ""
        self.rows = []
        self._parts = []
        self._len = 0
        self._maxLen = None

    def _Finish(self):
        if self._parts:
            self.pool = self.pool + "".join(self._parts)
            self._parts = []


class Data:
    def __init__(self):
        self.spec = app.DiffSpec()
        self.id = 0
        self.loading = True
        self.res = Result()
        self.blocks = []  # first row of each change block
        self.top = 0
        self.col = 0
        self.clientW = 0
        self.clientH = 0
        self.wheel = 0


g_ids = itertools.count(1)
_data = {}       # hwnd -> Data
_pending = None  # Data handed to WM_NCCREATE (lpCreateParams in C++)


def _tdiv(a, b):
    """C integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def HeaderH():
    return C.S(26)


def _ToUnits(s):
    """Splits non-BMP characters into surrogate pairs (one str element per UTF-16 code unit)."""
    if not s or max(s) <= "￿":
        return s
    a = array("H")
    a.frombytes(s.encode("utf-16-le", "surrogatepass"))
    return "".join(map(chr, a))


def ExpandTabs(s):
    if "\t" not in s:
        return s
    tab = C.g_settings.tabSize
    if "\r" not in s and "\n" not in s:
        return s.expandtabs(tab)
    o = []
    n = 0
    for c in s:
        if c == "\t":
            k = tab - n % tab
            o.append(" " * k)
            n += k
        else:
            o.append(c)
            n += 1
    return "".join(o)


def AddText(r, line):
    t = line
    if t and t[-1] == 0x0D:
        t = t[:-1]
    w = ExpandTabs(_ToUnits(C.Utf8ToWide(t)))
    off = r._len
    r._parts.append(w)
    r._len += len(w)
    return off, w


def Intraline(r, row, a, b):
    # a, b: the row's left / right text (pool[lt:lt+ll], pool[rt:rt+rl])
    la = row.ll; lb = row.rl; p = 0
    while p < la and p < lb and a[p] == b[p]:
        p += 1
    s = 0
    while s < la - p and s < lb - p and a[la - 1 - s] == b[lb - 1 - s]:
        s += 1
    row.lhs = p; row.lhe = la - s
    row.rhs = p; row.rhe = lb - s


def Flush(r, dele, add):
    n = max(len(dele), len(add))
    for i in range(n):
        row = Row()
        a = b = ""
        if i < len(dele):
            row.lno = dele[i][0]
            row.lt, a = AddText(r, dele[i][1])
            row.ll = r._len - row.lt
        if i < len(add):
            row.rno = add[i][0]
            row.rt, b = AddText(r, add[i][1])
            row.rl = r._len - row.rt
        row.kind = K_CHG if (i < len(dele) and i < len(add)) else (K_DEL if i < len(dele) else K_ADD)
        if row.kind == K_CHG:
            Intraline(r, row, a, b)
        r.rows.append(row)
    dele.clear()
    add.clear()


def _atoi(b, i):
    """atoi on bytes starting at index i (skips leading whitespace, optional sign)."""
    n = len(b)
    while i < n and b[i] in b" \t\n\v\f\r":
        i += 1
    neg = False
    if i < n and b[i] in b"+-":
        neg = b[i] == 0x2D
        i += 1
    v = 0
    while i < n and 0x30 <= b[i] <= 0x39:
        v = v * 10 + (b[i] - 0x30)
        i += 1
    return -v if neg else v


def ParseUnified(text, r):
    dele = []
    add = []
    inHunk = False
    l = 0
    rn = 0
    lines = text.split(b"\n")
    if lines and not lines[-1]:
        lines.pop()
    for line in lines:
        if line.startswith(b"@@"):
            Flush(r, dele, add)
            a = 1
            c = 1
            if line.startswith(b"@@ -"):  # sscanf_s("@@ -%d")
                i = 4
                n = len(line)
                while i < n and line[i] in b" \t\n\v\f\r":
                    i += 1
                j = i
                if j < n and line[j] in b"+-":
                    j += 1
                if j < n and 0x30 <= line[j] <= 0x39:
                    a = _atoi(line, i)
            plus = line.find(b" +")
            if plus >= 0:
                c = _atoi(line, plus + 2)
            l = a if a else 1
            rn = c if c else 1
            if r.rows:
                sep = Row()
                sep.kind = K_SEP
                r.rows.append(sep)
            inHunk = True
            continue
        if not inHunk:
            if line.startswith(b"Binary files"):
                r.message = "Binary files differ."
            continue
        if not line:
            continue
        c = line[0]
        if c == 0x20:  # ' '
            Flush(r, dele, add)
            row = Row()
            row.kind = K_CTX
            row.lno = l; l += 1
            row.rno = rn; rn += 1
            row.lt, _ = AddText(r, line[1:])
            row.rt = row.lt
            row.ll = row.rl = r._len - row.lt
            r.rows.append(row)
        elif c == 0x2D:  # '-'
            dele.append((l, line[1:]))
            l += 1
        elif c == 0x2B:  # '+'
            add.append((rn, line[1:]))
            rn += 1
        elif c == 0x64 and line.startswith(b"diff --git"):
            Flush(r, dele, add)
            inHunk = False
    Flush(r, dele, add)
    r._Finish()


def _ReadWholeFile(full):
    """CreateFileW/ReadFile as in the C++ Load(); returns (data bytes or None, message)."""
    f = W.CreateFileW(full, W.GENERIC_READ, W.FILE_SHARE_READ | W.FILE_SHARE_WRITE | W.FILE_SHARE_DELETE, None,
                      W.OPEN_EXISTING, 0, None)
    if not f or f == W.INVALID_HANDLE_VALUE:
        return None, "Cannot read file: " + C.FormatError(W.GetLastError())
    sz = W.c_int64(0)
    W.GetFileSizeEx(f, W.byref(sz))
    if sz.value > (64 << 20):
        W.CloseHandle(f)
        return None, "File is too large to show as a diff."
    data = b""
    if sz.value > 0:
        buf = W.create_string_buffer(sz.value)
        rd = W.DWORD(0)
        W.ReadFile(f, buf, sz.value, W.byref(rd), None)
        data = buf.raw[:rd.value]
    W.CloseHandle(f)
    return data, ""


def Load(s):
    r = Result()
    if s.kind == app.DiffSpec.Untracked:
        full = C.JoinPath(s.root, C.ToBackslashes(s.path))
        data, msg = _ReadWholeFile(full)
        if data is None:
            r.message = msg
            return r
        if b"\0" in data[:8000]:
            r.message = "Binary file."
            return r
        if len(data) >= 3 and data[0] == 0xEF and data[1] == 0xBB:
            data = data[3:]
        n = 1
        lines = data.split(b"\n")
        if lines and not lines[-1]:
            lines.pop()
        for ln in lines:
            row = Row()
            row.kind = K_ADD
            row.rno = n; n += 1
            row.rt, _ = AddText(r, ln)
            row.rl = r._len - row.rt
            r.rows.append(row)
        r._Finish()
        if not r.rows:
            r.message = "Empty file."
        return r
    ctx = "--unified=100000000"
    if s.kind == app.DiffSpec.Commit:
        if not s.parent:
            args = ["show", "--format=", "--no-color", "--no-ext-diff", "-M", ctx, s.commit]
        else:
            args = ["diff", "--no-color", "--no-ext-diff", "-M", ctx, s.parent, s.commit]
    else:
        args = ["diff", "--no-color", "--no-ext-diff", "-M", ctx]
        if s.kind == app.DiffSpec.Staged:
            args.insert(1, "--cached")
    args.append("--")
    args.append(s.path)
    if s.origPath:
        args.append(s.origPath)
    pr = git.Git(s.root, args, None, False)
    if pr.code != 0:
        r.message = "git diff failed:\n" + git.GitErrorText(pr)
        return r
    if len(pr.out) > (256 << 20):
        r.message = "Diff is too large to display."
        return r
    ParseUnified(pr.out, r)
    if not r.rows and not r.message:
        r.message = "No textual changes."
    return r


def From(h):
    return _data.get(h)


def PageRows(d):
    return max(1, _tdiv(d.clientH - HeaderH(), editor_view.g_ef.lh))


def _MaxLen(res):
    if res._maxLen is None:
        m = 0
        for row in itertools.islice(res.rows, 200000):
            if row.ll > m:
                m = row.ll
            if row.rl > m:
                m = row.rl
        res._maxLen = m
    return res._maxLen


def UpdateScroll(h, d):
    n = len(d.res.rows)
    page = PageRows(d)
    d.top = max(0, min(d.top, n - 1))
    si = W.SCROLLINFO()
    si.cbSize = W.sizeof(W.SCROLLINFO)
    si.fMask = W.SIF_RANGE | W.SIF_PAGE | W.SIF_POS
    si.nMax = max(0, n - 1 + page - 1)
    si.nPage = page
    si.nPos = d.top
    W.SetScrollInfo(h, W.SB_VERT, W.byref(si), True)
    maxLen = _MaxLen(d.res)
    paneCols = max(1, _tdiv(_tdiv(d.clientW, 2) - C.S(60), editor_view.g_ef.cw))
    si.nMax = maxLen + 4
    si.nPage = paneCols
    si.nPos = d.col
    W.SetScrollInfo(h, W.SB_HORZ, W.byref(si), True)


def ComputeBlocks(d):
    d.blocks = []
    rows = d.res.rows
    prevCh = False
    for i in range(len(rows)):
        ch = rows[i].kind != K_CTX and rows[i].kind != K_SEP
        if ch and not prevCh:
            d.blocks.append(i)
        prevCh = ch


def StartLoad(h):
    d = From(h)
    d.loading = True
    d.id = next(g_ids)
    id = d.id
    spec = d.spec.copy()
    W.InvalidateRect(h, None, False)

    def job():
        res = Load(spec)

        def done():
            if not W.IsWindow(h):
                return
            d = From(h)
            if not d or d.id != id:
                return
            first = not d.res.rows
            d.res = res
            d.loading = False
            ComputeBlocks(d)
            if first and d.blocks:
                d.top = max(0, d.blocks[0] - 3)
            UpdateScroll(h, d)
            W.InvalidateRect(h, None, False)

        C.RunOnUi(done)

    git.BgRun(job)


def FillR(dc, l, t, r, b, c):
    rc = W.RECT(l, t, r, b)
    W.SetBkColor(dc, c)
    W.ExtTextOutW(dc, 0, 0, W.ETO_OPAQUE, W.byref(rc), None, 0, None)


def DrawSide(dc, d, row, left, x0, x1, y):
    t = C.g_theme
    lh = editor_view.g_ef.lh
    cw = editor_view.g_ef.cw
    no = row.lno if left else row.rno
    has = (row.kind != K_ADD) if left else (row.kind != K_DEL)
    bg = t.bg
    if row.kind == K_SEP:
        bg = t.emptyBg
    elif not has:
        bg = t.emptyBg
    elif row.kind == K_CHG or (left and row.kind == K_DEL) or (not left and row.kind == K_ADD):
        bg = t.delBg if left else t.addBg
    FillR(dc, x0, y, x1, y + lh, bg)
    gutter = C.S(48)
    if row.kind == K_SEP:
        W.SetTextColor(dc, t.hunkFg)
        r = W.RECT(x0, y, x1, y + lh)
        W.DrawTextW(dc, "⋯", 1, W.byref(r), W.DT_CENTER | W.DT_VCENTER | W.DT_SINGLELINE)
        return
    if not has:
        return
    num = str(no)
    W.SetTextColor(dc, t.gutterFg)
    nr = W.RECT(x0, y, x0 + gutter - C.S(8), y + lh)
    W.DrawTextW(dc, num, len(num), W.byref(nr), W.DT_RIGHT | W.DT_VCENTER | W.DT_SINGLELINE)
    off = row.lt if left else row.rt
    ln = row.ll if left else row.rl
    tx = x0 + gutter
    hs = row.lhs if left else row.rhs
    he = row.lhe if left else row.rhe
    clip = W.RECT(tx, y, x1, y + lh)
    if row.kind == K_CHG and he > hs:
        a = tx + (hs - d.col) * cw
        b = tx + (he - d.col) * cw
        a = max(a, tx)
        b = min(b, x1)
        if b > a:
            FillR(dc, a, y, b, y + lh, t.delStrong if left else t.addStrong)
    if ln <= d.col:
        return
    start = d.col
    vis = max(0, _tdiv(x1 - tx, cw) + 1)
    cnt = min(ln - start, vis)
    if cnt <= 0:
        return
    tp = d.res.pool[off + start:off + start + cnt]
    if max(tp) < "ᄀ":
        dx = (W.c_int * cnt)(*([cw] * cnt))
    else:
        dx = (W.c_int * cnt)()
        k = 0
        while k < cnt:
            c = ord(tp[k])
            if 0xD800 <= c <= 0xDBFF and k + 1 < cnt:
                cp = 0x10000 + ((c - 0xD800) << 10) + (ord(tp[k + 1]) - 0xDC00)
                dx[k] = C.CharColumns(cp) * cw
                k += 1
                dx[k] = 0
            else:
                dx[k] = C.CharColumns(c) * cw
            k += 1
    W.SetTextColor(dc, t.fg)
    W.ExtTextOutW(dc, tx, y + C.S(1), W.ETO_CLIPPED, W.byref(clip), tp, cnt, dx)


def Paint(h, hdc):
    d = From(h)
    t = C.g_theme
    rc = W.RECT(0, 0, d.clientW, d.clientH)
    dc = W.CreateCompatibleDC(hdc)
    bmp = W.CreateCompatibleBitmap(hdc, max(1, d.clientW), max(1, d.clientH))
    ob = W.SelectObject(dc, bmp)
    FillR(dc, 0, 0, rc.right, rc.bottom, t.bg)
    W.SetBkMode(dc, W.TRANSPARENT)
    mid = _tdiv(d.clientW, 2)
    # header
    hh = HeaderH()
    FillR(dc, 0, 0, rc.right, hh, t.panelBg)
    W.SelectObject(dc, C.g_uiFont)
    W.SetTextColor(dc, t.panelFgDim)
    lname = "HEAD"
    rname = "Working Tree"
    k = d.spec.kind
    if k == app.DiffSpec.Unstaged:
        lname = "Index"; rname = "Working Tree"
    elif k == app.DiffSpec.Staged:
        lname = "HEAD"; rname = "Index"
    elif k == app.DiffSpec.Untracked:
        lname = "(new file)"; rname = "Working Tree"
    elif k == app.DiffSpec.Commit:
        lname = "(root)" if not d.spec.parent else d.spec.parent[:8]
        rname = d.spec.commit[:8]
    lt = lname + "  —  " + (d.spec.path if not d.spec.origPath else d.spec.origPath)
    rt = rname + "  —  " + d.spec.path + "      (F7 / Shift+F7: next / previous change)"
    lr = W.RECT(C.S(8), 0, mid - C.S(4), hh)
    rr = W.RECT(mid + C.S(8), 0, rc.right - C.S(4), hh)
    W.draw_text(dc, lt, lr, W.DT_LEFT | W.DT_VCENTER | W.DT_SINGLELINE | W.DT_END_ELLIPSIS | W.DT_NOPREFIX)
    W.draw_text(dc, rt, rr, W.DT_LEFT | W.DT_VCENTER | W.DT_SINGLELINE | W.DT_END_ELLIPSIS | W.DT_NOPREFIX)
    if d.loading or d.res.message:
        W.SetTextColor(dc, t.fg)
        mr = W.RECT(C.S(16), hh + C.S(16), rc.right - C.S(16), rc.bottom)
        msg = "Loading diff..." if d.loading else d.res.message
        W.draw_text(dc, msg, mr, W.DT_LEFT | W.DT_TOP | W.DT_WORDBREAK | W.DT_NOPREFIX)
    W.SelectObject(dc, editor_view.g_ef.normal)
    lh = editor_view.g_ef.lh
    rows = PageRows(d) + 1
    nrows = len(d.res.rows)
    for i in range(rows):
        ri = d.top + i
        if ri >= nrows:
            break
        row = d.res.rows[ri]
        y = hh + i * lh
        DrawSide(dc, d, row, True, 0, mid - 1, y)
        DrawSide(dc, d, row, False, mid + 1, rc.right, y)
    FillR(dc, mid - 1, hh, mid + 1, rc.bottom, t.border)
    W.BitBlt(hdc, 0, 0, rc.right, rc.bottom, dc, 0, 0, W.SRCCOPY)
    W.SelectObject(dc, ob)
    W.DeleteObject(bmp)
    W.DeleteDC(dc)


def ScrollTo(h, d, top):
    d.top = top
    UpdateScroll(h, d)
    W.InvalidateRect(h, None, False)


def JumpChange(h, d, next_):
    if not d.blocks:
        return
    cur = d.top + 3
    target = -1
    if next_:
        for b in d.blocks:
            if b > cur:
                target = b
                break
        if target < 0:
            target = d.blocks[0]
    else:
        for b in reversed(d.blocks):
            if b < cur:
                target = b
                break
        if target < 0:
            target = d.blocks[-1]
    ScrollTo(h, d, max(0, target - 3))


def Proc(h, m, w, l):
    global _pending
    if m == W.WM_NCCREATE and _pending is not None:
        _data[h] = _pending
        _pending = None
    d = From(h)
    if not d:
        return W.DefWindowProcW(h, m, w, l)
    if m == W.WM_NCDESTROY:
        _data.pop(h, None)
        return W.DefWindowProcW(h, m, w, l)
    if m == W.WM_SIZE:
        d.clientW = W.LOWORD(l)
        d.clientH = W.HIWORD(l)
        UpdateScroll(h, d)
        W.InvalidateRect(h, None, False)
        return 0
    if m == W.WM_ERASEBKGND:
        return 1
    if m == W.WM_PAINT:
        ps = W.PAINTSTRUCT()
        dc = W.BeginPaint(h, W.byref(ps))
        Paint(h, dc)
        W.EndPaint(h, W.byref(ps))
        return 0
    if m == W.WM_LBUTTONDOWN:
        W.SetFocus(h)
        return 0
    if m == W.WM_LBUTTONDBLCLK:
        ri = d.top + _tdiv(W.GET_Y_LPARAM(l) - HeaderH(), editor_view.g_ef.lh)
        line = 1
        if 0 <= ri < len(d.res.rows):
            r = d.res.rows[ri]
            line = r.rno if r.rno else (r.lno if r.lno else 1)
        full = C.JoinPath(d.spec.root, C.ToBackslashes(d.spec.path))
        if C.FileExists(full):
            app.OpenFile(full, line - 1)
        return 0
    if m == W.WM_MOUSEWHEEL:
        d.wheel += W.GET_WHEEL_DELTA_WPARAM(w)
        n = _tdiv(d.wheel, W.WHEEL_DELTA)
        d.wheel -= n * W.WHEEL_DELTA
        if W.GET_KEYSTATE_WPARAM(w) & W.MK_SHIFT:
            d.col = max(0, d.col - n * 8)
            UpdateScroll(h, d)
            W.InvalidateRect(h, None, False)
        else:
            ScrollTo(h, d, d.top - n * 3)
        return 0
    if m == W.WM_MOUSEHWHEEL:
        d.col = max(0, d.col + _tdiv(W.GET_WHEEL_DELTA_WPARAM(w), W.WHEEL_DELTA) * 8)
        UpdateScroll(h, d)
        W.InvalidateRect(h, None, False)
        return 0
    if m == W.WM_VSCROLL:
        si = W.SCROLLINFO()
        si.cbSize = W.sizeof(W.SCROLLINFO)
        si.fMask = W.SIF_ALL
        W.GetScrollInfo(h, W.SB_VERT, W.byref(si))
        t = d.top
        code = W.LOWORD(w)
        if code == W.SB_LINEUP:
            t -= 1
        elif code == W.SB_LINEDOWN:
            t += 1
        elif code == W.SB_PAGEUP:
            t -= PageRows(d)
        elif code == W.SB_PAGEDOWN:
            t += PageRows(d)
        elif code in (W.SB_THUMBTRACK, W.SB_THUMBPOSITION):
            t = si.nTrackPos
        ScrollTo(h, d, t)
        return 0
    if m == W.WM_HSCROLL:
        si = W.SCROLLINFO()
        si.cbSize = W.sizeof(W.SCROLLINFO)
        si.fMask = W.SIF_ALL
        W.GetScrollInfo(h, W.SB_HORZ, W.byref(si))
        code = W.LOWORD(w)
        if code == W.SB_LINELEFT:
            d.col -= 4
        elif code == W.SB_LINERIGHT:
            d.col += 4
        elif code == W.SB_PAGELEFT:
            d.col -= si.nPage
        elif code == W.SB_PAGERIGHT:
            d.col += si.nPage
        elif code in (W.SB_THUMBTRACK, W.SB_THUMBPOSITION):
            d.col = si.nTrackPos
        d.col = max(0, d.col)
        UpdateScroll(h, d)
        W.InvalidateRect(h, None, False)
        return 0
    if m == W.WM_GETDLGCODE:
        return W.DLGC_WANTARROWS
    if m == W.WM_KEYDOWN:
        if w == W.VK_UP:
            ScrollTo(h, d, d.top - 1)
        elif w == W.VK_DOWN:
            ScrollTo(h, d, d.top + 1)
        elif w == W.VK_PRIOR:
            ScrollTo(h, d, d.top - PageRows(d))
        elif w == W.VK_NEXT:
            ScrollTo(h, d, d.top + PageRows(d))
        elif w == W.VK_HOME:
            ScrollTo(h, d, 0)
        elif w == W.VK_END:
            ScrollTo(h, d, len(d.res.rows))
        elif w == W.VK_LEFT:
            d.col = max(0, d.col - 4)
            UpdateScroll(h, d)
            W.InvalidateRect(h, None, False)
        elif w == W.VK_RIGHT:
            d.col += 4
            UpdateScroll(h, d)
            W.InvalidateRect(h, None, False)
        elif w == W.VK_F7:
            JumpChange(h, d, W.GetKeyState(W.VK_SHIFT) >= 0)
        elif w == ord("N"):
            JumpChange(h, d, True)
        elif w == ord("P"):
            JumpChange(h, d, False)
        return 0
    return W.DefWindowProcW(h, m, w, l)


# ---- DiffView namespace ----

def Register():
    C.RegisterWindowClass("MyDiffView", Proc, style=W.CS_DBLCLKS, cursor=W.IDC_ARROW)


def Create(parent, spec):
    global _pending
    d = Data()
    d.spec = spec.copy()
    _pending = d
    h = W.CreateWindowExW(0, "MyDiffView", "", W.WS_CHILD | W.WS_VSCROLL | W.WS_HSCROLL | W.WS_CLIPSIBLINGS,
                          0, 0, 0, 0, parent, None, C.g_hinst, None)
    _pending = None
    if not h:
        return None
    StartLoad(h)
    return h


def Title(h):
    d = From(h)
    if not d:
        return ""
    name = C.FileNameOf(C.ToBackslashes(d.spec.path))
    k = d.spec.kind
    if k == app.DiffSpec.Unstaged:
        return name + " (Working Tree)"
    if k == app.DiffSpec.Staged:
        return name + " (Index)"
    if k == app.DiffSpec.Untracked:
        return name + " (Untracked)"
    return name + " (" + d.spec.commit[:7] + ")"


def Key(s):
    return "diff:" + str(int(s.kind)) + ":" + C.LowerW(s.root) + ":" + s.path + ":" + s.commit


def Reload(h):
    if From(h):
        StartLoad(h)
