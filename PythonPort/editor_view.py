# Custom-drawn text editor control. Only visible rows are measured/highlighted/drawn.
# Port of editor_view.h + editor_view.cpp (window plumbing, row/column mapping, word wrap, painting and
# scrolling) + editor_input.cpp (message handling, keyboard/mouse input, editing commands, clipboard and search).
import bisect
import itertools
import re
import threading

import common as C
import win32 as W
from win32 import *  # noqa: F401,F403
import resource as R
import text_buffer
import md_highlight as MD


class EditorFonts:
    def __init__(self):
        self.normal = None
        self.bold = None
        self.italic = None
        self.cw = 8
        self.lh = 16
        self.ascent = 12


g_ef = EditorFonts()

kLongLine = 64 * 1024        # lines longer than this use column checkpoints
kWrapMaxLine = 256 * 1024    # longer lines are never wrapped
kWrapMaxDoc = 64 << 20       # word wrap only for documents up to this size
kTimerIndex = 1
kTimerDrag = 2
kNotFound = W.UINT64_MAX

_WHEEL_PAGESCROLL = 0xFFFFFFFF
_SPECIAL = re.compile(rb"[\t\x80-\xff]")  # bytes that are not one plain column


def _tdiv(a, b):
    """C-style integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def _BufReplace(buf, off, delLen, s, caretBefore, mergeTyping=False):
    """TextBuffer.Replace(off, delLen, s, n, caretBefore, mergeTyping) with n = len(s)."""
    return buf.Replace(off, delLen, s, len(s), caretBefore, mergeTyping)


def CreateEditorFonts():
    """From g_settings.fontName/fontSize and g_dpi."""
    old_normal, old_bold, old_italic = g_ef.normal, g_ef.bold, g_ef.italic
    lf = LOGFONTW()
    lf.lfHeight = -MulDiv(C.g_settings.fontSize, C.g_dpi, 72)
    lf.lfWeight = FW_NORMAL
    lf.lfCharSet = DEFAULT_CHARSET
    lf.lfQuality = CLEARTYPE_QUALITY
    lf.lfPitchAndFamily = FIXED_PITCH | FF_MODERN
    lf.lfFaceName = C.g_settings.fontName[:31]
    g_ef.normal = CreateFontIndirectW(byref(lf))
    lf.lfWeight = FW_BOLD
    g_ef.bold = CreateFontIndirectW(byref(lf))
    lf.lfWeight = FW_NORMAL
    lf.lfItalic = 1
    g_ef.italic = CreateFontIndirectW(byref(lf))
    dc = GetDC(None)
    o = SelectObject(dc, g_ef.normal)
    tm = TEXTMETRICW()
    GetTextMetricsW(dc, byref(tm))
    sz = SIZE()
    GetTextExtentPoint32W(dc, "MMMMMMMMMM", 10, byref(sz))
    SelectObject(dc, o)
    ReleaseDC(None, dc)
    g_ef.cw = max(1, (sz.cx + 5) // 10)
    g_ef.lh = tm.tmHeight + tm.tmExternalLeading + MulDiv(3, C.g_dpi, 96)
    g_ef.ascent = tm.tmAscent
    if old_normal:
        DeleteObject(old_normal)
        DeleteObject(old_bold)
        DeleteObject(old_italic)


def WrapLine(p, n, wrapCols, tab, segs, i0=0):
    """Wraps p[i0:i0+n]; fills segs with segment starts relative to i0."""
    segs.clear()
    segs.append(0)
    # fast path: without tabs every char is at most as wide as its byte count
    if n <= wrapCols and p.find(b"\t", i0, i0 + n) < 0:
        return
    Utf8Decode = C.Utf8Decode
    CharColumns = C.CharColumns
    col = segCol = brkCol = 0
    segStart = brk = 0
    i = 0
    while i < n:
        c = p[i0 + i]
        ln = 1
        if c < 0x80:
            w = tab - (col % tab) if c == 9 else 1
        else:
            ln, cp = Utf8Decode(p, i0 + i, n - i)
            w = CharColumns(cp)
        if col + w - segCol > wrapCols and i > segStart:
            if brk > segStart:
                segStart = brk
                segCol = brkCol
            else:
                segStart = i
                segCol = col
            segs.append(segStart)
            brk = 0
            continue
        col += w
        i += ln
        if c == 32 or c == 9:
            brk = i
            brkCol = col


class FindQuery:
    def __init__(self, needle=b"", matchCase=False, wholeWord=False):
        self.needle = needle
        self.matchCase = matchCase
        self.wholeWord = wholeWord


# ---------------- search ----------------

def _CharClass(c):
    if c == 10 or c == 13:
        return 3
    if c == 32 or c == 9:
        return 0
    if (48 <= c <= 57) or (65 <= c <= 90) or (97 <= c <= 122) or c == 95 or c >= 0x80:
        return 1
    return 2


def _IsWordByte(c):
    return (48 <= c <= 57) or (65 <= c <= 90) or (97 <= c <= 122) or c == 95 or c >= 0x80


def _LowerAscii(s):
    return s.lower()  # bytes.lower() only maps A-Z


def _cancelled(cancel):
    return cancel is not None and cancel.is_set()


class _Searcher:
    kChunk = 4 << 20

    def __init__(self, b, qq, c):
        self.buf = b
        self.q = qq
        self.cancel = c  # threading.Event (or anything with is_set()) or None
        self.needle = bytes(qq.needle)
        if not qq.matchCase:
            self.needle = _LowerAscii(self.needle)
        self.total = b.Length()

    def Check(self, pos):
        if not self.q.wholeWord:
            return True
        if pos > 0 and _IsWordByte(self.buf.At(pos - 1)):
            return False
        if pos + len(self.needle) < self.total and _IsWordByte(self.buf.At(pos + len(self.needle))):
            return False
        return True

    def Scan(self, a, b, fn):
        """Calls fn(at) for each match starting in [a, b) in order; fn returns False to stop."""
        needle = self.needle
        n = len(needle)
        pos = a
        while pos < b:
            if _cancelled(self.cancel):
                return
            ln = min(self.kChunk, b - pos)
            chunk = self.buf.Read(pos, min(ln + n - 1, self.total - pos))
            if not self.q.matchCase:
                chunk = _LowerAscii(chunk)
            it = 0
            while True:
                r = chunk.find(needle, it)
                if r < 0:
                    break
                at = pos + r
                if at >= pos + ln:
                    break
                if self.Check(at) and not fn(at):
                    return
                it = r + 1
            pos += ln

    def First(self, a, b):
        res = [kNotFound]

        def f(at):
            res[0] = at
            return False
        self.Scan(a, b, f)
        return res[0]

    def Last(self, a, b):
        needle = self.needle
        n = len(needle)
        end = b
        while end > a:
            if _cancelled(self.cancel):
                return kNotFound
            st = end - self.kChunk if end - a > self.kChunk else a
            # last match starting in [st, end) (same result as scanning forward and keeping the last one)
            chunk = self.buf.Read(st, min(end - st + n - 1, self.total - st))
            if not self.q.matchCase:
                chunk = _LowerAscii(chunk)
            hi = len(chunk)
            while True:
                r = chunk.rfind(needle, 0, hi)
                if r < 0:
                    break
                if r < end - st and self.Check(st + r):
                    return st + r
                hi = r + n - 1
            end = st
        return kNotFound


def SearchBuffer(buf, q, start, forward, cancel):
    """Search helper shared with other components; returns UINT64_MAX when not found.
    cancel: a threading.Event (checked with is_set() between 4 MB chunks) or None."""
    if not q.needle or len(q.needle) > buf.Length():
        return kNotFound
    s = _Searcher(buf, q, cancel)
    start = min(start, buf.Length())
    if forward:
        r = s.First(start, buf.Length())
        if r == kNotFound:
            r = s.First(0, start)
    else:
        r = s.Last(0, start)
        if r == kNotFound:
            r = s.Last(start, buf.Length())
    return r


# ---------------- painting helpers ----------------

def _StyleColor(st):
    t = C.g_theme
    if st == MD.MS_HEADING: return t.heading
    if st == MD.MS_BOLD: return t.bold
    if st == MD.MS_ITALIC: return t.italic
    if st == MD.MS_CODE: return t.code
    if st == MD.MS_LINK: return t.link
    if st == MD.MS_URL: return t.url
    if st == MD.MS_QUOTE: return t.quote
    if st == MD.MS_LIST: return t.listMarker
    if st == MD.MS_RULE: return t.rule
    if st == MD.MS_FENCE: return t.fence
    if st == MD.MS_HTML: return t.html
    return t.fg


def _StyleFont(st):
    if st == MD.MS_HEADING or st == MD.MS_BOLD:
        return g_ef.bold
    if st == MD.MS_ITALIC:
        return g_ef.italic
    return g_ef.normal


def _Fill(dc, l, t, r, b, c):
    rc = RECT(l, t, r, b)
    SetBkColor(dc, c)
    ExtTextOutW(dc, 0, 0, ETO_OPAQUE, byref(rc), None, 0, None)


class _ColCp:
    __slots__ = ("lineStart", "version", "cps")

    def __init__(self):
        self.lineStart = W.UINT64_MAX
        self.version = 0
        self.cps = []  # list of (off, col)


class _Progress:
    """Stand-in for std::atomic<uint64_t>: TextBuffer.Save writes .value from its thread."""

    def __init__(self):
        self.value = 0


_views = {}       # hwnd -> EditorView
_creating = None  # EditorView being created (picked up in WM_NCCREATE)


def _WndProc(h, m, w, l):
    global _creating
    if m == WM_NCCREATE and _creating is not None:
        ev = _creating
        _creating = None
        ev.hwnd = h
        _views[h] = ev
    ev = _views.get(h)
    if ev is None:
        return DefWindowProcW(h, m, w, l)
    if m == WM_NCDESTROY:
        _views.pop(h, None)
        ev._Destroy()
        return DefWindowProcW(h, m, w, l)
    return ev.Handle(m, w, l)


class EditorView:
    # ---------------- construction ----------------

    def __init__(self):
        self.hwnd = None
        self.buf = None
        self.markdown = False
        self.untitledName = ""
        self.findMessage = ""  # last find result message for the find bar

        self.caret_ = 0
        self.anchor_ = 0
        self.firstRow_ = 0
        self.scrollCol_ = 0
        self.desiredCol_ = -1
        self.clientW_ = 0
        self.clientH_ = 0
        self.gutterW_ = 40
        self.wheelAccum_ = 0
        self.sbScale_ = 1
        self.dragging_ = False
        self.dragLines_ = False
        self.dragOrigin_ = 0
        self.maxCols_ = 0
        self.pendingHigh_ = 0

        self.wrapWanted_ = False
        self.wrap_ = False
        self.wrapCols_ = 80
        self.rowsPerLine_ = []
        self.rowPrefix_ = [0]

        self.md_ = MD.MdStateCache()
        self.scratch_ = b""

        self.colCache_ = []
        self.colCacheNext_ = 0

        self.findThread_ = None
        self.findCancel_ = threading.Event()
        self.searching_ = False
        self.saving_ = False
        self.saveProgress_ = -1.0
        self.findLen_ = 0

        self.backBmp_ = None
        self.backW_ = 0
        self.backH_ = 0

    @staticmethod
    def Register():
        C.RegisterWindowClass("MyEdView", _WndProc, style=CS_DBLCLKS, cursor=IDC_IBEAM)

    @staticmethod
    def From(h):
        return _views.get(h)

    @staticmethod
    def Create(parent, path):
        """path empty => new untitled document. Returns (view, err); view is None if the file can't be opened."""
        global _creating
        err = ""
        ev = EditorView()
        ev.buf = text_buffer.TextBuffer()
        _creating = ev
        h = CreateWindowExW(0, "MyEdView", "", WS_CHILD | WS_VSCROLL | WS_HSCROLL | WS_CLIPSIBLINGS, 0, 0, 0, 0,
                            parent, None, C.g_hinst, None)
        _creating = None
        if not h:
            return None, "Could not create editor window"
        if path:
            ok, err = ev.buf.Load(path, h)
            if not ok:
                DestroyWindow(h)
                return None, err
            if not ev.buf.Ready():
                SetTimer(h, kTimerIndex, 150, None)
        ev.SetPath(path)
        ev.wrapWanted_ = C.g_settings.wrap
        ev.SetWrap(C.g_settings.wrap)
        ev.UpdateGutter()
        ev.UpdateScrollbars()
        return ev, err

    def _Destroy(self):
        # ~EditorView
        self.findCancel_.set()
        self._JoinFind()
        if self.backBmp_:
            DeleteObject(self.backBmp_)
            self.backBmp_ = None

    def _JoinFind(self):
        if self.findThread_ is not None:
            self.findThread_.join()
            self.findThread_ = None

    # ---------------- public ----------------

    def Title(self):
        return self.untitledName if not self.buf.path else C.FileNameOf(self.buf.path)

    def SetPath(self, path):
        self.buf.path = path
        dot = path.rfind(".")
        ext = C.LowerW(path[dot:] if dot >= 0 else "")
        md = ext in (".md", ".markdown", ".mdown", ".mkd", ".mdx")
        if md != self.markdown:
            self.markdown = md
            self.md_ = MD.MdStateCache()
            if self.hwnd:
                InvalidateRect(self.hwnd, None, False)

    def Save(self, path):
        """Returns (ok, err)."""
        if not self.buf.Ready():
            return False, "The file is still being loaded."
        if self.saving_:
            return False, "A save is already in progress."
        err = ""
        if self.buf.Length() < (32 << 20):
            old = SetCursor(LoadCursorW(None, IDC_WAIT))
            ok, err = self.buf.Save(path)
            SetCursor(old)
        else:
            # Large file: write on a worker thread while keeping the UI painted (input disabled).
            self.saving_ = True
            written = _Progress()
            done = threading.Event()
            res = [False, ""]
            b = self.buf

            def worker():
                try:
                    res[0], res[1] = b.Save(path, written)
                except Exception:
                    C.ReportException()
                    res[0], res[1] = False, "Save failed"
                done.set()
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            top = GetAncestor(self.hwnd, GA_ROOT)
            EnableWindow(top, False)
            quit = False
            quitCode = 0
            m = MSG()
            while not done.is_set():
                MsgWaitForMultipleObjects(0, None, False, 100, QS_ALLINPUT)
                while PeekMessageW(byref(m), None, 0, 0, PM_REMOVE):
                    if m.message == WM_QUIT:
                        quit = True
                        quitCode = int(m.wParam)
                        continue
                    TranslateMessage(byref(m))
                    DispatchMessageW(byref(m))
                self.saveProgress_ = float(written.value) / float(max(1, b.Length()))
                self.Notify()
            t.join()
            EnableWindow(top, True)
            SetForegroundWindow(top)
            SetFocus(self.hwnd)
            self.saving_ = False
            self.saveProgress_ = -1.0
            ok, err = res[0], res[1]
            if quit:
                PostQuitMessage(quitCode)
        if ok:
            self.SetPath(path)
        self.Notify()
        return ok, err

    def Reload(self):
        """Returns (ok, err)."""
        if not self.buf.path:
            return False, ""
        self.findCancel_.set()
        self._JoinFind()
        self.searching_ = False
        nb = text_buffer.TextBuffer()
        ok, err = nb.Load(self.buf.path, self.hwnd)
        if not ok:
            return False, err
        line = self.CaretLine()
        self.buf = nb
        self.md_ = MD.MdStateCache()
        self.colCache_ = []
        if not self.buf.Ready():
            SetTimer(self.hwnd, kTimerIndex, 150, None)
        self.SetWrap(self.wrapWanted_)
        lc = self.buf.LineCount()
        self.caret_ = self.anchor_ = self.buf.LineStart(min(line, lc - 1))
        if self.firstRow_ >= self.RowCount():
            self.firstRow_ = self.RowCount() - 1 if self.RowCount() else 0
        self.UpdateGutter()
        self.UpdateScrollbars()
        InvalidateRect(self.hwnd, None, False)
        self.UpdateCaretPos()
        self.Notify()
        return True, err

    def WrapActive(self):
        return self.wrap_

    def Searching(self):
        return self.searching_

    def SaveProgress(self):
        """-1 when not saving."""
        return self.saveProgress_

    def SelectionLength(self):
        return self.caret_ - self.anchor_ if self.caret_ > self.anchor_ else self.anchor_ - self.caret_

    def Notify(self):
        SendMessageW(GetParent(self.hwnd), C.WM_APP_DOCCHANGED, self.hwnd or 0, 0)

    def FontChanged(self):
        self.UpdateGutter()
        if self.wrap_:
            self.RecomputeWrapAll()
        if GetFocus() == self.hwnd:
            DestroyCaret()
            CreateCaret(self.hwnd, None, max(2, C.S(2)), g_ef.lh)
            ShowCaret(self.hwnd)
        self.UpdateScrollbars()
        self.EnsureCaretVisible()
        InvalidateRect(self.hwnd, None, False)

    def UpdateGutter(self):
        lc = self.buf.LineCount()
        digits = 1
        while lc >= 10:
            lc //= 10
            digits += 1
        digits = max(digits, 3)
        self.gutterW_ = (digits + 2) * g_ef.cw

    # ---------------- geometry / rows ----------------

    def TextLeft(self):
        return self.gutterW_ + g_ef.cw // 2

    def PageRows(self):
        return max(1, self.clientH_ // g_ef.lh)

    def VisibleCols(self):
        return max(1, _tdiv(self.clientW_ - self.TextLeft(), g_ef.cw))

    def RowCount(self):
        return self.rowPrefix_[-1] if self.wrap_ else self.buf.LineCount()

    def RowToLine(self, row):
        """Returns (line, sub)."""
        if not self.wrap_:
            return row, 0
        l = bisect.bisect_right(self.rowPrefix_, row) - 1
        if l >= len(self.rowsPerLine_):
            l = len(self.rowsPerLine_) - 1
        return l, row - self.rowPrefix_[l]

    def LineFirstRow(self, line):
        if not self.wrap_:
            return line
        return self.rowPrefix_[min(line, len(self.rowsPerLine_) - 1)]

    def Segments(self, line, s, e, segs):
        """Fills segs with absolute segment starts."""
        segs.clear()
        if not self.wrap_ or e - s > kWrapMaxLine:
            segs.append(s)
            return
        text = self.buf.Read(s, e - s)
        rel = []
        WrapLine(text, len(text), self.wrapCols_, C.g_settings.tabSize, rel)
        for r in rel:
            segs.append(s + r)

    def ForEachLine(self, frm, to, fn):
        """fn(line, data, pos, n, trunc): line bytes are data[pos:pos+n] (terminator stripped)."""
        buf = self.buf
        off = buf.LineStart(frm)
        total = buf.Length()
        line = frm
        carry = bytearray()
        carryTrunc = False
        lim = kWrapMaxLine + 1
        while line < to and off < total:
            chunk = buf.Read(off, 1 << 20)
            if not chunk:
                break
            csz = len(chunk)
            p = 0
            find = chunk.find
            while line < to:
                e = find(b"\n", p)
                if e < 0:
                    room = lim - len(carry) if len(carry) < lim else 0
                    take = min(room, csz - p)
                    carry += chunk[p:p + take]
                    if take < csz - p:
                        carryTrunc = True
                    break
                trunc = False
                if carry or carryTrunc:
                    room = lim - len(carry) if len(carry) < lim else 0
                    carry += chunk[p:p + min(room, e - p)]
                    trunc = carryTrunc or room < e - p
                    lp = bytes(carry)
                    lpos = 0
                    ln = len(lp)
                else:
                    lp = chunk
                    lpos = p
                    ln = e - p
                if ln and lp[lpos + ln - 1] == 13:
                    ln -= 1
                fn(line, lp, lpos, ln, trunc or ln > kWrapMaxLine)
                carry = bytearray()
                carryTrunc = False
                p = e + 1
                line += 1
            off += csz
        if line < to:  # last line without trailing newline (possibly empty)
            lp = bytes(carry)
            ln = len(lp)
            if ln and lp[ln - 1] == 13:
                ln -= 1
            fn(line, lp, 0, ln, carryTrunc or ln > kWrapMaxLine)

    def RebuildRowPrefix(self, fromLine):
        n = len(self.rowsPerLine_)
        rp = self.rowPrefix_
        if len(rp) > n + 1:
            del rp[n + 1:]
        elif len(rp) < n + 1:
            rp.extend([0] * (n + 1 - len(rp)))
        rp[0] = 0
        i = min(fromLine, n)
        acc = itertools.accumulate(self.rowsPerLine_[i:], initial=rp[i])
        next(acc)  # skip the initial value (rp[i] itself)
        rp[i + 1:] = acc

    def _WrapFn(self):
        segs = []
        tab = C.g_settings.tabSize
        wc = self.wrapCols_
        rpl = self.rowsPerLine_

        def fn(l, p, pos, n, trunc):
            if trunc:
                return
            if n <= wc and p.find(b"\t", pos, pos + n) < 0:
                rpl[l] = 1
                return
            WrapLine(p, n, wc, tab, segs, pos)
            rpl[l] = len(segs)
        return fn

    def RecomputeWrapAll(self):
        lc = self.buf.LineCount()
        self.rowsPerLine_ = [1] * lc
        self.ForEachLine(0, lc, self._WrapFn())
        self.RebuildRowPrefix(0)

    def UpdateWrapAfterEdit(self, info):
        if info.whole or len(self.rowsPerLine_) != self.buf.LineCount() - info.linesIns + info.linesDel:
            self.RecomputeWrapAll()
            return
        l0 = info.line
        rpl = self.rowsPerLine_
        del rpl[l0:min(len(rpl), l0 + info.linesDel + 1)]
        rpl[l0:l0] = [1] * (info.linesIns + 1)
        self.ForEachLine(l0, l0 + info.linesIns + 1, self._WrapFn())
        self.RebuildRowPrefix(l0)

    def SetWrap(self, on):
        self.wrapWanted_ = on
        can = self.buf.Ready() and self.buf.Length() <= kWrapMaxDoc
        self.wrap_ = bool(on and can)
        if self.wrap_:
            self.wrapCols_ = max(10, self.VisibleCols() - 1)
            self.scrollCol_ = 0
            self.RecomputeWrapAll()
        else:
            self.rowsPerLine_ = []
            self.rowPrefix_ = [0]
        if self.hwnd:
            self.UpdateScrollbars()
            self.EnsureCaretVisible()
            InvalidateRect(self.hwnd, None, False)

    # ---------------- columns ----------------

    def ScanCol(self, lineEnd, off, col, stopOff, stopCol):
        """Returns (col, outOff)."""
        stopOff = min(stopOff, lineEnd)
        w = b""
        wOff = off
        wPos = 0
        tab = C.g_settings.tabSize
        Utf8Decode = C.Utf8Decode
        CharColumns = C.CharColumns
        special = _SPECIAL.search
        read = self.buf.Read
        while off < stopOff:
            if wPos + 4 > len(w) and wOff + len(w) < lineEnd:
                wOff = off
                w = read(off, min(lineEnd - off, 1 << 16))
                wPos = 0
                if not w:
                    break
            c = w[wPos]
            if c < 0x80 and c != 9:
                # run of plain one-column bytes: advance in one step
                m = special(w, wPos)
                runEnd = m.start() if m else len(w)
                run = min(runEnd - wPos, stopOff - off)
                if stopCol - col < run:
                    run = stopCol - col
                if run <= 0:
                    break
                col += run
                off += run
                wPos += run
                continue
            ln = 1
            if c < 0x80:
                cw = tab - (col % tab)
            else:
                ln, cp = Utf8Decode(w, wPos, len(w) - wPos)
                cw = CharColumns(cp)
            if col + cw > stopCol:
                break
            col += cw
            off += ln
            wPos += ln
        return col, min(off, lineEnd)

    def ColStart(self, s, e, targetOff, targetCol):
        """Returns (off, col) of the best column checkpoint for long lines."""
        if e - s < kLongLine:
            return s, 0
        c = None
        ver = self.buf.Version()
        for x in self.colCache_:
            if x.lineStart == s and x.version == ver:
                c = x
                break
        if c is None:
            if len(self.colCache_) < 16:
                c = _ColCp()
                self.colCache_.append(c)
            else:
                self.colCacheNext_ = (self.colCacheNext_ + 1) % len(self.colCache_)
                c = self.colCache_[self.colCacheNext_]
            c.lineStart = s
            c.version = ver
            c.cps = []
            o = s
            cl = 0
            c.cps.append((o, cl))
            while o < e:
                cl, no = self.ScanCol(e, o, cl, o + 16384, W.UINT64_MAX)
                if no == o:
                    break
                o = no
                c.cps.append((o, cl))
        # last checkpoint with off <= targetOff and col <= targetCol
        cps = c.cps
        lo, hi = 0, len(cps)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if cps[mid][0] <= targetOff and cps[mid][1] <= targetCol:
                lo = mid
            else:
                hi = mid
        return cps[lo][0], cps[lo][1]

    def ColAt(self, s, e, off):
        off = min(off, e)
        o, c = self.ColStart(s, e, off, W.UINT64_MAX)
        return self.ScanCol(e, o, c, off, W.UINT64_MAX)[0]

    def OffAt(self, s, e, col):
        o, c = self.ColStart(s, e, W.UINT64_MAX, col)
        return self.ScanCol(e, o, c, W.UINT64_MAX, col)[1]

    def OffToRowCol(self, off):
        """Returns (row, col)."""
        buf = self.buf
        line = buf.LineOf(off)
        s = buf.LineStart(line)
        e = buf.LineEnd(line)
        off = min(off, e)
        if not self.wrap_:
            return line, self.ColAt(s, e, off)
        segs = []
        self.Segments(line, s, e, segs)
        k = bisect.bisect_right(segs, off) - 1
        row = self.LineFirstRow(line) + k
        col = self.ColAt(s, e, off) - (self.ColAt(s, e, segs[k]) if k else 0)
        return row, col

    def RowColToOff(self, row, col):
        rc = self.RowCount()
        if row >= rc:
            row = rc - 1 if rc else 0
        line, sub = self.RowToLine(row)
        s = self.buf.LineStart(line)
        e = self.buf.LineEnd(line)
        if not self.wrap_:
            return self.OffAt(s, e, col)
        segs = []
        self.Segments(line, s, e, segs)
        if sub >= len(segs):
            sub = len(segs) - 1
        ss = segs[sub]
        se = segs[sub + 1] if sub + 1 < len(segs) else e
        base = self.ColAt(s, e, ss) if sub else 0
        o = self.OffAt(s, e, base + col)
        if sub + 1 < len(segs) and o >= se:
            o = self.PrevChar(se)
        return max(o, ss)

    def HitTest(self, x, y):
        r = -1 if y < 0 else y // g_ef.lh
        row = self.firstRow_ + r
        if row < 0:
            row = 0
        cx = x - self.TextLeft() + g_ef.cw // 2
        col = self.scrollCol_ + (cx // g_ef.cw if cx > 0 else 0)
        if self.wrap_ and cx <= 0:
            col = 0
        return self.RowColToOff(row, col)

    def CaretLine(self):
        return self.buf.LineOf(self.caret_)

    def CaretCol(self):
        line = self.buf.LineOf(self.caret_)
        return self.ColAt(self.buf.LineStart(line), self.buf.LineEnd(line), self.caret_)

    # ---------------- scrolling / caret ----------------

    def UpdateScrollbars(self):
        if not self.hwnd:
            return
        rows = self.RowCount()
        page = self.PageRows()
        maxRow = rows - 1 + page - 1 if rows else 0
        self.sbScale_ = 1
        while maxRow // self.sbScale_ > 0x3FFFFFFF:
            self.sbScale_ *= 2
        si = SCROLLINFO()
        si.cbSize = sizeof(SCROLLINFO)
        si.fMask = SIF_RANGE | SIF_PAGE | SIF_POS
        si.nMin = 0
        si.nMax = maxRow // self.sbScale_
        si.nPage = max(1, page // self.sbScale_)
        si.nPos = self.firstRow_ // self.sbScale_
        SetScrollInfo(self.hwnd, SB_VERT, byref(si), True)
        if self.wrap_:
            ShowScrollBar(self.hwnd, SB_HORZ, False)
        else:
            cols = max(self.maxCols_, self.buf.MaxLineLen()) + 8
            si.nMax = min(cols, 0x3FFFFFFF)
            si.nPage = self.VisibleCols()
            si.nPos = min(self.scrollCol_, 0x3FFFFFFF)
            SetScrollInfo(self.hwnd, SB_HORZ, byref(si), True)

    def ScrollToRow(self, row):
        rows = self.RowCount()
        if row > rows - 1:
            row = rows - 1
        if row < 0:
            row = 0
        if row == self.firstRow_:
            return
        self.firstRow_ = row
        self.UpdateScrollbars()
        InvalidateRect(self.hwnd, None, False)
        self.UpdateCaretPos()

    def UpdateCaretPos(self):
        if GetFocus() != self.hwnd:
            return
        row, col = self.OffToRowCol(self.caret_)
        x = y = -1000
        if self.firstRow_ <= row <= self.firstRow_ + self.PageRows() and col >= self.scrollCol_:
            x = self.TextLeft() + (col - self.scrollCol_) * g_ef.cw
            y = (row - self.firstRow_) * g_ef.lh
            if x > self.clientW_:
                x = -1000
        x = max(-0x80000000, min(0x7FFFFFFF, x))
        y = max(-0x80000000, min(0x7FFFFFFF, y))
        SetCaretPos(x, y)
        imc = ImmGetContext(self.hwnd)
        if imc:
            cf = COMPOSITIONFORM()
            cf.dwStyle = CFS_POINT
            cf.ptCurrentPos = POINT(max(x, 0), max(y, 0))
            ImmSetCompositionWindow(imc, byref(cf))
            lf = LOGFONTW()
            GetObjectW(g_ef.normal, sizeof(lf), byref(lf))
            ImmSetCompositionFontW(imc, byref(lf))
            ImmReleaseContext(self.hwnd, imc)

    def EnsureCaretVisible(self, center=False):
        if not self.hwnd or self.clientW_ <= 0 or self.clientH_ <= 0:
            return  # no geometry yet
        row, col = self.OffToRowCol(self.caret_)
        page = self.PageRows()
        old = self.firstRow_
        oldCol = self.scrollCol_
        if row < self.firstRow_ or row >= self.firstRow_ + page:
            if center:
                self.firstRow_ = row - page // 2 if row > page // 2 else 0
            elif row < self.firstRow_:
                self.firstRow_ = row
            else:
                self.firstRow_ = row - page + 1
        if not self.wrap_:
            vis = self.VisibleCols()
            if col < self.scrollCol_:
                self.scrollCol_ = col - 8 if col > 8 else 0
            elif col + 2 > self.scrollCol_ + vis:
                self.scrollCol_ = col + 8 - vis if col + 8 > vis else 0
        if old != self.firstRow_ or oldCol != self.scrollCol_:
            self.UpdateScrollbars()
            InvalidateRect(self.hwnd, None, False)
        self.UpdateCaretPos()

    # ---------------- painting ----------------

    def Paint(self, hdc):
        if not self.backBmp_ or self.backW_ < self.clientW_ or self.backH_ < self.clientH_:
            if self.backBmp_:
                DeleteObject(self.backBmp_)
            self.backW_ = max(self.clientW_, 16)
            self.backH_ = max(self.clientH_, 16)
            self.backBmp_ = CreateCompatibleBitmap(hdc, self.backW_, self.backH_)
        dc = CreateCompatibleDC(hdc)
        oldBmp = SelectObject(dc, self.backBmp_)
        t = C.g_theme
        cw = g_ef.cw
        lh = g_ef.lh
        tab = C.g_settings.tabSize
        clientW = self.clientW_
        gutterW = self.gutterW_
        buf = self.buf
        _Fill(dc, 0, 0, clientW, self.clientH_, t.bg)
        _Fill(dc, 0, 0, gutterW, self.clientH_, t.gutterBg)
        SetBkMode(dc, TRANSPARENT)
        oldFont = SelectObject(dc, g_ef.normal)

        selA = min(self.caret_, self.anchor_)
        selB = max(self.caret_, self.anchor_)
        caretRow, caretCol = self.OffToRowCol(self.caret_)
        caretLine = buf.LineOf(self.caret_)
        rc = self.RowCount()
        rows = self.PageRows() + 1
        textLeft = self.TextLeft()
        visCols = self.VisibleCols() + 2
        scrollCol = self.scrollCol_
        markdown = self.markdown
        Utf8Decode = C.Utf8Decode
        CharColumns = C.CharColumns
        MS_NORMAL = MD.MS_NORMAL
        ty_off = (lh - (g_ef.lh - MulDiv(3, C.g_dpi, 96))) // 2

        curLine = W.UINT64_MAX
        s = e = 0
        segs = []
        spans = []
        mdState = nextState = 0

        for r in range(rows):
            row = self.firstRow_ + r
            if row >= rc:
                break
            line, sub = self.RowToLine(row)
            if line != curLine:
                seq = curLine != W.UINT64_MAX and line == curLine + 1
                curLine = line
                s = buf.LineStart(line)
                e = buf.LineEnd(line)
                self.Segments(line, s, e, segs)
                spans = []
                if markdown:
                    mdState = nextState if seq else self.md_.StateAt(buf, line)
                    if e - s <= kLongLine:
                        lineText = buf.Read(s, e - s)
                        spans = MD.MdHighlightLine(lineText, len(lineText), mdState)  # void + out -> returns out
                        nextState = MD.MdNextState(lineText, min(len(lineText), 512), mdState)
                    else:
                        nextState = mdState
            k = min(sub, len(segs) - 1)
            ss = segs[k]
            se = segs[k + 1] if k + 1 < len(segs) else e
            lastSeg = k + 1 >= len(segs)
            y = r * lh
            if row == caretRow and selA == selB:
                _Fill(dc, gutterW, y, clientW, y + lh, t.curLine)
            if k == 0:
                num = str(line + 1)
                SetTextColor(dc, t.gutterFgCur if line == caretLine else t.gutterFg)
                SelectObject(dc, g_ef.normal)
                nr = RECT(0, y, gutterW - cw, y + lh)
                DrawTextW(dc, num, len(num), byref(nr), DT_RIGHT | DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX)
            # locate first visible char
            base = self.ColAt(s, e, ss) if k else 0
            startCol = base + scrollCol
            o0 = ss
            c0 = base
            if scrollCol:
                co, cc = self.ColStart(s, e, W.UINT64_MAX, startCol)
                if co < ss:
                    co = ss
                    cc = base
                c0, o0 = self.ScanCol(se, co, cc, se, startCol)
            maxBytes = min(se - o0, visCols * 4 + 16)
            chunk = buf.Read(o0, maxBytes)
            wt = []
            dx = []
            xs = []
            st = []
            uo = []
            col = c0
            endCol = startCol + visCols
            i = 0
            sp = 0
            nchunk = len(chunk)
            nspans = len(spans)
            while i < nchunk and col < endCol:
                ch = chunk[i]
                ln = 1
                if ch < 0x80:
                    cp = ch
                    w = tab - (col % tab) if ch == 9 else 1
                else:
                    ln, cp = Utf8Decode(chunk, i, nchunk - i)
                    w = CharColumns(cp)
                style = MS_NORMAL
                if nspans:
                    rel = o0 + i - s
                    while sp < nspans and spans[sp].end <= rel:
                        sp += 1
                    if sp < nspans and spans[sp].start <= rel:
                        style = spans[sp].style
                x = textLeft + (col - startCol) * cw
                bo = o0 + i
                if cp >= 0x10000:
                    v = cp - 0x10000
                    wt.append(chr(0xD800 + (v >> 10))); dx.append(w * cw); xs.append(x); st.append(style); uo.append(bo)
                    wt.append(chr(0xDC00 + (v & 0x3FF))); dx.append(0); xs.append(x + w * cw); st.append(style); uo.append(bo)
                else:
                    if ch == 9 or ch == 13:
                        wc = " "
                    elif cp < 0x20 or cp == 0x7F:
                        wc = "·"
                    else:
                        wc = chr(cp)
                    wt.append(wc); dx.append(w * cw); xs.append(x); st.append(style); uo.append(bo)
                col += w
                i += ln
            if lastSeg and o0 + i >= se and col > self.maxCols_:
                self.maxCols_ = col
            # selection
            if selA != selB:
                n = len(wt)
                a = 0
                while a < n:
                    if selA <= uo[a] < selB:
                        b = a
                        x0 = xs[a]
                        x1 = xs[a] + dx[a]
                        while b + 1 < n and selA <= uo[b + 1] < selB:
                            b += 1
                            x1 = max(x1, xs[b] + dx[b])
                        _Fill(dc, max(x0, gutterW), y, x1, y + lh, t.selBg)
                        a = b + 1
                    else:
                        a += 1
                if lastSeg and selA <= e < selB and o0 + i >= se:
                    xe = textLeft + (col - startCol) * cw
                    if xe >= gutterW:
                        _Fill(dc, xe, y, xe + cw // 2 + 1, y + lh, t.selBg)
            # text runs
            clip = RECT(gutterW, y, clientW, y + lh)
            ty = y + ty_off
            n = len(wt)
            a = 0
            while a < n:
                sa = st[a]
                b = a
                while b < n and st[b] == sa:
                    b += 1
                SelectObject(dc, _StyleFont(sa))
                SetTextColor(dc, _StyleColor(sa))
                cnt = b - a
                ExtTextOutW(dc, xs[a], ty, ETO_CLIPPED, byref(clip), "".join(wt[a:b]), cnt,
                            (c_int * cnt)(*dx[a:b]))
                a = b
        if not buf.Ready():
            msg = "  Indexing lines... %d%%  " % int(buf.Progress() * 100)
            SelectObject(dc, C.g_uiFont)
            sz = text_extent(dc, msg)
            mr = RECT(clientW - sz.cx - C.S(12), C.S(6), clientW - C.S(12), C.S(6) + sz.cy + C.S(6))
            _Fill(dc, mr.left, mr.top, mr.right, mr.bottom, t.accent)
            SetTextColor(dc, RGB(255, 255, 255))
            draw_text(dc, msg, mr, DT_CENTER | DT_VCENTER | DT_SINGLELINE)
        SelectObject(dc, oldFont)
        BitBlt(hdc, 0, 0, clientW, self.clientH_, dc, 0, 0, SRCCOPY)
        SelectObject(dc, oldBmp)
        DeleteDC(dc)

    # ======================= editor_input.cpp =======================

    # ---------------- search ----------------

    def Find(self, q, forward, incremental=False):
        """incremental: search from selection start."""
        if not q.needle or self.searching_:
            return
        if not self.buf.Ready():
            self.findMessage = "File is still loading"
            self.Notify()
            return
        self._JoinFind()
        self.searching_ = True
        self.findCancel_.clear()
        self.findLen_ = len(q.needle)
        self.findMessage = "Searching..."
        self.Notify()
        start = max(self.caret_, self.anchor_) if (forward and not incremental) else min(self.caret_, self.anchor_)
        h = self.hwnd
        b = self.buf
        cancel = self.findCancel_
        qc = FindQuery(bytes(q.needle), q.matchCase, q.wholeWord)

        def worker():
            r = kNotFound
            try:
                r = SearchBuffer(b, qc, start, forward, cancel)
            except Exception:
                C.ReportException()
            PostMessageW(h, C.WM_APP_FINDDONE, r, 0)  # result fits in WPARAM (64-bit)
        self.findThread_ = threading.Thread(target=worker, daemon=True)
        self.findThread_.start()

    def OnFindDone(self, r, ln):
        self._JoinFind()
        self.searching_ = False
        if r == kNotFound:
            self.findMessage = "No results"
        else:
            self.findMessage = ""
            self.anchor_ = r
            self.caret_ = r + ln
            self.desiredCol_ = -1
            self.EnsureCaretVisible(True)
            InvalidateRect(self.hwnd, None, False)
        self.Notify()

    def ReplaceOne(self, q, repl):
        if not self.CanEdit() or not q.needle:
            return False
        a = min(self.caret_, self.anchor_)
        b = max(self.caret_, self.anchor_)
        if b - a == len(q.needle):
            sel = self.buf.Read(a, b - a)
            n = bytes(q.needle)
            if not q.matchCase:
                sel = _LowerAscii(sel)
                n = _LowerAscii(n)
            if sel == n:
                self.InsertText(repl, False)
        self.Find(q, True)
        return True

    def ReplaceAll(self, q, repl):
        if not self.CanEdit() or not q.needle:
            return 0
        old = SetCursor(LoadCursorW(None, IDC_WAIT))
        s = _Searcher(self.buf, q, None)
        offs = []
        nxt = [0]
        nl = len(q.needle)

        def f(at):
            if at >= nxt[0]:
                offs.append(at)
                nxt[0] = at + nl
            return True
        s.Scan(0, self.buf.Length(), f)
        if offs:
            info = self.buf.ReplaceMany(offs, nl, repl, self.caret_)
            self.caret_ = self.anchor_ = min(self.caret_, self.buf.Length())
            self.AfterEdit(info)
        SetCursor(old)
        self.findMessage = "No results" if not offs else "Replaced " + str(len(offs)) + " occurrence(s)"
        self.Notify()
        return len(offs)

    def SelectionForFind(self):
        a = min(self.caret_, self.anchor_)
        b = max(self.caret_, self.anchor_)
        if b == a or b - a > 256:
            return b""
        s = self.buf.Read(a, b - a)
        if b"\n" in s:
            return b""
        return s

    # ---------------- editing ----------------

    def CanEdit(self):
        return self.buf.Ready() and not self.searching_ and not self.saving_

    def EolStr(self):
        return b"\r\n" if self.buf.crlf else b"\n"

    def AfterEdit(self, info):
        self.md_.Invalidate(0 if info.whole else info.line)
        if self.wrap_:
            self.UpdateWrapAfterEdit(info)
        self.desiredCol_ = -1
        self.UpdateGutter()
        rc = self.RowCount()
        if self.firstRow_ >= rc:
            self.firstRow_ = rc - 1 if rc else 0
        self.UpdateScrollbars()
        self.EnsureCaretVisible()
        InvalidateRect(self.hwnd, None, False)
        self.Notify()

    def InsertText(self, s, typing):
        if not self.CanEdit():
            MessageBeep(MB_OK)
            return
        a = min(self.caret_, self.anchor_)
        b = max(self.caret_, self.anchor_)
        if a == b and not s:
            return
        info = _BufReplace(self.buf, a, b - a, s, self.caret_, bool(typing and a == b))
        self.caret_ = self.anchor_ = a + len(s)
        self.AfterEdit(info)

    def DeleteRange(self, a, b):
        if not self.CanEdit():
            MessageBeep(MB_OK)
            return
        if a >= b:
            return
        info = _BufReplace(self.buf, a, b - a, b"", self.caret_)
        self.caret_ = self.anchor_ = a
        self.AfterEdit(info)

    def ReplaceLines(self, l0, l1, fn):
        if not self.CanEdit():
            return
        s = self.buf.LineStart(l0)
        e = self.buf.LineEnd(l1)
        if e - s > (32 << 20):
            MessageBeep(MB_ICONWARNING)
            return
        text = self.buf.Read(s, e - s)
        out = b"\n".join(fn(line) for line in text.split(b"\n"))
        if out == text:
            return
        info = _BufReplace(self.buf, s, e - s, out, self.caret_)
        self.anchor_ = s
        self.caret_ = s + len(out)
        self.AfterEdit(info)

    def PrevChar(self, off):
        if off == 0:
            return 0
        buf = self.buf
        if buf.At(off - 1) == 10 and off >= 2 and buf.At(off - 2) == 13:
            return off - 2
        p = off - 1
        while p > 0 and off - p < 4 and (buf.At(p) & 0xC0) == 0x80:
            p -= 1
        return p

    def NextChar(self, off):
        buf = self.buf
        total = buf.Length()
        if off >= total:
            return total
        c = buf.At(off)
        if c == 13 and off + 1 < total and buf.At(off + 1) == 10:
            return off + 2
        ln = 1 if c < 0x80 else 2 if (c & 0xE0) == 0xC0 else 3 if (c & 0xF0) == 0xE0 else 4 if (c & 0xF8) == 0xF0 else 1
        p = off + 1
        while p < total and p < off + ln and (buf.At(p) & 0xC0) == 0x80:
            p += 1
        return p

    def WordRight(self, off):
        buf = self.buf
        total = buf.Length()
        if off >= total:
            return total
        if _CharClass(buf.At(off)) == 3:
            return self.NextChar(off)
        p = off
        lim = off + (1 << 20)
        while p < total and p < lim and _CharClass(buf.At(p)) == 0:
            p += 1
        if p < total:
            k = _CharClass(buf.At(p))
            if k != 3:
                while p < total and p < lim and _CharClass(buf.At(p)) == k:
                    p += 1
        return p

    def WordLeft(self, off):
        if off == 0:
            return 0
        buf = self.buf
        if _CharClass(buf.At(off - 1)) == 3:
            return self.PrevChar(off)
        p = off
        lim = off - (1 << 20) if off > (1 << 20) else 0
        while p > lim and _CharClass(buf.At(p - 1)) == 0:
            p -= 1
        if p > lim:
            k = _CharClass(buf.At(p - 1))
            if k != 3:
                while p > lim and _CharClass(buf.At(p - 1)) == k:
                    p -= 1
        return p

    def SelectWordAt(self, off):
        buf = self.buf
        total = buf.Length()
        if off >= total or _CharClass(buf.At(off)) == 3:
            if off == 0 or _CharClass(buf.At(off - 1)) == 3:
                self.anchor_ = self.caret_ = off
                return
            off -= 1
        k = _CharClass(buf.At(off))
        a = b = off
        lim = 1 << 16
        while a > 0 and off - a < lim and _CharClass(buf.At(a - 1)) == k:
            a -= 1
        while b < total and b - off < lim and _CharClass(buf.At(b)) == k:
            b += 1
        self.anchor_ = a
        self.caret_ = b

    def MoveCaret(self, pos, extend):
        self.caret_ = min(pos, self.buf.Length())
        if not extend:
            self.anchor_ = self.caret_
        self.buf.BreakUndoMerge()
        self.EnsureCaretVisible()
        InvalidateRect(self.hwnd, None, False)
        self.Notify()

    def Undo(self):
        if not self.CanEdit():
            return
        ok, info, c = self.buf.Undo()
        if ok:
            self.caret_ = self.anchor_ = min(c, self.buf.Length())
            self.AfterEdit(info)

    def Redo(self):
        if not self.CanEdit():
            return
        ok, info, c = self.buf.Redo()
        if ok:
            self.caret_ = self.anchor_ = min(c, self.buf.Length())
            self.AfterEdit(info)

    def Copy(self):
        a = min(self.caret_, self.anchor_)
        b = max(self.caret_, self.anchor_)
        if a == b:
            line = self.buf.LineOf(self.caret_)
            a = self.buf.LineStart(line)
            b = self.buf.LineNext(line)
        if b - a > (256 << 20):
            C.MsgBox(self.hwnd, "Selection is too large to copy to the clipboard (limit 256 MB).", MB_ICONWARNING)
            return
        s = self.buf.Read(a, b - a)
        C.SetClipboardText(self.hwnd, C.Utf8ToWide(s))

    def Cut(self):
        if not self.CanEdit():
            return
        self.Copy()
        if self.caret_ != self.anchor_:
            self.DeleteRange(min(self.caret_, self.anchor_), max(self.caret_, self.anchor_))
        else:
            line = self.buf.LineOf(self.caret_)
            self.DeleteRange(self.buf.LineStart(line), self.buf.LineNext(line))

    def Paste(self):
        if not self.CanEdit():
            return
        w = C.GetClipboardText(self.hwnd)
        if w is None:
            return
        u = C.WideToUtf8(w)
        eol = self.EolStr()
        # "\r\n", lone "\r" and "\n" all become the document's line ending
        out = u.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        if eol != b"\n":
            out = out.replace(b"\n", eol)
        self.buf.BreakUndoMerge()
        self.InsertText(out, False)

    def SelectAll(self):
        self.anchor_ = 0
        self.caret_ = self.buf.Length()
        InvalidateRect(self.hwnd, None, False)
        self.UpdateCaretPos()
        self.Notify()

    def GotoLine(self, line, center=True):
        """0-based."""
        lc = self.buf.LineCount()
        self.caret_ = self.anchor_ = self.buf.LineStart(min(line, lc - 1))
        self.desiredCol_ = -1
        self.EnsureCaretVisible(center)
        InvalidateRect(self.hwnd, None, False)
        self.Notify()

    def SelectInLine(self, line, col, len_):
        """0-based line, byte column and length."""
        lc = self.buf.LineCount()
        line = min(line, lc - 1)
        s = self.buf.LineStart(line)
        e = self.buf.LineEnd(line)
        self.anchor_ = min(s + col, e)
        self.caret_ = min(self.anchor_ + len_, e)
        self.desiredCol_ = -1
        self.EnsureCaretVisible(True)
        InvalidateRect(self.hwnd, None, False)
        self.Notify()

    def OnChar(self, ch):
        if ch < 0x20 or ch == 0x7F:
            return  # controls handled in OnKeyDown
        if GetKeyState(VK_CONTROL) < 0 and GetKeyState(VK_MENU) >= 0:
            return
        if IS_HIGH_SURROGATE(ch):
            self.pendingHigh_ = ch
            return
        if IS_LOW_SURROGATE(ch):
            if not self.pendingHigh_:
                return
            text = chr(0x10000 + ((self.pendingHigh_ - 0xD800) << 10) + (ch - 0xDC00))
        else:
            text = chr(ch)
        self.pendingHigh_ = 0
        self.InsertText(C.WideToUtf8(text), True)

    def OnKeyDown(self, vk):
        ctrl = GetKeyState(VK_CONTROL) < 0
        shift = GetKeyState(VK_SHIFT) < 0
        buf = self.buf
        selA = min(self.caret_, self.anchor_)
        selB = max(self.caret_, self.anchor_)
        if vk == VK_LEFT:
            if not shift and not ctrl and selA != selB:
                self.MoveCaret(selA, False)
            else:
                self.MoveCaret(self.WordLeft(self.caret_) if ctrl else self.PrevChar(self.caret_), shift)
            self.desiredCol_ = -1
        elif vk == VK_RIGHT:
            if not shift and not ctrl and selA != selB:
                self.MoveCaret(selB, False)
            else:
                self.MoveCaret(self.WordRight(self.caret_) if ctrl else self.NextChar(self.caret_), shift)
            self.desiredCol_ = -1
        elif vk in (VK_UP, VK_DOWN, VK_PRIOR, VK_NEXT):
            up = vk == VK_UP or vk == VK_PRIOR
            step = 1 if (vk == VK_UP or vk == VK_DOWN) else self.PageRows() - 1
            if ctrl and (vk == VK_UP or vk == VK_DOWN):
                self.ScrollToRow(self.firstRow_ + (-1 if up else 1))
                return
            row, col = self.OffToRowCol(self.caret_)
            if self.desiredCol_ < 0:
                self.desiredCol_ = col
            keep = self.desiredCol_
            nr = row + (-step if up else step)
            rc = self.RowCount()
            if nr < 0:
                target = 0
            elif nr >= rc:
                target = buf.Length()
            else:
                target = self.RowColToOff(nr, self.desiredCol_)
            if step > 1:
                self.ScrollToRow(self.firstRow_ + (-step if up else step))
            self.MoveCaret(target, shift)
            if 0 <= nr < rc:
                self.desiredCol_ = keep
        elif vk == VK_HOME:
            if ctrl:
                self.MoveCaret(0, shift)
                return
            line = buf.LineOf(self.caret_)
            s = buf.LineStart(line)
            e = buf.LineEnd(line)
            p = s
            while p < e and p - s < 4096 and buf.At(p) in (32, 9):
                p += 1
            self.MoveCaret(s if self.caret_ == p else p, shift)
            self.desiredCol_ = -1
        elif vk == VK_END:
            if ctrl:
                self.MoveCaret(buf.Length(), shift)
            else:
                self.MoveCaret(buf.LineEnd(buf.LineOf(self.caret_)), shift)
            self.desiredCol_ = -1
        elif vk == VK_BACK:
            if selA != selB:
                self.DeleteRange(selA, selB)
            elif self.caret_ > 0:
                self.DeleteRange(self.WordLeft(self.caret_) if ctrl else self.PrevChar(self.caret_), self.caret_)
        elif vk == VK_DELETE:
            if shift and not ctrl:
                self.Cut()
                return
            if selA != selB:
                self.DeleteRange(selA, selB)
            else:
                self.DeleteRange(self.caret_, self.WordRight(self.caret_) if ctrl else self.NextChar(self.caret_))
        elif vk == VK_INSERT:
            if ctrl:
                self.Copy()
            elif shift:
                self.Paste()
        elif vk == VK_RETURN:
            if not self.CanEdit():
                return
            line = buf.LineOf(selA)
            s = buf.LineStart(line)
            head = buf.Read(s, min(selA - s, 1024))
            n = 0
            while n < len(head) and head[n] in (32, 9):
                n += 1
            indent = head[:n]
            buf.BreakUndoMerge()
            self.InsertText(self.EolStr() + indent, False)
        elif vk == VK_TAB:
            if not self.CanEdit():
                return
            la = buf.LineOf(selA)
            lb = buf.LineOf(selB)
            if lb > la and selB == buf.LineStart(lb):
                lb -= 1
            first = buf.Read(buf.LineStart(la), 1)
            tab = C.g_settings.tabSize
            unit = b"\t" if first == b"\t" else b" " * tab
            if shift:
                def outdent(l):
                    if l and l[0] == 9:
                        return l[1:]
                    n = 0
                    while n < len(l) and n < tab and l[n] == 32:
                        n += 1
                    return l[n:]
                self.ReplaceLines(la, lb, outdent)
            elif la != lb:
                def indent(l):
                    t = l
                    if t and t[-1] == 13:
                        t = t[:-1]
                    return l if not t else unit + l
                self.ReplaceLines(la, lb, indent)
            elif unit == b"\t":
                self.InsertText(b"\t", False)
            else:
                line = buf.LineOf(selA)
                col = self.ColAt(buf.LineStart(line), buf.LineEnd(line), selA)
                self.InsertText(b" " * (tab - (col % tab)), False)
        elif vk == VK_ESCAPE:
            if selA != selB:
                self.MoveCaret(self.caret_, False)
            SendMessageW(GetParent(self.hwnd), WM_COMMAND, R.ID_FIND_CLOSE, 0)
        else:
            if ctrl:
                if vk == ord('A'):
                    self.SelectAll()
                elif vk == ord('C'):
                    self.Copy()
                elif vk == ord('X'):
                    self.Cut()
                elif vk == ord('V'):
                    self.Paste()
                elif vk == ord('Z'):
                    if shift:
                        self.Redo()
                    else:
                        self.Undo()
                elif vk == ord('Y'):
                    self.Redo()
                elif vk == ord('L'):
                    line = buf.LineOf(self.caret_)
                    self.anchor_ = buf.LineStart(buf.LineOf(self.anchor_ if self.anchor_ < self.caret_ else self.caret_))
                    self.caret_ = buf.LineNext(line)
                    self.EnsureCaretVisible()
                    InvalidateRect(self.hwnd, None, False)
                    self.Notify()

    def ContextMenu(self, x, y):
        m = CreatePopupMenu()
        canEdit = MF_ENABLED if self.CanEdit() else MF_GRAYED
        AppendMenuW(m, MF_STRING | (canEdit if self.buf.CanUndo() else MF_GRAYED), 1, "Undo\tCtrl+Z")
        AppendMenuW(m, MF_STRING | (canEdit if self.buf.CanRedo() else MF_GRAYED), 2, "Redo\tCtrl+Y")
        AppendMenuW(m, MF_SEPARATOR, 0, None)
        AppendMenuW(m, MF_STRING | canEdit, 3, "Cut\tCtrl+X")
        AppendMenuW(m, MF_STRING, 4, "Copy\tCtrl+C")
        AppendMenuW(m, MF_STRING | canEdit, 5, "Paste\tCtrl+V")
        AppendMenuW(m, MF_SEPARATOR, 0, None)
        AppendMenuW(m, MF_STRING, 6, "Select All\tCtrl+A")
        cmd = TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, 0, self.hwnd, None)
        DestroyMenu(m)
        if cmd == 1:
            self.Undo()
        elif cmd == 2:
            self.Redo()
        elif cmd == 3:
            self.Cut()
        elif cmd == 4:
            self.Copy()
        elif cmd == 5:
            self.Paste()
        elif cmd == 6:
            self.SelectAll()

    # ---------------- message handler ----------------

    def Handle(self, m, w, l):
        hwnd = self.hwnd
        if m == WM_SIZE:
            self.clientW_ = LOWORD(l)
            self.clientH_ = HIWORD(l)
            if self.wrap_:
                cols = max(10, self.VisibleCols() - 1)
                if cols != self.wrapCols_:
                    self.wrapCols_ = cols
                    self.RecomputeWrapAll()
            self.UpdateScrollbars()
            InvalidateRect(hwnd, None, False)
            self.UpdateCaretPos()
            return 0
        if m == WM_PAINT:
            ps = PAINTSTRUCT()
            dc = BeginPaint(hwnd, byref(ps))
            try:
                self.Paint(dc)
            finally:
                EndPaint(hwnd, byref(ps))
            return 0
        if m == WM_ERASEBKGND:
            return 1
        if m == WM_SETFOCUS:
            CreateCaret(hwnd, None, max(2, C.S(2)), g_ef.lh)
            ShowCaret(hwnd)
            self.UpdateCaretPos()
            self.Notify()
            return 0
        if m == WM_KILLFOCUS:
            DestroyCaret()
            return 0
        if m == WM_GETDLGCODE:
            return DLGC_WANTALLKEYS | DLGC_WANTCHARS | DLGC_WANTTAB | DLGC_WANTARROWS
        if m == WM_TIMER:
            if w == kTimerIndex:
                if self.buf.Ready():
                    KillTimer(hwnd, kTimerIndex)
                    return 0
                self.UpdateGutter()
                self.UpdateScrollbars()
                InvalidateRect(hwnd, None, False)
                self.Notify()
            elif w == kTimerDrag and self.dragging_:
                pt = POINT()
                GetCursorPos(byref(pt))
                ScreenToClient(hwnd, byref(pt))
                lh = g_ef.lh
                if pt.y < 0:
                    self.ScrollToRow(self.firstRow_ - 1 - (-pt.y) // lh)
                elif pt.y > self.clientH_:
                    self.ScrollToRow(self.firstRow_ + 1 + (pt.y - self.clientH_) // lh)
                if not self.wrap_ and pt.x > self.clientW_:
                    self.scrollCol_ += 2
                    self.UpdateScrollbars()
                    InvalidateRect(hwnd, None, False)
                elif not self.wrap_ and pt.x < self.gutterW_ and self.scrollCol_ > 0 and not self.dragLines_:
                    self.scrollCol_ = self.scrollCol_ - 2 if self.scrollCol_ > 2 else 0
                    self.UpdateScrollbars()
                    InvalidateRect(hwnd, None, False)
                SendMessageW(hwnd, WM_MOUSEMOVE, MK_LBUTTON, MAKELPARAM(pt.x, pt.y))
            return 0
        if m == C.WM_APP_INDEXED:
            self.buf.OnIndexed()
            KillTimer(hwnd, kTimerIndex)
            self.SetWrap(self.wrapWanted_)
            self.UpdateGutter()
            self.UpdateScrollbars()
            InvalidateRect(hwnd, None, False)
            self.Notify()
            return 0
        if m == C.WM_APP_FINDDONE:
            self.OnFindDone(w, self.findLen_)
            return 0
        if m == WM_VSCROLL:
            si = SCROLLINFO()
            si.cbSize = sizeof(SCROLLINFO)
            si.fMask = SIF_ALL
            GetScrollInfo(hwnd, SB_VERT, byref(si))
            row = self.firstRow_
            page = self.PageRows()
            code = LOWORD(w)
            if code == SB_LINEUP:
                row -= 1
            elif code == SB_LINEDOWN:
                row += 1
            elif code == SB_PAGEUP:
                row -= page
            elif code == SB_PAGEDOWN:
                row += page
            elif code == SB_TOP:
                row = 0
            elif code == SB_BOTTOM:
                row = self.RowCount()
            elif code == SB_THUMBTRACK or code == SB_THUMBPOSITION:
                row = si.nTrackPos * self.sbScale_
            self.ScrollToRow(row)
            return 0
        if m == WM_HSCROLL:
            si = SCROLLINFO()
            si.cbSize = sizeof(SCROLLINFO)
            si.fMask = SIF_ALL
            GetScrollInfo(hwnd, SB_HORZ, byref(si))
            c = self.scrollCol_
            page = self.VisibleCols()
            code = LOWORD(w)
            if code == SB_LINELEFT:
                c -= 4
            elif code == SB_LINERIGHT:
                c += 4
            elif code == SB_PAGELEFT:
                c -= page
            elif code == SB_PAGERIGHT:
                c += page
            elif code == SB_LEFT:
                c = 0
            elif code == SB_THUMBTRACK or code == SB_THUMBPOSITION:
                c = si.nTrackPos
            if c < 0:
                c = 0
            self.scrollCol_ = c
            self.UpdateScrollbars()
            InvalidateRect(hwnd, None, False)
            self.UpdateCaretPos()
            return 0
        if m == WM_MOUSEWHEEL or m == WM_MOUSEHWHEEL:
            delta = GET_WHEEL_DELTA_WPARAM(w)
            if m == WM_MOUSEWHEEL and (GET_KEYSTATE_WPARAM(w) & MK_CONTROL):
                SendMessageW(GetParent(hwnd), WM_COMMAND, R.ID_VIEW_ZOOMIN if delta > 0 else R.ID_VIEW_ZOOMOUT, 0)
                return 0
            horiz = m == WM_MOUSEHWHEEL or bool(GET_KEYSTATE_WPARAM(w) & MK_SHIFT)
            self.wheelAccum_ += -delta if m == WM_MOUSEHWHEEL else delta
            lines = UINT(3)
            SystemParametersInfoW(SPI_GETWHEELSCROLLLINES, 0, byref(lines), 0)
            lines = lines.value
            if lines == _WHEEL_PAGESCROLL:
                lines = self.PageRows()
            notches = _tdiv(self.wheelAccum_, WHEEL_DELTA)
            if not notches:
                return 0
            self.wheelAccum_ -= notches * WHEEL_DELTA
            if horiz:
                if not self.wrap_:
                    c = self.scrollCol_ - notches * 8
                    self.scrollCol_ = 0 if c < 0 else c
                    self.UpdateScrollbars()
                    InvalidateRect(hwnd, None, False)
                    self.UpdateCaretPos()
            else:
                self.ScrollToRow(self.firstRow_ - notches * lines)
            return 0
        if m == WM_LBUTTONDOWN:
            SetFocus(hwnd)
            SetCapture(hwnd)
            x = GET_X_LPARAM(l)
            y = GET_Y_LPARAM(l)
            off = self.HitTest(x, y)
            self.dragLines_ = False
            if x < self.gutterW_ and not (w & MK_SHIFT):
                line = self.buf.LineOf(off)
                self.dragOrigin_ = line
                self.dragLines_ = True
                self.anchor_ = self.buf.LineStart(line)
                self.caret_ = self.buf.LineNext(line)
            elif w & MK_SHIFT:
                self.caret_ = off
            else:
                self.caret_ = self.anchor_ = off
            self.dragging_ = True
            SetTimer(hwnd, kTimerDrag, 40, None)
            self.buf.BreakUndoMerge()
            self.desiredCol_ = -1
            InvalidateRect(hwnd, None, False)
            self.UpdateCaretPos()
            self.Notify()
            return 0
        if m == WM_LBUTTONDBLCLK:
            x = GET_X_LPARAM(l)
            y = GET_Y_LPARAM(l)
            if x >= self.gutterW_:
                self.SelectWordAt(self.HitTest(x, y))
                InvalidateRect(hwnd, None, False)
                self.UpdateCaretPos()
                self.Notify()
            return 0
        if m == WM_MOUSEMOVE:
            if self.dragging_:
                off = self.HitTest(GET_X_LPARAM(l), GET_Y_LPARAM(l))
                if self.dragLines_:
                    line = self.buf.LineOf(off)
                    if line >= self.dragOrigin_:
                        self.anchor_ = self.buf.LineStart(self.dragOrigin_)
                        self.caret_ = self.buf.LineNext(line)
                    else:
                        self.anchor_ = self.buf.LineNext(self.dragOrigin_)
                        self.caret_ = self.buf.LineStart(line)
                else:
                    self.caret_ = off
                InvalidateRect(hwnd, None, False)
                self.UpdateCaretPos()
                self.Notify()
            return 0
        if m == WM_LBUTTONUP:
            if self.dragging_:
                self.dragging_ = False
                KillTimer(hwnd, kTimerDrag)
                ReleaseCapture()
            return 0
        if m == WM_CAPTURECHANGED:
            self.dragging_ = False
            KillTimer(hwnd, kTimerDrag)
            return 0
        if m == WM_CONTEXTMENU:
            x = GET_X_LPARAM(l)
            y = GET_Y_LPARAM(l)
            if x == -1 and y == -1:
                pt = POINT()
                GetCaretPos(byref(pt))
                ClientToScreen(hwnd, byref(pt))
                x = pt.x
                y = pt.y
            self.ContextMenu(x, y)
            return 0
        if m == WM_SETCURSOR:
            if LOWORD(l) == HTCLIENT:
                pt = POINT()
                GetCursorPos(byref(pt))
                ScreenToClient(hwnd, byref(pt))
                SetCursor(LoadCursorW(None, IDC_ARROW if pt.x < self.gutterW_ else IDC_IBEAM))
                return 1
        elif m == WM_KEYDOWN:
            self.OnKeyDown(w)
            return 0
        elif m == WM_CHAR:
            self.OnChar(w & 0xFFFF)
            return 0
        return DefWindowProcW(hwnd, m, w, l)
