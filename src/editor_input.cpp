// EditorView: message handling, keyboard/mouse input, editing commands, clipboard and search.
#include "editor_view.h"
#include "resource.h"

static const UINT_PTR kTimerIndex = 1, kTimerDrag = 2;
static const uint64_t kNotFound = UINT64_MAX;

static int CharClass(unsigned char c) {
    if (c == '\n' || c == '\r') return 3;
    if (c == ' ' || c == '\t') return 0;
    if (isalnum(c) || c == '_' || c >= 0x80) return 1;
    return 2;
}
static bool IsWordByte(unsigned char c) { return isalnum(c) || c == '_' || c >= 0x80; }
static void LowerAscii(std::string& s) {
    for (auto& c : s)
        if (c >= 'A' && c <= 'Z') c = (char)(c + 32);
}

// ---------------- search ----------------

namespace {
struct Searcher {
    const TextBuffer& buf;
    const FindQuery& q;
    const std::atomic<bool>* cancel;
    std::string needle, chunk;
    uint64_t total;
    static const uint64_t kChunk = 4 << 20;
    Searcher(const TextBuffer& b, const FindQuery& qq, const std::atomic<bool>* c) : buf(b), q(qq), cancel(c) {
        needle = q.needle;
        if (!q.matchCase) LowerAscii(needle);
        total = buf.Length();
    }
    bool Check(uint64_t pos) const {
        if (!q.wholeWord) return true;
        if (pos > 0 && IsWordByte((unsigned char)buf.At(pos - 1))) return false;
        if (pos + needle.size() < total && IsWordByte((unsigned char)buf.At(pos + needle.size()))) return false;
        return true;
    }
    // calls fn(at) for each match starting in [a, b) in order; fn returns false to stop
    template <class F> void Scan(uint64_t a, uint64_t b, F fn) {
        size_t n = needle.size();
        std::boyer_moore_horspool_searcher<std::string::const_iterator> srch(needle.begin(), needle.end());
        uint64_t pos = a;
        while (pos < b) {
            if (cancel && *cancel) return;
            uint64_t len = std::min(kChunk, b - pos);
            buf.Read(pos, std::min(len + n - 1, total - pos), chunk);
            if (!q.matchCase) LowerAscii(chunk);
            auto it = chunk.cbegin();
            for (;;) {
                auto r = std::search(it, chunk.cend(), srch);
                if (r == chunk.cend()) break;
                uint64_t at = pos + (uint64_t)(r - chunk.cbegin());
                if (at >= pos + len) break;
                if (Check(at) && !fn(at)) return;
                it = r + 1;
            }
            pos += len;
        }
    }
    uint64_t First(uint64_t a, uint64_t b) {
        uint64_t res = kNotFound;
        Scan(a, b, [&](uint64_t at) { res = at; return false; });
        return res;
    }
    uint64_t Last(uint64_t a, uint64_t b) {
        uint64_t end = b;
        while (end > a) {
            if (cancel && *cancel) return kNotFound;
            uint64_t st = end - a > kChunk ? end - kChunk : a;
            uint64_t best = kNotFound;
            Scan(st, end, [&](uint64_t at) { best = at; return true; });
            if (best != kNotFound) return best;
            end = st;
        }
        return kNotFound;
    }
};
}  // namespace

uint64_t SearchBuffer(const TextBuffer& buf, const FindQuery& q, uint64_t start, bool forward, const std::atomic<bool>* cancel) {
    if (q.needle.empty() || q.needle.size() > buf.Length()) return kNotFound;
    Searcher s(buf, q, cancel);
    start = std::min(start, buf.Length());
    uint64_t r;
    if (forward) {
        r = s.First(start, buf.Length());
        if (r == kNotFound) r = s.First(0, start);
    } else {
        r = s.Last(0, start);
        if (r == kNotFound) r = s.Last(start, buf.Length());
    }
    return r;
}

