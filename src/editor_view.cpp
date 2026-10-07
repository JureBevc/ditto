// EditorView: window plumbing, row/column mapping, word wrap, painting and scrolling.
#include "editor_view.h"
#include <imm.h>

EditorFonts g_ef;
static const uint64_t kLongLine = 64 * 1024;     // lines longer than this use column checkpoints
static const uint64_t kWrapMaxLine = 256 * 1024;  // longer lines are never wrapped
static const uint64_t kWrapMaxDoc = 64ull << 20;  // word wrap only for documents up to this size
static const UINT_PTR kTimerIndex = 1, kTimerDrag = 2;

void CreateEditorFonts() {
    EditorFonts old = g_ef;
    LOGFONTW lf = {};
    lf.lfHeight = -MulDiv(g_settings.fontSize, g_dpi, 72);
    lf.lfWeight = FW_NORMAL;
    lf.lfCharSet = DEFAULT_CHARSET;
    lf.lfQuality = CLEARTYPE_QUALITY;
    lf.lfPitchAndFamily = FIXED_PITCH | FF_MODERN;
    wcsncpy_s(lf.lfFaceName, g_settings.fontName.c_str(), _TRUNCATE);
    g_ef.normal = CreateFontIndirectW(&lf);
    lf.lfWeight = FW_BOLD;
    g_ef.bold = CreateFontIndirectW(&lf);
    lf.lfWeight = FW_NORMAL;
    lf.lfItalic = TRUE;
    g_ef.italic = CreateFontIndirectW(&lf);
    HDC dc = GetDC(nullptr);
    HGDIOBJ o = SelectObject(dc, g_ef.normal);
    TEXTMETRICW tm;
    GetTextMetricsW(dc, &tm);
    SIZE sz;
    GetTextExtentPoint32W(dc, L"MMMMMMMMMM", 10, &sz);
    SelectObject(dc, o);
    ReleaseDC(nullptr, dc);
    g_ef.cw = std::max(1, (int)((sz.cx + 5) / 10));
    g_ef.lh = tm.tmHeight + tm.tmExternalLeading + MulDiv(3, g_dpi, 96);
    g_ef.ascent = tm.tmAscent;
    if (old.normal) { DeleteObject(old.normal); DeleteObject(old.bold); DeleteObject(old.italic); }
}

static void WrapLine(const char* p, size_t n, int wrapCols, int tab, std::vector<uint32_t>& segs) {
    segs.clear();
    segs.push_back(0);
    uint64_t col = 0, segCol = 0, brkCol = 0;
    size_t segStart = 0, brk = 0, i = 0;
    while (i < n) {
        unsigned char c = (unsigned char)p[i];
        int len = 1, w;
        if (c < 0x80) w = c == '\t' ? tab - (int)(col % tab) : 1;
        else { uint32_t cp; len = Utf8Decode((const unsigned char*)p + i, n - i, &cp); w = CharColumns(cp); }
        if (col + w - segCol > (uint64_t)wrapCols && i > segStart) {
            if (brk > segStart) { segStart = brk; segCol = brkCol; }
            else { segStart = i; segCol = col; }
            segs.push_back((uint32_t)segStart);
            brk = 0;
            continue;
        }
        col += w;
        i += len;
        if (c == ' ' || c == '\t') { brk = i; brkCol = col; }
    }
}

void EditorView::Register() {
    WNDCLASSEXW wc = {sizeof(wc)};
    wc.style = CS_DBLCLKS;
    wc.lpfnWndProc = WndProc;
    wc.hInstance = g_hinst;
    wc.hCursor = LoadCursor(nullptr, IDC_IBEAM);
    wc.lpszClassName = L"MyEdView";
    RegisterClassExW(&wc);
}

EditorView* EditorView::From(HWND h) { return (EditorView*)GetWindowLongPtrW(h, GWLP_USERDATA); }

EditorView* EditorView::Create(HWND parent, const std::wstring& path, std::wstring& err) {
    EditorView* ev = new EditorView();
    ev->buf = std::make_unique<TextBuffer>();
    HWND h = CreateWindowExW(0, L"MyEdView", L"", WS_CHILD | WS_VSCROLL | WS_HSCROLL | WS_CLIPSIBLINGS, 0, 0, 0, 0,
                             parent, nullptr, g_hinst, ev);
    if (!h) { delete ev; err = L"Could not create editor window"; return nullptr; }
    if (!path.empty()) {
        if (!ev->buf->Load(path, h, err)) { DestroyWindow(h); return nullptr; }
        if (!ev->buf->Ready()) SetTimer(h, kTimerIndex, 150, nullptr);
    }
    ev->SetPath(path);
    ev->wrapWanted_ = g_settings.wrap;
    ev->SetWrap(g_settings.wrap);
    ev->UpdateGutter();
    ev->UpdateScrollbars();
    return ev;
}

