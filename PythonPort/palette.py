# Command palette / quick open (F1, Ctrl+Shift+P, Ctrl+P), modelled on VS Code:
#   ">query"  run a command      "query"  open a file from the folder      ":N"  go to line N
# Ctrl+R shows the same popup as a recent-folder picker.
# (port of palette.cpp, Palette:: in main_window.h)
import bisect
import collections
import heapq
import itertools
import os
import stat
import threading
import time
from array import array

import common as C
import win32 as W
from win32 import *  # noqa: F401,F403
import resource as R
import app
import main_window as MW

WA_INACTIVE = 0


class Cmd:
    __slots__ = ("label", "id", "key")

    def __init__(self, label, id, key):
        self.label = label
        self.id = id
        self.key = key


kCommands = [Cmd(*c) for c in (
    ("File: New File", R.ID_FILE_NEW, "Ctrl+N"),
    ("File: Open File...", R.ID_FILE_OPEN, "Ctrl+O"),
    ("File: Open Folder...", R.ID_FILE_OPENFOLDER, "Ctrl+Shift+O"),
    ("File: Open Recent Folder...", R.ID_FILE_OPENRECENT, "Ctrl+R"),
    ("File: Save", R.ID_FILE_SAVE, "Ctrl+S"),
    ("File: Save As...", R.ID_FILE_SAVEAS, "Ctrl+Shift+S"),
    ("File: Save All", R.ID_FILE_SAVEALL, ""),
    ("File: Close Editor", R.ID_FILE_CLOSETAB, "Ctrl+W"),
    ("File: Close Folder", R.ID_FILE_CLOSEFOLDER, ""),
    ("File: Reveal in File Explorer", R.ID_FILE_REVEAL, "Alt+Shift+R"),
    ("File: Exit", R.ID_FILE_EXIT, "Alt+F4"),
    ("Edit: Undo", R.ID_EDIT_UNDO, "Ctrl+Z"),
    ("Edit: Redo", R.ID_EDIT_REDO, "Ctrl+Y"),
    ("Edit: Select All", R.ID_EDIT_SELECTALL, "Ctrl+A"),
    ("Edit: Find", R.ID_FIND_SHOW, "Ctrl+F"),
    ("Edit: Replace", R.ID_FIND_REPLACE, "Ctrl+H"),
    ("Edit: Find Next", R.ID_FIND_NEXT, "F3"),
    ("Edit: Find Previous", R.ID_FIND_PREV, "Shift+F3"),
    ("Search: Find in Files", R.ID_VIEW_SEARCH, "Ctrl+Shift+F"),
    ("Go to Line...", R.ID_PALETTE_GOTO, "Ctrl+G"),
    ("Go to File...", R.ID_PALETTE_FILES, "Ctrl+P"),
    ("View: Show Explorer", R.ID_VIEW_EXPLORER, "Ctrl+Shift+E"),
    ("View: Show Search", R.ID_VIEW_SEARCH, "Ctrl+Shift+F"),
    ("View: Show Source Control", R.ID_VIEW_SCM, "Ctrl+Shift+G"),
    ("View: Toggle Side Bar", R.ID_VIEW_SIDEBAR, "Ctrl+B"),
    ("View: Toggle Output Panel", R.ID_VIEW_OUTPUT, "Ctrl+Shift+U"),
    ("View: Toggle Terminal", R.ID_VIEW_TERMINAL, "Ctrl+`"),
    ("Terminal: Create New Terminal", R.ID_TERM_NEW, "Ctrl+Shift+`"),
    ("Terminal: Split Terminal", R.ID_TERM_SPLIT, "Ctrl+Shift+5"),
    ("Terminal: Kill the Active Terminal", R.ID_TERM_KILL, ""),
    ("View: Toggle Word Wrap", R.ID_VIEW_WRAP, "Alt+Z"),
    ("View: Toggle Dark Theme", R.ID_VIEW_DARK, ""),
    ("View: Change Font...", R.ID_VIEW_FONT, ""),
    ("View: Zoom In", R.ID_VIEW_ZOOMIN, "Ctrl+="),
    ("View: Zoom Out", R.ID_VIEW_ZOOMOUT, "Ctrl+-"),
    ("View: Reset Zoom", R.ID_VIEW_ZOOMRESET, "Ctrl+0"),
    ("View: Next Editor", R.ID_VIEW_NEXTTAB, "Ctrl+Tab"),
    ("View: Previous Editor", R.ID_VIEW_PREVTAB, "Ctrl+Shift+Tab"),
    ("Git: Initialize Repository", R.ID_GIT_INIT, ""),
    ("Git: Clone...", R.ID_GIT_CLONE, ""),
    ("Git: Publish to GitHub...", R.ID_GIT_PUBLISH, ""),
    ("Git: Commit", R.ID_GIT_COMMIT, "Ctrl+Enter"),
    ("Git: Commit Staged", R.ID_GIT_COMMIT_STAGED, ""),
    ("Git: Commit All", R.ID_GIT_COMMIT_ALL, ""),
    ("Git: Commit (Amend)", R.ID_GIT_COMMIT_AMEND, ""),
    ("Git: Undo Last Commit", R.ID_GIT_UNDO_COMMIT, ""),
    ("Git: Stage All Changes", R.ID_GIT_STAGE_ALL, ""),
    ("Git: Unstage All Changes", R.ID_GIT_UNSTAGE_ALL, ""),
    ("Git: Discard All Changes", R.ID_GIT_DISCARD_ALL, ""),
    ("Git: Pull", R.ID_GIT_PULL, ""),
    ("Git: Push", R.ID_GIT_PUSH, ""),
    ("Git: Sync", R.ID_GIT_SYNC, ""),
    ("Git: Fetch", R.ID_GIT_FETCH, ""),
    ("Git: Force Push (with lease)", R.ID_GIT_PUSH_FORCE, ""),
    ("Git: Checkout to...", R.ID_GIT_CHECKOUT, ""),
    ("Git: Create Branch...", R.ID_GIT_BRANCH_CREATE, ""),
    ("Git: Delete Branch...", R.ID_GIT_BRANCH_DELETE, ""),
    ("Git: Merge Branch...", R.ID_GIT_MERGE, ""),
    ("Git: Stash", R.ID_GIT_STASH, ""),
    ("Git: Pop Stash...", R.ID_GIT_STASH_POP, ""),
    ("Git: Add Remote...", R.ID_GIT_REMOTE_ADD, ""),
    ("Git: Remove Remote...", R.ID_GIT_REMOTE_REMOVE, ""),
    ("Git: View History", R.ID_GIT_LOG, ""),
    ("Git: Refresh", R.ID_GIT_REFRESH, "F5"),
    ("Help: About", R.ID_HELP_ABOUT, ""),
)]

