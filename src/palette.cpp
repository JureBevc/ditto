// Command palette / quick open (F1, Ctrl+Shift+P, Ctrl+P), modelled on VS Code:
//   ">query"  run a command      "query"  open a file from the folder      ":N"  go to line N
#include "main_window.h"
#include "resource.h"

namespace {

struct Cmd {
    const wchar_t* label;
    int id;
    const wchar_t* key;
};

const Cmd kCommands[] = {
    {L"File: New File", ID_FILE_NEW, L"Ctrl+N"},
    {L"File: Open File...", ID_FILE_OPEN, L"Ctrl+O"},
    {L"File: Open Folder...", ID_FILE_OPENFOLDER, L"Ctrl+Shift+O"},
    {L"File: Save", ID_FILE_SAVE, L"Ctrl+S"},
    {L"File: Save As...", ID_FILE_SAVEAS, L"Ctrl+Shift+S"},
    {L"File: Save All", ID_FILE_SAVEALL, L""},
    {L"File: Close Editor", ID_FILE_CLOSETAB, L"Ctrl+W"},
    {L"File: Close Folder", ID_FILE_CLOSEFOLDER, L""},
    {L"File: Reveal in File Explorer", ID_FILE_REVEAL, L"Alt+Shift+R"},
    {L"File: Exit", ID_FILE_EXIT, L"Alt+F4"},
    {L"Edit: Undo", ID_EDIT_UNDO, L"Ctrl+Z"},
    {L"Edit: Redo", ID_EDIT_REDO, L"Ctrl+Y"},
    {L"Edit: Select All", ID_EDIT_SELECTALL, L"Ctrl+A"},
    {L"Edit: Find", ID_FIND_SHOW, L"Ctrl+F"},
    {L"Edit: Replace", ID_FIND_REPLACE, L"Ctrl+H"},
    {L"Edit: Find Next", ID_FIND_NEXT, L"F3"},
    {L"Edit: Find Previous", ID_FIND_PREV, L"Shift+F3"},
    {L"Go to Line...", ID_PALETTE_GOTO, L"Ctrl+G"},
    {L"Go to File...", ID_PALETTE_FILES, L"Ctrl+P"},
    {L"View: Show Explorer", ID_VIEW_EXPLORER, L"Ctrl+Shift+E"},
    {L"View: Show Source Control", ID_VIEW_SCM, L"Ctrl+Shift+G"},
    {L"View: Toggle Side Bar", ID_VIEW_SIDEBAR, L"Ctrl+B"},
    {L"View: Toggle Output Panel", ID_VIEW_OUTPUT, L"Ctrl+`"},
    {L"View: Toggle Word Wrap", ID_VIEW_WRAP, L"Alt+Z"},
    {L"View: Toggle Dark Theme", ID_VIEW_DARK, L""},
    {L"View: Change Font...", ID_VIEW_FONT, L""},
    {L"View: Zoom In", ID_VIEW_ZOOMIN, L"Ctrl+="},
    {L"View: Zoom Out", ID_VIEW_ZOOMOUT, L"Ctrl+-"},
    {L"View: Reset Zoom", ID_VIEW_ZOOMRESET, L"Ctrl+0"},
    {L"View: Next Editor", ID_VIEW_NEXTTAB, L"Ctrl+Tab"},
    {L"View: Previous Editor", ID_VIEW_PREVTAB, L"Ctrl+Shift+Tab"},
    {L"Git: Initialize Repository", ID_GIT_INIT, L""},
    {L"Git: Clone...", ID_GIT_CLONE, L""},
    {L"Git: Publish to GitHub...", ID_GIT_PUBLISH, L""},
    {L"Git: Commit", ID_GIT_COMMIT, L"Ctrl+Enter"},
    {L"Git: Commit Staged", ID_GIT_COMMIT_STAGED, L""},
    {L"Git: Commit All", ID_GIT_COMMIT_ALL, L""},
    {L"Git: Commit (Amend)", ID_GIT_COMMIT_AMEND, L""},
    {L"Git: Undo Last Commit", ID_GIT_UNDO_COMMIT, L""},
    {L"Git: Stage All Changes", ID_GIT_STAGE_ALL, L""},
    {L"Git: Unstage All Changes", ID_GIT_UNSTAGE_ALL, L""},
    {L"Git: Discard All Changes", ID_GIT_DISCARD_ALL, L""},
    {L"Git: Pull", ID_GIT_PULL, L""},
    {L"Git: Push", ID_GIT_PUSH, L""},
    {L"Git: Sync", ID_GIT_SYNC, L""},
    {L"Git: Fetch", ID_GIT_FETCH, L""},
    {L"Git: Force Push (with lease)", ID_GIT_PUSH_FORCE, L""},
    {L"Git: Checkout to...", ID_GIT_CHECKOUT, L""},
    {L"Git: Create Branch...", ID_GIT_BRANCH_CREATE, L""},
    {L"Git: Delete Branch...", ID_GIT_BRANCH_DELETE, L""},
    {L"Git: Merge Branch...", ID_GIT_MERGE, L""},
    {L"Git: Stash", ID_GIT_STASH, L""},
    {L"Git: Pop Stash...", ID_GIT_STASH_POP, L""},
    {L"Git: Add Remote...", ID_GIT_REMOTE_ADD, L""},
    {L"Git: Remove Remote...", ID_GIT_REMOTE_REMOVE, L""},
    {L"Git: View History", ID_GIT_LOG, L""},
    {L"Git: Refresh", ID_GIT_REFRESH, L"F5"},
    {L"Help: About", ID_HELP_ABOUT, L""},
};

enum Kind { K_CMD, K_FILE, K_GOTO, K_INFO };

struct Item {
    Kind kind;
    std::wstring label, detail, key;
    int cmd = 0;
    std::wstring path;
    std::vector<int> hlLabel, hlDetail;  // highlighted character indexes
    int score = 0;
};

HWND g_pop, g_edit;
std::vector<Item> g_items;
int g_sel = 0, g_top = 0;
HBRUSH g_inputBrush;
bool g_hiding = false;

// file index for quick open (filled by a background thread)
std::vector<std::wstring> g_files;  // paths relative to g_filesRoot
std::wstring g_filesRoot;
bool g_filesLoading = false;
std::atomic<uint64_t> g_filesGen{0};

const int kMaxRows = 14;
int EditH() { return S(40); }
int RowH() { return S(26); }

bool IsSep(wchar_t c) { return c == L' ' || c == L'/' || c == L'\\' || c == L':' || c == L'.' || c == L'-' || c == L'_'; }

// Fuzzy subsequence match (case-insensitive). Returns -1 if no match; positions receive matched indexes.
int Fuzzy(const std::wstring& candLower, const std::wstring& q, std::vector<int>* pos) {
    if (q.empty()) return 0;
    int score = 0;
    size_t ci = 0;
    int prev = -2;
    for (wchar_t qc : q) {
        // prefer a match at a word start ahead of the plain next occurrence
        size_t found = std::wstring::npos, wordStart = std::wstring::npos;
        for (size_t k = ci; k < candLower.size(); ++k) {
            if (candLower[k] != qc) continue;
            if (found == std::wstring::npos) found = k;
            if (k == 0 || IsSep(candLower[k - 1])) { wordStart = k; break; }
            if ((int)k == prev + 1) break;  // keep contiguous runs
        }
        if (found == std::wstring::npos) return -1;
        size_t k = found;
        if ((int)found != prev + 1 && wordStart != std::wstring::npos) k = wordStart;
        score += 1;
        if ((int)k == prev + 1) score += 6;
        if (k == 0 || IsSep(candLower[k - 1])) score += 8;
        score -= (int)std::min<size_t>(k - ci, 10) / 3;
        if (pos) pos->push_back((int)k);
        prev = (int)k;
        ci = k + 1;
    }
    return score;
}

std::wstring Query() {
    int n = GetWindowTextLengthW(g_edit);
    std::wstring s((size_t)n + 1, L'\0');
    GetWindowTextW(g_edit, &s[0], n + 1);
    s.resize((size_t)n);
    return s;
}

std::wstring Squash(const std::wstring& s) {  // lowercase, drop spaces
    std::wstring o;
    for (wchar_t c : LowerW(s))
        if (c != L' ') o += c;
    return o;
}

void StartFileScan() {
    std::wstring root = M.folder;
    if (root.empty()) return;
    if (g_filesLoading && root == g_filesRoot) return;
    if (root != g_filesRoot) g_files.clear();
    g_filesRoot = root;
    g_filesLoading = true;
    uint64_t gen = ++g_filesGen;
    std::thread([root, gen]() {
        auto files = std::make_shared<std::vector<std::wstring>>();
        std::vector<std::wstring> stack = {L""};
        while (!stack.empty() && files->size() < 500000) {
            if (g_filesGen != gen) return;
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
                    files->push_back(r);
                }
            } while (FindNextFileW(h, &fd));
            FindClose(h);
        }
        std::sort(files->begin(), files->end(), [](const std::wstring& a, const std::wstring& b) {
            return CompareStringOrdinal(a.c_str(), -1, b.c_str(), -1, TRUE) == CSTR_LESS_THAN;
        });
        RunOnUi([files, gen, root]() {
            if (gen != g_filesGen) return;
            g_files = std::move(*files);
            g_filesRoot = root;
            g_filesLoading = false;
            if (g_pop && IsWindowVisible(g_pop)) Palette::Refresh();
        });
    }).detach();
}

