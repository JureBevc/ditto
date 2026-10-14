# ditto - port of md_highlight.h / md_highlight.cpp
#
# Minimal line-based Markdown highlighter. Only fenced code blocks carry state across lines.
#
# Python interface:
#   state = MdNextState(line, n, state)        line: bytes (first n bytes used)
#   spans = MdHighlightLine(line, n, state)    -> list[MdSpan]
#   MdStateCache().StateAt(buf, line) / .Invalidate(fromLine)

(MS_NORMAL, MS_HEADING, MS_BOLD, MS_ITALIC, MS_CODE, MS_LINK, MS_URL, MS_QUOTE, MS_LIST, MS_RULE, MS_FENCE, MS_HTML,
 MS_COUNT) = range(13)

_SP = 0x20
_TAB = 0x09
_CR = 0x0D
_BT = 0x60   # `
_TILDE = 0x7E
_STAR = 0x2A
_US = 0x5F   # _
_BSL = 0x5C  # backslash
_LBR = 0x5B  # [
_RBR = 0x5D  # ]
_LPAR = 0x28
_RPAR = 0x29
_BANG = 0x21
_LT = 0x3C
_GT = 0x3E
_HASH = 0x23
_MINUS = 0x2D
_PLUS = 0x2B
_EQ = 0x3D
_DOT = 0x2E
_SLASH = 0x2F

SIZE_MAX = (1 << 64) - 1


def _isalnum(c):
    return 0x30 <= c <= 0x39 or 0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A


def _isalpha(c):
    return 0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A


def _isdigit(c):
    return 0x30 <= c <= 0x39


class MdSpan:
    __slots__ = ("start", "end", "style")

    def __init__(self, start=0, end=0, style=0):
        self.start = start
        self.end = end
        self.style = style

    def __repr__(self):
        return f"MdSpan({self.start}, {self.end}, {self.style})"


def LeadingSpaces(s, n):
    i = 0
    while i < n and s[i] == _SP:
        i += 1
    return i


def OnlyWs(s, a, n):
    """True if s[a:a+n] has only spaces / tabs / CRs."""
    for i in range(a, a + n):
        c = s[i]
        if c != _SP and c != _TAB and c != _CR:
            return False
    return True


def FenceRun(s, n):
    """Returns (fence run length, ch, after) if the line opens/closes a fence, else (0, 0, 0)."""
    i = LeadingSpaces(s, n)
    if i > 3 or i >= n or (s[i] != _BT and s[i] != _TILDE):
        return 0, 0, 0
    c = s[i]
    j = i
    while j < n and s[j] == c:
        j += 1
    if j - i < 3:
        return 0, 0, 0
    return j - i, c, j


def MdNextState(s, n, state):
    """state: 0 = normal, otherwise (fenceChar << 8) | fenceLen  (inside a fenced code block)"""
    run, c, after = FenceRun(s, n)
    if not state:
        if not run:
            return 0
        if c == _BT and s.find(b"`", after, n) >= 0:
            return 0  # backticks in info string: not a fence
        return ((c & 0xFF) << 8) | min(run, 255)
    if run and (state >> 8) == c and run >= (state & 0xFF) and OnlyWs(s, after, n - after):
        return 0
    return state


def Push(out, a, b, st):
    if b > a:
        out.append(MdSpan(a, b, st))


def IsRule(s, n):
    i = LeadingSpaces(s, n)
    if i > 3 or i >= n:
        return False
    c = s[i]
    if c != _MINUS and c != _STAR and c != _US:
        return False
    cnt = 0
    while i < n:
        x = s[i]
        if x == c:
            cnt += 1
        elif x != _SP and x != _TAB and x != _CR:
            return False
        i += 1
    return cnt >= 3


def StartsWith(s, n, i, lit):
    """lit: lowercase bytes; case-insensitive compare like _strnicmp."""
    l = len(lit)
    return i + l <= n and s[i:i + l].lower() == lit