K_CMD, K_FILE, K_GOTO, K_INFO = range(4)


class Item:
    __slots__ = ("kind", "label", "detail", "key", "cmd", "path", "hlLabel", "hlDetail", "score")

    def __init__(self, kind=K_CMD, label=""):
        self.kind = kind
        self.label = label
        self.detail = ""
        self.key = ""
        self.cmd = 0
        self.path = ""
        self.hlLabel = []   # highlighted character indexes
        self.hlDetail = []
        self.score = 0


g_pop = None
g_edit = None
g_items = []
g_sel = 0
g_top = 0
g_inputBrush = None
g_hiding = False
g_recentMode = False  # listing recent folders instead of files/commands

# file index for quick open, filled breadth-first by a background thread that publishes snapshots as it goes, so files
# near the root are searchable at once even in huge folders. Snapshots are immutable and share their chunks, so matcher
# threads can use them while the scan carries on.
_charBit = {c: 1 << i for i, c in enumerate("abcdefghijklmnopqrstuvwxyz0123456789._- ")}


def _CharMask(s):
    """Bit set of the common characters in s (others are not tracked, so they never rule a file out)."""
    return sum(map(_charBit.get, set(s), itertools.repeat(0)))


class FileChunk:
    def __init__(self, files):
        self.files = files  # paths relative to root
        # lowered in one CharLowerBuffW call (the mapping is per character, so it splits back exactly)
        self.lower = C.LowerW("\n".join(files)).split("\n")  # LowerW of files
        # which characters each path contains: one integer test rules out most files before any scoring
        self.masks = [_CharMask(f) for f in self.lower]


class FileIndex:
    def __init__(self, root="", chunks=None, count=0):
        self.root = root
        self.chunks = chunks or []  # in scan order (shallowest first)
        self.count = count


g_index = FileIndex()
g_filesRoot = ""
g_filesLoading = False
g_indexComplete = False  # g_index holds a finished scan of g_filesRoot (a rescan then replaces it only when done)
kPublishMs = 150  # how often a running scan publishes what it has found so far
g_filesGen = 0
g_matchQ = None  # query the latest Refresh wants background-matched (None: none); other matches give up
kSyncMatchLimit = 5000  # larger indexes are matched on a background thread (scoring costs ~4us a file here)