void Resize() {
    RECT mr;
    GetClientRect(M.hwnd, &mr);
    POINT tl = {0, 0};
    ClientToScreen(M.hwnd, &tl);
    int w = std::min(S(640), (int)mr.right - S(40));
    int rows = std::min((int)g_items.size(), kMaxRows);
    int h = EditH() + rows * RowH() + S(6);
    int x = tl.x + ((int)mr.right - w) / 2;
    int y = tl.y + S(6);
    SetWindowPos(g_pop, HWND_TOP, x, y, w, h, SWP_NOACTIVATE);
    MoveWindow(g_edit, S(8), S(8), w - S(16) - 2, EditH() - S(14), TRUE);
    InvalidateRect(g_pop, nullptr, TRUE);
}

void EnsureVisible() {
    if (g_sel < g_top) g_top = g_sel;
    if (g_sel >= g_top + kMaxRows) g_top = g_sel - kMaxRows + 1;
    g_top = std::max(0, g_top);
}

void Execute(int i) {
    if (i < 0 || i >= (int)g_items.size()) return;
    Item it = g_items[(size_t)i];
    if (it.kind == K_INFO && !it.cmd) return;
    Palette::Hide();
    switch (it.kind) {
    case K_CMD:
    case K_INFO: PostMessageW(M.hwnd, WM_COMMAND, (WPARAM)it.cmd, 0); break;
    case K_FILE: App::OpenFile(it.path); break;
    case K_GOTO: {
        EditorView* ed = ActiveEditor();
        if (ed) {
            ed->GotoLine((uint64_t)it.cmd);
            SetFocus(ed->hwnd);
        }
        break;
    }
    }
}