void EditorView::Find(const FindQuery& q, bool forward, bool incremental) {
    if (q.needle.empty() || searching_) return;
    if (!buf->Ready()) { findMessage = L"File is still loading"; Notify(); return; }
    if (findThread_.joinable()) findThread_.join();
    searching_ = true;
    findCancel_ = false;
    findLen_ = q.needle.size();
    findMessage = L"Searching...";
    Notify();
    uint64_t start = (forward && !incremental) ? std::max(caret_, anchor_) : std::min(caret_, anchor_);
    HWND h = hwnd;
    TextBuffer* b = buf.get();
    std::atomic<bool>* cancel = &findCancel_;
    findThread_ = std::thread([q, forward, start, h, b, cancel]() {
        uint64_t r = SearchBuffer(*b, q, start, forward, cancel);
        PostMessageW(h, WM_APP_FINDDONE, (WPARAM)r, 0);
    });
}

void EditorView::OnFindDone(uint64_t r, size_t len) {
    if (findThread_.joinable()) findThread_.join();
    searching_ = false;
    if (r == kNotFound) {
        findMessage = L"No results";
    } else {
        findMessage.clear();
        anchor_ = r;
        caret_ = r + len;
        desiredCol_ = -1;
        EnsureCaretVisible(true);
        InvalidateRect(hwnd, nullptr, FALSE);
    }
    Notify();
}

bool EditorView::ReplaceOne(const FindQuery& q, const std::string& repl) {
    if (!CanEdit() || q.needle.empty()) return false;
    uint64_t a = std::min(caret_, anchor_), b = std::max(caret_, anchor_);
    if (b - a == q.needle.size()) {
        std::string sel;
        buf->Read(a, b - a, sel);
        std::string n = q.needle;
        if (!q.matchCase) { LowerAscii(sel); LowerAscii(n); }
        if (sel == n) InsertText(repl, false);
    }
    Find(q, true);
    return true;
}

uint64_t EditorView::ReplaceAll(const FindQuery& q, const std::string& repl) {
    if (!CanEdit() || q.needle.empty()) return 0;
    HCURSOR old = SetCursor(LoadCursor(nullptr, IDC_WAIT));
    Searcher s(*buf, q, nullptr);
    std::vector<uint64_t> offs;
    uint64_t next = 0;
    s.Scan(0, buf->Length(), [&](uint64_t at) {
        if (at >= next) { offs.push_back(at); next = at + q.needle.size(); }
        return true;
    });
    if (!offs.empty()) {
        EditInfo info = buf->ReplaceMany(offs, q.needle.size(), repl, caret_);
        caret_ = anchor_ = std::min(caret_, buf->Length());
        AfterEdit(info);
    }
    SetCursor(old);
    findMessage = offs.empty() ? L"No results" : L"Replaced " + std::to_wstring(offs.size()) + L" occurrence(s)";
    Notify();
    return offs.size();
}

std::string EditorView::SelectionForFind() const {
    uint64_t a = std::min(caret_, anchor_), b = std::max(caret_, anchor_);
    if (b == a || b - a > 256) return std::string();
    std::string s;
    buf->Read(a, b - a, s);
    if (s.find('\n') != std::string::npos) return std::string();
    return s;
}

// ---------------- editing ----------------

void EditorView::AfterEdit(const EditInfo& info) {
    md_.Invalidate(info.whole ? 0 : info.line);
    if (wrap_) UpdateWrapAfterEdit(info);
    desiredCol_ = -1;
    UpdateGutter();
    uint64_t rc = RowCount();
    if (firstRow_ >= rc) firstRow_ = rc ? rc - 1 : 0;
    UpdateScrollbars();
    EnsureCaretVisible();
    InvalidateRect(hwnd, nullptr, FALSE);
    Notify();
}

void EditorView::InsertText(const std::string& s, bool typing) {
    if (!CanEdit()) { MessageBeep(MB_OK); return; }
    uint64_t a = std::min(caret_, anchor_), b = std::max(caret_, anchor_);
    if (a == b && s.empty()) return;
    EditInfo info = buf->Replace(a, b - a, s.data(), s.size(), caret_, typing && a == b);
    caret_ = anchor_ = a + s.size();
    AfterEdit(info);
}