kMaxRows = 14
_big = None  # static HFONT big in Palette::Show


def EditH():
    return C.S(40)


def RowH():
    return C.S(26)


_SEPS = frozenset(" /\\:.-_")


def IsSep(c):
    return c in _SEPS


kNoMatch = -(1 << 31)


def Fuzzy(candLower, q, pos):
    """Fuzzy subsequence match (case-insensitive). Returns kNoMatch if no match (real scores can be negative, e.g. a
    lone character deep in a long path); pos (list or None) receives matched indexes."""
    if not q:
        return 0
    score = 0
    ci = 0
    prev = -2
    find = candLower.find
    for qc in q:
        # prefer a match at a word start ahead of the plain next occurrence
        found = -1
        wordStart = -1
        k = find(qc, ci)
        while k >= 0:
            if found < 0:
                found = k
            if k == 0 or candLower[k - 1] in _SEPS:
                wordStart = k
                break
            if k == prev + 1:
                break  # keep contiguous runs
            k = find(qc, k + 1)
        if found < 0:
            return kNoMatch
        k = found
        if found != prev + 1 and wordStart >= 0:
            k = wordStart
        score += 1
        if k == prev + 1:
            score += 6
        if k == 0 or candLower[k - 1] in _SEPS:
            score += 8
        score -= min(k - ci, 10) // 3
        if pos is not None:
            pos.append(k)
        prev = k
        ci = k + 1
    return score


def Query():
    return text_of(g_edit)


def Squash(s):  # lowercase, drop spaces
    return C.LowerW(s).replace(" ", "")


def _SortKey(s):
    # CompareStringOrdinal(..., TRUE): ordinal comparison after simple uppercasing
    u = s.upper()
    return u if len(u) == len(s) else "".join(ch.upper() if len(ch.upper()) == 1 else ch for ch in s)