int DrawHighlighted(HDC dc, const std::wstring& s, const std::vector<int>& hl, RECT r, COLORREF fg, COLORREF hi, HFONT f, HFONT fb) {
    // draw consecutive runs: matched characters bold + accent, the rest normal
    std::vector<bool> mark(s.size(), false);
    for (int k : hl)
        if (k >= 0 && k < (int)s.size()) mark[(size_t)k] = true;
    TEXTMETRICW tm;
    SelectObject(dc, f);
    GetTextMetricsW(dc, &tm);
    int x = r.left, y = r.top + (r.bottom - r.top - tm.tmHeight) / 2;
    for (size_t a = 0; a < s.size() && x < r.right;) {
        size_t b = a;
        while (b < s.size() && mark[b] == mark[a]) ++b;
        SelectObject(dc, mark[a] ? fb : f);
        SetTextColor(dc, mark[a] ? hi : fg);
        ExtTextOutW(dc, x, y, ETO_CLIPPED, &r, s.c_str() + a, (UINT)(b - a), nullptr);
        SIZE sz;
        GetTextExtentPoint32W(dc, s.c_str() + a, (int)(b - a), &sz);
        x += sz.cx;
        a = b;
    }
    return x;
}

void Paint(HDC hdc) {
    RECT rc;
    GetClientRect(g_pop, &rc);
    HDC dc = CreateCompatibleDC(hdc);
    HBITMAP bmp = CreateCompatibleBitmap(hdc, rc.right, rc.bottom);
    HGDIOBJ ob = SelectObject(dc, bmp);
    const Theme& t = g_theme;
    HBRUSH bg = CreateSolidBrush(t.panelBg);
    FillRect(dc, &rc, bg);
    DeleteObject(bg);
    HBRUSH border = CreateSolidBrush(t.border);
    FrameRect(dc, &rc, border);
    RECT er;
    GetWindowRect(g_edit, &er);
    MapWindowPoints(nullptr, g_pop, (POINT*)&er, 2);
    InflateRect(&er, 2, 2);
    HBRUSH acc = CreateSolidBrush(t.accent);
    FrameRect(dc, &er, acc);
    DeleteObject(acc);
    DeleteObject(border);
    SetBkMode(dc, TRANSPARENT);
    int y = EditH();
    for (int i = g_top; i < (int)g_items.size() && i < g_top + kMaxRows; ++i, y += RowH()) {
        const Item& it = g_items[(size_t)i];
        RECT row = {1, y, rc.right - 1, y + RowH()};
        if (i == g_sel) {
            HBRUSH sb = CreateSolidBrush(t.selBg);
            FillRect(dc, &row, sb);
            DeleteObject(sb);
        }
        int x = S(12);
        RECT kr = {rc.right - S(150), y, rc.right - S(12), y + RowH()};
        int right = it.key.empty() ? rc.right - S(12) : kr.left - S(8);
        COLORREF fg = it.kind == K_INFO ? t.panelFgDim : t.panelFg;
        RECT lr = {x, y, right, y + RowH()};
        int endX = DrawHighlighted(dc, it.label, it.hlLabel, lr, fg, t.accent, g_uiFont, g_uiFontBold);
        if (!it.detail.empty() && endX + S(16) < right) {
            RECT dr = {endX + S(10), y, right, y + RowH()};
            DrawHighlighted(dc, it.detail, it.hlDetail, dr, t.panelFgDim, t.accent, g_uiFont, g_uiFontBold);
        }
        if (!it.key.empty()) {
            SelectObject(dc, g_uiFont);
            SetTextColor(dc, t.panelFgDim);
            DrawTextW(dc, it.key.c_str(), -1, &kr, DT_RIGHT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX);
        }
    }
    BitBlt(hdc, 0, 0, rc.right, rc.bottom, dc, 0, 0, SRCCOPY);
    SelectObject(dc, ob);
    DeleteObject(bmp);
    DeleteDC(dc);
}