EditorView::~EditorView() {
    findCancel_ = true;
    if (findThread_.joinable()) findThread_.join();
    if (backBmp_) DeleteObject(backBmp_);
}

std::wstring EditorView::Title() const {
    return buf->path.empty() ? untitledName : FileNameOf(buf->path);
}

void EditorView::SetPath(const std::wstring& path) {
    buf->path = path;
    std::wstring ext = LowerW(path.substr(std::min(path.size(), path.find_last_of(L'.') == std::wstring::npos ? path.size() : path.find_last_of(L'.'))));
    bool md = ext == L".md" || ext == L".markdown" || ext == L".mdown" || ext == L".mkd" || ext == L".mdx";
    if (md != markdown) {
        markdown = md;
        md_ = MdStateCache();
        if (hwnd) InvalidateRect(hwnd, nullptr, FALSE);
    }
}

bool EditorView::Save(const std::wstring& path, std::wstring& err) {
    if (!buf->Ready()) { err = L"The file is still being loaded."; return false; }
    if (saving_) { err = L"A save is already in progress."; return false; }
    bool ok;
    if (buf->Length() < (32ull << 20)) {
        HCURSOR old = SetCursor(LoadCursor(nullptr, IDC_WAIT));
        ok = buf->Save(path, err);
        SetCursor(old);
    } else {
        // Large file: write on a worker thread while keeping the UI painted (input disabled).
        saving_ = true;
        std::atomic<uint64_t> written{0};
        std::atomic<bool> done{false};
        std::wstring werr;
        TextBuffer* b = buf.get();
        std::thread t([&]() {
            ok = b->Save(path, werr, &written);
            done = true;
        });
        HWND top = GetAncestor(hwnd, GA_ROOT);
        EnableWindow(top, FALSE);
        bool quit = false;
        int quitCode = 0;
        while (!done) {
            MsgWaitForMultipleObjects(0, nullptr, FALSE, 100, QS_ALLINPUT);
            MSG m;
            while (PeekMessageW(&m, nullptr, 0, 0, PM_REMOVE)) {
                if (m.message == WM_QUIT) { quit = true; quitCode = (int)m.wParam; continue; }
                TranslateMessage(&m);
                DispatchMessageW(&m);
            }
            saveProgress_ = (double)written.load() / (double)std::max<uint64_t>(1, b->Length());
            Notify();
        }
        t.join();
        EnableWindow(top, TRUE);
        SetForegroundWindow(top);
        SetFocus(hwnd);
        saving_ = false;
        saveProgress_ = -1;
        err = werr;
        if (quit) PostQuitMessage(quitCode);
    }
    if (ok) SetPath(path);
    Notify();
    return ok;
}

bool EditorView::Reload(std::wstring& err) {
    if (buf->path.empty()) return false;
    findCancel_ = true;
    if (findThread_.joinable()) findThread_.join();
    searching_ = false;
    auto nb = std::make_unique<TextBuffer>();
    if (!nb->Load(buf->path, hwnd, err)) return false;
    uint64_t line = CaretLine();
    buf = std::move(nb);
    md_ = MdStateCache();
    colCache_.clear();
    if (!buf->Ready()) SetTimer(hwnd, kTimerIndex, 150, nullptr);
    SetWrap(wrapWanted_);
    uint64_t lc = buf->LineCount();
    caret_ = anchor_ = buf->LineStart(std::min(line, lc - 1));
    if (firstRow_ >= RowCount()) firstRow_ = RowCount() ? RowCount() - 1 : 0;
    UpdateGutter();
    UpdateScrollbars();
    InvalidateRect(hwnd, nullptr, FALSE);
    UpdateCaretPos();
    Notify();
    return true;
}

void EditorView::Notify() { SendMessageW(GetParent(hwnd), WM_APP_DOCCHANGED, (WPARAM)hwnd, 0); }