void EditorView::DeleteRange(uint64_t a, uint64_t b) {
    if (!CanEdit()) { MessageBeep(MB_OK); return; }
    if (a >= b) return;
    EditInfo info = buf->Replace(a, b - a, nullptr, 0, caret_);
    caret_ = anchor_ = a;
    AfterEdit(info);
}

void EditorView::ReplaceLines(uint64_t l0, uint64_t l1, const std::function<std::string(const std::string&)>& fn) {
    if (!CanEdit()) return;
    uint64_t s = buf->LineStart(l0), e = buf->LineEnd(l1);
    if (e - s > (32ull << 20)) { MessageBeep(MB_ICONWARNING); return; }
    std::string text, out;
    buf->Read(s, e - s, text);
    size_t p = 0;
    for (;;) {
        size_t nl = text.find('\n', p);
        std::string line = text.substr(p, nl == std::string::npos ? std::string::npos : nl - p);
        out += fn(line);
        if (nl == std::string::npos) break;
        out += '\n';
        p = nl + 1;
    }
    if (out == text) return;
    EditInfo info = buf->Replace(s, e - s, out.data(), out.size(), caret_);
    anchor_ = s;
    caret_ = s + out.size();
    AfterEdit(info);
}

uint64_t EditorView::PrevChar(uint64_t off) const {
    if (off == 0) return 0;
    if (buf->At(off - 1) == '\n' && off >= 2 && buf->At(off - 2) == '\r') return off - 2;
    uint64_t p = off - 1;
    while (p > 0 && off - p < 4 && IsUtf8Cont((unsigned char)buf->At(p))) --p;
    return p;
}

uint64_t EditorView::NextChar(uint64_t off) const {
    uint64_t total = buf->Length();
    if (off >= total) return total;
    unsigned char c = (unsigned char)buf->At(off);
    if (c == '\r' && off + 1 < total && buf->At(off + 1) == '\n') return off + 2;
    int len = c < 0x80 ? 1 : (c & 0xE0) == 0xC0 ? 2 : (c & 0xF0) == 0xE0 ? 3 : (c & 0xF8) == 0xF0 ? 4 : 1;
    uint64_t p = off + 1;
    while (p < total && p < off + len && IsUtf8Cont((unsigned char)buf->At(p))) ++p;
    return p;
}

uint64_t EditorView::WordRight(uint64_t off) const {
    uint64_t total = buf->Length();
    if (off >= total) return total;
    if (CharClass((unsigned char)buf->At(off)) == 3) return NextChar(off);
    uint64_t p = off, lim = off + (1 << 20);
    while (p < total && p < lim && CharClass((unsigned char)buf->At(p)) == 0) ++p;
    if (p < total) {
        int k = CharClass((unsigned char)buf->At(p));
        if (k != 3)
            while (p < total && p < lim && CharClass((unsigned char)buf->At(p)) == k) ++p;
    }
    return p;
}

uint64_t EditorView::WordLeft(uint64_t off) const {
    if (off == 0) return 0;
    if (CharClass((unsigned char)buf->At(off - 1)) == 3) return PrevChar(off);
    uint64_t p = off, lim = off > (1 << 20) ? off - (1 << 20) : 0;
    while (p > lim && CharClass((unsigned char)buf->At(p - 1)) == 0) --p;
    if (p > lim) {
        int k = CharClass((unsigned char)buf->At(p - 1));
        if (k != 3)
            while (p > lim && CharClass((unsigned char)buf->At(p - 1)) == k) --p;
    }
    return p;
}

void EditorView::SelectWordAt(uint64_t off) {
    uint64_t total = buf->Length();
    if (off >= total || CharClass((unsigned char)buf->At(off)) == 3) {
        if (off == 0 || CharClass((unsigned char)buf->At(off - 1)) == 3) { anchor_ = caret_ = off; return; }
        --off;
    }
    int k = CharClass((unsigned char)buf->At(off));
    uint64_t a = off, b = off, lim = 1 << 16;
    while (a > 0 && off - a < lim && CharClass((unsigned char)buf->At(a - 1)) == k) --a;
    while (b < total && b - off < lim && CharClass((unsigned char)buf->At(b)) == k) ++b;
    anchor_ = a;
    caret_ = b;
}