LRESULT CALLBACK EditSub(HWND h, UINT m, WPARAM w, LPARAM l, UINT_PTR, DWORD_PTR) {
    if (m == WM_KEYDOWN) {
        int n = (int)g_items.size();
        switch (w) {
        case VK_ESCAPE: Palette::Hide(); return 0;
        case VK_RETURN: Execute(g_sel); return 0;
        case VK_DOWN: if (n) g_sel = (g_sel + 1) % n; break;
        case VK_UP: if (n) g_sel = (g_sel + n - 1) % n; break;
        case VK_NEXT: g_sel = std::min(n - 1, g_sel + kMaxRows); break;
        case VK_PRIOR: g_sel = std::max(0, g_sel - kMaxRows); break;
        default: return DefSubclassProc(h, m, w, l);
        }
        EnsureVisible();
        InvalidateRect(g_pop, nullptr, FALSE);
        return 0;
    }
    if (m == WM_CHAR && (w == VK_RETURN || w == VK_ESCAPE || w == 1)) return 0;
    if (m == WM_KEYDOWN && w == 'A' && GetKeyState(VK_CONTROL) < 0) { SendMessageW(h, EM_SETSEL, 0, -1); return 0; }
    return DefSubclassProc(h, m, w, l);
}

LRESULT CALLBACK PopProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(h, &ps);
        Paint(dc);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_ERASEBKGND: return 1;
    case WM_ACTIVATE:
        if (LOWORD(w) == WA_INACTIVE && !g_hiding) Palette::Hide(false);
        return 0;
    case WM_CTLCOLOREDIT:
        SetTextColor((HDC)w, g_theme.panelFg);
        SetBkColor((HDC)w, g_theme.inputBg);
        return (LRESULT)g_inputBrush;
    case WM_COMMAND:
        if ((HWND)l == g_edit && HIWORD(w) == EN_CHANGE) Palette::Refresh();
        return 0;
    case WM_MOUSEMOVE: {
        int i = g_top + (GET_Y_LPARAM(l) - EditH()) / RowH();
        if (GET_Y_LPARAM(l) >= EditH() && i < (int)g_items.size() && i != g_sel) {
            g_sel = i;
            InvalidateRect(h, nullptr, FALSE);
        }
        return 0;
    }
    case WM_LBUTTONDOWN: {
        int y = GET_Y_LPARAM(l);
        if (y >= EditH()) Execute(g_top + (y - EditH()) / RowH());
        return 0;
    }
    case WM_MOUSEWHEEL: {
        int d = GET_WHEEL_DELTA_WPARAM(w) > 0 ? -3 : 3;
        g_top = std::max(0, std::min(g_top + d, (int)g_items.size() - kMaxRows));
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    }
    }
    return DefWindowProcW(h, m, w, l);
}

