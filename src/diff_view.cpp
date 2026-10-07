// Side-by-side diff viewer. Uses `git diff` with unlimited context so the whole file is shown.
#include "app.h"
#include "editor_view.h"

namespace {

enum Kind : uint8_t { K_CTX, K_CHG, K_DEL, K_ADD, K_SEP };

struct Row {
    int32_t lno = 0, rno = 0;  // 1-based line numbers, 0 = none
    uint8_t kind = K_CTX;
    uint32_t lt = 0, ll = 0, rt = 0, rl = 0;  // offsets into pool
    uint32_t lhs = 0, lhe = 0, rhs = 0, rhe = 0;  // intraline highlight ranges (relative)
};

struct Result {
    std::wstring pool, message;
    std::vector<Row> rows;
};

struct Data {
    DiffSpec spec;
    uint64_t id = 0;
    bool loading = true;
    Result res;
    std::vector<int> blocks;  // first row of each change block
    int64_t top = 0, col = 0;
    int clientW = 0, clientH = 0;
    int wheel = 0;
};

std::atomic<uint64_t> g_ids{1};

int HeaderH() { return S(26); }

std::wstring ExpandTabs(const std::wstring& s) {
    if (s.find(L'\t') == std::wstring::npos) return s;
    std::wstring o;
    int tab = g_settings.tabSize;
    for (wchar_t c : s) {
        if (c == L'\t') o.append((size_t)(tab - (int)(o.size() % tab)), L' ');
        else o += c;
    }
    return o;
}

uint32_t AddText(Result& r, const std::string& line) {
    std::string t = line;
    if (!t.empty() && t.back() == '\r') t.pop_back();
    std::wstring w = ExpandTabs(Utf8ToWide(t));
    uint32_t off = (uint32_t)r.pool.size();
    r.pool += w;
    return off;
}

void Intraline(Result& r, Row& row) {
    const wchar_t* a = r.pool.data() + row.lt;
    const wchar_t* b = r.pool.data() + row.rt;
    uint32_t la = row.ll, lb = row.rl, p = 0;
    while (p < la && p < lb && a[p] == b[p]) ++p;
    uint32_t s = 0;
    while (s < la - p && s < lb - p && a[la - 1 - s] == b[lb - 1 - s]) ++s;
    row.lhs = p; row.lhe = la - s;
    row.rhs = p; row.rhe = lb - s;
}

void Flush(Result& r, std::vector<std::pair<int, std::string>>& del, std::vector<std::pair<int, std::string>>& add) {
    size_t n = std::max(del.size(), add.size());
    for (size_t i = 0; i < n; ++i) {
        Row row;
        if (i < del.size()) {
            row.lno = del[i].first;
            row.lt = AddText(r, del[i].second);
            row.ll = (uint32_t)r.pool.size() - row.lt;
        }
        if (i < add.size()) {
            row.rno = add[i].first;
            row.rt = AddText(r, add[i].second);
            row.rl = (uint32_t)r.pool.size() - row.rt;
        }
        row.kind = (i < del.size() && i < add.size()) ? K_CHG : (i < del.size() ? K_DEL : K_ADD);
        if (row.kind == K_CHG) Intraline(r, row);
        r.rows.push_back(row);
    }
    del.clear();
    add.clear();
}

void ParseUnified(const std::string& text, Result& r) {
    std::vector<std::pair<int, std::string>> del, add;
    bool inHunk = false;
    int l = 0, rn = 0;
    size_t p = 0;
    while (p < text.size()) {
        size_t e = text.find('\n', p);
        if (e == std::string::npos) e = text.size();
        std::string line = text.substr(p, e - p);
        p = e + 1;
        if (line.compare(0, 2, "@@") == 0) {
            Flush(r, del, add);
            int a = 1, c = 1;
            sscanf_s(line.c_str(), "@@ -%d", &a);
            size_t plus = line.find(" +");
            if (plus != std::string::npos) c = atoi(line.c_str() + plus + 2);
            l = a ? a : 1;
            rn = c ? c : 1;
            if (!r.rows.empty()) { Row sep; sep.kind = K_SEP; r.rows.push_back(sep); }
            inHunk = true;
            continue;
        }
        if (!inHunk) {
            if (line.compare(0, 12, "Binary files") == 0) r.message = L"Binary files differ.";
            continue;
        }
        if (line.empty()) continue;
        char c = line[0];
        if (c == ' ') {
            Flush(r, del, add);
            Row row;
            row.kind = K_CTX;
            row.lno = l++;
            row.rno = rn++;
            row.lt = row.rt = AddText(r, line.substr(1));
            row.ll = row.rl = (uint32_t)r.pool.size() - row.lt;
            r.rows.push_back(row);
        } else if (c == '-') {
            del.push_back({l++, line.substr(1)});
        } else if (c == '+') {
            add.push_back({rn++, line.substr(1)});
        } else if (c == 'd' && line.compare(0, 10, "diff --git") == 0) {
            Flush(r, del, add);
            inHunk = false;
        }
    }
    Flush(r, del, add);
}

Result Load(const DiffSpec& s) {
    Result r;
    if (s.kind == DiffSpec::Untracked) {
        std::wstring full = JoinPath(s.root, ToBackslashes(s.path));
        HANDLE f = CreateFileW(full.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
                               OPEN_EXISTING, 0, nullptr);
        if (f == INVALID_HANDLE_VALUE) { r.message = L"Cannot read file: " + FormatError(GetLastError()); return r; }
        LARGE_INTEGER sz;
        GetFileSizeEx(f, &sz);
        if (sz.QuadPart > (64ll << 20)) { CloseHandle(f); r.message = L"File is too large to show as a diff."; return r; }
        std::string data((size_t)sz.QuadPart, '\0');
        DWORD rd = 0;
        if (!data.empty()) ReadFile(f, &data[0], (DWORD)data.size(), &rd, nullptr);
        CloseHandle(f);
        data.resize(rd);
        if (memchr(data.data(), 0, std::min<size_t>(data.size(), 8000))) { r.message = L"Binary file."; return r; }
        if (data.size() >= 3 && (unsigned char)data[0] == 0xEF && (unsigned char)data[1] == 0xBB) data.erase(0, 3);
        int n = 1;
        size_t p = 0;
        while (p < data.size()) {
            size_t e = data.find('\n', p);
            if (e == std::string::npos) e = data.size();
            Row row;
            row.kind = K_ADD;
            row.rno = n++;
            row.rt = AddText(r, data.substr(p, e - p));
            row.rl = (uint32_t)r.pool.size() - row.rt;
            r.rows.push_back(row);
            p = e + 1;
        }
        if (r.rows.empty()) r.message = L"Empty file.";
        return r;
    }
    std::vector<std::wstring> args;
    const wchar_t* ctx = L"--unified=100000000";
    if (s.kind == DiffSpec::Commit) {
        if (s.parent.empty()) args = {L"show", L"--format=", L"--no-color", L"--no-ext-diff", L"-M", ctx, s.commit};
        else args = {L"diff", L"--no-color", L"--no-ext-diff", L"-M", ctx, s.parent, s.commit};
    } else {
        args = {L"diff", L"--no-color", L"--no-ext-diff", L"-M", ctx};
        if (s.kind == DiffSpec::Staged) args.insert(args.begin() + 1, L"--cached");
    }
    args.push_back(L"--");
    args.push_back(s.path);
    if (!s.origPath.empty()) args.push_back(s.origPath);
    ProcResult pr = Git(s.root, args, nullptr, false);
    if (pr.code != 0) { r.message = L"git diff failed:\n" + GitErrorText(pr); return r; }
    if (pr.out.size() > (256u << 20)) { r.message = L"Diff is too large to display."; return r; }
    ParseUnified(pr.out, r);
    if (r.rows.empty() && r.message.empty()) r.message = L"No textual changes.";
    return r;
}

Data* From(HWND h) { return (Data*)GetWindowLongPtrW(h, GWLP_USERDATA); }

int PageRows(Data* d) { return std::max(1, (d->clientH - HeaderH()) / g_ef.lh); }

void UpdateScroll(HWND h, Data* d) {
    int64_t n = (int64_t)d->res.rows.size();
    int page = PageRows(d);
    d->top = std::max<int64_t>(0, std::min<int64_t>(d->top, n - 1));
    SCROLLINFO si = {sizeof(si), SIF_RANGE | SIF_PAGE | SIF_POS};
    si.nMax = (int)std::max<int64_t>(0, n - 1 + page - 1);
    si.nPage = (UINT)page;
    si.nPos = (int)d->top;
    SetScrollInfo(h, SB_VERT, &si, TRUE);
    uint32_t maxLen = 0;
    for (size_t i = 0; i < d->res.rows.size() && i < 200000; ++i)
        maxLen = std::max(maxLen, std::max(d->res.rows[i].ll, d->res.rows[i].rl));
    int paneCols = std::max(1, (d->clientW / 2 - S(60)) / g_ef.cw);
    si.nMax = (int)maxLen + 4;
    si.nPage = (UINT)paneCols;
    si.nPos = (int)d->col;
    SetScrollInfo(h, SB_HORZ, &si, TRUE);
}

void ComputeBlocks(Data* d) {
    d->blocks.clear();
    auto& rows = d->res.rows;
    for (size_t i = 0; i < rows.size(); ++i) {
        bool ch = rows[i].kind != K_CTX && rows[i].kind != K_SEP;
        bool prevCh = i > 0 && rows[i - 1].kind != K_CTX && rows[i - 1].kind != K_SEP;
        if (ch && !prevCh) d->blocks.push_back((int)i);
    }
}

void StartLoad(HWND h) {
    Data* d = From(h);
    d->loading = true;
    d->id = g_ids++;
    uint64_t id = d->id;
    DiffSpec spec = d->spec;
    InvalidateRect(h, nullptr, FALSE);
    BgRun([h, id, spec]() {
        auto res = std::make_shared<Result>(Load(spec));
        RunOnUi([h, id, res]() {
            if (!IsWindow(h)) return;
            Data* d = From(h);
            if (!d || d->id != id) return;
            bool first = d->res.rows.empty();
            d->res = std::move(*res);
            d->loading = false;
            ComputeBlocks(d);
            if (first && !d->blocks.empty()) d->top = std::max(0, d->blocks[0] - 3);
            UpdateScroll(h, d);
            InvalidateRect(h, nullptr, FALSE);
        });
    });
}

void FillR(HDC dc, int l, int t, int r, int b, COLORREF c) {
    RECT rc = {l, t, r, b};
    SetBkColor(dc, c);
    ExtTextOutW(dc, 0, 0, ETO_OPAQUE, &rc, nullptr, 0, nullptr);
}

void DrawSide(HDC dc, Data* d, const Row& row, bool left, int x0, int x1, int y) {
    const Theme& t = g_theme;
    int lh = g_ef.lh, cw = g_ef.cw;
    int no = left ? row.lno : row.rno;
    bool has = left ? (row.kind != K_ADD) : (row.kind != K_DEL);
    COLORREF bg = t.bg;
    if (row.kind == K_SEP) bg = t.emptyBg;
    else if (!has) bg = t.emptyBg;
    else if (row.kind == K_CHG || (left && row.kind == K_DEL) || (!left && row.kind == K_ADD)) bg = left ? t.delBg : t.addBg;
    FillR(dc, x0, y, x1, y + lh, bg);
    int gutter = S(48);
    if (row.kind == K_SEP) {
        SetTextColor(dc, t.hunkFg);
        RECT r = {x0, y, x1, y + lh};
        DrawTextW(dc, L"\x22EF", 1, &r, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        return;
    }
    if (!has) return;
    wchar_t num[16];
    int n = swprintf_s(num, L"%d", no);
    SetTextColor(dc, t.gutterFg);
    RECT nr = {x0, y, x0 + gutter - S(8), y + lh};
    DrawTextW(dc, num, n, &nr, DT_RIGHT | DT_VCENTER | DT_SINGLELINE);
    uint32_t off = left ? row.lt : row.rt, len = left ? row.ll : row.rl;
    int tx = x0 + gutter;
    uint32_t hs = left ? row.lhs : row.rhs, he = left ? row.lhe : row.rhe;
    RECT clip = {tx, y, x1, y + lh};
    if (row.kind == K_CHG && he > hs) {
        int a = tx + (int)((int64_t)hs - d->col) * cw, b = tx + (int)((int64_t)he - d->col) * cw;
        a = std::max(a, tx);
        b = std::min(b, x1);
        if (b > a) FillR(dc, a, y, b, y + lh, left ? t.delStrong : t.addStrong);
    }
    if ((int64_t)len <= d->col) return;
    uint32_t start = (uint32_t)d->col;
    uint32_t vis = (uint32_t)std::max(0, (x1 - tx) / cw + 1);
    uint32_t cnt = std::min(len - start, vis);
    static std::vector<int> dx;
    if (dx.size() < cnt) dx.resize(cnt);
    const wchar_t* tp = d->res.pool.data() + off + start;
    for (uint32_t k = 0; k < cnt; ++k) {
        wchar_t c = tp[k];
        if (IS_HIGH_SURROGATE(c) && k + 1 < cnt) {
            uint32_t cp = 0x10000 + (((uint32_t)c - 0xD800) << 10) + ((uint32_t)tp[k + 1] - 0xDC00);
            dx[k] = CharColumns(cp) * cw;
            dx[++k] = 0;
        } else {
            dx[k] = CharColumns(c) * cw;
        }
    }
    SetTextColor(dc, t.fg);
    ExtTextOutW(dc, tx, y + S(1), ETO_CLIPPED, &clip, d->res.pool.data() + off + start, cnt, dx.data());
}

void Paint(HWND h, HDC hdc) {
    Data* d = From(h);
    const Theme& t = g_theme;
    RECT rc = {0, 0, d->clientW, d->clientH};
    HDC dc = CreateCompatibleDC(hdc);
    HBITMAP bmp = CreateCompatibleBitmap(hdc, std::max(1, d->clientW), std::max(1, d->clientH));
    HGDIOBJ ob = SelectObject(dc, bmp);
    FillR(dc, 0, 0, rc.right, rc.bottom, t.bg);
    SetBkMode(dc, TRANSPARENT);
    int mid = d->clientW / 2;
    // header
    int hh = HeaderH();
    FillR(dc, 0, 0, rc.right, hh, t.panelBg);
    SelectObject(dc, g_uiFont);
    SetTextColor(dc, t.panelFgDim);
    const wchar_t* lname = L"HEAD";
    const wchar_t* rname = L"Working Tree";
    std::wstring lcustom, rcustom;
    switch (d->spec.kind) {
    case DiffSpec::Unstaged: lname = L"Index"; rname = L"Working Tree"; break;
    case DiffSpec::Staged: lname = L"HEAD"; rname = L"Index"; break;
    case DiffSpec::Untracked: lname = L"(new file)"; rname = L"Working Tree"; break;
    case DiffSpec::Commit:
        lcustom = d->spec.parent.empty() ? L"(root)" : d->spec.parent.substr(0, 8);
        rcustom = d->spec.commit.substr(0, 8);
        lname = lcustom.c_str();
        rname = rcustom.c_str();
        break;
    }
    std::wstring lt = std::wstring(lname) + L"  \x2014  " + (d->spec.origPath.empty() ? d->spec.path : d->spec.origPath);
    std::wstring rt = std::wstring(rname) + L"  \x2014  " + d->spec.path + L"      (F7 / Shift+F7: next / previous change)";
    RECT lr = {S(8), 0, mid - S(4), hh}, rr = {mid + S(8), 0, rc.right - S(4), hh};
    DrawTextW(dc, lt.c_str(), -1, &lr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX);
    DrawTextW(dc, rt.c_str(), -1, &rr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX);
    if (d->loading || !d->res.message.empty()) {
        SetTextColor(dc, t.fg);
        RECT mr = {S(16), hh + S(16), rc.right - S(16), rc.bottom};
        std::wstring msg = d->loading ? L"Loading diff..." : d->res.message;
        DrawTextW(dc, msg.c_str(), -1, &mr, DT_LEFT | DT_TOP | DT_WORDBREAK | DT_NOPREFIX);
    }
    SelectObject(dc, g_ef.normal);
    int lh = g_ef.lh;
    int rows = PageRows(d) + 1;
    for (int i = 0; i < rows; ++i) {
        int64_t ri = d->top + i;
        if (ri >= (int64_t)d->res.rows.size()) break;
        const Row& row = d->res.rows[(size_t)ri];
        int y = hh + i * lh;
        DrawSide(dc, d, row, true, 0, mid - 1, y);
        DrawSide(dc, d, row, false, mid + 1, rc.right, y);
    }
    FillR(dc, mid - 1, hh, mid + 1, rc.bottom, t.border);
    BitBlt(hdc, 0, 0, rc.right, rc.bottom, dc, 0, 0, SRCCOPY);
    SelectObject(dc, ob);
    DeleteObject(bmp);
    DeleteDC(dc);
}

void ScrollTo(HWND h, Data* d, int64_t top) {
    d->top = top;
    UpdateScroll(h, d);
    InvalidateRect(h, nullptr, FALSE);
}

void JumpChange(HWND h, Data* d, bool next) {
    if (d->blocks.empty()) return;
    int64_t cur = d->top + 3;
    int target = -1;
    if (next) {
        for (int b : d->blocks) if (b > cur) { target = b; break; }
        if (target < 0) target = d->blocks.front();
    } else {
        for (auto it = d->blocks.rbegin(); it != d->blocks.rend(); ++it) if (*it < cur) { target = *it; break; }
        if (target < 0) target = d->blocks.back();
    }
    ScrollTo(h, d, std::max(0, target - 3));
}

LRESULT CALLBACK Proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_NCCREATE) SetWindowLongPtrW(h, GWLP_USERDATA, (LONG_PTR)((CREATESTRUCTW*)l)->lpCreateParams);
    Data* d = From(h);
    if (!d) return DefWindowProcW(h, m, w, l);
    switch (m) {
    case WM_NCDESTROY:
        SetWindowLongPtrW(h, GWLP_USERDATA, 0);
        delete d;
        return DefWindowProcW(h, m, w, l);
    case WM_SIZE:
        d->clientW = LOWORD(l);
        d->clientH = HIWORD(l);
        UpdateScroll(h, d);
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    case WM_ERASEBKGND: return 1;
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(h, &ps);
        Paint(h, dc);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_LBUTTONDOWN: SetFocus(h); return 0;
    case WM_LBUTTONDBLCLK: {
        int64_t ri = d->top + (GET_Y_LPARAM(l) - HeaderH()) / g_ef.lh;
        int line = 1;
        if (ri >= 0 && ri < (int64_t)d->res.rows.size()) {
            const Row& r = d->res.rows[(size_t)ri];
            line = r.rno ? r.rno : (r.lno ? r.lno : 1);
        }
        std::wstring full = JoinPath(d->spec.root, ToBackslashes(d->spec.path));
        if (FileExists(full)) App::OpenFile(full, line - 1);
        return 0;
    }
    case WM_MOUSEWHEEL: {
        d->wheel += GET_WHEEL_DELTA_WPARAM(w);
        int n = d->wheel / WHEEL_DELTA;
        d->wheel -= n * WHEEL_DELTA;
        if (GET_KEYSTATE_WPARAM(w) & MK_SHIFT) {
            d->col = std::max<int64_t>(0, d->col - n * 8);
            UpdateScroll(h, d);
            InvalidateRect(h, nullptr, FALSE);
        } else {
            ScrollTo(h, d, d->top - n * 3);
        }
        return 0;
    }
    case WM_MOUSEHWHEEL:
        d->col = std::max<int64_t>(0, d->col + GET_WHEEL_DELTA_WPARAM(w) / WHEEL_DELTA * 8);
        UpdateScroll(h, d);
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    case WM_VSCROLL: {
        SCROLLINFO si = {sizeof(si), SIF_ALL};
        GetScrollInfo(h, SB_VERT, &si);
        int64_t t = d->top;
        switch (LOWORD(w)) {
        case SB_LINEUP: t--; break;
        case SB_LINEDOWN: t++; break;
        case SB_PAGEUP: t -= PageRows(d); break;
        case SB_PAGEDOWN: t += PageRows(d); break;
        case SB_THUMBTRACK: case SB_THUMBPOSITION: t = si.nTrackPos; break;
        }
        ScrollTo(h, d, t);
        return 0;
    }
    case WM_HSCROLL: {
        SCROLLINFO si = {sizeof(si), SIF_ALL};
        GetScrollInfo(h, SB_HORZ, &si);
        switch (LOWORD(w)) {
        case SB_LINELEFT: d->col -= 4; break;
        case SB_LINERIGHT: d->col += 4; break;
        case SB_PAGELEFT: d->col -= si.nPage; break;
        case SB_PAGERIGHT: d->col += si.nPage; break;
        case SB_THUMBTRACK: case SB_THUMBPOSITION: d->col = si.nTrackPos; break;
        }
        d->col = std::max<int64_t>(0, d->col);
        UpdateScroll(h, d);
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    }
    case WM_GETDLGCODE: return DLGC_WANTARROWS;
    case WM_KEYDOWN:
        switch (w) {
        case VK_UP: ScrollTo(h, d, d->top - 1); break;
        case VK_DOWN: ScrollTo(h, d, d->top + 1); break;
        case VK_PRIOR: ScrollTo(h, d, d->top - PageRows(d)); break;
        case VK_NEXT: ScrollTo(h, d, d->top + PageRows(d)); break;
        case VK_HOME: ScrollTo(h, d, 0); break;
        case VK_END: ScrollTo(h, d, (int64_t)d->res.rows.size()); break;
        case VK_LEFT: d->col = std::max<int64_t>(0, d->col - 4); UpdateScroll(h, d); InvalidateRect(h, nullptr, FALSE); break;
        case VK_RIGHT: d->col += 4; UpdateScroll(h, d); InvalidateRect(h, nullptr, FALSE); break;
        case VK_F7: JumpChange(h, d, GetKeyState(VK_SHIFT) >= 0); break;
        case 'N': JumpChange(h, d, true); break;
        case 'P': JumpChange(h, d, false); break;
        }
        return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

}  // namespace

namespace DiffView {

void Register() {
    WNDCLASSEXW wc = {sizeof(wc)};
    wc.style = CS_DBLCLKS;
    wc.lpfnWndProc = Proc;
    wc.hInstance = g_hinst;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = L"MyDiffView";
    RegisterClassExW(&wc);
}

HWND Create(HWND parent, const DiffSpec& spec) {
    Data* d = new Data();
    d->spec = spec;
    HWND h = CreateWindowExW(0, L"MyDiffView", L"", WS_CHILD | WS_VSCROLL | WS_HSCROLL | WS_CLIPSIBLINGS, 0, 0, 0, 0,
                             parent, nullptr, g_hinst, d);
    if (!h) { delete d; return nullptr; }
    StartLoad(h);
    return h;
}

std::wstring Title(HWND h) {
    Data* d = From(h);
    if (!d) return L"";
    std::wstring name = FileNameOf(ToBackslashes(d->spec.path));
    switch (d->spec.kind) {
    case DiffSpec::Unstaged: return name + L" (Working Tree)";
    case DiffSpec::Staged: return name + L" (Index)";
    case DiffSpec::Untracked: return name + L" (Untracked)";
    default: return name + L" (" + d->spec.commit.substr(0, 7) + L")";
    }
}

std::wstring Key(const DiffSpec& s) {
    return L"diff:" + std::to_wstring((int)s.kind) + L":" + LowerW(s.root) + L":" + s.path + L":" + s.commit;
}

void Reload(HWND h) {
    if (From(h)) StartLoad(h);
}

}  // namespace DiffView