void EditorView::FontChanged() {
    UpdateGutter();
    if (wrap_) RecomputeWrapAll();
    if (GetFocus() == hwnd) {
        DestroyCaret();
        CreateCaret(hwnd, nullptr, std::max(2, S(2)), g_ef.lh);
        ShowCaret(hwnd);
    }
    UpdateScrollbars();
    EnsureCaretVisible();
    InvalidateRect(hwnd, nullptr, FALSE);
}

void EditorView::UpdateGutter() {
    uint64_t lc = buf->LineCount();
    int digits = 1;
    while (lc >= 10) { lc /= 10; ++digits; }
    digits = std::max(digits, 3);
    gutterW_ = (digits + 2) * g_ef.cw;
}

// ---------------- rows ----------------

uint64_t EditorView::RowCount() const { return wrap_ ? rowPrefix_.back() : buf->LineCount(); }

void EditorView::RowToLine(uint64_t row, uint64_t* line, uint64_t* sub) const {
    if (!wrap_) { *line = row; *sub = 0; return; }
    size_t l = (size_t)(std::upper_bound(rowPrefix_.begin(), rowPrefix_.end(), row) - rowPrefix_.begin()) - 1;
    if (l >= rowsPerLine_.size()) l = rowsPerLine_.size() - 1;
    *line = l;
    *sub = row - rowPrefix_[l];
}

uint64_t EditorView::LineFirstRow(uint64_t line) const {
    if (!wrap_) return line;
    return rowPrefix_[(size_t)std::min<uint64_t>(line, rowsPerLine_.size() - 1)];
}

void EditorView::Segments(uint64_t /*line*/, uint64_t s, uint64_t e, std::vector<uint64_t>& segs) {
    segs.clear();
    if (!wrap_ || e - s > kWrapMaxLine) { segs.push_back(s); return; }
    std::string text;
    buf->Read(s, e - s, text);
    std::vector<uint32_t> rel;
    WrapLine(text.data(), text.size(), wrapCols_, g_settings.tabSize, rel);
    for (uint32_t r : rel) segs.push_back(s + r);
}

void EditorView::ForEachLine(uint64_t from, uint64_t to, const std::function<void(uint64_t, const char*, size_t, bool)>& fn) {
    uint64_t off = buf->LineStart(from), total = buf->Length(), line = from;
    std::string chunk, carry;
    bool carryTrunc = false;
    while (line < to && off < total) {
        buf->Read(off, 1 << 20, chunk);
        size_t p = 0;
        while (line < to) {
            const char* nl = (const char*)memchr(chunk.data() + p, '\n', chunk.size() - p);
            if (!nl) {
                size_t room = carry.size() < kWrapMaxLine + 1 ? kWrapMaxLine + 1 - carry.size() : 0;
                size_t take = std::min(room, chunk.size() - p);
                carry.append(chunk.data() + p, take);
                if (take < chunk.size() - p) carryTrunc = true;
                break;
            }
            size_t e = nl - chunk.data();
            const char* lp;
            size_t ln;
            bool trunc = false;
            if (!carry.empty() || carryTrunc) {
                size_t room = carry.size() < kWrapMaxLine + 1 ? kWrapMaxLine + 1 - carry.size() : 0;
                carry.append(chunk.data() + p, std::min(room, e - p));
                trunc = carryTrunc || room < e - p;
                lp = carry.data(); ln = carry.size();
            } else {
                lp = chunk.data() + p; ln = e - p;
            }
            if (ln && lp[ln - 1] == '\r') --ln;
            fn(line, lp, ln, trunc || ln > kWrapMaxLine);
            carry.clear();
            carryTrunc = false;
            p = e + 1;
            ++line;
        }
        off += chunk.size();
    }
    if (line < to) {  // last line without trailing newline (possibly empty)
        size_t ln = carry.size();
        if (ln && carry[ln - 1] == '\r') --ln;
        fn(line, carry.data(), ln, carryTrunc || ln > kWrapMaxLine);
    }
}

void EditorView::RebuildRowPrefix(uint64_t fromLine) {
    size_t n = rowsPerLine_.size();
    rowPrefix_.resize(n + 1);
    rowPrefix_[0] = 0;
    for (size_t i = (size_t)std::min<uint64_t>(fromLine, n); i < n; ++i) rowPrefix_[i + 1] = rowPrefix_[i] + rowsPerLine_[i];
}

