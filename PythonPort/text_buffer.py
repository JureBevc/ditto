# ditto - port of text_buffer.h / text_buffer.cpp
#
# Piece-table text buffer over a memory-mapped (or heap-loaded) file with a
# block-based newline index, so multi-GB files open instantly and edits are cheap.
#
# Python interface notes (for callers in other modules):
#   * All text is UTF-8 `bytes`. Offsets are byte offsets (plain ints).
#   * ok, err = buf.Load(path, notify)          err is "" on success
#   * ok, err = buf.Save(path, progress=None)   progress: any object with a settable int `.value`
#                                               attribute (e.g. a tiny class instance); Save adds the
#                                               number of bytes written to it as it goes (for UTF-16 it
#                                               is set to the number of UTF-8 source bytes converted).
#                                               Plain attribute writes, so it is safe to read from another thread.
#   * buf.Read(off, len) -> bytes
#   * buf.At(off) -> int byte value (0 when off >= Length()), like (unsigned char)At(off) in C++.
#   * buf.Chunk(off) -> (src, start, avail): the contiguous bytes at `off` are src[start:start+avail].
#     `src` is the piece's backing store (bytes / bytearray / mapped-view object); it supports
#     len(), integer indexing (-> int), slicing (-> bytes), .find(sub, a, b) and .count(sub, a, b).
#     Do not keep it across edits/reloads. avail == 0 (src == b"") at/after the end.
#   * info = buf.Replace(off, delLen, s, n, caretBefore, mergeTyping=False)   s: bytes (or None when n == 0)
#   * info = buf.ReplaceMany(offs, len, repl, caretBefore)                    repl: bytes
#   * ok, info, caret = buf.Undo()  /  buf.Redo()   (on failure: (False, EditInfo(), 0))
import bisect
import enum
import msvcrt
import os
import threading
from ctypes import byref, c_int64, c_ubyte, string_at
from ctypes import wintypes as _wt

import common as C
import win32 as W

kEmpty = b""
_NL = b"\n"


class _View:
    """Read-only bytes-like view over raw memory (a mapped file view, possibly offset past a BOM).
    Supports len(), [int] -> int, [slice] -> bytes, count(sub, a, b), find(sub, a, b)."""
    __slots__ = ("addr", "n")
    _CH = 16 << 20

    def __init__(self, addr, n):
        self.addr = addr
        self.n = n

    def __len__(self):
        return self.n

    def __getitem__(self, k):
        if isinstance(k, slice):
            a, b, _ = k.indices(self.n)
            return string_at(self.addr + a, b - a) if b > a else b""
        if k < 0:
            k += self.n
        if not 0 <= k < self.n:
            raise IndexError("view index out of range")
        return c_ubyte.from_address(self.addr + k).value

    def _bounds(self, a, b):
        if a is None:
            a = 0
        if b is None or b > self.n:
            b = self.n
        if a < 0:
            a = 0
        return a, b

    def count(self, sub, a=None, b=None):
        a, b = self._bounds(a, b)
        c = 0
        ls = len(sub)
        if ls == 1:
            while a < b:
                e = min(b, a + self._CH)
                c += string_at(self.addr + a, e - a).count(sub)
                a = e
            return c
        return self[a:b].count(sub)

    def find(self, sub, a=None, b=None):
        a, b = self._bounds(a, b)
        ls = len(sub)
        step = 4096  # grow the window so nearby hits don't copy a large span
        while a < b:
            e = min(b, a + step)
            if step < self._CH:
                step <<= 1
            r = string_at(self.addr + a, e - a).find(sub)
            if r >= 0:
                return a + r
            if e >= b:
                break
            a = e - (ls - 1) if ls > 1 else e
        return -1


def CountNewlines(d, a, b):
    """Number of '\\n' in d[a:b]."""
    if b <= a:
        return 0
    return d.count(_NL, a, b)


