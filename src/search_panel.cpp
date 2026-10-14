// Search side panel (Ctrl+Shift+F): find text in every file of the open folder.
// Files are listed with `git ls-files` (so .gitignore is honoured) or a directory walk, then searched by a pool of
// worker threads. Results stream into a custom-drawn list while the search runs, so the UI never waits on it.
#include "main_window.h"
#include "resource.h"
#include <shlwapi.h>

namespace {

enum { IDC_QUERY = 4001, IDC_INCLUDE, IDC_EXCLUDE, IDC_CASE, IDC_WORD, IDC_RERUN, IDC_CLEAR };

const size_t kMaxResults = 20000;
const uint64_t kMaxFileSize = 64ull << 20;  // larger files are skipped
const UINT_PTR kTimerDebounce = 1, kTimerDrain = 2;

const wchar_t* kIcoDown = L"\xE70D";
const wchar_t* kIcoRight = L"\xE76C";

struct Match {
    uint32_t line = 0, col = 0, len = 0;  // 0-based line; byte column and length within the line (UTF-8)
    std::wstring preview;
    int hlStart = 0, hlLen = 0;  // highlighted range in preview
};

struct FileHits {
    size_t order = 0;  // position in the sorted file list
    std::wstring rel;
    std::vector<Match> matches;
    bool collapsed = false;
};

struct Query {
    std::string needle;  // UTF-8 (lowercased unless matchCase)
    bool matchCase = false, wholeWord = false;
    std::vector<std::wstring> include, exclude;
};

// One search run, shared between the UI and its worker threads.
struct Job {
    std::atomic<bool> cancel{false};
    std::atomic<size_t> results{0};
    std::atomic<size_t> searched{0};
    std::atomic<size_t> fileCount{0};
    std::atomic<bool> listed{false}, limited{false};
    std::mutex mu;
    std::vector<FileHits> pending;  // found but not yet taken by the UI
};

struct Row {
    int file;
    int match;  // -1 = file header
};

struct State {
    HWND hwnd = nullptr, query, include, exclude, caseBtn, wordBtn, rerun, clear, list;
    HBRUSH inputBrush = nullptr;
    bool matchCase = false, wholeWord = false;
    std::shared_ptr<Job> job;
    bool searching = false;
    std::wstring root;              // folder the results belong to
    std::vector<FileHits> files;    // sorted by order
    size_t total = 0;               // matches in files
    std::vector<Row> rows;
    int sel = -1, top = 0, hover = -1;
    std::wstring selRel;  // file of the selected row (rows are rebuilt as results stream in)
    bool tracking = false;
    std::wstring status;
} P;

std::wstring GetText(HWND h) {
    int n = GetWindowTextLengthW(h);
    std::wstring s((size_t)n + 1, L'\0');
    GetWindowTextW(h, &s[0], n + 1);
    s.resize((size_t)n);
    return s;
}

// ---------------- file filtering ----------------

std::vector<std::wstring> SplitPatterns(const std::wstring& s) {
    std::vector<std::wstring> out;
    std::wstring cur;
    for (size_t i = 0; i <= s.size(); ++i) {
        if (i == s.size() || s[i] == L',' || s[i] == L';') {
            std::wstring p = LowerW(ToBackslashes(Trim(cur)));
            while (p.size() >= 2 && p[0] == L'.' && p[1] == L'\\') p.erase(0, 2);
            while (p.size() >= 3 && p.compare(0, 3, L"**\\") == 0) p.erase(0, 3);
            while (!p.empty() && p[0] == L'\\') p.erase(0, 1);
            while (!p.empty() && p.back() == L'\\') p.pop_back();
            if (!p.empty()) out.push_back(p);
            cur.clear();
        } else {
            cur += s[i];
        }
    }
    return out;
}

// pattern without a backslash: matches any path component ("*.cpp", "build", "node_modules");
// with one: matches the path from the root ("src\\ui", "src\\*.h"), including everything below a matching folder.
bool PatternMatches(const std::wstring& lowerRel, const std::wstring& p) {
    if (p.find(L'\\') == std::wstring::npos) {
        bool wild = p.find_first_of(L"*?") != std::wstring::npos;
        size_t a = 0;
        for (;;) {
            size_t b = lowerRel.find(L'\\', a);
            std::wstring part = lowerRel.substr(a, b == std::wstring::npos ? std::wstring::npos : b - a);
            if (wild ? PathMatchSpecW(part.c_str(), p.c_str()) != FALSE : part == p) return true;
            if (b == std::wstring::npos) return false;
            a = b + 1;
        }
    }
    if (lowerRel.size() > p.size() && lowerRel.compare(0, p.size(), p) == 0 && lowerRel[p.size()] == L'\\') return true;
    return PathMatchSpecW(lowerRel.c_str(), p.c_str()) || PathMatchSpecW(lowerRel.c_str(), (p + L"\\*").c_str());
}

bool Included(const std::wstring& rel, const Query& q) {
    if (q.include.empty() && q.exclude.empty()) return true;
    std::wstring lower = LowerW(rel);
    if (!q.include.empty()) {
        bool any = false;
        for (auto& p : q.include)
            if (PatternMatches(lower, p)) { any = true; break; }
        if (!any) return false;
    }
    for (auto& p : q.exclude)
        if (PatternMatches(lower, p)) return false;
    return true;
}

// ---------------- listing ----------------

bool InGitRepo(std::wstring dir) {
    for (;;) {
        if (GetFileAttributesW(JoinPath(dir, L".git").c_str()) != INVALID_FILE_ATTRIBUTES) return true;
        std::wstring up = DirOf(dir);
        if (up.empty() || up.size() >= dir.size()) return false;
        dir = up;
    }
}

bool GitList(const std::wstring& root, std::vector<std::wstring>& out) {
    if (!InGitRepo(root)) return false;
    ProcResult r = Git(root, {L"ls-files", L"-z", L"--cached", L"--others", L"--exclude-standard"}, nullptr, false);
    if (r.code != 0) return false;
    for (const auto& s : SplitNul(r.out)) {
        if (s.empty()) continue;
        std::wstring rel = ToBackslashes(Utf8ToWide(s));
        if (FileNameOf(rel).find(L".~mye-") == std::wstring::npos) out.push_back(rel);
    }
    return true;
}

void WalkList(const std::wstring& root, const Job& job, std::vector<std::wstring>& out) {
    std::vector<std::wstring> stack = {L""};
    while (!stack.empty() && !job.cancel) {
        std::wstring rel = stack.back();
        stack.pop_back();
        WIN32_FIND_DATAW fd;
        HANDLE h = FindFirstFileExW(JoinPath(JoinPath(root, rel), L"*").c_str(), FindExInfoBasic, &fd, FindExSearchNameMatch,
                                    nullptr, FIND_FIRST_EX_LARGE_FETCH);
        if (h == INVALID_HANDLE_VALUE) continue;
        do {
            std::wstring name = fd.cFileName;
            if (name == L"." || name == L"..") continue;
            std::wstring r = rel.empty() ? name : rel + L"\\" + name;
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                if (name == L".git" || name == L"node_modules" || (fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) continue;
                stack.push_back(r);
            } else if (name.find(L".~mye-") == std::wstring::npos) {
                out.push_back(r);
            }
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
}

// ---------------- searching ----------------

bool IsWordByte(unsigned char c) { return isalnum(c) || c == '_' || c >= 0x80; }

bool ReadWholeFile(const std::wstring& path, std::string& out) {
    HANDLE f = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
                           OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (f == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size;
    bool ok = GetFileSizeEx(f, &size) && size.QuadPart > 0 && (uint64_t)size.QuadPart <= kMaxFileSize;
    if (ok) {
        // read the head first so binary files (no BOM, NUL bytes) are skipped without reading them whole
        out.resize((size_t)size.QuadPart);
        DWORD head = (DWORD)std::min<uint64_t>(out.size(), 8192), got = 0, rest = 0;
        ok = ReadFile(f, &out[0], head, &got, nullptr) != FALSE;
        const unsigned char* u = (const unsigned char*)out.data();
        bool utf16 = got >= 2 && ((u[0] == 0xFF && u[1] == 0xFE) || (u[0] == 0xFE && u[1] == 0xFF));
        if (ok && !utf16 && memchr(out.data(), 0, got)) ok = false;
        if (ok && got == head && out.size() > head)
            ok = ReadFile(f, &out[head], (DWORD)(out.size() - head), &rest, nullptr) != FALSE;
        out.resize(got + rest);
    }
    CloseHandle(f);
    return ok && !out.empty();
}

// Text bytes of a file as UTF-8 (what the editor shows); false for binary files.
bool TextOf(std::string& data, const char** p, size_t* n) {
    const unsigned char* u = (const unsigned char*)data.data();
    size_t len = data.size();
    if (len >= 2 && ((u[0] == 0xFF && u[1] == 0xFE) || (u[0] == 0xFE && u[1] == 0xFF))) {
        bool be = u[0] == 0xFE;
        std::wstring w((len - 2) / 2, L'\0');
        for (size_t i = 0; i < w.size(); ++i) {
            unsigned char a = u[2 + 2 * i], b = u[3 + 2 * i];
            w[i] = be ? (wchar_t)((a << 8) | b) : (wchar_t)((b << 8) | a);
        }
        data = WideToUtf8(w);
        *p = data.data();
        *n = data.size();
        return true;
    }
    size_t skip = (len >= 3 && u[0] == 0xEF && u[1] == 0xBB && u[2] == 0xBF) ? 3 : 0;
    if (memchr(data.data() + skip, 0, std::min<size_t>(len - skip, 8192))) return false;
    *p = data.data() + skip;
    *n = len - skip;
    return true;
}

void MakePreview(const char* p, size_t ls, size_t le, size_t at, size_t me, Match& m) {
    size_t a = ls;
    while (a < at && (p[a] == ' ' || p[a] == '\t')) ++a;
    bool lead = false;
    if (at - a > 60) {
        a = at - 30;
        while (a < at && IsUtf8Cont((unsigned char)p[a])) ++a;
        lead = true;
    }
    size_t b = le;
    if (b - me > 250) {
        b = me + 250;
        while (b > me && IsUtf8Cont((unsigned char)p[b])) --b;
    }
    std::wstring pre = Utf8ToWide(p + a, at - a), mid = Utf8ToWide(p + at, me - at), post = Utf8ToWide(p + me, b - me);
    m.preview = (lead ? L"\x2026" : L"") + pre + mid + post;
    for (auto& c : m.preview)
        if (c == L'\t' || c < 0x20) c = L' ';
    m.hlStart = (int)pre.size() + (lead ? 1 : 0);
    m.hlLen = (int)mid.size();
}

typedef std::boyer_moore_horspool_searcher<const char*> Searcher;

void SearchText(const char* p, size_t n, const Query& q, const Searcher& srch, std::string& lower, Job& job, FileHits& out) {
    size_t L = q.needle.size();
    if (L == 0 || L > n) return;
    const char* hay = p;
    if (!q.matchCase) {
        lower.assign(p, n);
        for (auto& c : lower)
            if (c >= 'A' && c <= 'Z') c = (char)(c + 32);
        hay = lower.data();
    }
    size_t pos = 0, counted = 0, line = 0, ls = 0;
    while (pos + L <= n && !job.cancel) {
        const char* r = std::search(hay + pos, hay + n, srch);
        if (r == hay + n) break;
        size_t at = (size_t)(r - hay);
        if (q.wholeWord && ((at > 0 && IsWordByte((unsigned char)p[at - 1])) || (at + L < n && IsWordByte((unsigned char)p[at + L])))) {
            pos = at + 1;
            continue;
        }
        for (const char* nl = p + counted; (nl = (const char*)memchr(nl, '\n', (size_t)(p + at - nl))) != nullptr; ++nl) {
            ++line;
            ls = (size_t)(nl - p) + 1;
        }
        counted = at;
        const char* e = (const char*)memchr(p + at, '\n', n - at);
        size_t le = e ? (size_t)(e - p) : n;
        if (le > ls && p[le - 1] == '\r') --le;
        if (job.results.fetch_add(1) >= kMaxResults) {
            job.limited = true;
            job.cancel = true;
            return;
        }
        Match m;
        m.line = (uint32_t)line;
        m.col = (uint32_t)(at - ls);
        m.len = (uint32_t)L;
        MakePreview(p, ls, le, at, std::min(at + L, le), m);
        out.matches.push_back(std::move(m));
        pos = at + L;
    }
}

void RunJob(const std::shared_ptr<Job>& job, const std::wstring& root, const std::shared_ptr<const Query>& q) {
    std::vector<std::wstring> all;
    if (!GitList(root, all)) WalkList(root, *job, all);
    if (job->cancel) return;
    std::vector<std::wstring> files;
    files.reserve(all.size());
    for (auto& f : all)
        if (Included(f, *q)) files.push_back(std::move(f));
    std::sort(files.begin(), files.end(), [](const std::wstring& a, const std::wstring& b) {
        return CompareStringOrdinal(a.c_str(), -1, b.c_str(), -1, TRUE) == CSTR_LESS_THAN;
    });
    files.erase(std::unique(files.begin(), files.end()), files.end());  // ls-files lists conflicted paths once per stage
    job->fileCount = files.size();
    job->listed = true;

    std::atomic<size_t> next{0};
    auto worker = [&]() {
        Searcher srch(q->needle.data(), q->needle.data() + q->needle.size());
        std::string data, lower;
        for (;;) {
            if (job->cancel) return;
            size_t i = next++;
            if (i >= files.size()) return;
            FileHits fh;
            fh.order = i;
            fh.rel = files[i];
            const char* p = nullptr;
            size_t n = 0;
            if (ReadWholeFile(JoinPath(root, files[i]), data) && TextOf(data, &p, &n)) SearchText(p, n, *q, srch, lower, *job, fh);
            if (data.capacity() > (4u << 20)) std::string().swap(data);  // don't keep big buffers around
            if (lower.capacity() > (4u << 20)) std::string().swap(lower);
            ++job->searched;
            if (!fh.matches.empty()) {
                std::lock_guard<std::mutex> lk(job->mu);
                job->pending.push_back(std::move(fh));
            }
        }
    };
    unsigned n = std::max(1u, std::min(8u, std::thread::hardware_concurrency()));
    std::vector<std::thread> pool;
    for (unsigned i = 1; i < n; ++i) pool.emplace_back(worker);
    worker();
    for (auto& t : pool) t.join();
}

// ---------------- results list ----------------

int RowH() { return S(22); }

int VisibleRows() {
    RECT rc;
    GetClientRect(P.list, &rc);
    return std::max(1, (int)(rc.bottom / RowH()));
}

void UpdateListScroll() {
    int vis = VisibleRows();
    int maxTop = std::max(0, (int)P.rows.size() - vis);
    P.top = std::max(0, std::min(P.top, maxTop));
    SCROLLINFO si = {sizeof(si), SIF_RANGE | SIF_PAGE | SIF_POS};
    si.nMax = (int)P.rows.size() - 1;
    si.nPage = (UINT)vis;
    si.nPos = P.top;
    SetScrollInfo(P.list, SB_VERT, &si, TRUE);
}

void RebuildRows() {
    Row selRow = {-1, -1};
    if (P.sel >= 0 && P.sel < (int)P.rows.size()) selRow = P.rows[(size_t)P.sel];
    std::wstring selRel = selRow.file >= 0 ? P.selRel : L"";
    P.rows.clear();
    P.sel = -1;
    for (size_t f = 0; f < P.files.size(); ++f) {
        bool selFile = selRow.file >= 0 && P.files[f].rel == selRel;
        if (selFile && selRow.match < 0) P.sel = (int)P.rows.size();
        P.rows.push_back(Row{(int)f, -1});
        if (P.files[f].collapsed) continue;
        for (size_t m = 0; m < P.files[f].matches.size(); ++m) {
            if (selFile && selRow.match == (int)m) P.sel = (int)P.rows.size();
            P.rows.push_back(Row{(int)f, (int)m});
        }
    }
    UpdateListScroll();
    InvalidateRect(P.list, nullptr, FALSE);
}

std::wstring Plural(size_t n, const wchar_t* one, const wchar_t* many) {
    return std::to_wstring(n) + L" " + (n == 1 ? one : many);
}

RECT StatusRect() {
    RECT rc;
    GetClientRect(P.hwnd, &rc);
    RECT lr;
    GetWindowRect(P.list, &lr);
    MapWindowPoints(nullptr, P.hwnd, (POINT*)&lr, 2);
    return RECT{S(10), lr.top - S(26), rc.right - S(8), lr.top - S(2)};
}

void UpdateStatus() {
    std::wstring s;
    if (P.root.empty()) {
        s = L"Open a folder to search in files.";
    } else if (P.searching) {
        const Job& j = *P.job;
        s = L"Searching";
        if (j.listed) s += L" " + std::to_wstring(j.searched.load()) + L"/" + std::to_wstring(j.fileCount.load()) + L" files";
        s += L"... " + Plural(P.total, L"result", L"results");
    } else if (P.job) {
        if (P.total == 0) s = L"No results found.";
        else s = Plural(P.total, L"result", L"results") + L" in " + Plural(P.files.size(), L"file", L"files");
        if (P.job->limited) s += L" (limited to " + std::to_wstring(kMaxResults) + L")";
    }
    if (s == P.status) return;
    P.status = s;
    RECT sr = StatusRect();
    InvalidateRect(P.hwnd, &sr, TRUE);
}

// moves results found by the workers into the list
void Drain() {
    if (!P.job) return;
    std::vector<FileHits> got;
    {
        std::lock_guard<std::mutex> lk(P.job->mu);
        got.swap(P.job->pending);
    }
    for (auto& f : got) {
        P.total += f.matches.size();
        auto it = std::upper_bound(P.files.begin(), P.files.end(), f.order,
                                   [](size_t o, const FileHits& x) { return o < x.order; });
        P.files.insert(it, std::move(f));
    }
    if (!got.empty()) RebuildRows();
    UpdateStatus();
}

void ClearResults() {
    if (P.job) P.job->cancel = true;
    P.job.reset();
    P.searching = false;
    KillTimer(P.hwnd, kTimerDrain);
    KillTimer(P.hwnd, kTimerDebounce);
    P.files.clear();
    P.rows.clear();
    P.total = 0;
    P.sel = -1;
    P.top = 0;
    P.hover = -1;
    UpdateListScroll();
    InvalidateRect(P.list, nullptr, FALSE);
    UpdateStatus();
}

void StartSearch() {
    ClearResults();
    std::wstring text = GetText(P.query);
    P.root = App::Folder();
    if (P.root.empty() || text.empty()) { UpdateStatus(); return; }
    auto q = std::make_shared<Query>();
    q->needle = WideToUtf8(text);
    q->matchCase = P.matchCase;
    q->wholeWord = P.wholeWord;
    if (!q->matchCase)
        for (auto& c : q->needle)
            if (c >= 'A' && c <= 'Z') c = (char)(c + 32);
    q->include = SplitPatterns(GetText(P.include));
    q->exclude = SplitPatterns(GetText(P.exclude));
    auto job = std::make_shared<Job>();
    P.job = job;
    P.searching = true;
    SetTimer(P.hwnd, kTimerDrain, 100, nullptr);
    std::wstring root = P.root;
    std::thread([job, root, q]() {
        RunJob(job, root, q);
        RunOnUi([job]() {
            if (job != P.job) return;
            P.searching = false;
            KillTimer(P.hwnd, kTimerDrain);
            Drain();
            UpdateStatus();
        });
    }).detach();
    UpdateStatus();
}

void OpenMatch(int row) {
    if (row < 0 || row >= (int)P.rows.size()) return;
    Row r = P.rows[(size_t)row];
    const FileHits& f = P.files[(size_t)r.file];
    std::wstring full = JoinPath(P.root, f.rel);
    if (r.match < 0) {
        App::OpenFile(full);
        return;
    }
    Match m = f.matches[(size_t)r.match];
    App::OpenFile(full);  // may pump messages (error box), so nothing from P is used after this
    EditorView* ed = ActiveEditor();
    if (ed && PathEqualsI(ed->buf->path, full)) ed->SelectInLine(m.line, m.col, m.len);
}

void Select(int i) {
    if (P.rows.empty()) return;
    P.sel = std::max(0, std::min(i, (int)P.rows.size() - 1));
    P.selRel = P.files[(size_t)P.rows[(size_t)P.sel].file].rel;
    int vis = VisibleRows();
    if (P.sel < P.top) P.top = P.sel;
    else if (P.sel >= P.top + vis) P.top = P.sel - vis + 1;
    UpdateListScroll();
    InvalidateRect(P.list, nullptr, FALSE);
}

void ToggleCollapse(int fileIdx) {
    P.files[(size_t)fileIdx].collapsed = !P.files[(size_t)fileIdx].collapsed;
    RebuildRows();
}

void SetAllCollapsed(bool c) {
    for (auto& f : P.files) f.collapsed = c;
    RebuildRows();
}

COLORREF MatchBg() { return g_settings.dark ? RGB(110, 76, 34) : RGB(250, 214, 158); }

void PaintList(HDC hdc) {
    RECT rc;
    GetClientRect(P.list, &rc);
    HDC dc = CreateCompatibleDC(hdc);
    HBITMAP bmp = CreateCompatibleBitmap(hdc, std::max(1, (int)rc.right), std::max(1, (int)rc.bottom));
    HGDIOBJ ob = SelectObject(dc, bmp);
    const Theme& t = g_theme;
    HBRUSH bg = CreateSolidBrush(t.panelBg);
    FillRect(dc, &rc, bg);
    DeleteObject(bg);
    SetBkMode(dc, TRANSPARENT);
    int rh = RowH();
    bool focused = GetFocus() == P.list;
    for (int i = P.top; i < (int)P.rows.size(); ++i) {
        int y = (i - P.top) * rh;
        if (y > rc.bottom) break;
        const Row& r = P.rows[(size_t)i];
        const FileHits& f = P.files[(size_t)r.file];
        RECT row = {0, y, rc.right, y + rh};
        if (i == P.sel || i == P.hover) {
            HBRUSH b = CreateSolidBrush(i == P.sel && focused ? t.selBg : t.hoverBg);
            FillRect(dc, &row, b);
            DeleteObject(b);
        }
        if (r.match < 0) {
            RECT cr = {S(4), y, S(20), y + rh};
            SelectObject(dc, g_iconFont);
            SetTextColor(dc, t.panelFg);
            DrawTextW(dc, f.collapsed ? kIcoRight : kIcoDown, 1, &cr, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            std::wstring cnt = std::to_wstring(f.matches.size());
            SelectObject(dc, g_uiFont);
            SIZE sz;
            GetTextExtentPoint32W(dc, cnt.c_str(), (int)cnt.size(), &sz);
            int bw = std::max((int)sz.cx + S(10), S(18));
            RECT br = {rc.right - S(8) - bw, y + S(3), rc.right - S(8), y + rh - S(3)};
            HBRUSH bb = CreateSolidBrush(g_settings.dark ? RGB(77, 77, 77) : RGB(196, 196, 196));
            HGDIOBJ obr = SelectObject(dc, bb);
            HGDIOBJ op = SelectObject(dc, GetStockObject(NULL_PEN));
            RoundRect(dc, br.left, br.top, br.right, br.bottom, S(12), S(12));
            SelectObject(dc, op);
            SelectObject(dc, obr);
            DeleteObject(bb);
            DrawTextW(dc, cnt.c_str(), -1, &br, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            size_t sl = f.rel.find_last_of(L'\\');
            std::wstring name = sl == std::wstring::npos ? f.rel : f.rel.substr(sl + 1);
            std::wstring dir = sl == std::wstring::npos ? L"" : f.rel.substr(0, sl);
            int right = br.left - S(6);
            RECT nr = {S(22), y, right, y + rh};
            SetTextColor(dc, t.panelFg);
            SIZE nsz;
            GetTextExtentPoint32W(dc, name.c_str(), (int)name.size(), &nsz);
            DrawTextW(dc, name.c_str(), -1, &nr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX);
            if (!dir.empty() && S(22) + nsz.cx + S(8) < right) {
                RECT dr = {S(22) + nsz.cx + S(6), y, right, y + rh};
                SetTextColor(dc, t.panelFgDim);
                DrawTextW(dc, dir.c_str(), -1, &dr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX);
            }
        } else {
            const Match& m = f.matches[(size_t)r.match];
            SelectObject(dc, g_uiFont);
            TEXTMETRICW tm;
            GetTextMetricsW(dc, &tm);
            RECT clip = {S(30), y, rc.right - S(4), y + rh};
            int x = clip.left, ty = y + (rh - tm.tmHeight) / 2;
            const wchar_t* s = m.preview.c_str();
            int parts[3][2] = {{0, m.hlStart}, {m.hlStart, m.hlLen}, {m.hlStart + m.hlLen, (int)m.preview.size() - m.hlStart - m.hlLen}};
            for (int k = 0; k < 3 && x < clip.right; ++k) {
                int a = parts[k][0], len = parts[k][1];
                if (len <= 0) continue;
                SIZE sz;
                GetTextExtentPoint32W(dc, s + a, len, &sz);
                if (k == 1) {
                    RECT hr = {x, y + S(2), std::min(x + (int)sz.cx, (int)clip.right), y + rh - S(2)};
                    HBRUSH hb = CreateSolidBrush(MatchBg());
                    FillRect(dc, &hr, hb);
                    DeleteObject(hb);
                }
                SetTextColor(dc, t.panelFg);
                ExtTextOutW(dc, x, ty, ETO_CLIPPED, &clip, s + a, (UINT)len, nullptr);
                x += sz.cx;
            }
        }
    }
    BitBlt(hdc, 0, 0, rc.right, rc.bottom, dc, 0, 0, SRCCOPY);
    SelectObject(dc, ob);
    DeleteObject(bmp);
    DeleteDC(dc);
}

void ListContextMenu(int row, int x, int y) {
    HMENU m = CreatePopupMenu();
    if (row >= 0) {
        AppendMenuW(m, MF_STRING, 1, L"Open");
        AppendMenuW(m, MF_STRING, 2, L"Copy Path");
        AppendMenuW(m, MF_STRING, 3, L"Reveal in File Explorer");
        AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    }
    AppendMenuW(m, MF_STRING, 4, L"Collapse All");
    AppendMenuW(m, MF_STRING, 5, L"Expand All");
    int cmd = TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, 0, P.list, nullptr);
    DestroyMenu(m);
    if (cmd == 4 || cmd == 5) { SetAllCollapsed(cmd == 4); return; }
    if (row < 0 || row >= (int)P.rows.size()) return;
    std::wstring full = JoinPath(P.root, P.files[(size_t)P.rows[(size_t)row].file].rel);
    if (cmd == 1) OpenMatch(row);
    else if (cmd == 2) SetClipboardText(P.list, full);
    else if (cmd == 3) RevealInExplorer(full);
}

void FocusEditor() {
    EditorView* ed = ActiveEditor();
    if (ed) SetFocus(ed->hwnd);
}

LRESULT CALLBACK ListProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(h, &ps);
        PaintList(dc);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_ERASEBKGND: return 1;
    case WM_SIZE: UpdateListScroll(); InvalidateRect(h, nullptr, FALSE); return 0;
    case WM_SETFOCUS:
        if (P.sel < 0 && !P.rows.empty()) Select(0);
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    case WM_KILLFOCUS: InvalidateRect(h, nullptr, FALSE); return 0;
    case WM_GETDLGCODE: return DLGC_WANTARROWS | DLGC_WANTCHARS;
    case WM_MOUSEMOVE: {
        int i = P.top + GET_Y_LPARAM(l) / RowH();
        if (i >= (int)P.rows.size()) i = -1;
        if (i != P.hover) {
            P.hover = i;
            InvalidateRect(h, nullptr, FALSE);
        }
        if (!P.tracking) {
            TRACKMOUSEEVENT tme = {sizeof(tme), TME_LEAVE, h, 0};
            TrackMouseEvent(&tme);
            P.tracking = true;
        }
        return 0;
    }
    case WM_MOUSELEAVE:
        P.tracking = false;
        P.hover = -1;
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    case WM_LBUTTONDOWN: {
        int i = P.top + GET_Y_LPARAM(l) / RowH();
        if (i >= (int)P.rows.size()) {
            SetFocus(h);
            return 0;
        }
        Select(i);
        SetFocus(h);
        if (P.rows[(size_t)i].match < 0) ToggleCollapse(P.rows[(size_t)i].file);
        else OpenMatch(i);
        return 0;
    }
    case WM_RBUTTONDOWN: {
        int i = P.top + GET_Y_LPARAM(l) / RowH();
        if (i < (int)P.rows.size()) Select(i);
        SetFocus(h);
        return 0;
    }
    case WM_CONTEXTMENU: {
        int x = GET_X_LPARAM(l), y = GET_Y_LPARAM(l);
        if (x == -1 && y == -1) {
            POINT pt = {S(30), (std::max(P.sel, P.top) - P.top + 1) * RowH()};
            ClientToScreen(h, &pt);
            x = pt.x;
            y = pt.y;
        }
        ListContextMenu(P.sel, x, y);
        return 0;
    }
    case WM_MOUSEWHEEL: {
        int d = GET_WHEEL_DELTA_WPARAM(w);
        P.top -= d / WHEEL_DELTA * 3;
        UpdateListScroll();
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    }
    case WM_VSCROLL: {
        SCROLLINFO si = {sizeof(si), SIF_ALL};
        GetScrollInfo(h, SB_VERT, &si);
        switch (LOWORD(w)) {
        case SB_LINEUP: P.top--; break;
        case SB_LINEDOWN: P.top++; break;
        case SB_PAGEUP: P.top -= (int)si.nPage; break;
        case SB_PAGEDOWN: P.top += (int)si.nPage; break;
        case SB_THUMBTRACK: case SB_THUMBPOSITION: P.top = si.nTrackPos; break;
        }
        UpdateListScroll();
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    }
    case WM_KEYDOWN: {
        int n = (int)P.rows.size();
        int i = P.sel;
        switch (w) {
        case VK_DOWN: Select(i + 1); break;
        case VK_UP:
            if (i <= 0) SetFocus(P.query);
            else Select(i - 1);
            break;
        case VK_NEXT: Select(i + VisibleRows()); break;
        case VK_PRIOR: Select(i - VisibleRows()); break;
        case VK_HOME: Select(0); break;
        case VK_END: Select(n - 1); break;
        case VK_RETURN:
            if (i >= 0 && i < n) {
                if (P.rows[(size_t)i].match < 0) ToggleCollapse(P.rows[(size_t)i].file);
                else OpenMatch(i);
            }
            break;
        case VK_LEFT:
            if (i >= 0 && i < n) {
                Row r = P.rows[(size_t)i];
                if (r.match >= 0) {
                    while (i > 0 && P.rows[(size_t)i].match >= 0) --i;
                    Select(i);
                } else if (!P.files[(size_t)r.file].collapsed) {
                    ToggleCollapse(r.file);
                }
            }
            break;
        case VK_RIGHT:
            if (i >= 0 && i < n && P.rows[(size_t)i].match < 0 && P.files[(size_t)P.rows[(size_t)i].file].collapsed)
                ToggleCollapse(P.rows[(size_t)i].file);
            break;
        case VK_TAB: SetFocus(GetKeyState(VK_SHIFT) < 0 ? P.exclude : P.query); break;
        case VK_ESCAPE: FocusEditor(); break;
        }
        return 0;
    }
    case WM_CHAR: return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

// ---------------- panel ----------------

LRESULT CALLBACK EditSub(HWND h, UINT m, WPARAM w, LPARAM l, UINT_PTR, DWORD_PTR) {
    if (m == WM_KEYDOWN) {
        switch (w) {
        case VK_RETURN: StartSearch(); return 0;
        case VK_ESCAPE: FocusEditor(); return 0;
        case VK_DOWN:
            if (h == P.query && !P.rows.empty()) {
                SetFocus(P.list);
                return 0;
            }
            break;
        case VK_TAB: {
            bool back = GetKeyState(VK_SHIFT) < 0;
            HWND order[] = {P.query, P.include, P.exclude, P.list};
            int k = h == P.query ? 0 : h == P.include ? 1 : 2;
            SetFocus(order[(k + (back ? 3 : 1)) % 4]);
            return 0;
        }
        case 'A':
            if (GetKeyState(VK_CONTROL) < 0) {
                SendMessageW(h, EM_SETSEL, 0, -1);
                return 0;
            }
            break;
        }
    }
    if (m == WM_CHAR && (w == VK_RETURN || w == VK_ESCAPE || w == VK_TAB || w == 1)) return 0;
    return DefSubclassProc(h, m, w, l);
}

void LayoutPanel() {
    RECT rc;
    GetClientRect(P.hwnd, &rc);
    int W = rc.right, pad = S(8), bw = S(26), eh = S(24);
    MoveWindow(P.rerun, W - pad - 2 * bw, S(3), bw, bw - S(2), TRUE);
    MoveWindow(P.clear, W - pad - bw, S(3), bw, bw - S(2), TRUE);
    int y = S(30);
    int ew = std::max(S(40), W - 2 * pad - 2 * bw - S(4));
    MoveWindow(P.query, pad, y, ew, eh, TRUE);
    MoveWindow(P.caseBtn, pad + ew + S(4), y, bw, eh, TRUE);
    MoveWindow(P.wordBtn, pad + ew + S(4) + bw, y, bw, eh, TRUE);
    y += eh + S(6);
    MoveWindow(P.include, pad, y, std::max(S(40), W - 2 * pad), eh, TRUE);
    y += eh + S(6);
    MoveWindow(P.exclude, pad, y, std::max(S(40), W - 2 * pad), eh, TRUE);
    y += eh + S(28);
    MoveWindow(P.list, 0, y, W, std::max(0, (int)rc.bottom - y), TRUE);
    InvalidateRect(P.hwnd, nullptr, TRUE);
}

LRESULT CALLBACK PanelProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_SIZE: LayoutPanel(); return 0;
    case WM_ERASEBKGND: {
        RECT rc;
        GetClientRect(h, &rc);
        HBRUSH b = CreateSolidBrush(g_theme.panelBg);
        FillRect((HDC)w, &rc, b);
        DeleteObject(b);
        return 1;
    }
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(h, &ps);
        RECT rc;
        GetClientRect(h, &rc);
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, g_theme.panelFgDim);
        HGDIOBJ o = SelectObject(dc, g_uiFont);
        RECT tr = {S(10), 0, rc.right - S(70), S(30)};
        DrawTextW(dc, L"SEARCH", -1, &tr, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
        RECT sr = StatusRect();
        DrawTextW(dc, P.status.c_str(), -1, &sr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX);
        SelectObject(dc, o);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_CTLCOLOREDIT: {
        HDC dc = (HDC)w;
        SetTextColor(dc, g_theme.panelFg);
        SetBkColor(dc, g_theme.inputBg);
        return (LRESULT)P.inputBrush;
    }
    case WM_DRAWITEM: {
        auto* dis = (DRAWITEMSTRUCT*)l;
        bool toggled = (dis->hwndItem == P.caseBtn && P.matchCase) || (dis->hwndItem == P.wordBtn && P.wholeWord);
        DrawFlatButton(dis, false, toggled);
        return TRUE;
    }
    case WM_TIMER:
        if (w == kTimerDebounce) {
            KillTimer(h, kTimerDebounce);
            StartSearch();
        } else if (w == kTimerDrain) {
            Drain();
        }
        return 0;
    case WM_COMMAND:
        switch (LOWORD(w)) {
        case IDC_QUERY:
        case IDC_INCLUDE:
        case IDC_EXCLUDE:
            if (HIWORD(w) == EN_CHANGE) SetTimer(h, kTimerDebounce, 300, nullptr);
            break;
        case IDC_CASE:
            P.matchCase = !P.matchCase;
            InvalidateRect(P.caseBtn, nullptr, FALSE);
            StartSearch();
            break;
        case IDC_WORD:
            P.wholeWord = !P.wholeWord;
            InvalidateRect(P.wordBtn, nullptr, FALSE);
            StartSearch();
            break;
        case IDC_RERUN: StartSearch(); break;
        case IDC_CLEAR:
            SetWindowTextW(P.query, L"");
            ClearResults();
            SetFocus(P.query);
            break;
        }
        return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

HWND MakeEdit(int id, const wchar_t* cue) {
    HWND e = CreateWindowExW(0, L"EDIT", L"", WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL | WS_BORDER, 0, 0, 0, 0, P.hwnd,
                             (HMENU)(INT_PTR)id, g_hinst, nullptr);
    SendMessageW(e, WM_SETFONT, (WPARAM)g_uiFont, FALSE);
    SendMessageW(e, EM_SETCUEBANNER, TRUE, (LPARAM)cue);
    SetWindowSubclass(e, EditSub, 1, 0);
    return e;
}

void AddTip(HWND tip, HWND ctl, const wchar_t* text) {
    TOOLINFOW ti = {sizeof(ti)};
    ti.uFlags = TTF_IDISHWND | TTF_SUBCLASS;
    ti.hwnd = P.hwnd;
    ti.uId = (UINT_PTR)ctl;
    ti.lpszText = (LPWSTR)text;
    SendMessageW(tip, TTM_ADDTOOLW, 0, (LPARAM)&ti);
}

}  // namespace

namespace Search {

HWND Create(HWND parent) {
    WNDCLASSEXW wc = {sizeof(wc)};
    wc.lpfnWndProc = PanelProc;
    wc.hInstance = g_hinst;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = L"MySearchPanel";
    RegisterClassExW(&wc);
    wc.lpfnWndProc = ListProc;
    wc.lpszClassName = L"MySearchList";
    RegisterClassExW(&wc);

    P.inputBrush = CreateSolidBrush(g_theme.inputBg);
    P.hwnd = CreateWindowExW(0, L"MySearchPanel", L"", WS_CHILD | WS_CLIPCHILDREN, 0, 0, 0, 0, parent, nullptr, g_hinst, nullptr);
    P.query = MakeEdit(IDC_QUERY, L"Search");
    P.include = MakeEdit(IDC_INCLUDE, L"Files to include (e.g. *.cpp, src)");
    P.exclude = MakeEdit(IDC_EXCLUDE, L"Files to exclude");
    P.caseBtn = MakeButton(P.hwnd, IDC_CASE, L"Aa");
    P.wordBtn = MakeButton(P.hwnd, IDC_WORD, L"ab|");
    P.rerun = MakeButton(P.hwnd, IDC_RERUN, L"\xE72C");
    P.clear = MakeButton(P.hwnd, IDC_CLEAR, L"\xE894");
    P.list = CreateWindowExW(0, L"MySearchList", L"", WS_CHILD | WS_VISIBLE | WS_VSCROLL | WS_TABSTOP, 0, 0, 0, 0, P.hwnd, nullptr,
                             g_hinst, nullptr);
    HWND tip = CreateWindowExW(WS_EX_TOPMOST, TOOLTIPS_CLASSW, nullptr, WS_POPUP | TTS_ALWAYSTIP | TTS_NOPREFIX, 0, 0, 0, 0,
                               P.hwnd, nullptr, g_hinst, nullptr);
    AddTip(tip, P.caseBtn, L"Match Case");
    AddTip(tip, P.wordBtn, L"Match Whole Word");
    AddTip(tip, P.rerun, L"Search Again");
    AddTip(tip, P.clear, L"Clear Search Results");
    LayoutPanel();
    UpdateStatus();
    return P.hwnd;
}

void Focus(const std::wstring& seed) {
    if (!seed.empty() && seed != GetText(P.query)) {
        SetWindowTextW(P.query, seed.c_str());
        StartSearch();
    } else if (!P.job && GetWindowTextLengthW(P.query) > 0) {
        StartSearch();
    }
    SetFocus(P.query);
    SendMessageW(P.query, EM_SETSEL, 0, -1);
}

void SetRoot(const std::wstring& folder) {
    if (folder == P.root && P.job) return;
    ClearResults();
    P.root = folder;
    UpdateStatus();
}

void ThemeChanged() {
    if (P.inputBrush) DeleteObject(P.inputBrush);
    P.inputBrush = CreateSolidBrush(g_theme.inputBg);
    for (HWND e : {P.query, P.include, P.exclude}) SetWindowTheme(e, g_settings.dark ? L"DarkMode_CFD" : L"Explorer", nullptr);
    SetWindowTheme(P.list, g_settings.dark ? L"DarkMode_Explorer" : L"Explorer", nullptr);
    LayoutPanel();
    RedrawWindow(P.hwnd, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_FRAME);
}

}  // namespace Search