def StartFileScan():
    global g_index, g_filesRoot, g_filesLoading, g_filesGen, g_indexComplete
    root = MW.M.folder
    if not root:
        return
    if g_filesLoading and root == g_filesRoot:
        return
    if root != g_filesRoot:
        g_index = FileIndex()
        g_indexComplete = False
    g_filesRoot = root
    g_filesLoading = True
    g_filesGen += 1
    gen = g_filesGen
    partial = not g_indexComplete

    def worker():
        chunks = []
        count = 0
        files = []

        def publish(finished):
            nonlocal count, files
            if files:
                count += len(files)
                chunks.append(FileChunk(files))
                files = []
            index = FileIndex(root, list(chunks), count)

            def done():
                global g_index, g_filesRoot, g_filesLoading, g_indexComplete
                if gen != g_filesGen:
                    return
                g_index = index
                g_filesRoot = root
                if finished:
                    g_filesLoading = False
                    g_indexComplete = True
                if g_pop and IsWindowVisible(g_pop):
                    Refresh()
            C.RunOnUi(done)

        lastPublish = time.monotonic()
        queue = collections.deque([""])  # breadth-first: every file at depth N is found before any at depth N+1
        while queue and count + len(files) < 500000:
            if g_filesGen != gen:
                return
            rel = queue.popleft()
            try:
                it = os.scandir(C.JoinPath(root, rel) if rel else root)
            except OSError:
                continue
            with it:
                for e in it:
                    name = e.name
                    if name == "." or name == "..":
                        continue
                    r = name if not rel else rel + "\\" + name
                    try:
                        attrs = e.stat(follow_symlinks=False).st_file_attributes
                    except OSError:
                        continue
                    if attrs & stat.FILE_ATTRIBUTE_DIRECTORY:
                        if name == ".git" or name == "node_modules" or (attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                            continue
                        queue.append(r)
                    elif ".~mye-" not in name:
                        files.append(r)
            if partial and (time.monotonic() - lastPublish) * 1000 >= kPublishMs:
                publish(False)
                lastPublish = time.monotonic()
        publish(True)

    threading.Thread(target=worker, daemon=True).start()


def Resize():
    mr = client_rect(MW.M.hwnd)
    tl = POINT(0, 0)
    ClientToScreen(MW.M.hwnd, byref(tl))
    w = min(C.S(640), mr.right - C.S(40))
    rows = min(len(g_items), kMaxRows)
    h = EditH() + rows * RowH() + C.S(6)
    x = tl.x + int((mr.right - w) / 2)
    y = tl.y + C.S(6)
    SetWindowPos(g_pop, HWND_TOP, x, y, w, h, SWP_NOACTIVATE)
    MoveWindow(g_edit, C.S(8), C.S(8), w - C.S(16) - 2, EditH() - C.S(14), True)
    InvalidateRect(g_pop, None, True)


def EnsureVisible():
    global g_top
    if g_sel < g_top:
        g_top = g_sel
    if g_sel >= g_top + kMaxRows:
        g_top = g_sel - kMaxRows + 1
    g_top = max(0, g_top)


def Execute(i):
    if i < 0 or i >= len(g_items):
        return
    it = g_items[i]
    if it.kind == K_INFO and not it.cmd:
        return
    Hide()
    if it.kind == K_CMD or it.kind == K_INFO:
        PostMessageW(MW.M.hwnd, WM_COMMAND, it.cmd, 0)
    elif it.kind == K_FILE:
        app.OpenFile(it.path)
    elif it.kind == K_GOTO:
        ed = MW.ActiveEditor()
        if ed:
            ed.GotoLine(it.cmd)
            SetFocus(ed.hwnd)


def DrawHighlighted(dc, s, hl, r, fg, hi, f, fb):
    # draw consecutive runs: matched characters bold + accent, the rest normal
    n = len(s)
    mark = [False] * n
    for k in hl:
        if 0 <= k < n:
            mark[k] = True
    tm = TEXTMETRICW()
    SelectObject(dc, f)
    GetTextMetricsW(dc, byref(tm))
    x = r.left
    y = r.top + int((r.bottom - r.top - tm.tmHeight) / 2)
    a = 0
    while a < n and x < r.right:
        b = a
        while b < n and mark[b] == mark[a]:
            b += 1
        SelectObject(dc, fb if mark[a] else f)
        SetTextColor(dc, hi if mark[a] else fg)
        part = s[a:b]
        ext_text_out(dc, x, y, ETO_CLIPPED, r, part)
        x += text_extent(dc, part).cx
        a = b
    return x


def Paint(hdc):
    rc = client_rect(g_pop)
    dc = CreateCompatibleDC(hdc)
    bmp = CreateCompatibleBitmap(hdc, rc.right, rc.bottom)
    ob = SelectObject(dc, bmp)
    t = C.g_theme
    fill_solid(dc, rc, t.panelBg)
    er = window_rect(g_edit)
    MapWindowPoints(None, g_pop, byref(er), 2)
    InflateRect(er, 2, 2)
    frame_solid(dc, rc, t.border)
    frame_solid(dc, er, t.accent)
    SetBkMode(dc, TRANSPARENT)
    y = EditH()
    i = g_top
    while i < len(g_items) and i < g_top + kMaxRows:
        it = g_items[i]
        row = RECT(1, y, rc.right - 1, y + RowH())
        if i == g_sel:
            fill_solid(dc, row, t.selBg)
        x = C.S(12)
        kr = RECT(rc.right - C.S(150), y, rc.right - C.S(12), y + RowH())
        right = rc.right - C.S(12) if not it.key else kr.left - C.S(8)
        fg = t.panelFgDim if it.kind == K_INFO else t.panelFg
        lr = RECT(x, y, right, y + RowH())
        endX = DrawHighlighted(dc, it.label, it.hlLabel, lr, fg, t.accent, C.g_uiFont, C.g_uiFontBold)
        if it.detail and endX + C.S(16) < right:
            dr = RECT(endX + C.S(10), y, right, y + RowH())
            DrawHighlighted(dc, it.detail, it.hlDetail, dr, t.panelFgDim, t.accent, C.g_uiFont, C.g_uiFontBold)
        if it.key:
            SelectObject(dc, C.g_uiFont)
            SetTextColor(dc, t.panelFgDim)
            draw_text(dc, it.key, kr, DT_RIGHT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX)
        i += 1
        y += RowH()
    BitBlt(hdc, 0, 0, rc.right, rc.bottom, dc, 0, 0, SRCCOPY)
    SelectObject(dc, ob)
    DeleteObject(bmp)
    DeleteDC(dc)


def EditSub(h, m, w, l, uid, ref):
    global g_sel
    if m == WM_KEYDOWN and w == ord('A') and GetKeyState(VK_CONTROL) < 0:
        SendMessageW(h, EM_SETSEL, 0, -1)
        return 0
    if m == WM_KEYDOWN:
        n = len(g_items)
        if w == VK_ESCAPE:
            Hide()
            return 0
        elif w == VK_RETURN:
            Execute(g_sel)
            return 0
        elif w == VK_DOWN:
            if n:
                g_sel = (g_sel + 1) % n
        elif w == VK_UP:
            if n:
                g_sel = (g_sel + n - 1) % n
        elif w == VK_NEXT:
            g_sel = min(n - 1, g_sel + kMaxRows)
        elif w == VK_PRIOR:
            g_sel = max(0, g_sel - kMaxRows)
        else:
            return DefSubclassProc(h, m, w, l)
        EnsureVisible()
        InvalidateRect(g_pop, None, False)
        return 0
    if m == WM_CHAR and w in (VK_RETURN, VK_ESCAPE, 1):
        return 0
    return DefSubclassProc(h, m, w, l)


def PopProc(h, m, w, l):
    global g_sel, g_top
    if m == WM_PAINT:
        ps = PAINTSTRUCT()
        dc = BeginPaint(h, byref(ps))
        Paint(dc)
        EndPaint(h, byref(ps))
        return 0
    if m == WM_ERASEBKGND:
        return 1
    if m == WM_ACTIVATE:
        if LOWORD(w) == WA_INACTIVE and not g_hiding:
            Hide(False)
        return 0
    if m == WM_CTLCOLOREDIT:
        SetTextColor(w, C.g_theme.panelFg)
        SetBkColor(w, C.g_theme.inputBg)
        return g_inputBrush or 0
    if m == WM_COMMAND:
        if l and l == g_edit and HIWORD(w) == EN_CHANGE:
            Refresh()
        return 0
    if m == WM_MOUSEMOVE:
        y = GET_Y_LPARAM(l)
        i = g_top + int((y - EditH()) / RowH())
        if y >= EditH() and i < len(g_items) and i != g_sel:
            g_sel = i
            InvalidateRect(h, None, False)
        return 0
    if m == WM_LBUTTONDOWN:
        y = GET_Y_LPARAM(l)
        if y >= EditH():
            Execute(g_top + (y - EditH()) // RowH())
        return 0
    if m == WM_MOUSEWHEEL:
        d = -3 if GET_WHEEL_DELTA_WPARAM(w) > 0 else 3
        g_top = max(0, min(g_top + d, len(g_items) - kMaxRows))
        InvalidateRect(h, None, False)
        return 0
    return DefWindowProcW(h, m, w, l)


def ScoreFile(rel, lower, q, pos):
    """Scores a quick-open candidate (matches in the file name are preferred); None when it doesn't match.
    pos (list or None) receives absolute indexes."""
    fn = rel.rfind("\\") + 1
    fpos = [] if pos is not None else None
    sc = Fuzzy(lower[fn:], q, fpos)
    if sc != kNoMatch:
        sc += 20
        if pos is not None:
            pos.extend(p + fn for p in fpos)
    else:
        if pos is not None:
            del pos[:]
        sc = Fuzzy(lower, q, pos)
        if sc == kNoMatch:
            return None
    return sc - min(len(rel), 60) // 12  # shorter paths first


class _Matched:
    """What a search for q found in the first len(chunks) chunks of an index: the scored matches, and the files whose
    name doesn't match but whose path might (left unscored while they couldn't make the top 200). A later search for q
    plus more characters, or for q over the same index grown by more chunks, starts from these instead of the index."""
    def __init__(self, root, chunks, q, gis, scs, pend):
        self.root = root
        self.chunks = chunks
        self.q = q
        self.gis = gis  # array: index across all chunks
        self.scs = scs  # array: score of each
        self.pend = pend  # array: indexes of the unscored path-only candidates


_matched = []  # _Matched for prefixes of the latest query, shortest first; replaced whole, so threads can share it


def _FindMatched(ix, q):
    for m in reversed(_matched):
        if m.root == ix.root and q.startswith(m.q) and len(m.chunks) <= len(ix.chunks) and \
                all(a is b for a, b in zip(m.chunks, ix.chunks)):
            return m
    return None


def _RememberMatched(m):
    global _matched
    keep = [o for o in _matched if o.root == m.root and m.q.startswith(o.q) and o.q != m.q]
    keep.sort(key=lambda o: len(o.q))
    _matched = keep[-15:] + [m]


def _PathOnlyBound(q):
    """The most a match outside the file name can score: per Fuzzy, a character earns at most 9 (word start), or 15
    when it also continues a run, which needs the previous query character to be a separator."""
    return 9 + sum(15 if c in _SEPS else 9 for c in q[:-1])


def _Best(chunks, bases, gis, scs):
    """The 200 best (score, index, chunk, j) of the matched files, ties to the lower index."""
    top = range(len(scs))
    if len(scs) > 200:  # only what reaches the 200th best score needs sorting
        t = heapq.nlargest(200, scs)[-1]
        top = [k for k in top if scs[k] >= t]
    best = sorted(((scs[k], gis[k]) for k in top), key=lambda h: (-h[0], h[1]))[:200]
    hits = []
    for sc, gi in best:
        ci = bisect.bisect_right(bases, gi) - 1
        hits.append((sc, gi, chunks[ci], gi - bases[ci]))
    return hits


kProgressSecs = 0.25  # a long match shows its best results so far this often (shallow files are scored first)


def MatchFiles(ix, q, stale, progress=None):
    """Best 200 matches, the same as scoring every file with ScoreFile. Safe on any thread; gives up (returns None) once
    stale() says the result is no longer wanted, and passes interim results to progress() while a long match runs.
    Fast on huge indexes: a character mask rules most files out, files that can't match an earlier prefix of q are
    never looked at again, and paths are only scored when their file name doesn't match and they could still make the
    top 200."""
    hits = []  # (score, index across all chunks: ties go to the file found first, i.e. the shallowest, chunk, j)
    chunks = ix.chunks
    if not q:
        base = 0
        for ch in chunks:
            for j in range(min(len(ch.files), 200 - len(hits))):
                hits.append((ScoreFile(ch.files[j], ch.lower[j], q, None), base + j, ch, j))
            base += len(ch.files)
            if len(hits) >= 200:
                break
        return _Items(ix, q, hits)
    bases = list(itertools.accumulate((len(ch.files) for ch in chunks), initial=0))  # index of each chunk's first
    gis, scs = array("l"), array("l")
    pend = array("l")  # candidates whose file name doesn't match q
    nextProgress = time.monotonic() + kProgressSecs

    def check():  # every 1024 files: False to give up
        nonlocal nextProgress
        if stale and stale():
            return False
        if progress and time.monotonic() >= nextProgress:
            progress(_Items(ix, q, _Best(chunks, bases, gis, scs)))
            nextProgress = time.monotonic() + kProgressSecs
        return True

    def consider(gi, rel, lower):  # ScoreFile's file name half; the rest waits in pend
        fn = rel.rfind("\\") + 1
        sc = Fuzzy(lower[fn:], q, None)
        if sc == kNoMatch:
            pend.append(gi)
        else:
            gis.append(gi)
            scs.append(sc + 20 - min(len(rel), 60) // 12)

    need = _CharMask(q)
    start = 0
    n = 0
    m = _FindMatched(ix, q)
    if m:
        start = len(m.chunks)
        if m.q == q:
            gis, scs, pend = array("l", m.gis), array("l", m.scs), array("l", m.pend)
        else:
            for gi in m.gis:
                n += 1
                if (n & 1023) == 0 and not check():
                    return None
                ci = bisect.bisect_right(bases, gi) - 1
                ch = chunks[ci]
                j = gi - bases[ci]
                if ch.masks[j] & need == need:
                    consider(gi, ch.files[j], ch.lower[j])
            # a name that doesn't match a prefix of q doesn't match q either
            for gi in m.pend:
                ci = bisect.bisect_right(bases, gi) - 1
                if chunks[ci].masks[gi - bases[ci]] & need == need:
                    pend.append(gi)
    for ci in range(start, len(chunks)):
        ch = chunks[ci]
        base = bases[ci]
        files, lowers = ch.files, ch.lower
        for j in [j for j, mk in enumerate(ch.masks) if mk & need == need]:
            n += 1
            if (n & 1023) == 0 and not check():
                return None
            consider(base + j, files[j], lowers[j])
    # path-only matches can't beat 200 file name matches scoring above their bound (nor tie them): leave them unscored
    bound = _PathOnlyBound(q)
    if pend and sum(map(bound.__lt__, scs)) < 200:
        for gi in pend:
            n += 1
            if (n & 1023) == 0 and not check():
                return None
            ci = bisect.bisect_right(bases, gi) - 1
            ch = chunks[ci]
            j = gi - bases[ci]
            sc = Fuzzy(ch.lower[j], q, None)
            if sc != kNoMatch:
                gis.append(gi)
                scs.append(sc - min(len(ch.files[j]), 60) // 12)
        pend = array("l")
    _RememberMatched(_Matched(ix.root, chunks, q, gis, scs, pend))
    return _Items(ix, q, _Best(chunks, bases, gis, scs))


def _Items(ix, q, hits):
    res = []
    for sc, _, ch, j in hits:
        rel = ch.files[j]
        fn = rel.rfind("\\") + 1
        pos = []
        ScoreFile(rel, ch.lower[j], q, pos)
        it = Item(K_FILE)
        it.label = rel[fn:]
        it.detail = rel[:fn - 1] if fn else ""
        it.path = C.JoinPath(ix.root, rel)
        it.score = sc
        for p in pos:
            if p >= fn:
                it.hlLabel.append(p - fn)
            else:
                it.hlDetail.append(p)
        res.append(it)
    return res


def AddFileStatus(rawEmpty):
    if not g_items:
        g_items.append(Item(K_INFO, "Searching files..." if g_filesLoading else "No matching files"))
    if rawEmpty:
        hint = Item(K_INFO, "Type > to run a command, : to go to a line")
        hint.cmd = 0
        g_items.insert(0, hint)


_matchBusy = False  # a background match is running; a request meanwhile waits in _matchNext (only the latest)
_matchNext = None


def _StartMatch(ix, q):
    """Matches on a worker thread. One at a time, so a scan publishing a bigger index doesn't restart a long match: the
    next run only has the new chunks left to do."""
    global _matchBusy, _matchNext
    if _matchBusy:
        _matchNext = (ix, q)
        return
    _matchBusy = True

    def show(res):
        global g_items
        if q == g_matchQ and g_pop and IsWindowVisible(g_pop):
            g_items = res
            AddFileStatus(False)
            SelectFirst()

    def worker():
        res = MatchFiles(ix, q, lambda: g_matchQ != q, lambda part: C.RunOnUi(lambda: show(part)))

        def done():
            global _matchBusy, _matchNext
            _matchBusy = False
            if res is not None:
                show(res)
            nxt, _matchNext = _matchNext, None
            if nxt and nxt[1] == g_matchQ:
                _StartMatch(*nxt)
        C.RunOnUi(done)

    threading.Thread(target=worker, daemon=True).start()


def SelectFirst():
    global g_sel, g_top
    g_sel = 0
    if g_items and g_items[0].kind == K_INFO and g_items[0].cmd == 0 and len(g_items) > 1:
        g_sel = 1
    g_top = 0
    Resize()


def Create():
    global g_pop, g_edit
    C.RegisterWindowClass("MyPalette", PopProc, style=CS_DROPSHADOW, cursor=IDC_ARROW)
    g_pop = CreateWindowExW(WS_EX_TOOLWINDOW, "MyPalette", "", WS_POPUP | WS_CLIPCHILDREN, 0, 0, 10, 10, MW.M.hwnd,
                            None, C.g_hinst, None)
    g_edit = CreateWindowExW(0, "EDIT", "", WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL, 0, 0, 10, 10, g_pop, None,
                             C.g_hinst, None)
    C.Subclass(g_edit, EditSub, 1, 0)


def _SetCue(text):
    buf = create_unicode_buffer(text)
    SendMessageW(g_edit, EM_SETCUEBANNER, True, addressof(buf))


def _wtoi64(s):
    i, n = 0, len(s)
    while i < n and s[i] in " \t\n\r\f\v":
        i += 1
    neg = False
    if i < n and s[i] in "+-":
        neg = s[i] == "-"
        i += 1
    v = 0
    while i < n and "0" <= s[i] <= "9":
        v = v * 10 + (ord(s[i]) - 48)
        i += 1
    if neg:
        v = -v
    return max(-(1 << 63), min((1 << 63) - 1, v))


# ---- Palette:: ----
def Show(prefix):
    global g_recentMode, g_inputBrush, _big
    if not g_pop:
        Create()
    g_recentMode = False
    _SetCue("")
    if g_inputBrush:
        DeleteObject(g_inputBrush)
    g_inputBrush = CreateSolidBrush(C.g_theme.inputBg)
    lf = LOGFONTW()
    GetObjectW(C.g_uiFont, sizeof(lf), byref(lf))
    lf.lfHeight = int(lf.lfHeight * 5 / 4)
    if _big:
        DeleteObject(_big)
    _big = CreateFontIndirectW(byref(lf))
    SendMessageW(g_edit, WM_SETFONT, _big or 0, False)
    SetWindowTextW(g_edit, prefix)
    n = wstr_len(prefix)
    SendMessageW(g_edit, EM_SETSEL, n, n)
    if MW.M.folder:
        StartFileScan()
    Refresh()
    ShowWindow(g_pop, SW_SHOW)
    SetFocus(g_edit)


def ShowRecent():
    global g_recentMode
    Show("")
    g_recentMode = True
    _SetCue("Select a recent folder to open")
    Refresh()


def Hide(restoreFocus=True):
    global g_hiding
    if not g_pop or not IsWindowVisible(g_pop):
        return
    g_hiding = True
    ShowWindow(g_pop, SW_HIDE)
    g_hiding = False
    if restoreFocus:
        SetActiveWindow(MW.M.hwnd)
        if MW.M.active >= 0:
            SetFocus(MW.M.tabs[MW.M.active].hwnd)


def _byScoreDesc(items):
    items.sort(key=lambda it: -it.score)  # stable, like std::stable_sort with a.score > b.score


def Refresh():
    global g_items, g_matchQ
    raw = Query()
    g_matchQ = None
    prev = g_items
    g_items = []
    if g_recentMode:
        q = Squash(raw)
        r = C.g_settings.recentFolders
        for i in range(len(r)):
            it = Item(K_CMD)
            it.label = C.FileNameOf(r[i])
            it.detail = r[i]
            it.cmd = R.ID_FILE_RECENT0 + i
            if C.PathEqualsI(r[i], MW.M.folder):
                it.key = "current"
            it.score = Fuzzy(C.LowerW(it.label), q, it.hlLabel)  # prefer matches in the folder name
            if it.score != kNoMatch:
                it.score += 20
            else:
                it.score = Fuzzy(C.LowerW(it.detail), q, it.hlDetail)
            if it.score != kNoMatch:
                g_items.append(it)
        if q:
            _byScoreDesc(g_items)
        open_ = Item(K_INFO, "Open Folder...")
        open_.key = "Ctrl+Shift+O"
        open_.cmd = R.ID_FILE_OPENFOLDER
        if not g_items:
            g_items.append(Item(K_INFO, "No recent folders" if not r else "No matching folders"))
        g_items.append(open_)
    elif raw and raw[0] == ">":
        q = Squash(raw[1:])
        for c in kCommands:
            it = Item(K_CMD)
            it.label = c.label
            it.key = c.key
            it.cmd = c.id
            it.score = Fuzzy(C.LowerW(it.label), q, it.hlLabel)
            if it.score != kNoMatch:
                g_items.append(it)
        rf = C.g_settings.recentFolders
        for i in range(len(rf)):
            it = Item(K_CMD)
            it.label = "File: Open Recent: " + C.FileNameOf(rf[i])
            it.detail = rf[i]
            it.cmd = R.ID_FILE_RECENT0 + i
            it.score = Fuzzy(C.LowerW(it.label), q, it.hlLabel)
            if it.score != kNoMatch:
                g_items.append(it)
        if q:
            _byScoreDesc(g_items)
        if not g_items:
            g_items.append(Item(K_INFO, "No matching commands"))
    elif raw and raw[0] == ":":
        ed = MW.ActiveEditor()
        if not ed:
            g_items.append(Item(K_INFO, "Open a file to go to a line"))
        else:
            total = ed.buf.LineCount()
            n = _wtoi64(C.Trim(raw[1:]))
            it = Item()
            if n > 0:
                it.kind = K_GOTO
                it.cmd = min(n, total) - 1
                it.label = "Go to line " + str(it.cmd + 1)
            else:
                it.kind = K_INFO
                it.label = "Current line: " + str(ed.CaretLine() + 1) + \
                    ". Type a line number between 1 and " + str(total) + " to navigate to."
            g_items.append(it)
    else:
        q = Squash(raw)
        if not MW.M.folder:
            it = Item(K_INFO, "Open a folder to search for files")
            it.cmd = R.ID_FILE_OPENFOLDER
            g_items.append(it)
            AddFileStatus(not raw)
        elif not q or g_index.count <= kSyncMatchLimit:
            g_items = MatchFiles(g_index, q, None)
            AddFileStatus(not raw)
        else:
            # big folder: match on a worker thread so typing never blocks; keep the previous file results meanwhile
            g_matchQ = q
            _StartMatch(g_index, q)
            g_items = [it for it in prev if it.kind == K_FILE]
            if not g_items:
                g_items.append(Item(K_INFO, "Searching files..."))
    SelectFirst()