void EditorView::RecomputeWrapAll() {
    uint64_t lc = buf->LineCount();
    rowsPerLine_.assign((size_t)lc, 1);
    std::vector<uint32_t> segs;
    int tab = g_settings.tabSize;
    ForEachLine(0, lc, [&](uint64_t l, const char* p, size_t n, bool trunc) {
        if (trunc) return;
        WrapLine(p, n, wrapCols_, tab, segs);
        rowsPerLine_[(size_t)l] = (uint32_t)segs.size();
    });
    RebuildRowPrefix(0);
}

void EditorView::UpdateWrapAfterEdit(const EditInfo& info) {
    if (info.whole || rowsPerLine_.size() != buf->LineCount() - info.linesIns + info.linesDel) {
        RecomputeWrapAll();
        return;
    }
    size_t l0 = (size_t)info.line;
    rowsPerLine_.erase(rowsPerLine_.begin() + l0, rowsPerLine_.begin() + std::min(rowsPerLine_.size(), l0 + (size_t)info.linesDel + 1));
    rowsPerLine_.insert(rowsPerLine_.begin() + l0, (size_t)info.linesIns + 1, 1);
    std::vector<uint32_t> segs;
    int tab = g_settings.tabSize;
    ForEachLine(l0, l0 + info.linesIns + 1, [&](uint64_t l, const char* p, size_t n, bool trunc) {
        if (trunc) return;
        WrapLine(p, n, wrapCols_, tab, segs);
        rowsPerLine_[(size_t)l] = (uint32_t)segs.size();
    });
    RebuildRowPrefix(l0);
}

void EditorView::SetWrap(bool on) {
    wrapWanted_ = on;
    bool can = buf->Ready() && buf->Length() <= kWrapMaxDoc;
    wrap_ = on && can;
    if (wrap_) {
        wrapCols_ = std::max(10, VisibleCols() - 1);
        scrollCol_ = 0;
        RecomputeWrapAll();
    } else {
        rowsPerLine_.clear();
        rowPrefix_.assign(1, 0);
    }
    if (hwnd) {
        UpdateScrollbars();
        EnsureCaretVisible();
        InvalidateRect(hwnd, nullptr, FALSE);
    }
}

// ---------------- columns ----------------

uint64_t EditorView::ScanCol(uint64_t lineEnd, uint64_t off, uint64_t col, uint64_t stopOff, uint64_t stopCol, uint64_t* outOff) {
    stopOff = std::min(stopOff, lineEnd);
    std::string& w = scratch_;
    w.clear();
    uint64_t wOff = off;
    size_t wPos = 0;
    int tab = g_settings.tabSize;
    while (off < stopOff) {
        if (wPos + 4 > w.size() && wOff + w.size() < lineEnd) {
            wOff = off;
            buf->Read(off, std::min<uint64_t>(lineEnd - off, 1 << 16), w);
            wPos = 0;
            if (w.empty()) break;
        }
        unsigned char c = (unsigned char)w[wPos];
        int len = 1, cw;
        if (c < 0x80) cw = c == '\t' ? tab - (int)(col % tab) : 1;
        else { uint32_t cp; len = Utf8Decode((const unsigned char*)w.data() + wPos, w.size() - wPos, &cp); cw = CharColumns(cp); }
        if (col + cw > stopCol) break;
        col += cw;
        off += len;
        wPos += len;
    }
    *outOff = std::min(off, lineEnd);
    return col;
}

void EditorView::ColStart(uint64_t s, uint64_t e, uint64_t targetOff, uint64_t targetCol, uint64_t* off, uint64_t* col) {
    *off = s;
    *col = 0;
    if (e - s < kLongLine) return;
    ColCp* c = nullptr;
    for (auto& x : colCache_)
        if (x.lineStart == s && x.version == buf->Version()) { c = &x; break; }
    if (!c) {
        if (colCache_.size() < 16) {
            colCache_.emplace_back();
            c = &colCache_.back();
        } else {
            colCacheNext_ = (colCacheNext_ + 1) % colCache_.size();
            c = &colCache_[colCacheNext_];
        }
        c->lineStart = s;
        c->version = buf->Version();
        c->cps.clear();
        uint64_t o = s, cl = 0;
        c->cps.push_back({o, cl});
        while (o < e) {
            uint64_t no;
            cl = ScanCol(e, o, cl, o + 16384, UINT64_MAX, &no);
            if (no == o) break;
            o = no;
            c->cps.push_back({o, cl});
        }
    }
    // last checkpoint with off <= targetOff and col <= targetCol
    size_t lo = 0, hi = c->cps.size();
    while (hi - lo > 1) {
        size_t mid = (lo + hi) / 2;
        if (c->cps[mid].first <= targetOff && c->cps[mid].second <= targetCol) lo = mid;
        else hi = mid;
    }
    *off = c->cps[lo].first;
    *col = c->cps[lo].second;
}

