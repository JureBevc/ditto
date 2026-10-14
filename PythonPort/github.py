# Publish to GitHub: create the repository through the GitHub REST API (token from Git Credential
# Manager, or a personal access token typed by the user), add it as 'origin' and push. Port of github.cpp.
from ctypes import byref, sizeof, create_string_buffer
from win32 import DWORD

import common as C
import win32 as W
import app
import dialogs
from git import Git, BgRun, GitErrorText, SplitLines

MsgBox = C.MsgBox

_WS = b" \n\r\t"


def JsonEscape(s):
    o = bytearray()
    for c in s:
        if c == 0x22:
            o += b'\\"'
        elif c == 0x5C:
            o += b"\\\\"
        elif c == 0x0A:
            o += b"\\n"
        elif c == 0x0D:
            o += b"\\r"
        elif c == 0x09:
            o += b"\\t"
        elif c < 0x20:
            o += b"\\u%04x" % c
        else:
            o.append(c)
    return bytes(o)


def _strtoul16(b):
    v = 0
    for ch in b:
        if 0x30 <= ch <= 0x39:
            d = ch - 0x30
        elif 0x61 <= ch <= 0x66:
            d = ch - 0x61 + 10
        elif 0x41 <= ch <= 0x46:
            d = ch - 0x41 + 10
        else:
            break
        v = v * 16 + d
    return v


def JsonStr(json, key):
    """Returns the first string value for "key" in a JSON document (simple scanner, enough for GitHub responses).
    json: bytes, key: str or bytes; returns bytes."""
    if isinstance(key, str):
        key = key.encode("utf-8")
    pat = b'"' + key + b'"'
    n = len(json)
    p = json.find(pat)
    while p >= 0:
        q = p + len(pat)
        while q < n and json[q] in _WS:
            q += 1
        if q < n and json[q] == 0x3A:  # ':'
            q += 1
            while q < n and json[q] in _WS:
                q += 1
            if q < n and json[q] == 0x22:  # '"'
                out = bytearray()
                q += 1
                while q < n and json[q] != 0x22:
                    if json[q] == 0x5C and q + 1 < n:
                        q += 1
                        e = json[q]
                        if e == 0x6E:  # n
                            out.append(0x0A)
                        elif e == 0x74:  # t
                            out.append(0x09)
                        elif e == 0x75 and q + 4 < n:  # u
                            v = _strtoul16(json[q + 1:q + 5])
                            if 0xD800 <= v <= 0xDFFF:
                                out += b"\xef\xbf\xbd"  # lone surrogate -> U+FFFD (WideCharToMultiByte)
                            else:
                                out += chr(v).encode("utf-8")
                            q += 4
                        else:
                            out.append(e)
                    else:
                        out.append(json[q])
                    q += 1
                return bytes(out)
        p = json.find(pat, p + 1)
    return b""


def HttpRequest(method, path, token, body):
    """Returns (status, resp: bytes, err: str)."""
    resp = b""
    err = ""
    s = W.WinHttpOpen("ditto/1.0", W.WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY, None, None, 0)
    if not s:
        s = W.WinHttpOpen("ditto/1.0", W.WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, None, None, 0)
    if not s:
        err = C.FormatError(W.GetLastError())
        return -1, resp, err
    status = -1
    c = W.WinHttpConnect(s, "api.github.com", W.INTERNET_DEFAULT_HTTPS_PORT, 0)
    r = W.WinHttpOpenRequest(c, method, path, None, None, None, W.WINHTTP_FLAG_SECURE) if c else None
    if r:
        headers = ("Accept: application/vnd.github+json\r\nX-GitHub-Api-Version: 2022-11-28\r\nContent-Type: application/json\r\n"
                   "Authorization: Bearer " + C.Utf8ToWide(token) + "\r\n")
        bodyBuf = create_string_buffer(body, len(body)) if body else None
        ok = W.WinHttpSendRequest(r, headers, 0xFFFFFFFF, bodyBuf, len(body), len(body), 0)
        if ok:
            ok = W.WinHttpReceiveResponse(r, None)
        if ok:
            code = DWORD(0)
            sz = DWORD(sizeof(code))
            W.WinHttpQueryHeaders(r, W.WINHTTP_QUERY_STATUS_CODE | W.WINHTTP_QUERY_FLAG_NUMBER, None,
                                  byref(code), byref(sz), None)
            status = code.value
            chunks = []
            while True:
                avail = DWORD(0)
                if not W.WinHttpQueryDataAvailable(r, byref(avail)) or not avail.value:
                    break
                chunk = create_string_buffer(avail.value)
                rd = DWORD(0)
                if not W.WinHttpReadData(r, chunk, avail.value, byref(rd)) or not rd.value:
                    break
                chunks.append(chunk.raw[:rd.value])
            resp = b"".join(chunks)
        else:
            err = C.FormatError(W.GetLastError())
    else:
        err = C.FormatError(W.GetLastError())
    if r:
        W.WinHttpCloseHandle(r)
    if c:
        W.WinHttpCloseHandle(c)
    W.WinHttpCloseHandle(s)
    return status, resp, err


