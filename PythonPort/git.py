# Runs git.exe in the background and parses its porcelain output (port of git.h / git.cpp).
import threading
from collections import deque

import common as C
import win32 as W
from ctypes import byref, sizeof, create_unicode_buffer, create_string_buffer, c_void_p
from win32 import DWORD


class ProcResult:
    __slots__ = ("code", "out", "err")

    def __init__(self, code=-1, out=b"", err=b""):
        self.code = code
        self.out = out
        self.err = err

    def copy(self):
        return ProcResult(self.code, self.out, self.err)


g_gitLogger = None


def SetGitLogger(fn):
    """fn(text: str) is invoked on worker threads."""
    global g_gitLogger
    g_gitLogger = fn


def QuoteArg(a):
    if a and not any(ch in a for ch in " \t\n\v\""):
        return a
    r = ['"']
    i = 0
    n = len(a)
    while True:
        bs = 0
        while i < n and a[i] == "\\":
            i += 1
            bs += 1
        if i == n:
            r.append("\\" * (bs * 2))
            break
        if a[i] == '"':
            r.append("\\" * (bs * 2 + 1))
            r.append('"')
        else:
            r.append("\\" * bs)
            r.append(a[i])
        i += 1
    r.append('"')
    return "".join(r)


def _ReadAll(h, out):
    """Reads h until EOF, appending chunks to the list `out`."""
    buf = create_string_buffer(65536)
    n = DWORD(0)
    while W.ReadFile(h, buf, 65536, byref(n), None) and n.value:
        out.append(buf.raw[:n.value])


def RunProcess(cmdline, cwd, input=None):
    r = ProcResult()
    sa = W.SECURITY_ATTRIBUTES()
    sa.nLength = sizeof(sa)
    sa.lpSecurityDescriptor = None
    sa.bInheritHandle = True
    outR, outW, errR, errW, inR, inW = (c_void_p() for _ in range(6))
    if not W.CreatePipe(byref(outR), byref(outW), byref(sa), 0):
        r.err = b"CreatePipe failed"
        return r
    W.CreatePipe(byref(errR), byref(errW), byref(sa), 0)
    W.CreatePipe(byref(inR), byref(inW), byref(sa), 0)
    W.SetHandleInformation(outR, W.HANDLE_FLAG_INHERIT, 0)
    W.SetHandleInformation(errR, W.HANDLE_FLAG_INHERIT, 0)
    W.SetHandleInformation(inW, W.HANDLE_FLAG_INHERIT, 0)
    si = W.STARTUPINFOW()
    si.cb = sizeof(si)
    si.dwFlags = W.STARTF_USESTDHANDLES | W.STARTF_USESHOWWINDOW
    si.wShowWindow = W.SW_HIDE
    si.hStdInput = inR
    si.hStdOutput = outW
    si.hStdError = errW
    pi = W.PROCESS_INFORMATION()
    cl = create_unicode_buffer(cmdline)  # CreateProcessW may modify the command line buffer
    ok = W.CreateProcessW(None, cl, None, None, True, W.CREATE_NO_WINDOW, None,
                          None if not cwd else cwd, byref(si), byref(pi))
    createErr = W.GetLastError()
    W.CloseHandle(outW)
    W.CloseHandle(errW)
    W.CloseHandle(inR)
    if not ok:
        W.CloseHandle(outR)
        W.CloseHandle(errR)
        W.CloseHandle(inW)
        r.err = C.WideToUtf8(C.FormatError(createErr))
        return r
    errChunks = []
    errThread = threading.Thread(target=_ReadAll, args=(errR, errChunks), daemon=True)
    errThread.start()
    if input:
        wr = DWORD(0)
        W.WriteFile(inW, input, len(input), byref(wr), None)
    W.CloseHandle(inW)
    outChunks = []
    _ReadAll(outR, outChunks)
    errThread.join()
    W.WaitForSingleObject(pi.hProcess, W.INFINITE)
    code = DWORD(0)
    W.GetExitCodeProcess(pi.hProcess, byref(code))
    v = code.value
    r.code = v - (1 << 32) if v & 0x80000000 else v  # (int)code
    r.out = b"".join(outChunks)
    r.err = b"".join(errChunks)
    W.CloseHandle(pi.hProcess)
    W.CloseHandle(pi.hThread)
    W.CloseHandle(outR)
    W.CloseHandle(errR)
    return r


_exe = ""


def GitExe():
    global _exe
    if _exe:
        return _exe
    buf = create_unicode_buffer(W.MAX_PATH)
    if W.SearchPathW(None, "git.exe", None, W.MAX_PATH, buf, None):
        _exe = buf.value
    else:
        cands = ["C:\\Program Files\\Git\\cmd\\git.exe", "C:\\Program Files (x86)\\Git\\cmd\\git.exe"]
        for c in cands:
            if C.FileExists(c):
                _exe = c
                break
        if not _exe:
            _exe = "git.exe"
    return _exe


def Git(cwd, args, input=None, log=True):
    """log=True: the command line and its output are echoed to the Output panel."""
    cmd = QuoteArg(GitExe()) + " -c core.quotepath=off -c color.ui=never"
    shown = "> git"
    for a in args:
        cmd += " " + QuoteArg(a)
        shown += " " + QuoteArg(a)
    r = RunProcess(cmd, cwd, input)
    logger = g_gitLogger
    if logger and (log or r.code != 0):
        parts = [shown + "\r\n"]

        def add(s):
            t = C.TrimA(s)
            if not t:
                return
            if len(t) > 8000:
                t = t[:8000] + b"\n..."
            w = C.Utf8ToWide(t)
            o = w.replace("\r", "").replace("\n", "\r\n")
            parts.append(o + "\r\n")

        if log:
            add(r.out)
        add(r.err)
        if r.code != 0:
            parts.append("(exit code " + str(r.code) + ")\r\n")
        logger("".join(parts))
    return r