void EditorView::MoveCaret(uint64_t pos, bool extend) {
    caret_ = std::min(pos, buf->Length());
    if (!extend) anchor_ = caret_;
    buf->BreakUndoMerge();
    EnsureCaretVisible();
    InvalidateRect(hwnd, nullptr, FALSE);
    Notify();
}

void EditorView::Undo() {
    if (!CanEdit()) return;
    EditInfo info;
    uint64_t c;
    if (buf->Undo(info, c)) {
        caret_ = anchor_ = std::min(c, buf->Length());
        AfterEdit(info);
    }
}

void EditorView::Redo() {
    if (!CanEdit()) return;
    EditInfo info;
    uint64_t c;
    if (buf->Redo(info, c)) {
        caret_ = anchor_ = std::min(c, buf->Length());
        AfterEdit(info);
    }
}

void EditorView::Copy() {
    uint64_t a = std::min(caret_, anchor_), b = std::max(caret_, anchor_);
    if (a == b) {
        uint64_t line = buf->LineOf(caret_);
        a = buf->LineStart(line);
        b = buf->LineNext(line);
    }
    if (b - a > (256ull << 20)) { MsgBox(hwnd, L"Selection is too large to copy to the clipboard (limit 256 MB).", MB_ICONWARNING); return; }
    std::string s;
    buf->Read(a, b - a, s);
    SetClipboardText(hwnd, Utf8ToWide(s));
}

void EditorView::Cut() {
    if (!CanEdit()) return;
    Copy();
    if (caret_ != anchor_) {
        DeleteRange(std::min(caret_, anchor_), std::max(caret_, anchor_));
    } else {
        uint64_t line = buf->LineOf(caret_);
        DeleteRange(buf->LineStart(line), buf->LineNext(line));
    }
}

void EditorView::Paste() {
    if (!CanEdit()) return;
    std::wstring w;
    if (!GetClipboardText(hwnd, w)) return;
    std::string u = WideToUtf8(w), out;
    std::string eol = EolStr();
    out.reserve(u.size() + u.size() / 16);
    for (size_t i = 0; i < u.size(); ++i) {
        char c = u[i];
        if (c == '\r') {
            if (i + 1 < u.size() && u[i + 1] == '\n') ++i;
            out += eol;
        } else if (c == '\n') {
            out += eol;
        } else {
            out += c;
        }
    }
    buf->BreakUndoMerge();
    InsertText(out, false);
}

void EditorView::SelectAll() {
    anchor_ = 0;
    caret_ = buf->Length();
    InvalidateRect(hwnd, nullptr, FALSE);
    UpdateCaretPos();
    Notify();
}

void EditorView::GotoLine(uint64_t line, bool center) {
    uint64_t lc = buf->LineCount();
    caret_ = anchor_ = buf->LineStart(std::min(line, lc - 1));
    desiredCol_ = -1;
    EnsureCaretVisible(center);
    InvalidateRect(hwnd, nullptr, FALSE);
    Notify();
}

void EditorView::OnChar(wchar_t ch) {
    if (ch < 0x20 || ch == 0x7F) return;  // controls handled in OnKeyDown
    if (GetKeyState(VK_CONTROL) < 0 && GetKeyState(VK_MENU) >= 0) return;
    wchar_t w[2] = {ch, 0};
    int n = 1;
    if (IS_HIGH_SURROGATE(ch)) { pendingHigh_ = ch; return; }
    if (IS_LOW_SURROGATE(ch)) {
        if (!pendingHigh_) return;
        w[0] = pendingHigh_;
        w[1] = ch;
        n = 2;
    }
    pendingHigh_ = 0;
    InsertText(WideToUtf8(w, n), true);
}