def Inline(s, a, b, base, out):
    """Inline spans for [a, b) with base style for plain text."""
    i = a
    plain = a
    while i < b:
        c = s[i]
        if c == _BSL and i + 1 < b:
            i += 2
            continue
        if c == _BT:
            r = i
            while r < b and s[r] == _BT:
                r += 1
            run = r - i
            j = r
            close = SIZE_MAX
            while j < b:
                if s[j] == _BT:
                    k = j
                    while k < b and s[k] == _BT:
                        k += 1
                    if k - j == run:
                        close = k
                        break
                    j = k
                else:
                    j += 1
            if close != SIZE_MAX:
                Push(out, plain, i, base)
                Push(out, i, close, MS_CODE)
                i = plain = close
                continue
            i = r
            continue
        if c == _STAR or c == _US:
            dbl = i + 1 < b and s[i + 1] == c
            w = 2 if dbl else 1
            if i + w < b and s[i + w] != _SP and (c == _STAR or i == a or not _isalnum(s[i - 1])):
                # find closing
                j = i + w
                close = SIZE_MAX
                while j + w <= b:
                    sj = s[j]
                    if sj == _BSL:
                        j += 2
                        continue
                    if sj == _BT:
                        break
                    if (sj == c and (not dbl or (j + 1 < b and s[j + 1] == c)) and s[j - 1] != _SP and
                            (dbl or j + 1 >= b or s[j + 1] != c)):
                        close = j + w
                        break
                    j += 1
                if close != SIZE_MAX and close > i + 2 * w:
                    Push(out, plain, i, base)
                    Push(out, i, close, MS_BOLD if dbl else MS_ITALIC)
                    i = plain = close
                    continue
            i += w
            continue
        if c == _LBR or (c == _BANG and i + 1 < b and s[i + 1] == _LBR):
            open_ = i + 1 if c == _BANG else i
            j = open_ + 1
            depth = 1
            while j < b and depth:
                if s[j] == _LBR:
                    depth += 1
                elif s[j] == _RBR:
                    depth -= 1
                if depth:
                    j += 1
            if j < b and j + 1 < b and s[j + 1] == _LPAR:
                k = s.find(b")", j + 1, b)
                if k >= 0:
                    Push(out, plain, i, base)
                    Push(out, i, j + 1, MS_LINK)
                    Push(out, j + 1, k + 1, MS_URL)
                    i = plain = k + 1
                    continue
            elif j < b and j + 1 < b and s[j + 1] == _LBR:
                k = s.find(b"]", j + 2, b)
                if k >= 0:
                    Push(out, plain, i, base)
                    Push(out, i, k + 1, MS_LINK)
                    i = plain = k + 1
                    continue
            i += 1
            continue
        if c == _LT:
            k = s.find(b">", i, b)
            if k >= 0:
                url = (StartsWith(s, b, i + 1, b"http://") or StartsWith(s, b, i + 1, b"https://") or
                       StartsWith(s, b, i + 1, b"mailto:"))
                tag = i + 1 < b and (_isalpha(s[i + 1]) or s[i + 1] == _SLASH or s[i + 1] == _BANG)
                if url or tag:
                    Push(out, plain, i, base)
                    Push(out, i, k + 1, MS_URL if url else MS_HTML)
                    i = plain = k + 1
                    continue
            i += 1
            continue
        if ((c == 0x68 or c == 0x48) and (StartsWith(s, b, i, b"http://") or StartsWith(s, b, i, b"https://")) and
                (i == a or not _isalnum(s[i - 1]))):
            j = i
            while j < b:
                x = s[j]
                if x == _SP or x == _TAB or x == _RPAR or x == _GT or x == _CR:
                    break
                j += 1
            Push(out, plain, i, base)
            Push(out, i, j, MS_URL)
            i = plain = j
            continue
        i += 1
    Push(out, plain, b, base)