# ---------------- background worker ----------------

g_bgCv = threading.Condition(threading.Lock())
g_bgQueue = deque()
g_bgPending = 0
g_bgStarted = False


def _BgWorker():
    global g_bgPending
    while True:
        with g_bgCv:
            while not g_bgQueue:
                g_bgCv.wait()
            j = g_bgQueue.popleft()
        try:
            j()
        except Exception:
            C.ReportException()
        with g_bgCv:
            g_bgPending -= 1


def BgRun(job):
    """Single background worker thread: jobs run one at a time in submission order (avoids index.lock races)."""
    global g_bgStarted, g_bgPending
    with g_bgCv:
        if not g_bgStarted:
            g_bgStarted = True
            threading.Thread(target=_BgWorker, daemon=True).start()
        g_bgPending += 1
        g_bgQueue.append(job)
        g_bgCv.notify()


def BgPending():
    return g_bgPending


# ---------------- status ----------------

class GitFile:
    __slots__ = ("path", "origPath", "x", "y")

    def __init__(self, path="", origPath="", x=" ", y=" "):
        self.path = path          # repo-relative, forward slashes
        self.origPath = origPath
        self.x = x                # one-character str (C++ char)
        self.y = y

    def copy(self):
        return GitFile(self.path, self.origPath, self.x, self.y)

    def __repr__(self):
        return "GitFile(%r, %r, %r, %r)" % (self.path, self.origPath, self.x, self.y)


class GitStatus:
    def __init__(self):
        self.ok = False
        self.isRepo = False
        self.folder = ""
        self.root = ""
        self.branch = ""
        self.upstream = ""
        self.error = ""
        self.detached = False
        self.noCommits = False
        self.ahead = 0
        self.behind = 0
        self.files = []    # list[GitFile]
        self.remotes = []  # list[str]


def SplitNul(s):
    v = []
    p = 0
    n = len(s)
    while p < n:
        e = s.find(b"\0", p)
        if e < 0:
            e = n
        v.append(s[p:e])
        p = e + 1
    return v


def SplitLines(s):
    v = []
    p = 0
    n = len(s)
    while p < n:
        e = s.find(b"\n", p)
        if e < 0:
            e = n
        line = s[p:e]
        if line.endswith(b"\r"):
            line = line[:-1]
        v.append(line)
        p = e + 1
    return v


def GitErrorText(r):
    """stderr (or stdout) trimmed, for message boxes."""
    t = C.TrimA(r.err)
    if not t:
        t = C.TrimA(r.out)
    if len(t) > 2000:
        t = t[:2000] + b"..."
    return C.Utf8ToWide(t)


def _atoi(b, i):
    # atoi: optional whitespace, optional sign, digits
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


def QueryGitStatus(folder):
    s = GitStatus()
    s.folder = folder
    s.ok = True
    if not folder:
        return s
    r = Git(folder, ["rev-parse", "--show-toplevel"], None, False)
    if r.code != 0:
        return s
    s.root = C.ToBackslashes(C.Utf8ToWide(C.TrimA(r.out)))
    s.isRepo = True
    r = Git(s.root, ["status", "--porcelain=v1", "-b", "-z", "--untracked-files=all"], None, False)
    if r.code != 0:
        s.ok = False
        s.error = GitErrorText(r)
        return s
    f = SplitNul(r.out)
    i = 0
    while i < len(f):
        e = f[i]
        if len(e) >= 3 and e[0:2] == b"##":
            h = e[3:]
            if h.startswith(b"No commits yet on "):
                s.branch = C.Utf8ToWide(h[18:])
                s.noCommits = True
            elif h.startswith(b"Initial commit on "):
                s.branch = C.Utf8ToWide(h[18:])
                s.noCommits = True
            elif h.startswith(b"HEAD (no branch)"):
                s.detached = True
                s.branch = "HEAD"
            else:
                br = h.find(b" [")
                if br >= 0:
                    info = h[br + 2:]
                    a = info.find(b"ahead ")
                    if a >= 0:
                        s.ahead = _atoi(info, a + 6)
                    b = info.find(b"behind ")
                    if b >= 0:
                        s.behind = _atoi(info, b + 7)
                    h = h[:br]
                dots = h.find(b"...")
                if dots >= 0:
                    s.branch = C.Utf8ToWide(h[:dots])
                    s.upstream = C.Utf8ToWide(h[dots + 3:])
                else:
                    s.branch = C.Utf8ToWide(h)
            i += 1
            continue
        if len(e) < 4:
            i += 1
            continue
        gf = GitFile()
        gf.x = chr(e[0])
        gf.y = chr(e[1])
        gf.path = C.Utf8ToWide(e[3:])
        if (gf.x == "R" or gf.x == "C") and i + 1 < len(f):
            i += 1
            gf.origPath = C.Utf8ToWide(f[i])
        s.files.append(gf)
        i += 1
    if s.detached:
        r = Git(s.root, ["rev-parse", "--short", "HEAD"], None, False)
        if r.code == 0:
            s.branch = C.Utf8ToWide(C.TrimA(r.out))
    r = Git(s.root, ["remote"], None, False)
    for l in SplitLines(r.out):
        if C.TrimA(l):
            s.remotes.append(C.Utf8ToWide(C.TrimA(l)))
    return s