void EditorView::OnKeyDown(WPARAM vk) {
    bool ctrl = GetKeyState(VK_CONTROL) < 0, shift = GetKeyState(VK_SHIFT) < 0;
    uint64_t selA = std::min(caret_, anchor_), selB = std::max(caret_, anchor_);
    switch (vk) {
    case VK_LEFT:
        if (!shift && !ctrl && selA != selB) MoveCaret(selA, false);
        else MoveCaret(ctrl ? WordLeft(caret_) : PrevChar(caret_), shift);
        desiredCol_ = -1;
        break;
    case VK_RIGHT:
        if (!shift && !ctrl && selA != selB) MoveCaret(selB, false);
        else MoveCaret(ctrl ? WordRight(caret_) : NextChar(caret_), shift);
        desiredCol_ = -1;
        break;
    case VK_UP:
    case VK_DOWN:
    case VK_PRIOR:
    case VK_NEXT: {
        bool up = vk == VK_UP || vk == VK_PRIOR;
        int64_t step = (vk == VK_UP || vk == VK_DOWN) ? 1 : PageRows() - 1;
        if (ctrl && (vk == VK_UP || vk == VK_DOWN)) { ScrollToRow((int64_t)firstRow_ + (up ? -1 : 1)); break; }
        uint64_t row, col;
        OffToRowCol(caret_, &row, &col);
        if (desiredCol_ < 0) desiredCol_ = (int64_t)col;
        int64_t keep = desiredCol_;
        int64_t nr = (int64_t)row + (up ? -step : step);
        int64_t rc = (int64_t)RowCount();
        uint64_t target;
        if (nr < 0) target = 0;
        else if (nr >= rc) target = buf->Length();
        else target = RowColToOff((uint64_t)nr, (uint64_t)desiredCol_);
        if (step > 1) ScrollToRow((int64_t)firstRow_ + (up ? -step : step));
        MoveCaret(target, shift);
        if (nr >= 0 && nr < rc) desiredCol_ = keep;
        break;
    }
    case VK_HOME: {
        if (ctrl) { MoveCaret(0, shift); break; }
        uint64_t line = buf->LineOf(caret_);
        uint64_t s = buf->LineStart(line), e = buf->LineEnd(line), p = s;
        while (p < e && p - s < 4096 && (buf->At(p) == ' ' || buf->At(p) == '\t')) ++p;
        MoveCaret(caret_ == p ? s : p, shift);
        desiredCol_ = -1;
        break;
    }
    case VK_END:
        if (ctrl) MoveCaret(buf->Length(), shift);
        else MoveCaret(buf->LineEnd(buf->LineOf(caret_)), shift);
        desiredCol_ = -1;
        break;
    case VK_BACK:
        if (selA != selB) DeleteRange(selA, selB);
        else if (caret_ > 0) DeleteRange(ctrl ? WordLeft(caret_) : PrevChar(caret_), caret_);
        break;
    case VK_DELETE:
        if (shift && !ctrl) { Cut(); break; }
        if (selA != selB) DeleteRange(selA, selB);
        else DeleteRange(caret_, ctrl ? WordRight(caret_) : NextChar(caret_));
        break;
    case VK_INSERT:
        if (ctrl) Copy();
        else if (shift) Paste();
        break;
    case VK_RETURN: {
        if (!CanEdit()) break;
        uint64_t line = buf->LineOf(selA);
        uint64_t s = buf->LineStart(line);
        std::string head, indent;
        buf->Read(s, std::min<uint64_t>(selA - s, 1024), head);
        for (char c : head) {
            if (c == ' ' || c == '\t') indent += c;
            else break;
        }
        buf->BreakUndoMerge();
        InsertText(EolStr() + indent, false);
        break;
    }
    case VK_TAB: {
        if (!CanEdit()) break;
        uint64_t la = buf->LineOf(selA), lb = buf->LineOf(selB);
        if (lb > la && selB == buf->LineStart(lb)) --lb;
        std::string first;
        buf->Read(buf->LineStart(la), 1, first);
        int tab = g_settings.tabSize;
        std::string unit = first == "\t" ? "\t" : std::string((size_t)tab, ' ');
        if (shift) {
            ReplaceLines(la, lb, [tab](const std::string& l) {
                if (!l.empty() && l[0] == '\t') return l.substr(1);
                size_t n = 0;
                while (n < l.size() && n < (size_t)tab && l[n] == ' ') ++n;
                return l.substr(n);
            });
        } else if (la != lb) {
            ReplaceLines(la, lb, [&unit](const std::string& l) {
                std::string t = l;
                if (!t.empty() && t.back() == '\r') t.pop_back();
                return t.empty() ? l : unit + l;
            });
        } else if (unit == "\t") {
            InsertText("\t", false);
        } else {
            uint64_t line = buf->LineOf(selA);
            uint64_t col = ColAt(buf->LineStart(line), buf->LineEnd(line), selA);
            InsertText(std::string((size_t)(tab - (int)(col % tab)), ' '), false);
        }
        break;
    }
    case VK_ESCAPE:
        if (selA != selB) MoveCaret(caret_, false);
        SendMessageW(GetParent(hwnd), WM_COMMAND, ID_FIND_CLOSE, 0);
        break;
    default:
        if (ctrl) {
            switch (vk) {
            case 'A': SelectAll(); break;
            case 'C': Copy(); break;
            case 'X': Cut(); break;
            case 'V': Paste(); break;
            case 'Z': if (shift) Redo(); else Undo(); break;
            case 'Y': Redo(); break;
            case 'L': {
                uint64_t line = buf->LineOf(caret_);
                anchor_ = buf->LineStart(buf->LineOf(anchor_ < caret_ ? anchor_ : caret_));
                caret_ = buf->LineNext(line);
                EnsureCaretVisible();
                InvalidateRect(hwnd, nullptr, FALSE);
                Notify();
                break;
            }
            }
        }
        break;
    }
}

