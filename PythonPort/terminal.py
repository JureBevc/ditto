# Integrated terminal: shells run under ConPTY and are rendered by a small xterm-compatible
# screen emulator. Terminals live in groups; the panes of a group sit side by side (splits),
# and the panel header shows one entry per group, like VS Code.
# (port of terminal.cpp / the Term:: namespace of main_window.h)
import re
import threading
from collections import deque

import common as C
import win32 as W
from win32 import (byref, sizeof, c_void_p, c_int, create_unicode_buffer, create_string_buffer, addressof,
                   wstring_at)
import main_window as MW
import editor_view

PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016


def _tdiv(a, b):
    """C/C++ integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


# ConPTY is resolved at runtime so ditto still starts on Windows versions without it.
pCreatePc = None
pResizePc = None
pClosePc = None
_conpty_state = -1


def LoadConPty():
    global pCreatePc, pResizePc, pClosePc, _conpty_state
    if _conpty_state < 0:
        fns = W.LoadConPty()
        if fns:
            pCreatePc, pResizePc, pClosePc = fns
        _conpty_state = 1 if fns else 0
    return _conpty_state == 1


def ShellCommand():
    """Returns (command line, shell name)."""
    path = create_unicode_buffer(W.MAX_PATH)
    if W.SearchPathW(None, "pwsh.exe", None, W.MAX_PATH, path, None):
        return '"' + path.value + '" -NoLogo', "pwsh"
    sys_ = create_unicode_buffer(W.MAX_PATH)
    W.GetSystemDirectoryW(sys_, W.MAX_PATH)
    ps = sys_.value + "\\WindowsPowerShell\\v1.0\\powershell.exe"
    if C.FileExists(ps):
        return '"' + ps + '" -NoLogo', "powershell"
    comspec = create_unicode_buffer(W.MAX_PATH)
    if W.GetEnvironmentVariableW("COMSPEC", comspec, W.MAX_PATH):
        return comspec.value, "cmd"
    return "cmd.exe", "cmd"


def ShellEnvironment():
    """Our own environment minus GIT_TERMINAL_PROMPT=0 (set for background git; shells should prompt).
    Returns a ctypes wchar buffer holding the double-NUL-terminated block."""
    env = []
    block = W.GetEnvironmentStringsW()
    p = block
    while True:
        s = wstring_at(p)
        if not s:
            break
        if s[:20].lower() != "git_terminal_prompt=":
            env.append(s + "\0")
        p += (W.wstr_len(s) + 1) * 2
    W.FreeEnvironmentStringsW(block)
    if not env:
        env.append("\0")
    env.append("\0")
    text = "".join(env)
    return create_unicode_buffer(text, W.wstr_len(text) + 1)


# ---- screen model ----

kDefColor = 0xFFFFFFFF
kIndexed = 0x80000000  # low byte = xterm 256-color index; otherwise a COLORREF
A_BOLD = 1
A_DIM = 2
A_ITALIC = 4
A_UNDER = 8
A_INVERSE = 16
A_TAIL = 32  # right half of a wide char
# A Cell is an immutable tuple (ch, fg, bg, attr); a Line is a list of cells.
CELL_DEFAULT = (0x20, kDefColor, kDefColor, 0)
kMaxHistory = 5000

_dark16 = [W.RGB(0, 0, 0), W.RGB(205, 49, 49), W.RGB(13, 188, 121), W.RGB(229, 229, 16),
           W.RGB(36, 114, 200), W.RGB(188, 63, 188), W.RGB(17, 168, 205), W.RGB(229, 229, 229),
           W.RGB(102, 102, 102), W.RGB(241, 76, 76), W.RGB(35, 209, 139), W.RGB(245, 245, 67),
           W.RGB(59, 142, 234), W.RGB(214, 112, 214), W.RGB(41, 184, 219), W.RGB(229, 229, 229)]
_light16 = [W.RGB(0, 0, 0), W.RGB(205, 49, 49), W.RGB(0, 188, 0), W.RGB(148, 152, 0),
            W.RGB(4, 81, 165), W.RGB(188, 5, 188), W.RGB(5, 152, 188), W.RGB(85, 85, 85),
            W.RGB(102, 102, 102), W.RGB(205, 49, 49), W.RGB(20, 206, 20), W.RGB(181, 186, 0),
            W.RGB(4, 81, 165), W.RGB(188, 5, 188), W.RGB(5, 152, 188), W.RGB(165, 165, 165)]
_lv = (0, 95, 135, 175, 215, 255)


def Indexed(i):
    if i < 16:
        return (_dark16 if C.g_settings.dark else _light16)[i]
    if i < 232:
        i -= 16
        return W.RGB(_lv[i // 36], _lv[(i // 6) % 6], _lv[i % 6])
    g = 8 + 10 * (i - 232)
    return W.RGB(g, g, g)


def ResolveColor(v, default, bright):
    if v == kDefColor:
        return default
    if v & kIndexed:
        i = v & 255
        if bright and i < 8:
            i += 8
        return Indexed(i)
    return v


def AppendCp(s, cp):
    """Python strs hold whole code points; returns s + the character."""
    return s + chr(cp)


def PadL():
    return C.S(10)


def SbWidth():
    """Custom-drawn scrollbar, half the width of the system one."""
    return max(C.S(4), W.GetSystemMetricsForDpi(W.SM_CXVSCROLL, C.g_dpi) // 2)


def PadT():
    return C.S(4)


# parser states
Ground, Esc, Csi, Osc, OscEsc, Str, StrEsc, Charset = range(8)

# Fast paths for the parser (Ground state, no partial UTF-8 sequence pending):
# a run of printable bytes (UTF-8 validated as a whole), and a plain CSI sequence.
_TEXT_RE = re.compile(rb"[\x20-\x7e\x80-\xff]+")
_CSI_RE = re.compile(rb"\x1b\[([\x20-\x3f]*)([\x40-\x7e])")
_WIDTH1_RE = re.compile("[\x00-ჿ]+|[^\x00-ჿ]+")  # CharColumns() is 1 below U+1100


class Saved:
    __slots__ = ("cx", "cy", "pen")

    def __init__(self, cx=0, cy=0, pen=(kDefColor, kDefColor, 0)):
        self.cx = cx
        self.cy = cy
        self.pen = pen


class Terminal:
    def __init__(self):
        self.hwnd = None
        self.title = ""
        self.leftBorder = False

        # ---- process ----
        self.pc = None
        self.inW = None
        self.outR = None
        self.proc = None
        self.reader = None
        self.waiter = None
        self.mu = threading.Lock()
        self.pending = bytearray()  # output read by the reader thread, not yet parsed (guarded by mu)
        self.posted = False  # a WM_APP_TERMDATA is in flight (guarded by mu)

        # ---- screen ----
        self.cols = 0
        self.rows = 0
        self.lines = []
        self.history = deque()
        self.mainLines = []  # primary screen while the alternate screen is active
        self.alt = False
        self.cx = self.cy = self.top = self.bot = 0
        self.wrapPending = False
        # pen (Cell without the character)
        self.penFg = kDefColor
        self.penBg = kDefColor
        self.penAttr = 0
        self.saved = Saved()
        self.cursorVisible = True
        self.appCursor = False
        self.bracketedPaste = False

        # ---- parser ----
        self.state = Ground
        self.seq = bytearray()
        self.u8 = 0
        self.u8need = 0

        # ---- view ----
        self.scroll = 0  # lines scrolled back into history
        self.hasSel = False
        self.selecting = False
        self.sbGrab = -1  # while dragging the scrollbar thumb: mouse offset from the thumb top
        self.selA = (0, 0)  # (x, y): x = column boundary, y = absolute line (history + screen)
        self.selB = (0, 0)
        self.eatChar = False
        self.hiSurrogate = 0
        self.wheelAcc = 0

    # ---------------- process ----------------

    def Start(self, cwd):
        """Returns (ok, err)."""
        if not LoadConPty():
            return False, "The integrated terminal needs Windows 10 version 1809 or later."
        inR = c_void_p()
        inW = c_void_p()
        outR = c_void_p()
        outW = c_void_p()
        ok1 = W.CreatePipe(byref(inR), byref(inW), None, 0)
        ok2 = ok1 and W.CreatePipe(byref(outR), byref(outW), None, 0)
        self.inW = inW.value
        self.outR = outR.value
        if not ok1 or not ok2:
            err = C.FormatError(W.GetLastError())
            if inR.value:
                W.CloseHandle(inR)
            return False, err
        pc = c_void_p()
        hr = pCreatePc(W.COORD(self.cols, self.rows), inR, outW, 0, byref(pc))
        W.CloseHandle(inR)
        W.CloseHandle(outW)
        if W.FAILED(hr):
            self.pc = None
            return False, C.FormatError(hr & 0xFFFFFFFF)
        self.pc = pc.value

        size = W.SIZE_T(0)
        W.InitializeProcThreadAttributeList(None, 1, 0, byref(size))
        attrBuf = create_string_buffer(max(1, size.value))
        attrs = addressof(attrBuf)
        W.InitializeProcThreadAttributeList(attrs, 1, 0, byref(size))
        # lpValue is the HPCON itself (not a pointer to it), as in the C++
        W.UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, self.pc, sizeof(c_void_p),
                                    None, None)
        si = W.STARTUPINFOEXW()
        si.StartupInfo.cb = sizeof(W.STARTUPINFOEXW)
        si.StartupInfo.dwFlags = W.STARTF_USESTDHANDLES
        si.lpAttributeList = attrs
        cmd, self.title = ShellCommand()
        cmdBuf = create_unicode_buffer(cmd, W.wstr_len(cmd) + 1)
        env = ShellEnvironment()
        pi = W.PROCESS_INFORMATION()
        ok = W.CreateProcessW(None, cmdBuf, None, None, False,
                              W.EXTENDED_STARTUPINFO_PRESENT | W.CREATE_UNICODE_ENVIRONMENT,
                              byref(env), cwd if cwd else None, byref(si), byref(pi))
        e = W.GetLastError()
        W.DeleteProcThreadAttributeList(attrs)
        if not ok:
            return False, C.FormatError(e)
        W.CloseHandle(pi.hThread)
        self.proc = pi.hProcess

        h = self.hwnd
        outRh = self.outR
        proc = self.proc

        def read_loop():
            buf = create_string_buffer(65536)
            n = W.DWORD(0)
            while W.ReadFile(outRh, buf, 65536, byref(n), None) and n.value:
                data = W.string_at(buf, n.value)
                with self.mu:
                    self.pending += data
                    if not self.posted:
                        self.posted = True
                        W.PostMessageW(h, C.WM_APP_TERMDATA, 0, 0)

        def wait_loop():
            W.WaitForSingleObject(proc, W.INFINITE)
            W.PostMessageW(h, C.WM_APP_TERMEXIT, 0, 0)

        self.reader = threading.Thread(target=read_loop, daemon=True)
        self.waiter = threading.Thread(target=wait_loop, daemon=True)
        self.reader.start()
        self.waiter.start()
        return True, ""

    def Stop(self):
        if self.inW:
            W.CloseHandle(self.inW)
            self.inW = None
        if self.pc:
            pClosePc(self.pc)  # ends conhost, which closes the output pipe
            self.pc = None
        if self.proc:
            W.TerminateProcess(self.proc, 0)
        if self.waiter is not None:
            self.waiter.join()
            self.waiter = None
        if self.reader is not None:
            self.reader.join()
            self.reader = None
        if self.outR:
            W.CloseHandle(self.outR)
            self.outR = None
        if self.proc:
            W.CloseHandle(self.proc)
            self.proc = None

    def Send(self, s):
        if not self.inW or not s:
            return
        n = W.DWORD(0)
        W.WriteFile(self.inW, s, len(s), byref(n), None)

    def Input(self, s):
        """User input: jump back to the live screen and drop the selection."""
        if self.scroll or self.hasSel:
            self.scroll = 0
            self.hasSel = False
            W.InvalidateRect(self.hwnd, None, False)
        self.Send(s)

    # ---------------- screen operations ----------------

    def Blank(self):
        return (0x20, kDefColor, self.penBg, 0)

    def BlankLine(self):
        return [self.Blank()] * self.cols

    def Reset(self):
        self.penFg = self.penBg = kDefColor
        self.penAttr = 0
        self.lines = [[CELL_DEFAULT] * self.cols for _ in range(self.rows)]
        self.mainLines = []
        self.alt = False
        self.cx = self.cy = 0
        self.top = 0
        self.bot = self.rows - 1
        self.wrapPending = False
        self.cursorVisible = True
        self.appCursor = self.bracketedPaste = False
        self.saved = Saved()

    def Resize(self, c, r):
        c = max(2, c)
        r = max(1, r)
        if c == self.cols and r == self.rows and self.lines:
            return
        if not self.lines:
            self.cols = c
            self.rows = r
            self.Reset()
            return
        # keep the cursor row on screen by moving top lines into history
        while self.cy >= r:
            self.PushHistory(self.lines.pop(0))
            self.cy -= 1

        def fit(ls):
            if len(ls) > r:
                del ls[r:]
            while len(ls) < r:
                ls.append([CELL_DEFAULT] * c)
            for l in ls:
                if len(l) > c:
                    del l[c:]
                elif len(l) < c:
                    l.extend([CELL_DEFAULT] * (c - len(l)))

        fit(self.lines)
        if self.alt:
            fit(self.mainLines)
        self.cols = c
        self.rows = r
        self.top = 0
        self.bot = self.rows - 1
        self.cx = min(self.cx, self.cols - 1)
        self.wrapPending = False
        if self.pc:
            pResizePc(self.pc, W.COORD(self.cols, self.rows))

    def PushHistory(self, l):
        if self.alt:
            return
        self.history.append(l)
        if len(self.history) > kMaxHistory:
            self.history.popleft()
        elif self.scroll > 0:
            self.scroll += 1  # keep the scrolled-back view still

    def ScrollUp(self, n):
        n = min(n, self.bot - self.top + 1)
        lines = self.lines
        for _ in range(n):
            l = lines.pop(self.top)
            if self.top == 0:
                self.PushHistory(l)
            lines.insert(self.bot, self.BlankLine())

    def ScrollDown(self, n):
        n = min(n, self.bot - self.top + 1)
        lines = self.lines
        for _ in range(n):
            del lines[self.bot]
            lines.insert(self.top, self.BlankLine())

    def LineFeed(self):
        self.wrapPending = False
        if self.cy == self.bot:
            self.ScrollUp(1)
        elif self.cy < self.rows - 1:
            self.cy += 1

    def Put(self, cp):
        w = C.CharColumns(cp)
        if self.wrapPending:
            self.cx = 0
            self.LineFeed()
        if w == 2 and self.cx == self.cols - 1:
            self.lines[self.cy][self.cx] = self.Blank()
            self.cx = 0
            self.LineFeed()
        l = self.lines[self.cy]
        cx = self.cx
        if (l[cx][3] & A_TAIL) and cx > 0:
            l[cx - 1] = self.Blank()  # broke a wide char in half
        l[cx] = (cp, self.penFg, self.penBg, self.penAttr)
        if w == 2:
            l[cx + 1] = (0, self.penFg, self.penBg, self.penAttr | A_TAIL)
        elif cx + 1 < self.cols and (l[cx + 1][3] & A_TAIL):
            l[cx + 1] = self.Blank()
        cx += w
        if cx >= self.cols:
            cx = self.cols - 1
            self.wrapPending = True
        self.cx = cx

    def PutRun(self, s):
        """Put() for a run of characters that are all one column wide (same result, done in slices)."""
        i = 0
        n = len(s)
        fg, bg, at = self.penFg, self.penBg, self.penAttr
        cols = self.cols
        while i < n:
            if self.wrapPending:
                self.cx = 0
                self.LineFeed()
            l = self.lines[self.cy]
            cx = self.cx
            k = min(n - i, cols - cx)
            if (l[cx][3] & A_TAIL) and cx > 0:
                l[cx - 1] = self.Blank()  # broke a wide char in half
            l[cx:cx + k] = [(ord(ch), fg, bg, at) for ch in s[i:i + k]]
            end = cx + k
            if end < cols and (l[end][3] & A_TAIL):
                l[end] = self.Blank()
            i += k
            if end >= cols:
                self.cx = cols - 1
                self.wrapPending = True
            else:
                self.cx = end

    def PutText(self, s):
        for m in _WIDTH1_RE.finditer(s):
            seg = m.group()
            if seg[0] < "ᄀ":
                self.PutRun(seg)
            else:
                for ch in seg:
                    self.Put(ord(ch))

    def EraseCells(self, y, x0, x1):
        l = self.lines[y]
        a = max(0, x0)
        b = min(self.cols, x1)
        if a < b:
            l[a:b] = [self.Blank()] * (b - a)

    def SaveCursor(self):
        self.saved = Saved(self.cx, self.cy, (self.penFg, self.penBg, self.penAttr))

    def RestoreCursor(self):
        self.cx = min(self.saved.cx, self.cols - 1)
        self.cy = min(self.saved.cy, self.rows - 1)
        self.penFg, self.penBg, self.penAttr = self.saved.pen
        self.wrapPending = False

    def SetMode(self, m, on):
        if m == 1:
            self.appCursor = on
        elif m == 25:
            self.cursorVisible = on
        elif m == 2004:
            self.bracketedPaste = on
        elif m in (47, 1047, 1049):
            if on and not self.alt:
                if m == 1049:
                    self.SaveCursor()
                self.mainLines = [list(l) for l in self.lines]
                self.lines = [[CELL_DEFAULT] * self.cols for _ in range(self.rows)]
                self.alt = True
            elif not on and self.alt:
                self.lines = self.mainLines
                self.mainLines = []
                self.alt = False
                if m == 1049:
                    self.RestoreCursor()
            self.scroll = 0
            self.hasSel = False

    def Sgr(self, p):
        def B(v):
            return max(0, min(255, v))

        i = 0
        np_ = len(p)
        while i < np_:
            v = max(0, p[i])
            if v == 0:
                self.penFg = self.penBg = kDefColor
                self.penAttr = 0
            elif v == 1:
                self.penAttr |= A_BOLD
            elif v == 2:
                self.penAttr |= A_DIM
            elif v == 3:
                self.penAttr |= A_ITALIC
            elif v == 4:
                self.penAttr |= A_UNDER
            elif v == 7:
                self.penAttr |= A_INVERSE
            elif v == 22:
                self.penAttr &= ~(A_BOLD | A_DIM) & 0xFF
            elif v == 23:
                self.penAttr &= ~A_ITALIC & 0xFF
            elif v == 24:
                self.penAttr &= ~A_UNDER & 0xFF
            elif v == 27:
                self.penAttr &= ~A_INVERSE & 0xFF
            elif 30 <= v <= 37:
                self.penFg = kIndexed | (v - 30)
            elif 40 <= v <= 47:
                self.penBg = kIndexed | (v - 40)
            elif 90 <= v <= 97:
                self.penFg = kIndexed | (v - 90 + 8)
            elif 100 <= v <= 107:
                self.penBg = kIndexed | (v - 100 + 8)
            elif v == 39:
                self.penFg = kDefColor
            elif v == 49:
                self.penBg = kDefColor
            elif v == 38 or v == 48:
                if i + 2 < np_ and p[i + 1] == 5:
                    c = kIndexed | (max(0, p[i + 2]) & 255)
                    i += 2
                elif i + 4 < np_ and p[i + 1] == 2:
                    c = W.RGB(B(p[i + 2]), B(p[i + 3]), B(p[i + 4]))
                    i += 4
                else:
                    break
                if v == 38:
                    self.penFg = c
                else:
                    self.penBg = c
            i += 1

    def DoCsi(self, fin):
        seq = self.seq
        priv = ""
        i = 0
        if seq and seq[0] in b"?<=>":
            priv = chr(seq[0])
            i = 1
        p = []
        cur = -1  # -1 = omitted parameter
        n_ = len(seq)
        while i < n_:
            c = seq[i]
            if 0x30 <= c <= 0x39:
                cur = min(65535, (0 if cur < 0 else cur) * 10 + (c - 0x30))
            elif c == 0x3B or c == 0x3A:
                p.append(cur)
                cur = -1
            else:
                return  # intermediate bytes: a sequence we don't support
            i += 1
        p.append(cur)

        def P(k, default):
            return p[k] if k < len(p) and p[k] > 0 else default

        n = P(0, 1)
        if fin in "ABCDEFGHadef`LMru":
            self.wrapPending = False
        cols, rows, top, bot = self.cols, self.rows, self.top, self.bot
        if fin == "A":
            self.cy = max(top if self.cy >= top else 0, self.cy - n)
        elif fin == "B" or fin == "e":
            self.cy = min(bot if self.cy <= bot else rows - 1, self.cy + n)
        elif fin == "C" or fin == "a":
            self.cx = min(cols - 1, self.cx + n)
        elif fin == "D":
            self.cx = max(0, self.cx - n)
        elif fin == "E":
            self.cy = min(bot if self.cy <= bot else rows - 1, self.cy + n)
            self.cx = 0
        elif fin == "F":
            self.cy = max(top if self.cy >= top else 0, self.cy - n)
            self.cx = 0
        elif fin == "G" or fin == "`":
            self.cx = min(cols - 1, n - 1)
        elif fin == "d":
            self.cy = min(rows - 1, n - 1)
        elif fin == "H" or fin == "f":
            self.cy = min(rows - 1, P(0, 1) - 1)
            self.cx = min(cols - 1, P(1, 1) - 1)
        elif fin == "J":
            m = max(0, p[0])
            if m == 0:
                self.EraseCells(self.cy, self.cx, cols)
                for y in range(self.cy + 1, rows):
                    self.EraseCells(y, 0, cols)
            elif m == 1:
                for y in range(0, self.cy):
                    self.EraseCells(y, 0, cols)
                self.EraseCells(self.cy, 0, self.cx + 1)
            elif m == 2:
                for y in range(rows):
                    self.EraseCells(y, 0, cols)
            elif m == 3:
                self.history.clear()
                self.scroll = 0
                self.hasSel = False
        elif fin == "K":
            m = max(0, p[0])
            if m == 0:
                self.EraseCells(self.cy, self.cx, cols)
            elif m == 1:
                self.EraseCells(self.cy, 0, self.cx + 1)
            else:
                self.EraseCells(self.cy, 0, cols)
        elif fin == "L" or fin == "M":
            if top <= self.cy <= bot:
                n = min(n, bot - self.cy + 1)
                lines = self.lines
                for _ in range(n):
                    if fin == "L":
                        del lines[bot]
                        lines.insert(self.cy, self.BlankLine())
                    else:
                        del lines[self.cy]
                        lines.insert(bot, self.BlankLine())
                self.cx = 0
        elif fin == "@":
            l = self.lines[self.cy]
            n = min(n, cols - self.cx)
            l[self.cx:self.cx] = [self.Blank()] * n
            del l[cols:]
        elif fin == "P":
            l = self.lines[self.cy]
            n = min(n, cols - self.cx)
            del l[self.cx:self.cx + n]
            if len(l) < cols:
                l.extend([self.Blank()] * (cols - len(l)))
        elif fin == "X":
            self.EraseCells(self.cy, self.cx, self.cx + n)
        elif fin == "S":
            if not priv:
                self.ScrollUp(n)
        elif fin == "T":
            if not priv:
                self.ScrollDown(n)
        elif fin == "m":
            if not priv:
                self.Sgr(p)
        elif fin == "r":
            if not priv:
                t = P(0, 1) - 1
                b = P(1, rows) - 1
                if t < b < rows:
                    self.top = t
                    self.bot = b
                else:
                    self.top = 0
                    self.bot = rows - 1
                self.cx = self.cy = 0
        elif fin == "h" or fin == "l":
            if priv == "?":
                for m in p:
                    self.SetMode(m, fin == "h")
        elif fin == "n":
            if not priv and p[0] == 6:
                self.Send(("\x1b[" + str(self.cy + 1) + ";" + str(self.cx + 1) + "R").encode())
            elif not priv and p[0] == 5:
                self.Send(b"\x1b[0n")
        elif fin == "c":
            if not priv:
                self.Send(b"\x1b[?1;0c")
        elif fin == "s":
            if not priv:
                self.SaveCursor()
        elif fin == "u":
            if not priv:
                self.RestoreCursor()
        self.cx = max(0, min(self.cols - 1, self.cx))
        self.cy = max(0, min(self.rows - 1, self.cy))

    def DoOsc(self):
        semi = self.seq.find(b";")
        if semi < 0:
            return
        code = bytes(self.seq[:semi])
        if code == b"0" or code == b"2":
            self.title = C.Utf8ToWide(bytes(self.seq[semi + 1:]))
            W.InvalidateRect(MW.M.hwnd, byref(MW.M.rcOutputHeader), False)

    def EscByte(self, c):
        self.state = Ground
        if c == 0x5B:  # '['
            self.state = Csi
            self.seq = bytearray()
        elif c == 0x5D:  # ']'
            self.state = Osc
            self.seq = bytearray()
        elif c in (0x50, 0x58, 0x5E, 0x5F):  # P X ^ _ : DCS/SOS/PM/APC: ignored
            self.state = Str
        elif c in (0x28, 0x29, 0x2A, 0x2B):  # ( ) * +
            self.state = Charset
        elif c == 0x37:  # '7'
            self.SaveCursor()
        elif c == 0x38:  # '8'
            self.RestoreCursor()
        elif c == 0x44:  # 'D'
            self.LineFeed()
        elif c == 0x45:  # 'E'
            self.cx = 0
            self.LineFeed()
        elif c == 0x4D:  # 'M'
            self.wrapPending = False
            if self.cy == self.top:
                self.ScrollDown(1)
            elif self.cy > 0:
                self.cy -= 1
        elif c == 0x63:  # 'c'
            self.Reset()
        elif c == 0x1B:
            self.state = Esc

    def Control(self, c):
        if c == 0x08:
            if self.cx > 0:
                self.cx -= 1
            self.wrapPending = False
        elif c == 0x09:
            self.cx = min(self.cols - 1, (self.cx // 8 + 1) * 8)
        elif c in (0x0A, 0x0B, 0x0C):
            self.LineFeed()
        elif c == 0x0D:
            self.cx = 0
            self.wrapPending = False
        elif c == 0x1B:
            self.state = Esc

    def Byte(self, c):
        state = self.state
        if state == Ground:
            pass
        elif state == Esc:
            self.EscByte(c)
            return
        elif state == Csi:
            if 0x40 <= c <= 0x7E:
                self.state = Ground
                self.DoCsi(chr(c))
            elif c >= 0x20:
                if len(self.seq) < 256:
                    self.seq.append(c)
            elif c == 0x18 or c == 0x1A:
                self.state = Ground
            else:
                self.Control(c)
            return
        elif state == Osc or state == Str:
            if c == 0x07:
                if state == Osc:
                    self.DoOsc()
                self.state = Ground
            elif c == 0x1B:
                self.state = OscEsc if state == Osc else StrEsc
            elif state == Osc and len(self.seq) < 4096:
                self.seq.append(c)
            return
        elif state == OscEsc or state == StrEsc:
            if state == OscEsc:
                self.DoOsc()  # ESC \ ends the string; any other escape does too
            self.state = Ground
            if c != 0x5C:
                self.EscByte(c)
            return
        elif state == Charset:
            self.state = Ground
            return
        if self.u8need:
            if (c & 0xC0) == 0x80:
                self.u8 = (self.u8 << 6) | (c & 0x3F)
                self.u8need -= 1
                if self.u8need == 0:
                    u8 = self.u8
                    self.Put(0xFFFD if u8 >= 0x110000 or 0xD800 <= u8 < 0xE000 else u8)
                return
            self.u8need = 0
            self.Put(0xFFFD)
        if c < 0x20:
            self.Control(c)
        elif c < 0x7F:
            self.Put(c)
        elif c == 0x7F:
            pass
        elif 0xC2 <= c <= 0xDF:
            self.u8 = c & 0x1F
            self.u8need = 1
        elif 0xE0 <= c <= 0xEF:
            self.u8 = c & 0x0F
            self.u8need = 2
        elif 0xF0 <= c <= 0xF4:
            self.u8 = c & 0x07
            self.u8need = 3
        else:
            self.Put(0xFFFD)

    def Feed(self, s):
        """Parses output bytes. Same result as feeding Byte() one byte at a time; printable runs and
        plain CSI sequences take a fast path."""
        n = len(s)
        i = 0
        text_match = _TEXT_RE.match
        csi_match = _CSI_RE.match
        while i < n:
            if self.state == Ground and not self.u8need:
                c = s[i]
                if c >= 0x20 and c != 0x7F:
                    m = text_match(s, i)
                    run = m.group()
                    try:
                        self.PutText(run.decode("utf-8"))
                        i = m.end()
                        continue
                    except UnicodeDecodeError as e:
                        good = e.start
                    if good:
                        self.PutText(run[:good].decode("utf-8"))
                        i += good
                    # the exact byte-wise decoder handles the invalid / incomplete sequence
                    self.Byte(s[i])
                    i += 1
                    while i < n and self.u8need:
                        self.Byte(s[i])
                        i += 1
                    continue
                if c == 0x1B:
                    m = csi_match(s, i)
                    if m:
                        self.seq = bytearray(m.group(1)[:256])
                        self.DoCsi(chr(m.group(2)[0]))
                        i = m.end()
                        continue
                self.Byte(c)
                i += 1
                continue
            self.Byte(s[i])
            i += 1

    # ---------------- view ----------------

    def LineAt(self, abs_):
        h = len(self.history)
        return self.history[abs_] if abs_ < h else self.lines[abs_ - h]

    def ViewTop(self):
        return len(self.history) - self.scroll

    def FitToWindow(self):
        rc = W.client_rect(self.hwnd)
        if rc.right <= 0 or rc.bottom <= 0:
            return  # hidden or minimized: keep the old size
        ef = editor_view.g_ef
        self.Resize(_tdiv(rc.right - SbWidth() - PadL() - C.S(4), ef.cw), _tdiv(rc.bottom - PadT(), ef.lh))
        W.InvalidateRect(self.hwnd, None, False)

    def ScrollTo(self, viewTop):
        h = len(self.history)
        self.scroll = max(0, min(h, h - viewTop))
        W.InvalidateRect(self.hwnd, None, False)

    def ThumbRect(self):
        """Scrollbar thumb in client coordinates; empty when there is no history to scroll."""
        rc = W.client_rect(self.hwnd)
        hist = len(self.history)
        trackH = rc.bottom
        if not hist or trackH <= 0:
            return W.RECT()
        thumbH = min(trackH, max(C.S(20), _tdiv(trackH * self.rows, hist + self.rows)))
        y = _tdiv((trackH - thumbH) * self.ViewTop(), hist)
        return W.RECT(rc.right - SbWidth(), y, rc.right, y + thumbH)

    def OnScrollBar(self, l):
        rc = W.client_rect(self.hwnd)
        return W.GET_X_LPARAM(l) >= rc.right - SbWidth()

    def DragThumb(self, y):
        """Drag the thumb so its top sits at mouse y minus the grab offset."""
        th = self.ThumbRect()
        rc = W.client_rect(self.hwnd)
        span = rc.bottom - (th.bottom - th.top)
        if span <= 0:
            return
        hist = len(self.history)
        self.ScrollTo(_tdiv((y - self.sbGrab) * hist + _tdiv(span, 2), span))

    def SelRange(self):
        """Returns (ok, a, b) with a <= b."""
        if not self.hasSel:
            return False, (0, 0), (0, 0)
        a = self.selA
        b = self.selB
        if b[1] < a[1] or (b[1] == a[1] and b[0] < a[0]):
            a, b = b, a
        return True, a, b

    def CellAt(self, l, round_):
        """round_: snap to the nearest column boundary (for drag selection) instead of the cell under the mouse."""
        ef = editor_view.g_ef
        x = W.GET_X_LPARAM(l) - PadL() + (_tdiv(ef.cw, 2) if round_ else 0)
        y = W.GET_Y_LPARAM(l) - PadT()
        col = max(0, min(self.cols, _tdiv(x, ef.cw)))
        row = max(0, min(self.rows - 1, 0 if y < 0 else _tdiv(y, ef.lh)))
        return (col, self.ViewTop() + row)

    def SelectWord(self, p):
        l = self.LineAt(p[1])
        x = min(p[0], len(l) - 1)
        if x < 0:
            return

        def isWord(i):
            return l[i][0] > 0x20 or (l[i][3] & A_TAIL)

        if not isWord(x):
            return
        a = x
        b = x + 1
        while a > 0 and isWord(a - 1):
            a -= 1
        while b < len(l) and isWord(b):
            b += 1
        self.selA = (a, p[1])
        self.selB = (b, p[1])
        self.hasSel = True
        W.InvalidateRect(self.hwnd, None, False)

    def SelectedText(self):
        ok, a, b = self.SelRange()
        if not ok:
            return ""
        out = []
        total = len(self.history) + self.rows
        y = max(0, a[1])
        while y <= b[1] and y < total:
            l = self.LineAt(y)
            x0 = a[0] if y == a[1] else 0
            x1 = b[0] if y == b[1] else len(l)
            s = []
            for x in range(x0, min(x1, len(l))):
                c = l[x]
                if not (c[3] & A_TAIL):
                    s.append(" " if c[0] < 0x20 else chr(c[0]))
            out.append("".join(s).rstrip(" "))
            if y != b[1]:
                out.append("\r\n")
            y += 1
        return "".join(out)

    def Copy(self):
        s = self.SelectedText()
        if s:
            C.SetClipboardText(self.hwnd, s)
        self.hasSel = False
        W.InvalidateRect(self.hwnd, None, False)

    def Paste(self):
        s = C.GetClipboardText(self.hwnd)
        if not s:
            return
        n = []
        i = 0
        ln = len(s)
        while i < ln:
            ch = s[i]
            if ch == "\r":
                if i + 1 < ln and s[i + 1] == "\n":
                    i += 1
                n.append("\r")
            else:
                n.append("\r" if ch == "\n" else ch)
            i += 1
        u = C.WideToUtf8("".join(n))
        if self.bracketedPaste:
            u = b"\x1b[200~" + u + b"\x1b[201~"
        self.Input(u)

    def Key(self, vk):
        ctrl = W.GetKeyState(W.VK_CONTROL) < 0
        shift = W.GetKeyState(W.VK_SHIFT) < 0
        altKey = W.GetKeyState(W.VK_MENU) < 0
        if ctrl and vk == ord("C") and (shift or self.hasSel):
            self.Copy()
            self.eatChar = True
            return True
        if ctrl and vk == ord("V"):
            self.Paste()
            self.eatChar = True
            return True
        if shift and not ctrl and (vk == W.VK_PRIOR or vk == W.VK_NEXT):
            self.ScrollTo(self.ViewTop() + (-(self.rows - 1) if vk == W.VK_PRIOR else self.rows - 1))
            return True
        mod = 1 + (1 if shift else 0) + (2 if altKey else 0) + (4 if ctrl else 0)
        letter = ""
        num = 0
        ss3 = False
        if vk == W.VK_UP:
            letter, ss3 = "A", self.appCursor
        elif vk == W.VK_DOWN:
            letter, ss3 = "B", self.appCursor
        elif vk == W.VK_RIGHT:
            letter, ss3 = "C", self.appCursor
        elif vk == W.VK_LEFT:
            letter, ss3 = "D", self.appCursor
        elif vk == W.VK_HOME:
            letter, ss3 = "H", self.appCursor
        elif vk == W.VK_END:
            letter, ss3 = "F", self.appCursor
        elif vk == W.VK_F1:
            letter, ss3 = "P", True
        elif vk == W.VK_F2:
            letter, ss3 = "Q", True
        elif vk == W.VK_F3:
            letter, ss3 = "R", True
        elif vk == W.VK_F4:
            letter, ss3 = "S", True
        elif vk == W.VK_INSERT:
            num = 2
        elif vk == W.VK_DELETE:
            num = 3
        elif vk == W.VK_PRIOR:
            num = 5
        elif vk == W.VK_NEXT:
            num = 6
        elif vk == W.VK_F5:
            num = 15
        elif vk == W.VK_F6:
            num = 17
        elif vk == W.VK_F7:
            num = 18
        elif vk == W.VK_F8:
            num = 19
        elif vk == W.VK_F9:
            num = 20
        elif vk == W.VK_F11:
            num = 23
        elif vk == W.VK_F12:
            num = 24
        else:
            return False
        if letter:
            if mod > 1:
                out = "\x1b[1;" + str(mod) + letter
            else:
                out = ("\x1bO" if ss3 else "\x1b[") + letter
        else:
            out = "\x1b[" + str(num) + (";" + str(mod) if mod > 1 else "") + "~"
        self.Input(out.encode())
        return True

    def Char(self, c):
        if self.eatChar:
            self.eatChar = False
            return
        if 0xD800 <= c < 0xDC00:
            self.hiSurrogate = c
            return
        if 0xDC00 <= c < 0xE000:
            if not self.hiSurrogate:
                return
            s = chr(0x10000 + ((self.hiSurrogate - 0xD800) << 10) + (c - 0xDC00))
            self.hiSurrogate = 0
        elif c == 0x08:
            s = "\x7f"  # Backspace sends DEL, Ctrl+Backspace sends ^H
        elif c == 0x7F:
            s = "\x08"
        else:
            s = chr(c)
        self.Input(C.WideToUtf8(s))

    def Paint(self, hdc):
        ef = editor_view.g_ef
        th_ = C.g_theme
        rc = W.client_rect(self.hwnd)
        dc = W.CreateCompatibleDC(hdc)
        bmp = W.CreateCompatibleBitmap(hdc, max(1, rc.right), max(1, rc.bottom))
        ob = W.SelectObject(dc, bmp)
        of = W.SelectObject(dc, ef.normal)
        SetBkColor = W.SetBkColor
        SetTextColor = W.SetTextColor
        SelectObject = W.SelectObject
        ExtTextOutW = W.ExtTextOutW

        def fill(r, c):
            SetBkColor(dc, c)
            ExtTextOutW(dc, 0, 0, W.ETO_OPAQUE, byref(r), None, 0, None)

        fill(rc, th_.bg)
        cw = ef.cw
        lh = ef.lh
        padL = PadL()
        padT = PadT()
        ty = _tdiv(lh - (ef.lh - C.S(3)), 2)
        sel, sa, sb = self.SelRange()
        themeFg, themeBg, selBg = th_.fg, th_.bg, th_.selBg
        fNormal, fBold, fItalic = ef.normal, ef.bold, ef.italic
        cache = {}

        def resolve(c, selected):
            key = (c[1], c[2], c[3] & 31, selected)
            r = cache.get(key)
            if r is None:
                at = c[3]
                fg = ResolveColor(c[1], themeFg, (at & A_BOLD) != 0)
                bg = ResolveColor(c[2], themeBg, False)
                if at & A_INVERSE:
                    fg, bg = bg, fg
                if at & A_DIM:
                    fg = W.RGB((W.GetRValue(fg) + W.GetRValue(bg)) // 2, (W.GetGValue(fg) + W.GetGValue(bg)) // 2,
                               (W.GetBValue(fg) + W.GetBValue(bg)) // 2)
                if selected:
                    bg = selBg
                f = fBold if (at & A_BOLD) else fItalic if (at & A_ITALIC) else fNormal
                r = (fg, bg, f, (at & A_UNDER) != 0)
                cache[key] = r
            return r

        viewTop = self.ViewTop()
        rows, cols = self.rows, self.cols
        cr = W.RECT()
        S1, S2 = C.S(1), C.S(2)
        for r in range(rows):
            abs_ = viewTop + r
            l = self.LineAt(abs_)
            y = padT + r * lh
            ncols = min(len(l), cols)
            # selection columns on this row: [s0, s1)
            if sel and sa[1] <= abs_ <= sb[1]:
                s0 = sa[0] if abs_ == sa[1] else 0
                s1 = sb[0] if abs_ == sb[1] else 1 << 30
            else:
                s0 = s1 = -1
            x = 0
            while x < ncols:
                cur = resolve(l[x], s0 <= x < s1)
                x0 = x
                text = []
                dx = []
                while x < ncols:
                    c = l[x]
                    if x > x0 and resolve(c, s0 <= x < s1) != cur:
                        break
                    tail = (c[3] & A_TAIL) != 0
                    wide = not tail and x + 1 < ncols and (l[x + 1][3] & A_TAIL) != 0
                    ch = 0x20 if tail or c[0] < 0x20 else c[0]
                    text.append(chr(ch))
                    dx.append(2 * cw if wide else cw)
                    if ch >= 0x10000:
                        dx.append(0)
                    x += 2 if wide else 1
                fg, bg, f, under = cur
                cr.left = padL + x0 * cw
                cr.top = y
                cr.right = padL + x * cw
                cr.bottom = y + lh
                SelectObject(dc, f)
                SetTextColor(dc, fg)
                SetBkColor(dc, bg)
                arr = (c_int * len(dx))(*dx)
                ExtTextOutW(dc, cr.left, y + ty, W.ETO_OPAQUE | W.ETO_CLIPPED, byref(cr), "".join(text), len(dx), arr)
                if under:
                    fill(W.RECT(cr.left, cr.bottom - S2, cr.right, cr.bottom - S1), fg)
        curRow = self.cy + self.scroll
        if self.cursorVisible and curRow < rows:
            crc = W.RECT(padL + self.cx * cw, padT + curRow * lh, padL + (self.cx + 1) * cw, padT + (curRow + 1) * lh)
            if W.GetFocus() == self.hwnd:
                fill(crc, th_.fg)
                c = self.lines[self.cy][self.cx]
                if c[0] > 0x20 and not (c[3] & A_TAIL):
                    s = chr(c[0])
                    SelectObject(dc, ef.normal)
                    SetTextColor(dc, th_.bg)
                    W.SetBkMode(dc, W.TRANSPARENT)
                    ExtTextOutW(dc, crc.left, crc.top + ty, W.ETO_CLIPPED, byref(crc), s, W.wstr_len(s), None)
                    W.SetBkMode(dc, W.OPAQUE)
            else:
                b = W.CreateSolidBrush(th_.fg)
                W.FrameRect(dc, byref(crc), b)
                W.DeleteObject(b)
        th = self.ThumbRect()
        if th.bottom > th.top:
            pct = 45 if self.sbGrab >= 0 else 30
            a = th_.fg
            b = th_.bg
            fill(th, W.RGB((W.GetRValue(a) * pct + W.GetRValue(b) * (100 - pct)) // 100,
                           (W.GetGValue(a) * pct + W.GetGValue(b) * (100 - pct)) // 100,
                           (W.GetBValue(a) * pct + W.GetBValue(b) * (100 - pct)) // 100))
        if self.leftBorder:
            fill(W.RECT(0, 0, 1, rc.bottom), th_.border)
        W.BitBlt(hdc, 0, 0, rc.right, rc.bottom, dc, 0, 0, W.SRCCOPY)
        SelectObject(dc, of)
        SelectObject(dc, ob)
        W.DeleteObject(bmp)
        W.DeleteDC(dc)


# ---------------- groups ----------------

class Group:
    def __init__(self, panes=None, active=0):
        self.panes = panes if panes is not None else []
        self.active = active


g_groups = []
g_activeGroup = -1
kClass = "DittoTerminal"
_wnd = {}  # hwnd -> Terminal (the C++ keeps the pointer in GWLP_USERDATA)
_registered = False


def ActivePane():
    if g_activeGroup < 0:
        return None
    g = g_groups[g_activeGroup]
    return g.panes[g.active] if g.panes else None


def FindPane(h):
    if not h:
        return None
    for g in g_groups:
        for t in g.panes:
            if t.hwnd == h:
                return t
    return None


def RemovePane(t):
    global g_activeGroup
    for gi in range(len(g_groups)):
        ps = g_groups[gi].panes
        if t not in ps:
            continue
        hadFocus = W.GetFocus() == t.hwnd
        ps.remove(t)
        if not ps:
            del g_groups[gi]
            if gi < g_activeGroup or g_activeGroup >= len(g_groups):
                g_activeGroup -= 1
        else:
            g_groups[gi].active = min(g_groups[gi].active, len(ps) - 1)
        W.DestroyWindow(t.hwnd)
        t.Stop()  # stops the shell and joins its threads
        MW.TerminalsChanged()
        if hadFocus:
            a = ActivePane()
            if a:
                W.SetFocus(a.hwnd)
            else:
                W.SetFocus(MW.M.hwnd)
        return


def TermProc(h, m, w, l):
    t = _wnd.get(h)
    if t is None:
        return W.DefWindowProcW(h, m, w, l)
    if m == C.WM_APP_TERMDATA:
        with t.mu:
            data = t.pending
            t.pending = bytearray()
            t.posted = False
        t.Feed(bytes(data))
        W.InvalidateRect(h, None, False)
        return 0
    if m == C.WM_APP_TERMEXIT:
        RemovePane(t)  # like VS Code: the pane closes when its shell exits
        return 0
    if m == W.WM_NCDESTROY:
        _wnd.pop(h, None)
        return W.DefWindowProcW(h, m, w, l)
    if m == W.WM_SIZE:
        t.FitToWindow()
        return 0
    if m == W.WM_ERASEBKGND:
        return 1
    if m == W.WM_PAINT:
        ps = W.PAINTSTRUCT()
        dc = W.BeginPaint(h, byref(ps))
        t.Paint(dc)
        W.EndPaint(h, byref(ps))
        return 0
    if m == W.WM_SETFOCUS:
        for g in g_groups:
            for i, p in enumerate(g.panes):
                if p is t:
                    g.active = i
        W.InvalidateRect(h, None, False)
        return 0
    if m == W.WM_KILLFOCUS:
        W.InvalidateRect(h, None, False)
        return 0
    if m == W.WM_KEYDOWN:
        if t.Key(w):
            return 0
        return W.DefWindowProcW(h, m, w, l)
    if m == W.WM_CHAR:
        t.Char(w & 0xFFFF)
        return 0
    if m == W.WM_SETCURSOR:
        if W.LOWORD(l) == W.HTCLIENT:
            pt = W.cursor_pos()
            W.ScreenToClient(h, byref(pt))
            W.SetCursor(W.LoadCursorW(None, W.IDC_ARROW if t.OnScrollBar(W.MAKELPARAM(pt.x, pt.y)) or t.sbGrab >= 0
                                      else W.IDC_IBEAM))
            return 1
        return W.DefWindowProcW(h, m, w, l)
    if m == W.WM_LBUTTONDOWN:
        W.SetFocus(h)
        W.SetCapture(h)
        if t.OnScrollBar(l):
            th = t.ThumbRect()
            if th.bottom <= th.top:
                W.ReleaseCapture()
                return 0
            y = W.GET_Y_LPARAM(l)
            # grabbing the thumb keeps its offset; clicking the track centres the thumb on the mouse
            t.sbGrab = y - th.top if th.top <= y < th.bottom else _tdiv(th.bottom - th.top, 2)
            t.DragThumb(y)
            return 0
        t.selA = t.selB = t.CellAt(l, True)
        t.hasSel = False
        t.selecting = True
        W.InvalidateRect(h, None, False)
        return 0
    if m == W.WM_MOUSEMOVE:
        if t.sbGrab >= 0:
            t.DragThumb(W.GET_Y_LPARAM(l))
        elif t.selecting:
            t.selB = t.CellAt(l, True)
            t.hasSel = t.selA != t.selB
            W.InvalidateRect(h, None, False)
        return 0
    if m == W.WM_LBUTTONUP:
        if t.selecting or t.sbGrab >= 0:
            W.ReleaseCapture()
        return 0
    if m == W.WM_CAPTURECHANGED:
        t.selecting = False
        if t.sbGrab >= 0:
            t.sbGrab = -1
            W.InvalidateRect(h, None, False)
        return 0
    if m == W.WM_LBUTTONDBLCLK:
        if t.OnScrollBar(l):
            return 0
        t.SelectWord(t.CellAt(l, False))
        return 0
    if m == W.WM_RBUTTONUP:  # Windows Terminal / VS Code style: copy the selection, or paste
        W.SetFocus(h)
        if t.hasSel:
            t.Copy()
        else:
            t.Paste()
        return 0
    if m == W.WM_MOUSEWHEEL:
        t.wheelAcc += W.GET_WHEEL_DELTA_WPARAM(w)
        notches = _tdiv(t.wheelAcc, W.WHEEL_DELTA)
        t.wheelAcc -= notches * W.WHEEL_DELTA
        if not notches:
            return 0
        if t.alt:  # full-screen apps (less, vim...) get arrow keys
            if t.appCursor:
                key = b"\x1bOA" if notches > 0 else b"\x1bOB"
            else:
                key = b"\x1b[A" if notches > 0 else b"\x1b[B"
            t.Send(key * (3 * abs(notches)))
        else:
            t.ScrollTo(t.ViewTop() - 3 * notches)
        return 0
    return W.DefWindowProcW(h, m, w, l)


def AddPane(split):
    global _registered, g_activeGroup
    if not _registered:
        C.RegisterWindowClass(kClass, TermProc, style=W.CS_DBLCLKS, cursor=W.IDC_IBEAM)
        _registered = True
    t = Terminal()
    t.hwnd = W.CreateWindowExW(0, kClass, "", W.WS_CHILD | W.WS_CLIPSIBLINGS, 0, 0, 0, 0, MW.M.hwnd, None,
                               C.g_hinst, None)
    _wnd[t.hwnd] = t
    t.Resize(80, 24)
    if split and g_activeGroup >= 0:
        g = g_groups[g_activeGroup]
        g.panes.insert(g.active + 1, t)
        g.active += 1
    else:
        g_groups.append(Group([t], 0))
        g_activeGroup = len(g_groups) - 1
    MW.TerminalsChanged()  # lays the pane out first so the shell starts with the right size
    cwd = MW.M.folder
    if not cwd:
        home = create_unicode_buffer(W.MAX_PATH)
        if W.GetEnvironmentVariableW("USERPROFILE", home, W.MAX_PATH):
            cwd = home.value
    ok, err = t.Start(cwd)
    if not ok:
        RemovePane(t)
        C.MsgBox(MW.M.hwnd, "Could not start the terminal.\n\n" + err, W.MB_ICONERROR)
        return
    W.SetFocus(t.hwnd)


def PaneName(t):
    s = t.title
    if "\\" in s or "/" in s:
        s = C.FileNameOf(s)
    if len(s) > 4 and s[-4:].lower() == ".exe":
        s = s[:-4]
    return s if s else "terminal"


# ---------------- Term:: ----------------

def GroupCount():
    return len(g_groups)


def ActiveGroup():
    return g_activeGroup


def GroupTitle(i):
    s = ""
    for t in g_groups[i].panes:
        s += ("" if not s else ", ") + PaneName(t)
    return str(i + 1) + ": " + s


def SelectGroup(i):
    global g_activeGroup
    if i < 0 or i >= len(g_groups):
        return
    g_activeGroup = i
    MW.TerminalsChanged()
    Focus()


def New():
    AddPane(False)


def Split():
    AddPane(g_activeGroup >= 0)


def KillActive():
    t = ActivePane()
    if t:
        RemovePane(t)


def KillGroup(i):
    if i < 0 or i >= len(g_groups):
        return
    ps = list(g_groups[i].panes)
    for t in ps:
        RemovePane(t)


def Layout(rc, visible):
    """Place the active group's panes (main-window client coordinates)."""
    for gi, g in enumerate(g_groups):
        ps = g.panes
        for pi, t in enumerate(ps):
            if visible and gi == g_activeGroup:
                w = rc.right - rc.left
                n = len(ps)
                x0 = rc.left + _tdiv(w * pi, n)
                x1 = rc.left + _tdiv(w * (pi + 1), n)
                t.leftBorder = pi > 0
                W.MoveWindow(t.hwnd, x0, rc.top, x1 - x0, rc.bottom - rc.top, True)
                W.ShowWindow(t.hwnd, W.SW_SHOW)
            else:
                W.ShowWindow(t.hwnd, W.SW_HIDE)


