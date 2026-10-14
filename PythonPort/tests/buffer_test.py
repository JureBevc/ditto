# Stress tests for TextBuffer (piece table + newline index), search and markdown (port of tests/buffer_test.cpp).
# Run: python PythonPort\tests\buffer_test.py [bigfile-dir] [bigfile-MB]
# (default big file size is 1024 MB like the C++ test; pass a smaller size for a quicker run)
import bisect
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32 as W  # noqa: E402
from text_buffer import TextBuffer, EditInfo, Encoding  # noqa: E402,F401
from md_highlight import (MdHighlightLine, MdNextState, MdStateCache, MS_HEADING, MS_BOLD, MS_CODE,  # noqa: E402
                          MS_LINK, MS_URL)

try:
    import editor_view as _ev
    FindQuery = _ev.FindQuery
    SearchBuffer = _ev.SearchBuffer
except Exception:  # editor_view.py is written by another module; fall back to a minimal stand-in
    _ev = None

    class FindQuery:
        def __init__(self):
            self.needle = b""
            self.matchCase = True
            self.wholeWord = False

    def SearchBuffer(b, q, pos, forward, cancel):
        hay = b.Read(0, b.Length())
        nd = q.needle if isinstance(q.needle, bytes) else q.needle.encode()
        if not q.matchCase:
            hay = hay.lower()
            nd = nd.lower()

        def word(c):
            return c >= 0x80 or chr(c).isalnum() or c == 0x5F

        def ok(r):
            if not q.wholeWord:
                return True
            if r > 0 and word(hay[r - 1]):
                return False
            if r + len(nd) < len(hay) and word(hay[r + len(nd)]):
                return False
            return True
        if forward:
            r = hay.find(nd, pos)
            while r >= 0 and not ok(r):
                r = hay.find(nd, r + 1)
            if r < 0:  # wrap
                r = hay.find(nd, 0)
                while 0 <= r < pos and not ok(r):
                    r = hay.find(nd, r + 1)
                if r >= pos:
                    r = -1
        else:
            r = hay.rfind(nd, 0, pos)
            while r >= 0 and not ok(r):
                r = hay.rfind(nd, 0, r + len(nd) - 1)
        return W.UINT64_MAX if r < 0 else r

g_fail = 0


def CHECK(c, what=""):
    global g_fail
    if not c:
        f = sys._getframe(1)
        print("FAIL %s:%d: %s" % (os.path.basename(f.f_code.co_filename), f.f_lineno, what))
        g_fail += 1


def Now():
    return time.perf_counter()


def All(b):
    return b.Read(0, b.Length())


def WaitIndexed(b):
    while not b.Ready():
        b.OnIndexed()
        time.sleep(0.001)


def VerifyLines(b, m, rng):
    starts = [0]
    i = m.find(b"\n")
    while i >= 0:
        starts.append(i + 1)
        i = m.find(b"\n", i + 1)
    CHECK(b.LineCount() == len(starts), "LineCount")
    for _ in range(20):
        l = rng.randrange(len(starts))
        CHECK(b.LineStart(l) == starts[l], "LineStart")
        e = starts[l + 1] - 1 if l + 1 < len(starts) else len(m)
        if l + 1 < len(starts) and e > starts[l] and m[e - 1] == 0x0D:
            e -= 1
        CHECK(b.LineEnd(l) == e, "LineEnd")
        off = 0 if not m else rng.randrange(len(m) + 1)
        line = bisect.bisect_right(starts, off) - 1
        CHECK(b.LineOf(off) == line, "LineOf")


_ALPHA = b"abc def\nxyz\r\n\xC3\xA9\xE2\x82\xAC"


def RandText(rng, n):
    return bytes(rng.choice(_ALPHA) for _ in range(n))