class Ctx:
    def __init__(self):
        self.folder = ""
        self.root = ""
        self.branch = ""
        self.name = ""
        self.desc = ""
        self.priv = True
        self.needInit = False
        self.needCommit = False
        self.token = b""  # credential from GCM (user may be empty)
        self.user = b""
        self.manualToken = False


def Fail(msg):
    def ui():
        app.SetStatusText("Publish failed")
        app.RefreshGit()
        MsgBox(app.Main(), msg, W.MB_ICONERROR)

    C.RunOnUi(ui)


def AskToken(c, why):
    def ui():
        tok = ""
        prompt = why + "\nPaste a GitHub personal access token (scope 'repo') to continue:"
        ok, tok = dialogs.InputBox(app.Main(), "GitHub Token", prompt, tok, True)
        if not ok or not C.Trim(tok):
            app.SetStatusText("Publish cancelled")
            app.RefreshGit()
            return
        c.token = C.WideToUtf8(C.Trim(tok))
        c.user = b""
        c.manualToken = True
        BgRun(lambda: Step2(c))

    C.RunOnUi(ui)


def Step1(c):
    """Step 1 (worker): init/commit if needed, then fetch a token from the git credential helper."""
    dir = c.folder if not c.root else c.root
    if c.needInit:
        r = Git(dir, ["init"])
        if r.code != 0:
            Fail("git init failed:\n\n" + GitErrorText(r))
            return
        c.root = dir
    if c.needCommit:
        r = Git(dir, ["add", "-A"])
        if r.code == 0:
            r = Git(dir, ["commit", "-m", "Initial commit"])
        if r.code != 0:
            Fail("Creating the initial commit failed:\n\n" + GitErrorText(r))
            return
    br = Git(dir, ["rev-parse", "--abbrev-ref", "HEAD"], None, False)
    c.branch = C.Utf8ToWide(C.TrimA(br.out))
    inp = b"protocol=https\nhost=github.com\n\n"
    cr = Git(dir, ["credential", "fill"], inp, False)
    if cr.code == 0:
        for l in SplitLines(cr.out):
            if l.startswith(b"password="):
                c.token = l[9:]
            elif l.startswith(b"username="):
                c.user = l[9:]
    if not c.token:
        AskToken(c, "No GitHub credentials were found (Git Credential Manager did not return a token).")
        return
    Step2(c)