def MdHighlightLine(s, n, state):
    """Returns the list of MdSpan for line s[:n] given the fence state at its start."""
    out = []
    if state:  # inside fenced block (closing fence line included)
        Push(out, 0, n, MS_FENCE)
        return out
    if FenceRun(s, n)[0]:
        Push(out, 0, n, MS_FENCE)
        return out
    i = LeadingSpaces(s, n)
    if i >= 4:  # indented code block
        Push(out, 0, n, MS_FENCE)
        return out
    if i < n and s[i] == _HASH:
        j = i
        while j < n and s[j] == _HASH:
            j += 1
        if j - i <= 6 and (j == n or s[j] == _SP or s[j] == _TAB or s[j] == _CR):
            Push(out, 0, n, MS_HEADING)
            return out
    if IsRule(s, n):
        Push(out, 0, n, MS_RULE)
        return out
    if i < n and s[i] == _GT:
        j = i
        while j < n and (s[j] == _GT or s[j] == _SP):
            j += 1
        Push(out, 0, j, MS_LIST)
        Inline(s, j, n, MS_QUOTE, out)
        return out
    # list markers: -, *, + or 1. / 1)
    j = i
    if j < n and (s[j] == _MINUS or s[j] == _STAR or s[j] == _PLUS) and j + 1 < n and s[j + 1] == _SP:
        Push(out, 0, j + 1, MS_LIST)
        k = j + 2
        if (k + 3 <= n and s[k] == _LBR and (s[k + 1] == _SP or s[k + 1] == 0x78 or s[k + 1] == 0x58) and
                s[k + 2] == _RBR):
            Push(out, j + 1, k + 3, MS_LIST)
            Inline(s, k + 3, n, MS_NORMAL, out)
        else:
            Inline(s, j + 1, n, MS_NORMAL, out)
        return out
    while j < n and _isdigit(s[j]) and j - i < 9:
        j += 1
    if j > i and j + 1 < n and (s[j] == _DOT or s[j] == _RPAR) and s[j + 1] == _SP:
        Push(out, 0, j + 1, MS_LIST)
        Inline(s, j + 1, n, MS_NORMAL, out)
        return out
    # setext underline (=== / ---) is shown as heading
    if i < n and s[i] == _EQ:
        k = i
        while k < n and s[k] == _EQ:
            k += 1
        if OnlyWs(s, k, n - k):
            Push(out, 0, n, MS_HEADING)
            return out
    Inline(s, 0, n, MS_NORMAL, out)
    return out


class MdStateCache:
    """Caches the fence state at every kStep-th line so the state at any line is cheap to find."""
    kStep = 256

    def __init__(self):
        self.cp_ = [0]  # cp_[k] = state at the start of line k*kStep

    def Invalidate(self, fromLine):
        keep = fromLine // MdStateCache.kStep + 1  # cp_[k] covers line k*kStep; valid if k*kStep <= fromLine
        if len(self.cp_) > keep:
            del self.cp_[keep:]

    def StateAt(self, buf, line):
        kStep = MdStateCache.kStep
        cp = self.cp_
        k = min(line // kStep, len(cp) - 1)
        cur = k * kStep
        st = cp[k]
        if cur == line:
            return st
        # sequential scan using large reads
        off = buf.LineStart(cur)
        total = buf.Length()
        carry = b""
        kChunk = 1 << 20
        while cur < line and off < total:
            chunk = buf.Read(off, kChunk)
            csz = len(chunk)
            p = 0
            while cur < line:
                e = chunk.find(b"\n", p)
                if e < 0:
                    if len(carry) < 512:
                        carry += chunk[p:p + min(csz - p, 512)]
                    break
                if carry:
                    carry += chunk[p:p + min(e - p, 512)]
                    st = MdNextState(carry, len(carry), st)
                    carry = b""
                else:
                    st = MdNextState(chunk[p:p + 512] if e - p > 512 else chunk[p:e], min(e - p, 512), st)
                p = e + 1
                cur += 1
                if cur % kStep == 0 and cur // kStep == len(cp):
                    cp.append(st)
            off += csz
        return st