def Focus():
    """Focus the active pane; False if there is none."""
    t = ActivePane()
    if not t:
        return False
    W.SetFocus(t.hwnd)
    return True


def HasFocus():
    return FindPane(W.GetFocus()) is not None


def WantsKey(msg):
    """Keystroke should go to a focused terminal instead of the accelerators. msg: win32.MSG."""
    if (msg.message != W.WM_KEYDOWN and msg.message != W.WM_SYSKEYDOWN) or not FindPane(msg.hwnd):
        return False
    ctrl = W.GetKeyState(W.VK_CONTROL) < 0
    shift = W.GetKeyState(W.VK_SHIFT) < 0
    vk = msg.wParam
    # workbench shortcuts that still work while a terminal has focus; everything else goes to the shell
    glob = ((ctrl and vk == W.VK_OEM_3) or (ctrl and shift and vk == ord("5")) or vk == W.VK_F1 or
            (ctrl and vk == ord("P")) or (ctrl and vk in (W.VK_TAB, W.VK_PRIOR, W.VK_NEXT)) or
            (ctrl and shift and vk in (ord("E"), ord("G"))))
    return not glob


def FontChanged():
    for g in g_groups:
        for t in g.panes:
            t.FitToWindow()


def ThemeChanged():
    for g in g_groups:
        for t in g.panes:
            W.InvalidateRect(t.hwnd, None, False)


def CloseAll():
    global g_activeGroup
    for g in g_groups:
        for t in g.panes:
            W.DestroyWindow(t.hwnd)
            t.Stop()
    g_groups.clear()
    g_activeGroup = -1