def Step2(c):
    """Step 2 (worker): create the repository, add the remote and push."""
    body = (b'{"name":"' + JsonEscape(C.WideToUtf8(c.name)) + b'","description":"' + JsonEscape(C.WideToUtf8(c.desc)) +
            b'","private":' + (b"true" if c.priv else b"false") + b"}")
    status, resp, err = HttpRequest("POST", "/user/repos", c.token, body)
    if status == 401 or status == 403:
        if not c.manualToken:
            inp = b"protocol=https\nhost=github.com\nusername=" + c.user + b"\npassword=" + c.token + b"\n\n"
            if status == 401:
                Git(c.root, ["credential", "reject"], inp, False)
        msg = C.Utf8ToWide(JsonStr(resp, "message"))
        AskToken(c, "GitHub rejected the credentials (HTTP " + str(status) + ": " + msg + ").")
        return
    if status != 201:
        msg = err if status < 0 else C.Utf8ToWide(JsonStr(resp, "message"))
        em = resp.find(b'"errors"')
        if em >= 0:
            detail = JsonStr(resp[em:], "message")
            if detail:
                msg += "\n" + C.Utf8ToWide(detail)
        Fail("Could not create the GitHub repository (HTTP " + str(status) + "):\n\n" + msg)
        return
    cloneUrl = C.Utf8ToWide(JsonStr(resp, "clone_url"))
    fullName = C.Utf8ToWide(JsonStr(resp, "full_name"))
    login = JsonStr(resp, "login")
    if not cloneUrl:
        Fail("Unexpected response from GitHub.")
        return
    r = Git(c.root, ["remote", "add", "origin", cloneUrl])
    if r.code != 0:
        Fail("The repository was created on GitHub, but adding the remote failed:\n\n" + GitErrorText(r))
        return
    if c.manualToken:
        # store the token with the credential helper for future pushes
        user = b"x-access-token" if not login else login
        inp = b"protocol=https\nhost=github.com\nusername=" + user + b"\npassword=" + c.token + b"\n\n"
        Git(c.root, ["credential", "approve"], inp, False)
        hdr = b"AUTHORIZATION: basic " + C.Base64(user + b":" + c.token).encode("ascii")
        r = Git(c.root, ["-c", "http.https://github.com/.extraheader=" + C.Utf8ToWide(hdr), "push", "-u", "origin",
                         c.branch], None, False)
        rr = r
        C.RunOnUi(lambda: app.AppendOutput("> git push -u origin <branch>   (authenticated with the provided token)\r\n" +
                                           (GitErrorText(rr) + "\r\n" if rr.code else "")))
    else:
        r = Git(c.root, ["push", "-u", "origin", c.branch])
    if r.code != 0:
        Fail("The repository was created on GitHub, but the push failed:\n\n" + GitErrorText(r))
        return
    url = "https://github.com/" + fullName

    def ui():
        app.SetStatusText("Published to " + url)
        app.RefreshGit()
        if MsgBox(app.Main(), "Successfully published to GitHub:\n" + url + "\n\nOpen it in your browser?",
                  W.MB_YESNO | W.MB_ICONINFORMATION) == W.IDYES:
            W.ShellExecuteW(None, "open", url, None, None, W.SW_SHOWNORMAL)

    C.RunOnUi(ui)


def GitPublish():
    folder = app.Folder()
    if not folder:
        MsgBox(app.Main(), "Open a folder first (File > Open Folder).", W.MB_ICONINFORMATION)
        return
    st = app.Status()
    c = Ctx()
    c.folder = folder
    c.root = st.root if st.isRepo else folder
    if st.isRepo and "origin" in st.remotes:
        MsgBox(app.Main(), "This repository already has a remote named 'origin'. Use Push instead.", W.MB_ICONINFORMATION)
        return
    if st.isRepo and st.detached:
        MsgBox(app.Main(), "Checkout a branch before publishing.", W.MB_ICONWARNING)
        return
    c.needInit = not st.isRepo
    if not st.isRepo or st.noCommits:
        if MsgBox(app.Main(), "The repository has no commits yet.\n\nStage all files and create an 'Initial commit' before publishing?",
                  W.MB_OKCANCEL | W.MB_ICONQUESTION) != W.IDOK:
            return
        c.needCommit = True
    name = C.FileNameOf(c.root)
    name = name.replace(" ", "-")
    desc = ""
    priv = True
    ok, name, desc, priv = dialogs.PublishBox(app.Main(), name, desc, priv)
    if not ok:
        return
    c.name = name
    c.desc = desc
    c.priv = priv
    app.SetStatusText("Publishing to GitHub...")
    app.ShowOutput()
    BgRun(lambda: Step1(c))