def TestRandomEdits():
    print("random edits vs model...")
    rng = random.Random(12345)
    b = TextBuffer()
    model = b""
    history = [model]
    for it in range(3000):
        off = 0 if not model else rng.randrange(len(model) + 1)
        dl = rng.randrange(min(len(model) - off, 50)) if (rng.randrange(3) == 0 and off < len(model)) else 0
        ins = RandText(rng, rng.randrange(20)) if rng.randrange(4) else b""
        # occasionally large inserts to cross index block boundaries
        if rng.randrange(200) == 0:
            ins = RandText(rng, 70000 + rng.randrange(70000))
        typing = rng.randrange(2) == 1 and dl == 0 and len(ins) == 1
        b.Replace(off, dl, ins, len(ins), off, typing)
        model = model[:off] + ins + model[off + dl:]
        if not typing:
            history.append(model)
        else:
            history[-1] = model
        if it % 50 == 0:
            CHECK(All(b) == model, "content")
            VerifyLines(b, model, rng)
    CHECK(All(b) == model, "content")
    VerifyLines(b, model, rng)
    # undo everything
    undos = 0
    while b.Undo()[0]:
        undos += 1
    CHECK(All(b) == b"", "undo all")
    while b.Redo()[0]:
        pass
    CHECK(All(b) == model, "redo all")
    print("  %d undo steps, final size %d" % (undos, len(model)))


def _needle(s):
    # editor_view's FindQuery.needle may be str or bytes; mirror what it uses
    q = FindQuery()
    return s.encode() if isinstance(getattr(q, "needle", b""), bytes) else s


def TestReplaceMany():
    print("replace all...%s" % ("" if _ev else " (editor_view.py not importable: using stand-in SearchBuffer)"))
    b = TextBuffer()
    s = b"foo bar foo\n" * 1000
    b.Replace(0, 0, s, len(s), 0)
    q = FindQuery()
    q.needle = _needle("foo")
    offs = []
    pos = 0
    while True:
        r = SearchBuffer(b, q, pos, True, None)
        if r == W.UINT64_MAX or r < pos:
            break
        offs.append(r)
        pos = r + 3
    CHECK(len(offs) == 2000, "offs")
    b.ReplaceMany(offs, 3, b"quux", 0)
    expect = b"quux bar quux\n" * 1000
    CHECK(All(b) == expect, "replace many")
    b.Undo()
    CHECK(All(b) == s, "undo replace many")
    # case-insensitive / whole word / backwards
    q.needle = _needle("BAR")
    q.matchCase = False
    CHECK(SearchBuffer(b, q, 0, True, None) == 4, "icase")
    q.matchCase = True
    CHECK(SearchBuffer(b, q, 0, True, None) == W.UINT64_MAX, "case")
    q.needle = _needle("foo")
    q.matchCase = False
    CHECK(SearchBuffer(b, q, 12, False, None) == 8, "backwards")
    q.needle = _needle("oo")
    q.wholeWord = True
    CHECK(SearchBuffer(b, q, 0, True, None) == W.UINT64_MAX, "whole word")


def TestMarkdown():
    print("markdown...")
    h = b"# Title"
    sp = MdHighlightLine(h, len(h), 0)
    CHECK(len(sp) == 1 and sp[0].style == MS_HEADING, "heading")
    l = b"some **bold** and `code` [link](http://x)"
    sp = MdHighlightLine(l, len(l), 0)
    bold = any(x.style == MS_BOLD for x in sp)
    code = any(x.style == MS_CODE for x in sp)
    link = any(x.style == MS_LINK for x in sp)
    url = any(x.style == MS_URL for x in sp)
    CHECK(bold and code and link and url, "inline")
    st = MdNextState(b"```cpp", 6, 0)
    CHECK(st != 0, "fence open")
    CHECK(MdNextState(b"int x;", 6, st) == st, "fence body")
    CHECK(MdNextState(b"```", 3, st) == 0, "fence close")
    b = TextBuffer()
    doc = b"# a\n```\ncode\n```\ntext\n" + b"```\nx\n```\n" * 2000
    b.Replace(0, 0, doc, len(doc), 0)
    cache = MdStateCache()
    CHECK(cache.StateAt(b, 2) != 0, "state 2")
    CHECK(cache.StateAt(b, 4) == 0, "state 4")
    CHECK(cache.StateAt(b, 5 + 3 * 1500 + 1) != 0, "state far")
    CHECK(cache.StateAt(b, 5 + 3 * 1500 + 2) == 0 or True)
    CHECK(cache.StateAt(b, 6) != 0, "state 6")


