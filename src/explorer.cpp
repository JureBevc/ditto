// Folder tree (lazy loaded) in the sidebar.
#include "app.h"
#include "dialogs.h"
#include <shlwapi.h>
#include <set>

namespace {

struct Node {
    std::wstring path;
    bool dir;
    bool loaded = false;
};

HWND g_tree = nullptr;
std::wstring g_root;
bool g_allowEdit = false;  // label editing only via F2 / menu, not click-on-selected

Node* NodeOf(HTREEITEM it) {
    TVITEMW tv = {TVIF_PARAM};
    tv.hItem = it;
    if (!TreeView_GetItem(g_tree, &tv)) return nullptr;
    return (Node*)tv.lParam;
}

int IconIndex(const std::wstring& path, bool dir, bool open) {
    SHFILEINFOW fi = {};
    UINT flags = SHGFI_SYSICONINDEX | SHGFI_SMALLICON | SHGFI_USEFILEATTRIBUTES | (open ? SHGFI_OPENICON : 0);
    SHGetFileInfoW(path.c_str(), dir ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_NORMAL, &fi, sizeof(fi), flags);
    return fi.iIcon;
}

HTREEITEM Insert(HTREEITEM parent, const std::wstring& name, const std::wstring& path, bool dir) {
    TVINSERTSTRUCTW is = {};
    is.hParent = parent;
    is.hInsertAfter = TVI_LAST;
    is.item.mask = TVIF_TEXT | TVIF_PARAM | TVIF_CHILDREN | TVIF_IMAGE | TVIF_SELECTEDIMAGE;
    is.item.pszText = (LPWSTR)name.c_str();
    is.item.cChildren = dir ? 1 : 0;
    is.item.lParam = (LPARAM) new Node{path, dir};
    is.item.iImage = IconIndex(path, dir, false);
    is.item.iSelectedImage = dir ? is.item.iImage : is.item.iImage;
    return TreeView_InsertItem(g_tree, &is);
}

void Populate(HTREEITEM item) {
    Node* n = NodeOf(item);
    if (!n || !n->dir) return;
    // remove existing children
    HTREEITEM c;
    while ((c = TreeView_GetChild(g_tree, item)) != nullptr) TreeView_DeleteItem(g_tree, c);
    n->loaded = true;
    struct Ent { std::wstring name; bool dir; };
    std::vector<Ent> ents;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileExW(JoinPath(n->path, L"*").c_str(), FindExInfoBasic, &fd, FindExSearchNameMatch, nullptr,
                                FIND_FIRST_EX_LARGE_FETCH);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            std::wstring name = fd.cFileName;
            if (name == L"." || name == L".." || name == L".git") continue;
            if (name.find(L".~mye-") != std::wstring::npos) continue;
            ents.push_back({name, (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0});
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    std::sort(ents.begin(), ents.end(), [](const Ent& a, const Ent& b) {
        if (a.dir != b.dir) return a.dir;
        return StrCmpLogicalW(a.name.c_str(), b.name.c_str()) < 0;
    });
    for (auto& e : ents) Insert(item, e.name, JoinPath(n->path, e.name), e.dir);
    if (ents.empty()) {
        TVITEMW tv = {TVIF_CHILDREN};
        tv.hItem = item;
        tv.cChildren = 0;
        TreeView_SetItem(g_tree, &tv);
    }
}

void CollectExpanded(HTREEITEM it, std::set<std::wstring>& out) {
    for (; it; it = TreeView_GetNextSibling(g_tree, it)) {
        Node* n = NodeOf(it);
        if (n && n->dir && (TreeView_GetItemState(g_tree, it, TVIS_EXPANDED) & TVIS_EXPANDED)) {
            out.insert(LowerW(n->path));
            CollectExpanded(TreeView_GetChild(g_tree, it), out);
        }
    }
}

void Reexpand(HTREEITEM it, const std::set<std::wstring>& exp) {
    for (; it; it = TreeView_GetNextSibling(g_tree, it)) {
        Node* n = NodeOf(it);
        if (n && n->dir && exp.count(LowerW(n->path))) {
            TreeView_Expand(g_tree, it, TVE_EXPAND);
            Reexpand(TreeView_GetChild(g_tree, it), exp);
        }
    }
}

std::wstring TargetDir(HTREEITEM it) {
    Node* n = it ? NodeOf(it) : nullptr;
    if (!n) return g_root;
    return n->dir ? n->path : DirOf(n->path);
}

HTREEITEM FindItem(HTREEITEM it, const std::wstring& path) {
    for (; it; it = TreeView_GetNextSibling(g_tree, it)) {
        Node* n = NodeOf(it);
        if (n && PathEqualsI(n->path, path)) return it;
        if (n && n->dir && n->loaded) {
            HTREEITEM r = FindItem(TreeView_GetChild(g_tree, it), path);
            if (r) return r;
        }
    }
    return nullptr;
}

void RefreshDir(const std::wstring& dir) {
    HTREEITEM it = FindItem(TreeView_GetRoot(g_tree), dir);
    if (!it) { Explorer::Refresh(); return; }
    std::set<std::wstring> exp;
    CollectExpanded(TreeView_GetChild(g_tree, it), exp);
    Populate(it);
    TreeView_Expand(g_tree, it, TVE_EXPAND);
    Reexpand(TreeView_GetChild(g_tree, it), exp);
}

void ContextMenu(HTREEITEM it, POINT pt) {
    Node* n = it ? NodeOf(it) : nullptr;
    HMENU m = CreatePopupMenu();
    if (n && !n->dir) AppendMenuW(m, MF_STRING, 1, L"Open");
    AppendMenuW(m, MF_STRING, 2, L"New File...");
    AppendMenuW(m, MF_STRING, 3, L"New Folder...");
    if (n && it != TreeView_GetRoot(g_tree)) {
        AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(m, MF_STRING, 4, L"Rename...\tF2");
        AppendMenuW(m, MF_STRING, 5, L"Delete\tDel");
    }
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    if (n) {
        AppendMenuW(m, MF_STRING, 6, L"Copy Path");
        AppendMenuW(m, MF_STRING, 7, L"Reveal in File Explorer");
    }
    AppendMenuW(m, MF_STRING, 8, L"Refresh");
    int cmd = TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTBUTTON, pt.x, pt.y, 0, GetParent(g_tree), nullptr);
    DestroyMenu(m);
    HWND owner = App::Main();
    switch (cmd) {
    case 1: App::OpenFile(n->path); break;
    case 2:
    case 3: {
        std::wstring dir = TargetDir(it), name;
        if (!InputBox(owner, cmd == 2 ? L"New File" : L"New Folder", L"Name (in " + dir + L"):", name)) break;
        name = Trim(name);
        if (name.empty()) break;
        std::wstring full = JoinPath(dir, ToBackslashes(name));
        if (cmd == 2) {
            SHCreateDirectoryExW(nullptr, DirOf(full).c_str(), nullptr);
            HANDLE h = CreateFileW(full.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
            if (h == INVALID_HANDLE_VALUE) { MsgBox(owner, L"Could not create file:\n" + FormatError(GetLastError()), MB_ICONERROR); break; }
            CloseHandle(h);
            RefreshDir(dir);
            App::OpenFile(full);
        } else {
            int r = SHCreateDirectoryExW(nullptr, full.c_str(), nullptr);
            if (r != ERROR_SUCCESS) { MsgBox(owner, L"Could not create folder:\n" + FormatError((DWORD)r), MB_ICONERROR); break; }
            RefreshDir(dir);
        }
        App::RefreshGit();
        break;
    }
    case 4: g_allowEdit = true; TreeView_EditLabel(g_tree, it); break;
    case 5: {
        std::wstring from = n->path;
        from.push_back(L'\0');
        SHFILEOPSTRUCTW op = {};
        op.hwnd = owner;
        op.wFunc = FO_DELETE;
        op.pFrom = from.c_str();
        op.fFlags = FOF_ALLOWUNDO;
        std::wstring dir = DirOf(n->path);
        if (SHFileOperationW(&op) == 0 && !op.fAnyOperationsAborted) {
            RefreshDir(dir);
            App::RefreshGit();
        }
        break;
    }
    case 6: SetClipboardText(owner, n->path); break;
    case 7:
        ShellExecuteW(nullptr, L"open", L"explorer.exe", (L"/select,\"" + n->path + L"\"").c_str(), nullptr, SW_SHOWNORMAL);
        break;
    case 8: Explorer::Refresh(); break;
    }
}

LRESULT CALLBACK HostProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_SIZE:
        MoveWindow(g_tree, 0, 0, LOWORD(l), HIWORD(l), TRUE);
        return 0;
    case WM_NOTIFY: {
        auto* nm = (NMHDR*)l;
        if (nm->hwndFrom != g_tree) break;
        switch (nm->code) {
        case TVN_ITEMEXPANDINGW: {
            auto* tv = (NMTREEVIEWW*)l;
            Node* n = (Node*)tv->itemNew.lParam;
            if (tv->action == TVE_EXPAND && n && n->dir && !n->loaded) Populate(tv->itemNew.hItem);
            return 0;
        }
        case TVN_DELETEITEMW: {
            auto* tv = (NMTREEVIEWW*)l;
            delete (Node*)tv->itemOld.lParam;
            return 0;
        }
        case TVN_SELCHANGEDW: {
            auto* tv = (NMTREEVIEWW*)l;
            Node* n = (Node*)tv->itemNew.lParam;
            if (tv->action == TVC_BYMOUSE && n && !n->dir) App::OpenFile(n->path);
            return 0;
        }
        case NM_CLICK: {
            // clicking the already-selected file re-opens it (e.g. after its tab was closed)
            TVHITTESTINFO hi = {};
            GetCursorPos(&hi.pt);
            ScreenToClient(g_tree, &hi.pt);
            HTREEITEM it = TreeView_HitTest(g_tree, &hi);
            if (it && it == TreeView_GetSelection(g_tree) && (hi.flags & TVHT_ONITEM)) {
                Node* n = NodeOf(it);
                if (n && !n->dir) App::OpenFile(n->path);
            }
            return 0;
        }
        case TVN_KEYDOWN: {
            auto* kd = (NMTVKEYDOWN*)l;
            HTREEITEM it = TreeView_GetSelection(g_tree);
            Node* n = it ? NodeOf(it) : nullptr;
            if (kd->wVKey == VK_RETURN && n && !n->dir) App::OpenFile(n->path);
            else if (kd->wVKey == VK_F2 && it) { g_allowEdit = true; TreeView_EditLabel(g_tree, it); }
            else if (kd->wVKey == VK_DELETE && it && it != TreeView_GetRoot(g_tree)) {
                RECT r;
                TreeView_GetItemRect(g_tree, it, &r, TRUE);
                POINT pt = {r.left, r.bottom};
                ClientToScreen(g_tree, &pt);
                (void)pt;
                std::wstring from = n->path;
                from.push_back(L'\0');
                SHFILEOPSTRUCTW op = {};
                op.hwnd = App::Main();
                op.wFunc = FO_DELETE;
                op.pFrom = from.c_str();
                op.fFlags = FOF_ALLOWUNDO;
                std::wstring dir = DirOf(n->path);
                if (SHFileOperationW(&op) == 0 && !op.fAnyOperationsAborted) { RefreshDir(dir); App::RefreshGit(); }
            } else if (kd->wVKey == VK_F5) Explorer::Refresh();
            return 0;
        }
        case TVN_BEGINLABELEDITW: {
            auto* di = (NMTVDISPINFOW*)l;
            bool deny = !g_allowEdit || di->item.hItem == TreeView_GetRoot(g_tree);
            g_allowEdit = false;
            return deny;
        }
        case TVN_ENDLABELEDITW: {
            auto* di = (NMTVDISPINFOW*)l;
            if (!di->item.pszText) return FALSE;
            Node* n = (Node*)di->item.lParam;
            std::wstring name = Trim(di->item.pszText);
            if (!n || name.empty()) return FALSE;
            std::wstring to = JoinPath(DirOf(n->path), name);
            if (!MoveFileExW(n->path.c_str(), to.c_str(), 0)) {
                MsgBox(App::Main(), L"Rename failed:\n" + FormatError(GetLastError()), MB_ICONERROR);
                return FALSE;
            }
            std::wstring dir = DirOf(n->path);
            RunOnUi([dir]() { RefreshDir(dir); App::RefreshGit(); });
            return TRUE;
        }
        case NM_RCLICK: {
            TVHITTESTINFO hi = {};
            GetCursorPos(&hi.pt);
            POINT screen = hi.pt;
            ScreenToClient(g_tree, &hi.pt);
            HTREEITEM it = TreeView_HitTest(g_tree, &hi);
            if (it) TreeView_SelectItem(g_tree, it);
            ContextMenu(it, screen);
            return TRUE;
        }
        case NM_CUSTOMDRAW:
            break;
        }
        break;
    }
    }
    return DefWindowProcW(h, m, w, l);
}

}  // namespace