void Create() {
    WNDCLASSEXW wc = {sizeof(wc)};
    wc.lpfnWndProc = PopProc;
    wc.hInstance = g_hinst;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = L"MyPalette";
    wc.style = CS_DROPSHADOW;
    RegisterClassExW(&wc);
    g_pop = CreateWindowExW(WS_EX_TOOLWINDOW, L"MyPalette", L"", WS_POPUP | WS_CLIPCHILDREN, 0, 0, 10, 10, M.hwnd, nullptr, g_hinst, nullptr);
    g_edit = CreateWindowExW(0, L"EDIT", L"", WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL, 0, 0, 10, 10, g_pop, nullptr, g_hinst, nullptr);
    SetWindowSubclass(g_edit, EditSub, 1, 0);
}

}  // namespace

namespace Palette {

void Show(const std::wstring& prefix) {
    if (!g_pop) Create();
    if (g_inputBrush) DeleteObject(g_inputBrush);
    g_inputBrush = CreateSolidBrush(g_theme.inputBg);
    LOGFONTW lf;
    GetObjectW(g_uiFont, sizeof(lf), &lf);
    lf.lfHeight = lf.lfHeight * 5 / 4;
    static HFONT big = nullptr;
    if (big) DeleteObject(big);
    big = CreateFontIndirectW(&lf);
    SendMessageW(g_edit, WM_SETFONT, (WPARAM)big, FALSE);
    SetWindowTextW(g_edit, prefix.c_str());
    SendMessageW(g_edit, EM_SETSEL, prefix.size(), prefix.size());
    if (!M.folder.empty()) StartFileScan();
    Refresh();
    ShowWindow(g_pop, SW_SHOW);
    SetFocus(g_edit);
}

void Hide(bool restoreFocus) {
    if (!g_pop || !IsWindowVisible(g_pop)) return;
    g_hiding = true;
    ShowWindow(g_pop, SW_HIDE);
    g_hiding = false;
    if (restoreFocus) {
        SetActiveWindow(M.hwnd);
        if (M.active >= 0) SetFocus(M.tabs[(size_t)M.active].hwnd);
    }
}

void Refresh() {
    std::wstring raw = Query();
    g_items.clear();
    if (!raw.empty() && raw[0] == L'>') {
        std::wstring q = Squash(raw.substr(1));
        for (auto& c : kCommands) {
            Item it;
            it.kind = K_CMD;
            it.label = c.label;
            it.key = c.key;
            it.cmd = c.id;
            it.score = Fuzzy(LowerW(it.label), q, &it.hlLabel);
            if (it.score >= 0) g_items.push_back(it);
        }
        for (size_t i = 0; i < g_settings.recentFolders.size(); ++i) {
            Item it;
            it.kind = K_CMD;
            it.label = L"File: Open Recent: " + FileNameOf(g_settings.recentFolders[i]);
            it.detail = g_settings.recentFolders[i];
            it.cmd = ID_FILE_RECENT0 + (int)i;
            it.score = Fuzzy(LowerW(it.label), q, &it.hlLabel);
            if (it.score >= 0) g_items.push_back(it);
        }
        if (!q.empty())
            std::stable_sort(g_items.begin(), g_items.end(), [](const Item& a, const Item& b) { return a.score > b.score; });
        if (g_items.empty()) g_items.push_back(Item{K_INFO, L"No matching commands"});
    } else if (!raw.empty() && raw[0] == L':') {
        EditorView* ed = ActiveEditor();
        if (!ed) {
            g_items.push_back(Item{K_INFO, L"Open a file to go to a line"});
        } else {
            uint64_t total = ed->buf->LineCount();
            long long n = _wtoi64(Trim(raw.substr(1)).c_str());
            Item it;
            if (n > 0) {
                it.kind = K_GOTO;
                it.cmd = (int)std::min<long long>(n, (long long)total) - 1;
                it.label = L"Go to line " + std::to_wstring(it.cmd + 1);
            } else {
                it.kind = K_INFO;
                it.label = L"Current line: " + std::to_wstring(ed->CaretLine() + 1) + L". Type a line number between 1 and " +
                           std::to_wstring(total) + L" to navigate to.";
            }
            g_items.push_back(it);
        }
    } else {
        std::wstring q = Squash(raw);
        if (M.folder.empty()) {
            Item it{K_INFO, L"Open a folder to search for files"};
            it.cmd = ID_FILE_OPENFOLDER;
            g_items.push_back(it);
        } else {
            std::vector<Item> res;
            for (const auto& rel : g_files) {
                size_t sl = rel.find_last_of(L'\\');
                size_t fn = sl == std::wstring::npos ? 0 : sl + 1;
                std::wstring lower = LowerW(rel);
                std::vector<int> pos;
                int sc;
                std::vector<int> fpos;
                int fsc = Fuzzy(lower.substr(fn), q, &fpos);  // prefer matches within the file name
                if (fsc >= 0) {
                    sc = fsc + 20;
                    for (int& p : fpos) p += (int)fn;
                    pos = fpos;
                } else {
                    sc = Fuzzy(lower, q, &pos);
                    if (sc < 0) continue;
                }
                sc -= (int)std::min<size_t>(rel.size(), 60) / 12;  // shorter paths first
                Item it;
                it.kind = K_FILE;
                it.label = rel.substr(fn);
                it.detail = fn ? rel.substr(0, fn - 1) : L"";
                it.path = JoinPath(g_filesRoot, rel);
                it.score = sc;
                for (int p : pos) {
                    if (p >= (int)fn) it.hlLabel.push_back(p - (int)fn);
                    else it.hlDetail.push_back(p);
                }
                res.push_back(std::move(it));
                if (q.empty() && res.size() >= 200) break;
            }
            if (!q.empty()) {
                size_t keep = std::min<size_t>(res.size(), 200);
                std::partial_sort(res.begin(), res.begin() + keep, res.end(),
                                  [](const Item& a, const Item& b) { return a.score > b.score; });
                res.resize(keep);
            }
            g_items = std::move(res);
            if (g_items.empty())
                g_items.push_back(Item{K_INFO, g_filesLoading ? L"Searching files..." : L"No matching files"});
        }
        if (raw.empty()) {
            Item hint{K_INFO, L"Type > to run a command, : to go to a line"};
            hint.cmd = 0;
            g_items.insert(g_items.begin(), hint);
        }
    }
    g_sel = 0;
    if (!g_items.empty() && g_items[0].kind == K_INFO && g_items[0].cmd == 0 && g_items.size() > 1) g_sel = 1;
    g_top = 0;
    Resize();
}

}  // namespace Palette