uint64_t EditorView::ColAt(uint64_t s, uint64_t e, uint64_t off) {
    uint64_t o, c, dummy;
    off = std::min(off, e);
    ColStart(s, e, off, UINT64_MAX, &o, &c);
    return ScanCol(e, o, c, off, UINT64_MAX, &dummy);
}

uint64_t EditorView::OffAt(uint64_t s, uint64_t e, uint64_t col) {
    uint64_t o, c, out;
    ColStart(s, e, UINT64_MAX, col, &o, &c);
    ScanCol(e, o, c, UINT64_MAX, col, &out);
    return out;
}

void EditorView::OffToRowCol(uint64_t off, uint64_t* row, uint64_t* col) {
    uint64_t line = buf->LineOf(off);
    uint64_t s = buf->LineStart(line), e = buf->LineEnd(line);
    off = std::min(off, e);
    if (!wrap_) { *row = line; *col = ColAt(s, e, off); return; }
    std::vector<uint64_t> segs;
    Segments(line, s, e, segs);
    size_t k = (size_t)(std::upper_bound(segs.begin(), segs.end(), off) - segs.begin()) - 1;
    *row = LineFirstRow(line) + k;
    *col = ColAt(s, e, off) - (k ? ColAt(s, e, segs[k]) : 0);
}

uint64_t EditorView::RowColToOff(uint64_t row, uint64_t col) {
    uint64_t rc = RowCount();
    if (row >= rc) row = rc ? rc - 1 : 0;
    uint64_t line, sub;
    RowToLine(row, &line, &sub);
    uint64_t s = buf->LineStart(line), e = buf->LineEnd(line);
    if (!wrap_) return OffAt(s, e, col);
    std::vector<uint64_t> segs;
    Segments(line, s, e, segs);
    if (sub >= segs.size()) sub = segs.size() - 1;
    uint64_t ss = segs[(size_t)sub];
    uint64_t se = sub + 1 < segs.size() ? segs[(size_t)sub + 1] : e;
    uint64_t base = sub ? ColAt(s, e, ss) : 0;
    uint64_t o = OffAt(s, e, base + col);
    if (sub + 1 < segs.size() && o >= se) o = PrevChar(se);
    return std::max(o, ss);
}

uint64_t EditorView::HitTest(int x, int y) {
    int64_t r = y < 0 ? -1 : y / g_ef.lh;
    int64_t row = (int64_t)firstRow_ + r;
    if (row < 0) row = 0;
    int64_t cx = x - TextLeft() + g_ef.cw / 2;
    uint64_t col = scrollCol_ + (cx > 0 ? cx / g_ef.cw : 0);
    if (wrap_ && cx <= 0) col = 0;
    return RowColToOff((uint64_t)row, col);
}

uint64_t EditorView::CaretLine() const { return buf->LineOf(caret_); }

uint64_t EditorView::CaretCol() const {
    EditorView* self = const_cast<EditorView*>(this);
    uint64_t line = buf->LineOf(caret_);
    return self->ColAt(buf->LineStart(line), buf->LineEnd(line), caret_);
}

// ---------------- scrolling / caret ----------------

void EditorView::UpdateScrollbars() {
    if (!hwnd) return;
    uint64_t rows = RowCount();
    int page = PageRows();
    uint64_t maxRow = rows ? rows - 1 + page - 1 : 0;
    sbScale_ = 1;
    while (maxRow / sbScale_ > 0x3FFFFFFF) sbScale_ *= 2;
    SCROLLINFO si = {sizeof(si), SIF_RANGE | SIF_PAGE | SIF_POS};
    si.nMin = 0;
    si.nMax = (int)(maxRow / sbScale_);
    si.nPage = (UINT)std::max<int64_t>(1, page / sbScale_);
    si.nPos = (int)(firstRow_ / sbScale_);
    SetScrollInfo(hwnd, SB_VERT, &si, TRUE);
    if (wrap_) {
        ShowScrollBar(hwnd, SB_HORZ, FALSE);
    } else {
        uint64_t cols = std::max(maxCols_, buf->MaxLineLen()) + 8;
        si.nMax = (int)std::min<uint64_t>(cols, 0x3FFFFFFF);
        si.nPage = (UINT)VisibleCols();
        si.nPos = (int)std::min<uint64_t>(scrollCol_, 0x3FFFFFFF);
        SetScrollInfo(hwnd, SB_HORZ, &si, TRUE);
    }
}