def _ScanNth(d, s, e, skip):
    # position of the skip-th (0-based) '\n' in [s, e), or UINT64_MAX
    p = s
    # skip ahead quickly over whole sub-chunks (same result as the memchr loop)
    while skip > 64:
        q = min(e, p + 4096)
        if q <= p:
            break
        c = d.count(_NL, p, q)
        if c <= skip:
            skip -= c
            p = q
            if q >= e:
                return W.UINT64_MAX
        else:
            break
    while True:
        if p >= e:
            return W.UINT64_MAX
        p = d.find(_NL, p, e)
        if p < 0:
            return W.UINT64_MAX
        if not skip:
            return p
        skip -= 1
        p += 1


class NlIndex:
    """Newline index over a byte array: cum[k] = number of '\\n' in [0, k*kBlock).
    Only full blocks are indexed; the tail is scanned on demand."""
    kBlock = 1 << 16

    def __init__(self):
        self.cum = [0]
        self.done_ = 0  # written last by the indexer (after cum[k]); GIL makes it atomic

    def Reset(self, expectedLen):
        self.cum = [0] * (expectedLen // NlIndex.kBlock + 2)
        self.done_ = 0

    def Done(self):
        return self.done_

    def Prefix(self, d, x):  # newlines in [0, x)
        k = min(x // NlIndex.kBlock, self.Done())
        return self.cum[k] + CountNewlines(d, k * NlIndex.kBlock, x)

    def Count(self, d, a, b):  # newlines in [a, b)
        if b <= a:
            return 0
        if b - a <= 2 * NlIndex.kBlock:
            return CountNewlines(d, a, b)
        return self.Prefix(d, b) - self.Prefix(d, a)

    def FindNth(self, d, a, b, n):  # pos of n-th '\n' in [a,b)
        if b - a <= 2 * NlIndex.kBlock:
            return _ScanNth(d, a, b, n)
        target = self.Prefix(d, a) + n
        dn = self.Done()
        k = bisect.bisect_right(self.cum, target, 0, dn + 1) - 1
        start = k * NlIndex.kBlock
        if start < a:
            start = a
            skip = n
        else:
            skip = target - self.cum[k]
        return _ScanNth(d, start, b, skip)

    def Extend(self, d, newLen):  # single-threaded growth
        full = newLen // NlIndex.kBlock
        kB = NlIndex.kBlock
        cum = self.cum
        while self.done_ < full:
            k = self.done_
            if len(cum) < k + 2:
                cum.extend([0] * (k + 2 - len(cum)))
            cum[k + 1] = cum[k] + d.count(_NL, k * kB, (k + 1) * kB)
            self.done_ = k + 1


class Encoding(enum.IntEnum):
    Utf8 = 0
    Utf8Bom = 1
    Utf16LE = 2
    Utf16BE = 3


class Piece:
    __slots__ = ("start", "len", "nl", "src")  # src: 0 = original, 1 = add buffer

    def __init__(self, start=0, len=0, nl=0, src=0):
        self.start = start
        self.len = len
        self.nl = nl
        self.src = src

    def copy(self):
        return Piece(self.start, self.len, self.nl, self.src)

    def __repr__(self):
        return f"Piece(start={self.start}, len={self.len}, nl={self.nl}, src={self.src})"


class EditInfo:
    __slots__ = ("off", "delLen", "insLen", "line", "linesDel", "linesIns", "whole")

    def __init__(self):
        self.off = 0
        self.delLen = 0
        self.insLen = 0
        self.line = 0       # first affected line
        self.linesDel = 0   # newline counts
        self.linesIns = 0
        self.whole = False  # whole document replaced

    def __repr__(self):
        return ("EditInfo(off=%d, delLen=%d, insLen=%d, line=%d, linesDel=%d, linesIns=%d, whole=%r)" %
                (self.off, self.delLen, self.insLen, self.line, self.linesDel, self.linesIns, self.whole))


class _Rec:
    __slots__ = ("off", "removed", "inserted", "insLen", "delLen", "caretBefore", "caretAfter")

    def __init__(self):
        self.off = 0
        self.removed = []
        self.inserted = []
        self.insLen = 0
        self.delLen = 0
        self.caretBefore = 0
        self.caretAfter = 0


def _StatPath(path):
    fa = W.WIN32_FILE_ATTRIBUTE_DATA()
    if not W.GetFileAttributesExW(path, W.GetFileExInfoStandard, byref(fa)):
        return None
    t = (fa.ftLastWriteTime.dwHighDateTime << 32) | fa.ftLastWriteTime.dwLowDateTime
    sz = (fa.nFileSizeHigh << 32) | fa.nFileSizeLow
    return t, sz


def _WriteAll(h, data, progress=None):
    """data: bytes. Writes in <= 64 MB WriteFile calls."""
    n = len(data)
    p = 0
    w = _wt.DWORD()
    while n:
        want = min(n, 64 << 20)
        part = data if (p == 0 and want == len(data)) else data[p:p + want]
        if not W.WriteFile(h, part, want, byref(w), None) or w.value != want:
            return False
        p += w.value
        n -= w.value
        if progress is not None:
            progress.value += w.value
    return True


class TextBuffer:
    Rec = _Rec

    def __init__(self):
        self.encoding = Encoding.Utf8
        self.crlf = True
        self.mapped = False
        self.path = ""

        self.orig_ = kEmpty
        self.origLen_ = 0
        self.owned_ = b""          # heap copy for small / converted files
        self.add_ = bytearray()
        self.origIdx_ = NlIndex()
        self.addIdx_ = NlIndex()
        self.pieces_ = []
        self.prefBytes_ = [0]      # size len(pieces_)+1
        self.prefNl_ = [0]
        self.total_ = 0
        self.version_ = 1
        self.maxLineLen_ = 0
        self.maxLineAtomic_ = 0
        self.ready_ = False
        self.indexDone_ = False
        self.stop_ = False
        self.indexer_ = None

        self.undo_ = []
        self.undoPos_ = 0
        self.savePos_ = 0
        self.mergeOpen_ = False

        self.file_ = W.INVALID_HANDLE_VALUE
        self.map_ = None
        self.view_ = None
        self.pendingDelete_ = []
        self.diskTime_ = 0
        self.diskSize_ = 0
        self.LoadEmpty()

    def __del__(self):
        try:
            self.Close()
        except Exception:
            pass

    # ---- simple accessors ----
    def Ready(self):
        return self.ready_

    def Length(self):
        return self.total_

    def MaxLineLen(self):
        return self.maxLineLen_

    def BreakUndoMerge(self):
        self.mergeOpen_ = False

    def CanUndo(self):
        return self.undoPos_ > 0

    def CanRedo(self):
        return self.undoPos_ < len(self.undo_)

    def Modified(self):
        return self.undoPos_ != self.savePos_

    def Version(self):
        return self.version_

    def Src(self, s):
        return self.add_ if s else self.orig_

    def Idx(self, s):
        return self.addIdx_ if s else self.origIdx_

    # ------------------------------------------------------------------------------------

    def Close(self):
        self.stop_ = True
        t = self.indexer_
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join()
        self.indexer_ = None
        self.stop_ = False
        self.orig_ = kEmpty
        if self.view_:
            W.UnmapViewOfFile(self.view_)
        if self.map_:
            W.CloseHandle(self.map_)
        if self.file_ != W.INVALID_HANDLE_VALUE and self.file_:
            W.CloseHandle(self.file_)
        self.view_ = None
        self.map_ = None
        self.file_ = W.INVALID_HANDLE_VALUE
        for p in self.pendingDelete_:
            W.DeleteFileW(p)
        self.pendingDelete_ = []
        self.mapped = False
        self.owned_ = b""
        self.add_ = bytearray()
        self.pieces_ = []
        self.undo_ = []
        self.undoPos_ = self.savePos_ = 0
        self.mergeOpen_ = False

    def LoadEmpty(self):
        self.Close()
        self.orig_ = kEmpty
        self.origLen_ = 0
        self.origIdx_.Reset(0)
        self.addIdx_.Reset(0)
        self.prefBytes_ = [0]
        self.prefNl_ = [0]
        self.total_ = 0
        self.maxLineLen_ = 0
        self.ready_ = True
        self.indexDone_ = True
        self.encoding = Encoding.Utf8
        self.crlf = True
        self.path = ""
        self.version_ += 1

    def StatDisk(self):
        st = _StatPath(self.path)
        if st is not None:
            self.diskTime_, self.diskSize_ = st

    def ChangedOnDisk(self):
        if not self.path:
            return False
        st = _StatPath(self.path)
        if st is None:
            return False
        return st[0] != self.diskTime_ or st[1] != self.diskSize_

    def Load(self, p, notify):
        """Returns (ok, err)."""
        f = W.CreateFileW(p, W.GENERIC_READ, W.FILE_SHARE_READ | W.FILE_SHARE_DELETE, None, W.OPEN_EXISTING,
                          W.FILE_ATTRIBUTE_NORMAL, None)
        if (f is None or f == W.INVALID_HANDLE_VALUE) and W.GetLastError() == W.ERROR_SHARING_VIOLATION:
            f = W.CreateFileW(p, W.GENERIC_READ, W.FILE_SHARE_READ | W.FILE_SHARE_WRITE | W.FILE_SHARE_DELETE, None,
                              W.OPEN_EXISTING, W.FILE_ATTRIBUTE_NORMAL, None)
        if f is None or f == W.INVALID_HANDLE_VALUE:
            return False, C.FormatError(W.GetLastError())
        sz = c_int64(0)
        W.GetFileSizeEx(f, byref(sz))
        self.LoadEmpty()
        self.path = p
        size = sz.value
        kHeapLimit = 64 << 20
        data = kEmpty
        if size == 0:
            W.CloseHandle(f)
        elif size <= kHeapLimit:
            fd = msvcrt.open_osfhandle(f, os.O_RDONLY | os.O_BINARY)
            parts = []
            got = 0
            with open(fd, "rb", buffering=0) as fh:  # closes the handle
                while got < size:
                    try:
                        b = fh.read(min(size - got, 1 << 30))
                    except OSError:
                        b = None
                    if not b:
                        break
                    parts.append(b)
                    got += len(b)
            self.owned_ = parts[0] if len(parts) == 1 else b"".join(parts)
            size = got
            data = self.owned_
        else:
            self.map_ = W.CreateFileMappingW(f, None, W.PAGE_READONLY, 0, 0, None)
            if not self.map_:
                err = C.FormatError(W.GetLastError())
                W.CloseHandle(f)
                self.map_ = None
                return False, err
            self.view_ = W.MapViewOfFile(self.map_, W.FILE_MAP_READ, 0, 0, 0)
            if not self.view_:
                err = C.FormatError(W.GetLastError())
                W.CloseHandle(self.map_)
                self.map_ = None
                self.view_ = None
                W.CloseHandle(f)
                return False, err
            self.file_ = f
            self.mapped = True
            data = _View(self.view_, size)
        # encoding detection
        head = data[0:3] if size else b""
        if size >= 3 and head == b"\xEF\xBB\xBF":
            self.encoding = Encoding.Utf8Bom
            if isinstance(data, _View):
                data = _View(data.addr + 3, size - 3)
            else:
                data = data[3:]
                self.owned_ = data
            size -= 3
        elif size >= 2 and head[:2] in (b"\xFF\xFE", b"\xFE\xFF"):
            be = head[0] == 0xFE
            self.encoding = Encoding.Utf16BE if be else Encoding.Utf16LE
            units = (size - 2) // 2
            raw = data[2:2 + units * 2]
            conv = raw.decode("utf-16-be" if be else "utf-16-le", "replace").encode("utf-8", "surrogatepass")
            raw = None
            if self.mapped:
                data = None
                W.UnmapViewOfFile(self.view_)
                W.CloseHandle(self.map_)
                W.CloseHandle(self.file_)
                self.view_ = None
                self.map_ = None
                self.file_ = W.INVALID_HANDLE_VALUE
                self.mapped = False
            self.owned_ = conv
            data = self.owned_
            size = len(self.owned_)
        self.orig_ = data if size else kEmpty
        self.origLen_ = size
        nlp = self.orig_.find(_NL, 0, min(size, 1 << 16)) if size else -1
        if nlp >= 0:
            self.crlf = nlp > 0 and self.orig_[nlp - 1] == 0x0D
        self.StatDisk()
        if not size:
            return True, ""

        self.pieces_.append(Piece(0, size, 0, 0))
        self.prefBytes_ = [0, size]
        self.prefNl_ = [0, 0]
        self.total_ = size
        self.origIdx_.Reset(size)
        self.ready_ = False
        self.indexDone_ = False

        idx = self.origIdx_
        d = self.orig_

        def work():
            n = size
            kB = NlIndex.kBlock
            nBlocks = n // kB
            count = 0
            lineStart = 0
            maxLen = 0
            cum = idx.cum
            for k in range(nBlocks + 1):
                if self.stop_:
                    return
                s = k * kB
                e = min(n, s + kB)
                blk = d[s:e]
                c = blk.count(_NL)
                if c:
                    parts = blk.split(_NL)
                    first = s + len(parts[0]) - lineStart  # line continuing from before this block
                    if first > maxLen:
                        maxLen = first
                    if c > 1:
                        m = max(map(len, parts[1:-1]))
                        if m > maxLen:
                            maxLen = m
                    lineStart = e - len(parts[-1])
                    count += c
                if e - s == kB:
                    cum[k + 1] = count
                    idx.done_ = k + 1
            maxLen = max(maxLen, n - lineStart)
            self.maxLineAtomic_ = maxLen
            self.indexDone_ = True
            if notify:
                W.PostMessageW(notify, C.WM_APP_INDEXED, 0, 0)

        if size <= (4 << 20):
            work()
            self.OnIndexed()
        else:
            self.indexer_ = threading.Thread(target=work, daemon=True, name="TextBuffer-indexer")
            self.indexer_.start()
        return True, ""

    def Progress(self):
        if self.ready_ or not self.origLen_:
            return 1.0
        return min(1.0, float(self.origIdx_.Done() * NlIndex.kBlock) / float(self.origLen_))

    def OnIndexed(self):  # UI thread: finalize after background indexing
        if self.ready_ or not self.indexDone_:
            return
        t = self.indexer_
        if t is not None:
            if t is not threading.current_thread():
                t.join()
            self.indexer_ = None
        if self.pieces_:
            self.pieces_[0].nl = self.origIdx_.Count(self.orig_, 0, self.origLen_)
        self.RebuildPrefix(0)
        self.maxLineLen_ = self.maxLineAtomic_
        self.ready_ = True
        self.version_ += 1

    def RebuildPrefix(self, frm):
        pieces = self.pieces_
        n = len(pieces)
        pb = self.prefBytes_
        pn = self.prefNl_
        if len(pb) > n + 1:
            del pb[n + 1:]
            del pn[n + 1:]
        elif len(pb) < n + 1:
            pb.extend([0] * (n + 1 - len(pb)))
            pn.extend([0] * (n + 1 - len(pn)))
        pb[0] = 0
        pn[0] = 0
        b = pb[frm]
        l = pn[frm]
        for i in range(frm, n):
            p = pieces[i]
            b += p.len
            l += p.nl
            pb[i + 1] = b
            pn[i + 1] = l
        self.total_ = pb[n]

    def LineCount(self):
        if not self.ready_:
            idx = self.origIdx_
            return idx.cum[idx.Done()] + 1
        return self.prefNl_[-1] + 1

    def LineStart(self, line):
        if line == 0:
            return 0
        if line >= self.LineCount():
            return self.total_
        n = line - 1
        if not self.ready_:
            pos = self.origIdx_.FindNth(self.orig_, 0, self.origLen_, n)
            return self.total_ if pos == W.UINT64_MAX else pos + 1
        i = bisect.bisect_right(self.prefNl_, n) - 1
        if i >= len(self.pieces_):
            return self.total_
        p = self.pieces_[i]
        pos = self.Idx(p.src).FindNth(self.Src(p.src), p.start, p.start + p.len, n - self.prefNl_[i])
        if pos == W.UINT64_MAX:
            return self.total_
        return self.prefBytes_[i] + (pos - p.start) + 1

    def LineNext(self, line):  # start of next line (or Length)
        if line + 1 < self.LineCount():
            return self.LineStart(line + 1)
        if not self.ready_:
            # last partially indexed line: look ahead a bounded distance
            s = self.LineStart(line)
            lim = min(self.origLen_ - s, 1 << 20)
            q = self.orig_.find(_NL, s, s + lim)
            return q + 1 if q >= 0 else s + lim
        return self.total_

    def LineEnd(self, line):  # excluding line terminator
        s = self.LineStart(line)
        nx = self.LineNext(line)
        e = nx
        if e > s and self.At(e - 1) == 0x0A:
            e -= 1
            if e > s and self.At(e - 1) == 0x0D:
                e -= 1
        return e

    def LineOf(self, off):
        if off > self.total_:
            off = self.total_
        if not self.ready_:
            return self.origIdx_.Prefix(self.orig_, off)
        i = self.PieceAt(off)
        if i >= len(self.pieces_):
            return self.prefNl_[-1]
        p = self.pieces_[i]
        return self.prefNl_[i] + self.Idx(p.src).Count(self.Src(p.src), p.start, p.start + (off - self.prefBytes_[i]))

    def PieceAt(self, off):
        # index of piece containing off (len(pieces_) if off == total)
        if off >= self.total_:
            return len(self.pieces_)
        return bisect.bisect_right(self.prefBytes_, off) - 1

    def Read(self, off, ln):
        """Returns bytes [off, off+ln) clipped to the buffer."""
        if off >= self.total_:
            return b""
        ln = min(ln, self.total_ - off)
        i = self.PieceAt(off)
        pieces = self.pieces_
        out = []
        while ln and i < len(pieces):
            p = pieces[i]
            within = off - self.prefBytes_[i]
            take = min(p.len - within, ln)
            a = p.start + within
            out.append(self.Src(p.src)[a:a + take])
            off += take
            ln -= take
            i += 1
        if len(out) == 1:
            r = out[0]
            return r if type(r) is bytes else bytes(r)
        return b"".join(out)

    def At(self, off):
        """Byte value (int) at off, 0 if out of range."""
        i = self.PieceAt(off)
        if i >= len(self.pieces_):
            return 0
        p = self.pieces_[i]
        return self.Src(p.src)[p.start + (off - self.prefBytes_[i])]

    def Chunk(self, off):
        """Contiguous bytes at off: returns (src, start, avail); the bytes are src[start:start+avail]."""
        i = self.PieceAt(off)
        if i >= len(self.pieces_):
            return kEmpty, 0, 0
        p = self.pieces_[i]
        within = off - self.prefBytes_[i]
        return self.Src(p.src), p.start + within, p.len - within

    def MakePiece(self, src, start, ln):
        return Piece(start, ln, self.Idx(src).Count(self.Src(src), start, start + ln), src)

    def SplitAt(self, off):
        # ensure a piece boundary at off; returns index of piece starting there
        i = self.PieceAt(off)
        if i >= len(self.pieces_) or self.prefBytes_[i] == off:
            return i
        p = self.pieces_[i]
        leftLen = off - self.prefBytes_[i]
        L = self.MakePiece(p.src, p.start, leftLen)
        R = Piece(p.start + leftLen, p.len - leftLen, p.nl - L.nl, p.src)
        self.pieces_[i] = L
        self.pieces_.insert(i + 1, R)
        self.prefBytes_.insert(i + 1, off)
        self.prefNl_.insert(i + 1, self.prefNl_[i] + L.nl)
        return i + 1

    def Splice(self, off, delLen, ins):
        """Returns (removed pieces, EditInfo)."""
        info = EditInfo()
        info.off = off
        info.delLen = delLen
        info.line = self.LineOf(off)
        a = self.SplitAt(off)
        b = self.SplitAt(off + delLen)
        pieces = self.pieces_
        removed = pieces[a:b]
        for p in removed:
            info.linesDel += p.nl
        pieces[a:b] = [p.copy() for p in ins]
        for p in ins:
            info.insLen += p.len
            info.linesIns += p.nl
        # merge contiguous neighbours around the edit
        lo = a - 1 if a > 0 else 0
        hi = min(len(pieces), a + len(ins) + 1)
        i = lo
        while i + 1 < hi:
            x = pieces[i]
            y = pieces[i + 1]
            if x.src == y.src and x.start + x.len == y.start:
                x.len += y.len
                x.nl += y.nl
                del pieces[i + 1]
                hi -= 1
            else:
                i += 1
        self.RebuildPrefix(lo)
        info.whole = (off == 0 and delLen > 0 and off + info.insLen == self.total_ and len(removed) > 1)
        self.version_ += 1
        return removed, info

    def Slice(self, a, b):
        out = []
        i = self.PieceAt(a)
        pieces = self.pieces_
        while a < b and i < len(pieces):
            p = pieces[i]
            within = a - self.prefBytes_[i]
            take = min(p.len - within, b - a)
            if within == 0 and take == p.len:
                out.append(p.copy())
            else:
                out.append(self.MakePiece(p.src, p.start + within, take))
            a += take
            i += 1
        return out

    def PushRec(self, r):
        del self.undo_[self.undoPos_:]
        if self.savePos_ > self.undoPos_:
            self.savePos_ = W.SIZE_MAX
        self.undo_.append(r)
        self.undoPos_ += 1

    def Replace(self, off, delLen, s, n, caretBefore, mergeTyping=False):
        """Editing (only when Ready()). mergeTyping coalesces consecutive single inserts into one undo step.
        s: bytes-like (first n bytes are inserted) or None when n == 0. Returns EditInfo."""
        info = EditInfo()
        if not self.ready_:
            return info
        off = min(off, self.total_)
        delLen = min(delLen, self.total_ - off)
        if not delLen and not n:
            return info
        ins = []
        if n:
            st = len(self.add_)
            self.add_ += s[:n] if len(s) != n else s
            self.addIdx_.Extend(self.add_, len(self.add_))
            ins.append(self.MakePiece(1, st, n))
        if mergeTyping and self.mergeOpen_ and delLen == 0 and self.undoPos_ > 0 and self.undoPos_ == len(self.undo_):
            last = self.undo_[-1]
            if last.off + last.insLen == off:
                _, info = self.Splice(off, 0, ins)
                if (last.inserted and last.inserted[-1].src == 1 and
                        last.inserted[-1].start + last.inserted[-1].len == ins[0].start):
                    last.inserted[-1].len += ins[0].len
                    last.inserted[-1].nl += ins[0].nl
                else:
                    last.inserted.append(ins[0])
                last.insLen += n
                last.caretAfter = off + n
                if self.savePos_ == self.undoPos_:
                    self.savePos_ = W.SIZE_MAX
                return info
        r = _Rec()
        r.off = off
        r.removed, info = self.Splice(off, delLen, ins)
        r.inserted = ins
        r.insLen = n
        r.delLen = delLen
        r.caretBefore = caretBefore
        r.caretAfter = off + n
        self.PushRec(r)
        self.mergeOpen_ = mergeTyping
        return info

    def ReplaceMany(self, offs, ln, repl, caretBefore):
        """Replace every range [o, o+ln) for o in offs (ascending, non-overlapping) with repl (bytes)."""
        info = EditInfo()
        if not self.ready_ or not offs:
            return info
        R = None
        if repl:
            st = len(self.add_)
            self.add_ += repl
            self.addIdx_.Extend(self.add_, len(self.add_))
            R = self.MakePiece(1, st, len(repl))
        np = []
        cur = 0
        for o in offs:
            np.extend(self.Slice(cur, o))
            if R is not None:
                np.append(R)
            cur = o + ln
        np.extend(self.Slice(cur, self.total_))
        oldTotal = self.total_
        r = _Rec()
        r.off = 0
        r.removed, info = self.Splice(0, self.total_, np)
        r.inserted = np
        r.insLen = self.total_
        r.delLen = oldTotal
        r.caretBefore = caretBefore
        r.caretAfter = min(caretBefore, self.total_)
        self.PushRec(r)
        self.mergeOpen_ = False
        info.whole = True
        return info

    def Undo(self):
        """Returns (ok, info, caret)."""
        if not self.CanUndo():
            return False, EditInfo(), 0
        self.undoPos_ -= 1
        r = self.undo_[self.undoPos_]
        _, info = self.Splice(r.off, r.insLen, r.removed)
        caret = r.caretBefore
        self.mergeOpen_ = False
        if len(r.removed) + len(r.inserted) > 64:
            info.whole = True
        return True, info, caret

    def Redo(self):
        """Returns (ok, info, caret)."""
        if not self.CanRedo():
            return False, EditInfo(), 0
        r = self.undo_[self.undoPos_]
        self.undoPos_ += 1
        _, info = self.Splice(r.off, r.delLen, r.inserted)
        caret = r.caretAfter
        self.mergeOpen_ = False
        if len(r.removed) + len(r.inserted) > 64:
            info.whole = True
        return True, info, caret

    def Save(self, target, progress=None):
        """Returns (ok, err). progress (optional): object with an int `.value` attribute that receives the
        number of bytes written so far; safe to read from another thread."""
        samePath = bool(self.path) and C.PathEqualsI(target, self.path)
        viaTemp = self.mapped and samePath
        dest = target + ".~mye-tmp" if viaTemp else target
        h = W.CreateFileW(dest, W.GENERIC_WRITE, W.FILE_SHARE_READ, None, W.OPEN_ALWAYS, W.FILE_ATTRIBUTE_NORMAL, None)
        if h is None or h == W.INVALID_HANDLE_VALUE:
            return False, C.FormatError(W.GetLastError())
        ok = True
        if self.encoding == Encoding.Utf8Bom:
            ok = _WriteAll(h, b"\xEF\xBB\xBF")
        if self.encoding == Encoding.Utf16LE or self.encoding == Encoding.Utf16BE:
            be = self.encoding == Encoding.Utf16BE
            ok = _WriteAll(h, b"\xFE\xFF" if be else b"\xFF\xFE")
            off = 0
            total = self.total_
            while ok and off < total:
                take = min(total - off, 1 << 20)
                chunk = self.Read(off, take)
                # don't cut a UTF-8 sequence
                n = len(chunk)
                if off + n < total:
                    while n > 0 and C.IsUtf8Cont(chunk[n - 1]):
                        n -= 1
                    if n > 0 and chunk[n - 1] >= 0xC0:
                        n -= 1
                    if not n:
                        n = len(chunk)
                w = chunk[:n].decode("utf-8", "replace").encode("utf-16-be" if be else "utf-16-le", "surrogatepass")
                ok = _WriteAll(h, w)
                if progress is not None:
                    progress.value = off + n
                off += n
        else:
            for p in self.pieces_:
                if not ok:
                    break
                src = self.Src(p.src)
                pos = p.start
                end = p.start + p.len
                while ok and pos < end:  # copy at most 64 MB at a time out of the source
                    e = min(end, pos + (64 << 20))
                    ok = _WriteAll(h, bytes(src[pos:e]), progress)
                    pos = e
        if ok:
            ok = W.SetEndOfFile(h) != 0
        e = W.GetLastError()
        W.CloseHandle(h)
        if not ok:
            err = C.FormatError(e)
            if viaTemp:
                W.DeleteFileW(dest)
            return False, err
        if viaTemp:
            old = target + ".~mye-old" + str(W.GetTickCount64())
            if not W.MoveFileExW(target, old, 0):
                err = C.FormatError(W.GetLastError())
                W.DeleteFileW(dest)
                return False, err
            W.SetFileAttributesW(old, W.FILE_ATTRIBUTE_HIDDEN)
            if not W.MoveFileExW(dest, target, W.MOVEFILE_REPLACE_EXISTING | W.MOVEFILE_COPY_ALLOWED):
                err = C.FormatError(W.GetLastError())
                W.MoveFileExW(old, target, 0)
                return False, err
            self.pendingDelete_.append(old)
        self.path = target
        self.savePos_ = self.undoPos_
        self.mergeOpen_ = False
        self.StatDisk()
        return True, ""
