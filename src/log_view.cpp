// Commit history view: commit list on top, files changed by the selected commit below.
#include "app.h"
#include "dialogs.h"

namespace {

struct Commit {
    std::wstring hash, shortHash, author, date, refs, subject;
    std::vector<std::wstring> parents;
};
struct FileChange {
    std::wstring status, path, origPath;
};

struct Data {
    std::wstring root;
    HWND commits = nullptr, files = nullptr;
    std::vector<Commit> list;
    std::vector<FileChange> changes;
    int selected = -1;
    uint64_t gen = 0;
    std::wstring message = L"Loading history...";
};

Data* From(HWND h) { return (Data*)GetWindowLongPtrW(h, GWLP_USERDATA); }

void ApplyColors(Data* d) {
    for (HWND lv : {d->commits, d->files}) {
        ListView_SetBkColor(lv, g_theme.bg);
        ListView_SetTextBkColor(lv, g_theme.bg);
        ListView_SetTextColor(lv, g_theme.fg);
        SetWindowTheme(lv, g_settings.dark ? L"DarkMode_Explorer" : L"Explorer", nullptr);
        HWND hdr = ListView_GetHeader(lv);
        if (hdr) SetWindowTheme(hdr, g_settings.dark ? L"DarkMode_ItemsView" : L"Explorer", nullptr);
        InvalidateRect(lv, nullptr, TRUE);
    }
}

void LoadFiles(HWND h, int idx) {
    Data* d = From(h);
    if (idx < 0 || idx >= (int)d->list.size()) return;
    d->selected = idx;
    Commit c = d->list[(size_t)idx];
    std::wstring root = d->root;
    uint64_t gen = ++d->gen;
    BgRun([h, gen, c, root]() {
        std::vector<std::wstring> args = {L"diff-tree", L"-r", L"--no-commit-id", L"--name-status", L"-M", L"-z"};
        if (c.parents.empty()) { args.push_back(L"--root"); args.push_back(c.hash); }
        else { args.push_back(c.parents[0]); args.push_back(c.hash); }
        ProcResult r = Git(root, args, nullptr, false);
        auto changes = std::make_shared<std::vector<FileChange>>();
        auto f = SplitNul(r.out);
        for (size_t i = 0; i < f.size(); ++i) {
            if (f[i].empty()) continue;
            FileChange fc;
            fc.status = Utf8ToWide(f[i].substr(0, 1));
            if ((f[i][0] == 'R' || f[i][0] == 'C') && i + 2 < f.size()) {
                fc.origPath = Utf8ToWide(f[i + 1]);
                fc.path = Utf8ToWide(f[i + 2]);
                i += 2;
            } else if (i + 1 < f.size()) {
                fc.path = Utf8ToWide(f[i + 1]);
                i += 1;
            }
            changes->push_back(fc);
        }
        RunOnUi([h, gen, changes]() {
            if (!IsWindow(h)) return;
            Data* d = From(h);
            if (!d || d->gen != gen) return;
            d->changes = std::move(*changes);
            ListView_SetItemCountEx(d->files, (int)d->changes.size(), 0);
            InvalidateRect(d->files, nullptr, TRUE);
        });
    });
}

void LoadCommits(HWND h) {
    Data* d = From(h);
    std::wstring root = d->root;
    uint64_t gen = ++d->gen;
    BgRun([h, gen, root]() {
        ProcResult r = Git(root, {L"log", L"-n", L"5000", L"--date=format:%Y-%m-%d %H:%M",
                                  L"--format=%H%x1f%h%x1f%an%x1f%ad%x1f%P%x1f%D%x1f%s%x1e"}, nullptr, false);
        auto list = std::make_shared<std::vector<Commit>>();
        std::wstring msg;
        if (r.code != 0) {
            msg = GitErrorText(r);
            if (msg.find(L"does not have any commits") != std::wstring::npos) msg = L"No commits yet.";
        }
        size_t p = 0;
        const std::string& o = r.out;
        while (p < o.size()) {
            size_t e = o.find('\x1e', p);
            if (e == std::string::npos) break;
            std::string rec = o.substr(p, e - p);
            p = e + 1;
            while (!rec.empty() && (rec[0] == '\n' || rec[0] == '\r')) rec.erase(0, 1);
            std::vector<std::string> parts;
            size_t q = 0;
            for (;;) {
                size_t z = rec.find('\x1f', q);
                parts.push_back(rec.substr(q, z == std::string::npos ? std::string::npos : z - q));
                if (z == std::string::npos) break;
                q = z + 1;
            }
            if (parts.size() < 7) continue;
            Commit c;
            c.hash = Utf8ToWide(parts[0]);
            c.shortHash = Utf8ToWide(parts[1]);
            c.author = Utf8ToWide(parts[2]);
            c.date = Utf8ToWide(parts[3]);
            std::string ps = parts[4];
            size_t s = 0;
            while (s < ps.size()) {
                size_t sp = ps.find(' ', s);
                if (sp == std::string::npos) sp = ps.size();
                if (sp > s) c.parents.push_back(Utf8ToWide(ps.substr(s, sp - s)));
                s = sp + 1;
            }
            c.refs = Utf8ToWide(parts[5]);
            c.subject = Utf8ToWide(parts[6]);
            list->push_back(c);
        }
        RunOnUi([h, gen, list, msg]() {
            if (!IsWindow(h)) return;
            Data* d = From(h);
            if (!d || d->gen != gen) return;
            d->list = std::move(*list);
            d->message = msg;
            d->changes.clear();
            ListView_SetItemCountEx(d->files, 0, 0);
            ListView_SetItemCountEx(d->commits, (int)d->list.size(), 0);
            InvalidateRect(d->commits, nullptr, TRUE);
            InvalidateRect(h, nullptr, TRUE);
            if (!d->list.empty()) {
                ListView_SetItemState(d->commits, 0, LVIS_SELECTED | LVIS_FOCUSED, LVIS_SELECTED | LVIS_FOCUSED);
            }
        });
    });
}

void OpenChange(HWND h, int fi) {
    Data* d = From(h);
    if (d->selected < 0 || fi < 0 || fi >= (int)d->changes.size()) return;
    const Commit& c = d->list[(size_t)d->selected];
    DiffSpec s;
    s.kind = DiffSpec::Commit;
    s.root = d->root;
    s.commit = c.hash;
    s.parent = c.parents.empty() ? L"" : c.parents[0];
    s.path = d->changes[(size_t)fi].path;
    s.origPath = d->changes[(size_t)fi].origPath;
    App::OpenDiff(s);
}

void CommitMenu(HWND h, int idx, POINT pt) {
    Data* d = From(h);
    if (idx < 0 || idx >= (int)d->list.size()) return;
    const Commit c = d->list[(size_t)idx];
    HMENU m = CreatePopupMenu();
    AppendMenuW(m, MF_STRING, 1, L"Checkout (detached)");
    AppendMenuW(m, MF_STRING, 2, L"Create Branch Here...");
    AppendMenuW(m, MF_STRING, 3, L"Revert Commit");
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(m, MF_STRING, 4, L"Copy Commit Hash");
    AppendMenuW(m, MF_STRING, 5, L"Copy Commit Message");
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(m, MF_STRING, 6, L"Refresh");
    int cmd = TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTBUTTON, pt.x, pt.y, 0, h, nullptr);
    DestroyMenu(m);
    auto after = [h](bool, const ProcResult&) { if (IsWindow(h)) LoadCommits(h); };
    switch (cmd) {
    case 1:
        RunGitOp(L"Checkout " + c.shortHash, d->root, {{L"checkout", c.hash}}, true, after);
        break;
    case 2: {
        std::wstring name;
        if (InputBox(h, L"Create Branch", L"New branch name (from " + c.shortHash + L"):", name) && !Trim(name).empty())
            RunGitOp(L"Create branch", d->root, {{L"checkout", L"-b", Trim(name), c.hash}}, true, after);
        break;
    }
    case 3:
        if (MsgBox(h, L"Revert commit " + c.shortHash + L" \"" + c.subject + L"\"?\n\nThis creates a new commit that undoes its changes.",
                   MB_YESNO | MB_ICONQUESTION) == IDYES)
            RunGitOp(L"Revert", d->root, {{L"revert", L"--no-edit", c.hash}}, true, after);
        break;
    case 4: SetClipboardText(h, c.hash); break;
    case 5: SetClipboardText(h, c.subject); break;
    case 6: LoadCommits(h); break;
    }
}