void EditorView::ScrollToRow(int64_t row) {
    int64_t rows = (int64_t)RowCount();
    if (row > rows - 1) row = rows - 1;
    if (row < 0) row = 0;
    if ((uint64_t)row == firstRow_) return;
    firstRow_ = (uint64_t)row;
    UpdateScrollbars();
    InvalidateRect(hwnd, nullptr, FALSE);
    UpdateCaretPos();
}

void EditorView::UpdateCaretPos() {
    if (GetFocus() != hwnd) return;
    uint64_t row, col;
    OffToRowCol(caret_, &row, &col);
    int x = -1000, y = -1000;
    if (row >= firstRow_ && row <= firstRow_ + PageRows() && col >= scrollCol_) {
        x = TextLeft() + (int)(col - scrollCol_) * g_ef.cw;
        y = (int)(row - firstRow_) * g_ef.lh;
        if (x > clientW_) x = -1000;
    }
    SetCaretPos(x, y);
    HIMC imc = ImmGetContext(hwnd);
    if (imc) {
        COMPOSITIONFORM cf = {CFS_POINT, {std::max(x, 0), std::max(y, 0)}};
        ImmSetCompositionWindow(imc, &cf);
        LOGFONTW lf;
        GetObjectW(g_ef.normal, sizeof(lf), &lf);
        ImmSetCompositionFontW(imc, &lf);
        ImmReleaseContext(hwnd, imc);
    }
}

void EditorView::EnsureCaretVisible(bool center) {
    if (!hwnd || clientW_ <= 0 || clientH_ <= 0) return;  // no geometry yet
    uint64_t row, col;
    OffToRowCol(caret_, &row, &col);
    uint64_t page = (uint64_t)PageRows();
    uint64_t old = firstRow_, oldCol = scrollCol_;
    if (row < firstRow_ || row >= firstRow_ + page) {
        if (center) firstRow_ = row > page / 2 ? row - page / 2 : 0;
        else if (row < firstRow_) firstRow_ = row;
        else firstRow_ = row - page + 1;
    }
    if (!wrap_) {
        uint64_t vis = (uint64_t)VisibleCols();
        if (col < scrollCol_) scrollCol_ = col > 8 ? col - 8 : 0;
        else if (col + 2 > scrollCol_ + vis) scrollCol_ = col + 8 > vis ? col + 8 - vis : 0;
    }
    if (old != firstRow_ || oldCol != scrollCol_) {
        UpdateScrollbars();
        InvalidateRect(hwnd, nullptr, FALSE);
    }
    UpdateCaretPos();
}

// ---------------- painting ----------------

static COLORREF StyleColor(uint8_t st) {
    const Theme& t = g_theme;
    switch (st) {
    case MS_HEADING: return t.heading;
    case MS_BOLD: return t.bold;
    case MS_ITALIC: return t.italic;
    case MS_CODE: return t.code;
    case MS_LINK: return t.link;
    case MS_URL: return t.url;
    case MS_QUOTE: return t.quote;
    case MS_LIST: return t.listMarker;
    case MS_RULE: return t.rule;
    case MS_FENCE: return t.fence;
    case MS_HTML: return t.html;
    default: return t.fg;
    }
}
static HFONT StyleFont(uint8_t st) {
    if (st == MS_HEADING || st == MS_BOLD) return g_ef.bold;
    if (st == MS_ITALIC) return g_ef.italic;
    return g_ef.normal;
}

static void Fill(HDC dc, int l, int t, int r, int b, COLORREF c) {
    RECT rc = {l, t, r, b};
    SetBkColor(dc, c);
    ExtTextOutW(dc, 0, 0, ETO_OPAQUE, &rc, nullptr, 0, nullptr);
}