namespace Explorer {

HWND Create(HWND parent) {
    WNDCLASSEXW wc = {sizeof(wc)};
    wc.lpfnWndProc = HostProc;
    wc.hInstance = g_hinst;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = L"MyExplorerHost";
    RegisterClassExW(&wc);
    HWND host = CreateWindowExW(0, L"MyExplorerHost", L"", WS_CHILD | WS_CLIPCHILDREN, 0, 0, 0, 0, parent, nullptr, g_hinst, nullptr);
    g_tree = CreateWindowExW(0, WC_TREEVIEWW, L"",
                             WS_CHILD | WS_VISIBLE | WS_TABSTOP | TVS_HASBUTTONS | TVS_SHOWSELALWAYS | TVS_FULLROWSELECT |
                                 TVS_EDITLABELS | TVS_TRACKSELECT,
                             0, 0, 0, 0, host, nullptr, g_hinst, nullptr);
    TreeView_SetExtendedStyle(g_tree, TVS_EX_DOUBLEBUFFER | TVS_EX_FADEINOUTEXPANDOS, TVS_EX_DOUBLEBUFFER | TVS_EX_FADEINOUTEXPANDOS);
    SendMessageW(g_tree, WM_SETFONT, (WPARAM)g_uiFont, FALSE);
    SHFILEINFOW fi = {};
    HIMAGELIST il = (HIMAGELIST)SHGetFileInfoW(L"C:\\", 0, &fi, sizeof(fi), SHGFI_SYSICONINDEX | SHGFI_SMALLICON);
    TreeView_SetImageList(g_tree, il, TVSIL_NORMAL);
    ThemeChanged();
    return host;
}

void SetRoot(const std::wstring& folder) {
    g_root = folder;
    TreeView_DeleteAllItems(g_tree);
    if (folder.empty()) return;
    std::wstring name = FileNameOf(folder);
    if (name.empty()) name = folder;
    HTREEITEM root = Insert(TVI_ROOT, name, folder, true);
    TreeView_Expand(g_tree, root, TVE_EXPAND);
}

void Refresh() {
    if (g_root.empty()) return;
    HTREEITEM root = TreeView_GetRoot(g_tree);
    if (!root) { SetRoot(g_root); return; }
    std::set<std::wstring> exp;
    CollectExpanded(root, exp);
    std::wstring selPath;
    HTREEITEM sel = TreeView_GetSelection(g_tree);
    if (sel && NodeOf(sel)) selPath = NodeOf(sel)->path;
    int scroll = GetScrollPos(g_tree, SB_VERT);
    SendMessageW(g_tree, WM_SETREDRAW, FALSE, 0);
    Populate(root);
    TreeView_Expand(g_tree, root, TVE_EXPAND);
    Reexpand(TreeView_GetChild(g_tree, root), exp);
    if (!selPath.empty()) {
        HTREEITEM it = FindItem(root, selPath);
        if (it) TreeView_Select(g_tree, it, TVGN_CARET);
    }
    SendMessageW(g_tree, WM_SETREDRAW, TRUE, 0);
    // restore approximate scroll position
    HTREEITEM first = TreeView_GetRoot(g_tree);
    for (int i = 0; i < scroll && first; ++i) first = TreeView_GetNextVisible(g_tree, first);
    if (first) TreeView_Select(g_tree, first, TVGN_FIRSTVISIBLE);
    InvalidateRect(g_tree, nullptr, TRUE);
}

void ThemeChanged() {
    TreeView_SetBkColor(g_tree, g_theme.panelBg);
    TreeView_SetTextColor(g_tree, g_theme.panelFg);
    TreeView_SetLineColor(g_tree, g_theme.border);
    SetWindowTheme(g_tree, g_settings.dark ? L"DarkMode_Explorer" : L"Explorer", nullptr);
    TreeView_SetItemHeight(g_tree, S(22));
    InvalidateRect(g_tree, nullptr, TRUE);
}

}  // namespace Explorer