void EditorView::ContextMenu(int x, int y) {
    HMENU m = CreatePopupMenu();
    UINT canEdit = CanEdit() ? MF_ENABLED : MF_GRAYED;
    AppendMenuW(m, MF_STRING | (buf->CanUndo() ? canEdit : MF_GRAYED), 1, L"Undo\tCtrl+Z");
    AppendMenuW(m, MF_STRING | (buf->CanRedo() ? canEdit : MF_GRAYED), 2, L"Redo\tCtrl+Y");
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(m, MF_STRING | canEdit, 3, L"Cut\tCtrl+X");
    AppendMenuW(m, MF_STRING, 4, L"Copy\tCtrl+C");
    AppendMenuW(m, MF_STRING | canEdit, 5, L"Paste\tCtrl+V");
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(m, MF_STRING, 6, L"Select All\tCtrl+A");
    int cmd = TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, 0, hwnd, nullptr);
    DestroyMenu(m);
    switch (cmd) {
    case 1: Undo(); break;
    case 2: Redo(); break;
    case 3: Cut(); break;
    case 4: Copy(); break;
    case 5: Paste(); break;
    case 6: SelectAll(); break;
    }
}

// ---------------- message handler ----------------

LRESULT EditorView::Handle(UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_SIZE: {
        clientW_ = LOWORD(l);
        clientH_ = HIWORD(l);
        if (wrap_) {
            int cols = std::max(10, VisibleCols() - 1);
            if (cols != wrapCols_) { wrapCols_ = cols; RecomputeWrapAll(); }
        }
        UpdateScrollbars();
        InvalidateRect(hwnd, nullptr, FALSE);
        UpdateCaretPos();
        return 0;
    }
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(hwnd, &ps);
        Paint(dc);
        EndPaint(hwnd, &ps);
        return 0;
    }
    case WM_ERASEBKGND:
        return 1;
    case WM_SETFOCUS:
        CreateCaret(hwnd, nullptr, std::max(2, S(2)), g_ef.lh);
        ShowCaret(hwnd);
        UpdateCaretPos();
        Notify();
        return 0;
    case WM_KILLFOCUS:
        DestroyCaret();
        return 0;
    case WM_GETDLGCODE:
        return DLGC_WANTALLKEYS | DLGC_WANTCHARS | DLGC_WANTTAB | DLGC_WANTARROWS;
    case WM_TIMER:
        if (w == kTimerIndex) {
            if (buf->Ready()) { KillTimer(hwnd, kTimerIndex); return 0; }
            UpdateGutter();
            UpdateScrollbars();
            InvalidateRect(hwnd, nullptr, FALSE);
            Notify();
        } else if (w == kTimerDrag && dragging_) {
            POINT pt;
            GetCursorPos(&pt);
            ScreenToClient(hwnd, &pt);
            if (pt.y < 0) ScrollToRow((int64_t)firstRow_ - 1 - (-pt.y) / g_ef.lh);
            else if (pt.y > clientH_) ScrollToRow((int64_t)firstRow_ + 1 + (pt.y - clientH_) / g_ef.lh);
            if (!wrap_ && pt.x > clientW_) { scrollCol_ += 2; UpdateScrollbars(); InvalidateRect(hwnd, nullptr, FALSE); }
            else if (!wrap_ && pt.x < gutterW_ && scrollCol_ > 0 && !dragLines_) { scrollCol_ = scrollCol_ > 2 ? scrollCol_ - 2 : 0; UpdateScrollbars(); InvalidateRect(hwnd, nullptr, FALSE); }
            SendMessageW(hwnd, WM_MOUSEMOVE, MK_LBUTTON, MAKELPARAM((short)pt.x, (short)pt.y));
        }
        return 0;
    case WM_APP_INDEXED:
        buf->OnIndexed();
        KillTimer(hwnd, kTimerIndex);
        SetWrap(wrapWanted_);
        UpdateGutter();
        UpdateScrollbars();
        InvalidateRect(hwnd, nullptr, FALSE);
        Notify();
        return 0;
    case WM_APP_FINDDONE:
        OnFindDone((uint64_t)w, findLen_);
        return 0;
    case WM_VSCROLL: {
        SCROLLINFO si = {sizeof(si), SIF_ALL};
        GetScrollInfo(hwnd, SB_VERT, &si);
        int64_t row = (int64_t)firstRow_, page = PageRows();
        switch (LOWORD(w)) {
        case SB_LINEUP: row -= 1; break;
        case SB_LINEDOWN: row += 1; break;
        case SB_PAGEUP: row -= page; break;
        case SB_PAGEDOWN: row += page; break;
        case SB_TOP: row = 0; break;
        case SB_BOTTOM: row = (int64_t)RowCount(); break;
        case SB_THUMBTRACK:
        case SB_THUMBPOSITION: row = (int64_t)si.nTrackPos * sbScale_; break;
        }
        ScrollToRow(row);
        return 0;
    }
    case WM_HSCROLL: {
        SCROLLINFO si = {sizeof(si), SIF_ALL};
        GetScrollInfo(hwnd, SB_HORZ, &si);
        int64_t c = (int64_t)scrollCol_, page = VisibleCols();
        switch (LOWORD(w)) {
        case SB_LINELEFT: c -= 4; break;
        case SB_LINERIGHT: c += 4; break;
        case SB_PAGELEFT: c -= page; break;
        case SB_PAGERIGHT: c += page; break;
        case SB_LEFT: c = 0; break;
        case SB_THUMBTRACK:
        case SB_THUMBPOSITION: c = si.nTrackPos; break;
        }
        if (c < 0) c = 0;
        scrollCol_ = (uint64_t)c;
        UpdateScrollbars();
        InvalidateRect(hwnd, nullptr, FALSE);
        UpdateCaretPos();
        return 0;
    }
    case WM_MOUSEWHEEL:
    case WM_MOUSEHWHEEL: {
        int delta = GET_WHEEL_DELTA_WPARAM(w);
        if (m == WM_MOUSEWHEEL && (GET_KEYSTATE_WPARAM(w) & MK_CONTROL)) {
            SendMessageW(GetParent(hwnd), WM_COMMAND, delta > 0 ? ID_VIEW_ZOOMIN : ID_VIEW_ZOOMOUT, 0);
            return 0;
        }
        bool horiz = m == WM_MOUSEHWHEEL || (GET_KEYSTATE_WPARAM(w) & MK_SHIFT);
        wheelAccum_ += (m == WM_MOUSEHWHEEL) ? -delta : delta;
        UINT lines = 3;
        SystemParametersInfoW(SPI_GETWHEELSCROLLLINES, 0, &lines, 0);
        if (lines == WHEEL_PAGESCROLL) lines = PageRows();
        int notches = wheelAccum_ / WHEEL_DELTA;
        if (!notches) return 0;
        wheelAccum_ -= notches * WHEEL_DELTA;
        if (horiz) {
            if (!wrap_) {
                int64_t c = (int64_t)scrollCol_ - notches * 8;
                scrollCol_ = c < 0 ? 0 : (uint64_t)c;
                UpdateScrollbars();
                InvalidateRect(hwnd, nullptr, FALSE);
                UpdateCaretPos();
            }
        } else {
            ScrollToRow((int64_t)firstRow_ - (int64_t)notches * lines);
        }
        return 0;
    }
    case WM_LBUTTONDOWN: {
        SetFocus(hwnd);
        SetCapture(hwnd);
        int x = GET_X_LPARAM(l), y = GET_Y_LPARAM(l);
        uint64_t off = HitTest(x, y);
        dragLines_ = false;
        if (x < gutterW_ && !(w & MK_SHIFT)) {
            uint64_t line = buf->LineOf(off);
            dragOrigin_ = line;
            dragLines_ = true;
            anchor_ = buf->LineStart(line);
            caret_ = buf->LineNext(line);
        } else if (w & MK_SHIFT) {
            caret_ = off;
        } else {
            caret_ = anchor_ = off;
        }
        dragging_ = true;
        SetTimer(hwnd, kTimerDrag, 40, nullptr);
        buf->BreakUndoMerge();
        desiredCol_ = -1;
        InvalidateRect(hwnd, nullptr, FALSE);
        UpdateCaretPos();
        Notify();
        return 0;
    }
    case WM_LBUTTONDBLCLK: {
        int x = GET_X_LPARAM(l), y = GET_Y_LPARAM(l);
        if (x >= gutterW_) {
            SelectWordAt(HitTest(x, y));
            InvalidateRect(hwnd, nullptr, FALSE);
            UpdateCaretPos();
            Notify();
        }
        return 0;
    }
    case WM_MOUSEMOVE:
        if (dragging_) {
            uint64_t off = HitTest(GET_X_LPARAM(l), GET_Y_LPARAM(l));
            if (dragLines_) {
                uint64_t line = buf->LineOf(off);
                if (line >= dragOrigin_) { anchor_ = buf->LineStart(dragOrigin_); caret_ = buf->LineNext(line); }
                else { anchor_ = buf->LineNext(dragOrigin_); caret_ = buf->LineStart(line); }
            } else {
                caret_ = off;
            }
            InvalidateRect(hwnd, nullptr, FALSE);
            UpdateCaretPos();
            Notify();
        }
        return 0;
    case WM_LBUTTONUP:
        if (dragging_) {
            dragging_ = false;
            KillTimer(hwnd, kTimerDrag);
            ReleaseCapture();
        }
        return 0;
    case WM_CAPTURECHANGED:
        dragging_ = false;
        KillTimer(hwnd, kTimerDrag);
        return 0;
    case WM_CONTEXTMENU: {
        int x = GET_X_LPARAM(l), y = GET_Y_LPARAM(l);
        if (x == -1 && y == -1) {
            POINT pt;
            GetCaretPos(&pt);
            ClientToScreen(hwnd, &pt);
            x = pt.x; y = pt.y;
        }
        ContextMenu(x, y);
        return 0;
    }
    case WM_SETCURSOR:
        if (LOWORD(l) == HTCLIENT) {
            POINT pt;
            GetCursorPos(&pt);
            ScreenToClient(hwnd, &pt);
            SetCursor(LoadCursor(nullptr, pt.x < gutterW_ ? IDC_ARROW : IDC_IBEAM));
            return TRUE;
        }
        break;
    case WM_KEYDOWN:
        OnKeyDown(w);
        return 0;
    case WM_CHAR:
        OnChar((wchar_t)w);
        return 0;
    }
    return DefWindowProcW(hwnd, m, w, l);
}