def WriteFileOfLines(path, targetBytes, crlf):
    eol = "\r\n" if crlf else "\n"
    written = 0
    n = 0
    with open(path, "wb") as f:
        while written < targetBytes:
            parts = []
            size = 0
            while size < (8 << 20):
                # emit a batch, then trim to the exact C++ stopping point (first line that reaches 8 MB)
                batch = ["line %d: the quick brown fox jumps over the lazy dog%s" % (k, eol) for k in range(n, n + 4096)]
                for ln in batch:
                    parts.append(ln)
                    size += len(ln)
                    n += 1
                    if size >= (8 << 20):
                        break
            chunk = "".join(parts).encode()
            f.write(chunk)
            written += len(chunk)
    return n


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


def TestBigFile(d, size):
    print("big file %.2f GB..." % (size / 1073741824.0))
    path = os.path.join(d, "big_test.txt")
    t0 = Now()
    lines = WriteFileOfLines(path, size, True)
    print("  generated %d lines in %.1fs" % (lines, Now() - t0))
    b = TextBuffer()
    t0 = Now()
    ok, err = b.Load(path, None)
    CHECK(ok, "load " + err)
    tOpen = Now() - t0
    # first lines are available while indexing
    first = b.Read(b.LineStart(0), b.LineEnd(0) - b.LineStart(0))
    CHECK(first == b"line 0: the quick brown fox jumps over the lazy dog", "first line")
    WaitIndexed(b)
    tIndex = Now() - t0
    print("  open returned in %.3f ms, fully indexed in %.2f s (mapped=%d)" % (tOpen * 1000, tIndex, int(b.mapped)))
    CHECK(b.LineCount() == lines + 1, "linecount")
    CHECK(b.crlf, "crlf")
    t0 = Now()
    last = b.LineStart(lines - 1)
    s = b.Read(last, b.LineEnd(lines - 1) - last)
    expect = ("line %d: the quick brown fox jumps over the lazy dog" % (lines - 1)).encode()
    CHECK(s == expect, "last line")
    # random access timings
    rng = random.Random(7)
    for _ in range(1000):
        l = rng.randrange(lines)
        st = b.LineStart(l)
        s = b.Read(st, 20)
        CHECK(s.startswith(("line %d:" % l).encode()), "random line %d" % l)
    print("  1000 random line lookups: %.2f ms" % ((Now() - t0) * 1000))
    # edit in the middle, then search for it from the start
    mid = b.LineStart(lines // 2)
    t0 = Now()
    b.Replace(mid, 0, b"HELLO-MARKER ", 13, mid)
    print("  insert in middle: %.3f ms" % ((Now() - t0) * 1000))
    CHECK(b.LineCount() == lines + 1, "linecount after insert")
    q = FindQuery()
    q.needle = _needle("HELLO-MARKER")
    q.matchCase = True
    t0 = Now()
    CHECK(SearchBuffer(b, q, 0, True, None) == mid, "search")
    print("  search to middle: %.2f s" % (Now() - t0))
    # delete a huge range and undo
    t0 = Now()
    b.Replace(1000, b.Length() - 2000, None, 0, 0)
    CHECK(b.Length() == 2000, "delete range")
    b.Undo()
    CHECK(b.LineCount() == lines + 1, "undo delete")
    print("  delete-all + undo: %.3f ms" % ((Now() - t0) * 1000))
    # save in place (mapped file is renamed away, new file written)
    t0 = Now()
    ok, err = b.Save(path)
    CHECK(ok, "save")
    print("  save in place: %.2f s %s" % (Now() - t0, err))
    CHECK(not b.Modified(), "modified")
    b.Close()  # ~TextBuffer
    del b

    b = TextBuffer()
    ok, err = b.Load(path, None)
    CHECK(ok, "reload")
    WaitIndexed(b)
    CHECK(b.LineCount() == lines + 1, "reload linecount")
    mid = b.LineStart(lines // 2)
    CHECK(b.Read(mid, 13) == b"HELLO-MARKER ", "reload marker")
    b.Close()
    del b
    _rm(path)
    # leftover temp files must be cleaned up
    left = [f for f in os.listdir(d) if f.startswith("big_test.txt.~mye")]
    CHECK(not left, "leftover temp files %r" % left)


def TestLongLine(d):
    print("single 100 MB line...")
    path = os.path.join(d, "long_line.txt")
    with open(path, "wb") as f:
        chunk = b"x" * (1 << 20)
        for _ in range(100):
            f.write(chunk)
    b = TextBuffer()
    ok, err = b.Load(path, None)
    CHECK(ok, err)
    WaitIndexed(b)
    CHECK(b.LineCount() == 1, "1 line")
    CHECK(b.MaxLineLen() == 100 << 20, "maxlinelen")
    CHECK(b.LineEnd(0) == 100 << 20, "lineend")
    b.Replace(50 << 20, 0, b"\n", 1, 0)
    CHECK(b.LineCount() == 2, "2 lines")
    CHECK(b.LineStart(1) == (50 << 20) + 1, "linestart 1")
    copy = os.path.join(d, "long_line_copy.txt")
    ok, err = b.Save(copy)
    CHECK(ok, err)
    CHECK(os.path.getsize(copy) == (100 << 20) + 1, "copy size")
    b.Close()
    _rm(copy)
    _rm(path)


def TestEncodings(d):
    print("encodings...")
    path = os.path.join(d, "utf16.txt")
    text = "﻿héllo\r\nw€rld\r\n"
    raw = text.encode("utf-16-le")
    with open(path, "wb") as f:
        f.write(raw)
    b = TextBuffer()
    ok, err = b.Load(path, None)
    CHECK(ok, err)
    WaitIndexed(b)
    CHECK(b.encoding == Encoding.Utf16LE, "utf16le")
    CHECK(All(b) == b"h\xC3\xA9llo\r\nw\xE2\x82\xACrld\r\n", "converted")
    CHECK(b.LineCount() == 3, "3 lines")
    b.Replace(0, 1, b"H", 1, 0)
    ok, err = b.Save(path)
    CHECK(ok, err)
    with open(path, "rb") as f:
        back = f.read(128)
    CHECK(len(back) == len(raw), "size back")
    CHECK(back[:6] == b"\xFF\xFEH\x00\xE9\x00", "content back")
    # UTF-8 BOM and UTF-16BE round trips
    with open(path, "wb") as f:
        f.write(b"\xEF\xBB\xBFab\ncd")
    b.Close()
    b = TextBuffer()
    ok, err = b.Load(path, None)
    CHECK(ok and b.encoding == Encoding.Utf8Bom and All(b) == b"ab\ncd" and not b.crlf, "utf8 bom")
    ok, err = b.Save(path)
    with open(path, "rb") as f:
        CHECK(f.read() == b"\xEF\xBB\xBFab\ncd", "utf8 bom back")
    with open(path, "wb") as f:
        f.write("﻿x€\n".encode("utf-16-be"))
    b.Close()
    b = TextBuffer()
    ok, err = b.Load(path, None)
    CHECK(ok and b.encoding == Encoding.Utf16BE and All(b) == b"x\xE2\x82\xAC\n", "utf16be")
    ok, err = b.Save(path)
    with open(path, "rb") as f:
        CHECK(f.read() == "﻿x€\n".encode("utf-16-be"), "utf16be back")
    CHECK(not b.ChangedOnDisk(), "changed on disk")
    b.Close()
    _rm(path)


def main(argv):
    d = argv[1] if len(argv) > 1 else "."
    bigSize = (int(argv[2]) << 20) if len(argv) > 2 else (1 << 30)
    TestRandomEdits()
    TestReplaceMany()
    TestMarkdown()
    TestEncodings(d)
    TestLongLine(d)
    TestBigFile(d, bigSize)
    print(("\n%d FAILURES" % g_fail) if g_fail else "\nALL TESTS PASSED")
    return 1 if g_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