void Layout(HWND h) {
    Data* d = From(h);
    RECT rc;
    GetClientRect(h, &rc);
    int top = S(26);
    int split = top + (rc.bottom - top) * 62 / 100;
    MoveWindow(d->commits, 0, top, rc.right, split - top, TRUE);
    MoveWindow(d->files, 0, split + S(2), rc.right, rc.bottom - split - S(2), TRUE);
    int w = rc.right - GetSystemMetrics(SM_CXVSCROLL) - S(4);
    ListView_SetColumnWidth(d->commits, 0, S(80));
    ListView_SetColumnWidth(d->commits, 2, S(150));
    ListView_SetColumnWidth(d->commits, 3, S(120));
    ListView_SetColumnWidth(d->commits, 1, std::max(S(100), w - S(80) - S(150) - S(120)));
    ListView_SetColumnWidth(d->files, 0, S(60));
    ListView_SetColumnWidth(d->files, 1, std::max(S(100), w - S(60)));
}

HWND MakeList(HWND parent, const std::vector<std::pair<const wchar_t*, int>>& cols) {
    HWND lv = CreateWindowExW(0, WC_LISTVIEWW, L"",
                              WS_CHILD | WS_VISIBLE | LVS_REPORT | LVS_SINGLESEL | LVS_SHOWSELALWAYS | LVS_OWNERDATA,
                              0, 0, 0, 0, parent, nullptr, g_hinst, nullptr);
    ListView_SetExtendedListViewStyle(lv, LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER);
    SendMessageW(lv, WM_SETFONT, (WPARAM)g_uiFont, FALSE);
    for (size_t i = 0; i < cols.size(); ++i) {
        LVCOLUMNW c = {LVCF_TEXT | LVCF_WIDTH};
        c.pszText = (LPWSTR)cols[i].first;
        c.cx = S(cols[i].second);
        ListView_InsertColumn(lv, (int)i, &c);
    }
    return lv;
}