void EditorView::Paint(HDC hdc) {
    if (!backBmp_ || backW_ < clientW_ || backH_ < clientH_) {
        if (backBmp_) DeleteObject(backBmp_);
        backW_ = std::max(clientW_, 16);
        backH_ = std::max(clientH_, 16);
        backBmp_ = CreateCompatibleBitmap(hdc, backW_, backH_);
    }
    HDC dc = CreateCompatibleDC(hdc);
    HGDIOBJ oldBmp = SelectObject(dc, backBmp_);
    const Theme& t = g_theme;
    const int cw = g_ef.cw, lh = g_ef.lh, tab = g_settings.tabSize;
    Fill(dc, 0, 0, clientW_, clientH_, t.bg);
    Fill(dc, 0, 0, gutterW_, clientH_, t.gutterBg);
    SetBkMode(dc, TRANSPARENT);
    HGDIOBJ oldFont = SelectObject(dc, g_ef.normal);

    uint64_t selA = std::min(caret_, anchor_), selB = std::max(caret_, anchor_);
    uint64_t caretRow, caretCol;
    OffToRowCol(caret_, &caretRow, &caretCol);
    uint64_t caretLine = buf->LineOf(caret_);
    uint64_t rc = RowCount();
    int rows = PageRows() + 1;
    int textLeft = TextLeft();
    uint64_t visCols = (uint64_t)VisibleCols() + 2;

    uint64_t curLine = UINT64_MAX, s = 0, e = 0;
    std::vector<uint64_t> segs;
    std::string lineText, chunk;
    std::vector<MdSpan> spans;
    uint16_t mdState = 0, nextState = 0;
    std::wstring wt;
    std::vector<int> dx, xs;
    std::vector<uint8_t> st;
    std::vector<uint64_t> uo;

    for (int r = 0; r < rows; ++r) {
        uint64_t row = firstRow_ + r;
        if (row >= rc) break;
        uint64_t line, sub;
        RowToLine(row, &line, &sub);
        if (line != curLine) {
            bool seq = curLine != UINT64_MAX && line == curLine + 1;
            curLine = line;
            s = buf->LineStart(line);
            e = buf->LineEnd(line);
            Segments(line, s, e, segs);
            spans.clear();
            if (markdown) {
                mdState = seq ? nextState : md_.StateAt(*buf, line);
                if (e - s <= kLongLine) {
                    buf->Read(s, e - s, lineText);
                    MdHighlightLine(lineText.data(), lineText.size(), mdState, spans);
                    nextState = MdNextState(lineText.data(), std::min<size_t>(lineText.size(), 512), mdState);
                } else {
                    nextState = mdState;
                }
            }
        }
        size_t k = (size_t)std::min<uint64_t>(sub, segs.size() - 1);
        uint64_t ss = segs[k];
        uint64_t se = k + 1 < segs.size() ? segs[k + 1] : e;
        bool lastSeg = k + 1 >= segs.size();
        int y = r * lh;
        if (row == caretRow && selA == selB) Fill(dc, gutterW_, y, clientW_, y + lh, t.curLine);
        if (k == 0) {
            wchar_t num[24];
            int n = swprintf_s(num, L"%llu", (unsigned long long)line + 1);
            SetTextColor(dc, line == caretLine ? t.gutterFgCur : t.gutterFg);
            SelectObject(dc, g_ef.normal);
            RECT nr = {0, y, gutterW_ - cw, y + lh};
            DrawTextW(dc, num, n, &nr, DT_RIGHT | DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX);
        }
        // locate first visible char
        uint64_t base = k ? ColAt(s, e, ss) : 0;
        uint64_t startCol = base + scrollCol_;
        uint64_t o0 = ss, c0 = base;
        if (scrollCol_) {
            uint64_t co, cc;
            ColStart(s, e, UINT64_MAX, startCol, &co, &cc);
            if (co < ss) { co = ss; cc = base; }
            c0 = ScanCol(se, co, cc, se, startCol, &o0);
        }
        uint64_t maxBytes = std::min<uint64_t>(se - o0, visCols * 4 + 16);
        buf->Read(o0, maxBytes, chunk);
        wt.clear(); dx.clear(); xs.clear(); st.clear(); uo.clear();
        uint64_t col = c0, endCol = startCol + visCols;
        size_t i = 0, sp = 0;
        while (i < chunk.size() && col < endCol) {
            unsigned char ch = (unsigned char)chunk[i];
            uint32_t cp;
            int len = 1, w;
            if (ch < 0x80) { cp = ch; w = ch == '\t' ? tab - (int)(col % tab) : 1; }
            else { len = Utf8Decode((const unsigned char*)chunk.data() + i, chunk.size() - i, &cp); w = CharColumns(cp); }
            uint8_t style = MS_NORMAL;
            if (!spans.empty()) {
                uint64_t rel = o0 + i - s;
                while (sp < spans.size() && spans[sp].end <= rel) ++sp;
                if (sp < spans.size() && spans[sp].start <= rel) style = spans[sp].style;
            }
            int x = textLeft + (int)((int64_t)col - (int64_t)startCol) * cw;
            uint64_t bo = o0 + i;
            if (cp >= 0x10000) {
                cp -= 0x10000;
                wt.push_back((wchar_t)(0xD800 + (cp >> 10))); dx.push_back(w * cw); xs.push_back(x); st.push_back(style); uo.push_back(bo);
                wt.push_back((wchar_t)(0xDC00 + (cp & 0x3FF))); dx.push_back(0); xs.push_back(x + w * cw); st.push_back(style); uo.push_back(bo);
            } else {
                wchar_t wc = (wchar_t)cp;
                if (ch == '\t' || ch == '\r') wc = L' ';
                else if (cp < 0x20 || cp == 0x7F) wc = 0x00B7;
                wt.push_back(wc); dx.push_back(w * cw); xs.push_back(x); st.push_back(style); uo.push_back(bo);
            }
            col += w;
            i += len;
        }
        if (lastSeg && o0 + i >= se && col > maxCols_) maxCols_ = col;
        // selection
        if (selA != selB) {
            size_t n = wt.size();
            for (size_t a = 0; a < n;) {
                if (uo[a] >= selA && uo[a] < selB) {
                    size_t b = a;
                    int x0 = xs[a], x1 = xs[a] + dx[a];
                    while (b + 1 < n && uo[b + 1] >= selA && uo[b + 1] < selB) { ++b; x1 = std::max(x1, xs[b] + dx[b]); }
                    Fill(dc, std::max(x0, gutterW_), y, x1, y + lh, t.selBg);
                    a = b + 1;
                } else ++a;
            }
            if (lastSeg && selA <= e && selB > e && o0 + i >= se) {
                int xe = textLeft + (int)((int64_t)col - (int64_t)startCol) * cw;
                if (xe >= gutterW_) Fill(dc, xe, y, xe + cw / 2 + 1, y + lh, t.selBg);
            }
        }
        // text runs
        RECT clip = {gutterW_, y, clientW_, y + lh};
        int ty = y + (lh - (g_ef.lh - MulDiv(3, g_dpi, 96))) / 2;
        for (size_t a = 0; a < wt.size();) {
            size_t b = a;
            while (b < wt.size() && st[b] == st[a]) ++b;
            SelectObject(dc, StyleFont(st[a]));
            SetTextColor(dc, StyleColor(st[a]));
            ExtTextOutW(dc, xs[a], ty, ETO_CLIPPED, &clip, wt.data() + a, (UINT)(b - a), dx.data() + a);
            a = b;
        }
    }
    if (!buf->Ready()) {
        wchar_t msg[64];
        swprintf_s(msg, L"  Indexing lines... %d%%  ", (int)(buf->Progress() * 100));
        SelectObject(dc, g_uiFont);
        SIZE sz;
        GetTextExtentPoint32W(dc, msg, (int)wcslen(msg), &sz);
        RECT mr = {clientW_ - sz.cx - S(12), S(6), clientW_ - S(12), S(6) + sz.cy + S(6)};
        Fill(dc, mr.left, mr.top, mr.right, mr.bottom, t.accent);
        SetTextColor(dc, RGB(255, 255, 255));
        DrawTextW(dc, msg, -1, &mr, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    }
    SelectObject(dc, oldFont);
    BitBlt(hdc, 0, 0, clientW_, clientH_, dc, 0, 0, SRCCOPY);
    SelectObject(dc, oldBmp);
    DeleteDC(dc);
}

// ---------------- window procedure ----------------

LRESULT CALLBACK EditorView::WndProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_NCCREATE) {
        auto* ev = (EditorView*)((CREATESTRUCTW*)l)->lpCreateParams;
        ev->hwnd = h;
        SetWindowLongPtrW(h, GWLP_USERDATA, (LONG_PTR)ev);
    }
    EditorView* ev = From(h);
    if (!ev) return DefWindowProcW(h, m, w, l);
    if (m == WM_NCDESTROY) {
        SetWindowLongPtrW(h, GWLP_USERDATA, 0);
        delete ev;
        return DefWindowProcW(h, m, w, l);
    }
    return ev->Handle(m, w, l);
}