LRESULT CALLBACK Proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_NCCREATE) SetWindowLongPtrW(h, GWLP_USERDATA, (LONG_PTR)((CREATESTRUCTW*)l)->lpCreateParams);
    Data* d = From(h);
    if (!d) return DefWindowProcW(h, m, w, l);
    switch (m) {
    case WM_CREATE:
        d->commits = MakeList(h, {{L"Commit", 80}, {L"Message", 400}, {L"Author", 150}, {L"Date", 120}});
        d->files = MakeList(h, {{L"Status", 60}, {L"File", 500}});
        ApplyColors(d);
        LoadCommits(h);
        return 0;
    case WM_NCDESTROY:
        SetWindowLongPtrW(h, GWLP_USERDATA, 0);
        delete d;
        return DefWindowProcW(h, m, w, l);
    case WM_SIZE: Layout(h); return 0;
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
        rc.bottom = S(26);
        rc.left += S(8);
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, g_theme.panelFgDim);
        HGDIOBJ o = SelectObject(dc, g_uiFont);
        std::wstring t = L"History of " + d->root + L"   \x2014   double-click a file to view its diff, right-click a commit for actions";
        if (!d->message.empty()) t = d->message;
        DrawTextW(dc, t.c_str(), -1, &rc, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX);
        SelectObject(dc, o);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_NOTIFY: {
        auto* nm = (NMHDR*)l;
        if (nm->code == LVN_GETDISPINFOW) {
            auto* di = (NMLVDISPINFOW*)l;
            if (!(di->item.mask & LVIF_TEXT)) return 0;
            static std::wstring tmp;
            tmp.clear();
            if (nm->hwndFrom == d->commits && di->item.iItem < (int)d->list.size()) {
                const Commit& c = d->list[(size_t)di->item.iItem];
                switch (di->item.iSubItem) {
                case 0: tmp = c.shortHash; break;
                case 1: tmp = c.refs.empty() ? c.subject : L"[" + c.refs + L"]  " + c.subject; break;
                case 2: tmp = c.author; break;
                case 3: tmp = c.date; break;
                }
            } else if (nm->hwndFrom == d->files && di->item.iItem < (int)d->changes.size()) {
                const FileChange& f = d->changes[(size_t)di->item.iItem];
                tmp = di->item.iSubItem == 0 ? f.status : (f.origPath.empty() ? f.path : f.origPath + L"  \x2192  " + f.path);
            }
            wcsncpy_s(di->item.pszText, di->item.cchTextMax, tmp.c_str(), _TRUNCATE);
            return 0;
        }
        if (nm->code == LVN_ITEMCHANGED && nm->hwndFrom == d->commits) {
            auto* iv = (NMLISTVIEW*)l;
            if ((iv->uNewState & LVIS_SELECTED) && !(iv->uOldState & LVIS_SELECTED)) LoadFiles(h, iv->iItem);
            return 0;
        }
        if ((nm->code == NM_DBLCLK || nm->code == NM_RETURN) && nm->hwndFrom == d->files) {
            OpenChange(h, ListView_GetNextItem(d->files, -1, LVNI_SELECTED));
            return 0;
        }
        if (nm->code == NM_RCLICK && nm->hwndFrom == d->commits) {
            auto* ia = (NMITEMACTIVATE*)l;
            POINT pt = ia->ptAction;
            ClientToScreen(d->commits, &pt);
            CommitMenu(h, ia->iItem, pt);
            return 0;
        }
        break;
    }
    }
    return DefWindowProcW(h, m, w, l);
}

}  // namespace

namespace LogView {

void Register() {
    WNDCLASSEXW wc = {sizeof(wc)};
    wc.lpfnWndProc = Proc;
    wc.hInstance = g_hinst;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = L"MyLogView";
    RegisterClassExW(&wc);
}

HWND Create(HWND parent, const std::wstring& root) {
    Data* d = new Data();
    d->root = root;
    HWND h = CreateWindowExW(0, L"MyLogView", L"", WS_CHILD | WS_CLIPCHILDREN | WS_CLIPSIBLINGS, 0, 0, 0, 0, parent,
                             nullptr, g_hinst, d);
    if (!h) delete d;
    return h;
}

void Reload(HWND h) {
    if (From(h)) LoadCommits(h);
}

void ThemeChanged(HWND h) {
    if (From(h)) { ApplyColors(From(h)); InvalidateRect(h, nullptr, TRUE); }
}

}  // namespace LogView
