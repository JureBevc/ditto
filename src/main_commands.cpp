// Menu bar, keyboard accelerators and command handling for the main window.
#include "main_window.h"
#include "dialogs.h"
#include "resource.h"

static HMENU g_recentMenu, g_fileMenu, g_editMenu, g_viewMenu, g_gitMenu;

HMENU BuildMenu() {
    HMENU bar = CreateMenu();
    HMENU f = CreatePopupMenu();
    AppendMenuW(f, MF_STRING, ID_FILE_NEW, L"&New File\tCtrl+N");
    AppendMenuW(f, MF_STRING, ID_FILE_OPEN, L"&Open File...\tCtrl+O");
    AppendMenuW(f, MF_STRING, ID_FILE_OPENFOLDER, L"Open &Folder...\tCtrl+Shift+O");
    g_recentMenu = CreatePopupMenu();
    AppendMenuW(f, MF_POPUP, (UINT_PTR)g_recentMenu, L"Open &Recent");
    AppendMenuW(f, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(f, MF_STRING, ID_FILE_SAVE, L"&Save\tCtrl+S");
    AppendMenuW(f, MF_STRING, ID_FILE_SAVEAS, L"Save &As...\tCtrl+Shift+S");
    AppendMenuW(f, MF_STRING, ID_FILE_SAVEALL, L"Save A&ll");
    AppendMenuW(f, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(f, MF_STRING, ID_FILE_CLOSETAB, L"&Close Tab\tCtrl+W");
    AppendMenuW(f, MF_STRING, ID_FILE_CLOSEFOLDER, L"Close Fol&der");
    AppendMenuW(f, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(f, MF_STRING, ID_FILE_EXIT, L"E&xit\tAlt+F4");
    AppendMenuW(bar, MF_POPUP, (UINT_PTR)f, L"&File");
    g_fileMenu = f;

    HMENU e = CreatePopupMenu();
    AppendMenuW(e, MF_STRING, ID_EDIT_UNDO, L"&Undo\tCtrl+Z");
    AppendMenuW(e, MF_STRING, ID_EDIT_REDO, L"&Redo\tCtrl+Y");
    AppendMenuW(e, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(e, MF_STRING, ID_EDIT_CUT, L"Cu&t\tCtrl+X");
    AppendMenuW(e, MF_STRING, ID_EDIT_COPY, L"&Copy\tCtrl+C");
    AppendMenuW(e, MF_STRING, ID_EDIT_PASTE, L"&Paste\tCtrl+V");
    AppendMenuW(e, MF_STRING, ID_EDIT_SELECTALL, L"Select &All\tCtrl+A");
    AppendMenuW(e, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(e, MF_STRING, ID_FIND_SHOW, L"&Find...\tCtrl+F");
    AppendMenuW(e, MF_STRING, ID_FIND_REPLACE, L"R&eplace...\tCtrl+H");
    AppendMenuW(e, MF_STRING, ID_FIND_NEXT, L"Find &Next\tF3");
    AppendMenuW(e, MF_STRING, ID_FIND_PREV, L"Find Pre&vious\tShift+F3");
    AppendMenuW(e, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(e, MF_STRING, ID_EDIT_GOTO, L"&Go to Line...\tCtrl+G");
    AppendMenuW(bar, MF_POPUP, (UINT_PTR)e, L"&Edit");
    g_editMenu = e;

    HMENU v = CreatePopupMenu();
    AppendMenuW(v, MF_STRING, ID_PALETTE_COMMANDS, L"&Command Palette...\tF1");
    AppendMenuW(v, MF_STRING, ID_PALETTE_FILES, L"&Go to File...\tCtrl+P");
    AppendMenuW(v, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(v, MF_STRING, ID_VIEW_EXPLORER, L"&Explorer\tCtrl+Shift+E");
    AppendMenuW(v, MF_STRING, ID_VIEW_SCM, L"&Source Control\tCtrl+Shift+G");
    AppendMenuW(v, MF_STRING, ID_VIEW_SIDEBAR, L"Show Side &Bar\tCtrl+B");
    AppendMenuW(v, MF_STRING, ID_VIEW_OUTPUT, L"Show &Output\tCtrl+`");
    AppendMenuW(v, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(v, MF_STRING, ID_VIEW_WRAP, L"&Word Wrap\tAlt+Z");
    AppendMenuW(v, MF_STRING, ID_VIEW_DARK, L"&Dark Theme");
    AppendMenuW(v, MF_STRING, ID_VIEW_FONT, L"&Font...");
    AppendMenuW(v, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(v, MF_STRING, ID_VIEW_ZOOMIN, L"Zoom &In\tCtrl+=");
    AppendMenuW(v, MF_STRING, ID_VIEW_ZOOMOUT, L"Zoom O&ut\tCtrl+-");
    AppendMenuW(v, MF_STRING, ID_VIEW_ZOOMRESET, L"&Reset Zoom\tCtrl+0");
    AppendMenuW(v, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(v, MF_STRING, ID_VIEW_NEXTTAB, L"Ne&xt Tab\tCtrl+Tab");
    AppendMenuW(v, MF_STRING, ID_VIEW_PREVTAB, L"Pre&vious Tab\tCtrl+Shift+Tab");
    AppendMenuW(bar, MF_POPUP, (UINT_PTR)v, L"&View");
    g_viewMenu = v;

    HMENU g = CreatePopupMenu();
    AppendMenuW(g, MF_STRING, ID_GIT_INIT, L"&Initialize Repository");
    AppendMenuW(g, MF_STRING, ID_GIT_CLONE, L"C&lone Repository...");
    AppendMenuW(g, MF_STRING, ID_GIT_PUBLISH, L"Publish to &GitHub...");
    AppendMenuW(g, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(g, MF_STRING, ID_GIT_COMMIT, L"&Commit\tCtrl+Enter");
    AppendMenuW(g, MF_STRING, ID_GIT_COMMIT_STAGED, L"Commit Staged");
    AppendMenuW(g, MF_STRING, ID_GIT_COMMIT_ALL, L"Commit All");
    AppendMenuW(g, MF_STRING, ID_GIT_COMMIT_AMEND, L"Commit (Amend)");
    AppendMenuW(g, MF_STRING, ID_GIT_UNDO_COMMIT, L"Undo Last Commit");
    AppendMenuW(g, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(g, MF_STRING, ID_GIT_STAGE_ALL, L"Stage All Changes");
    AppendMenuW(g, MF_STRING, ID_GIT_UNSTAGE_ALL, L"Unstage All Changes");
    AppendMenuW(g, MF_STRING, ID_GIT_DISCARD_ALL, L"Discard All Changes...");
    AppendMenuW(g, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(g, MF_STRING, ID_GIT_PULL, L"P&ull");
    AppendMenuW(g, MF_STRING, ID_GIT_PUSH, L"&Push");
    AppendMenuW(g, MF_STRING, ID_GIT_SYNC, L"&Sync");
    AppendMenuW(g, MF_STRING, ID_GIT_FETCH, L"&Fetch");
    AppendMenuW(g, MF_STRING, ID_GIT_PUSH_FORCE, L"Force Push (with lease)...");
    AppendMenuW(g, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(g, MF_STRING, ID_GIT_CHECKOUT, L"Check&out to...");
    AppendMenuW(g, MF_STRING, ID_GIT_BRANCH_CREATE, L"Create &Branch...");
    AppendMenuW(g, MF_STRING, ID_GIT_BRANCH_DELETE, L"&Delete Branch...");
    AppendMenuW(g, MF_STRING, ID_GIT_MERGE, L"&Merge Branch...");
    AppendMenuW(g, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(g, MF_STRING, ID_GIT_STASH, L"S&tash...");
    AppendMenuW(g, MF_STRING, ID_GIT_STASH_POP, L"Pop St&ash...");
    AppendMenuW(g, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(g, MF_STRING, ID_GIT_REMOTE_ADD, L"Add &Remote...");
    AppendMenuW(g, MF_STRING, ID_GIT_REMOTE_REMOVE, L"Remove Remote...");
    AppendMenuW(g, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(g, MF_STRING, ID_GIT_LOG, L"View &History");
    AppendMenuW(g, MF_STRING, ID_GIT_REFRESH, L"&Refresh\tF5");
    AppendMenuW(bar, MF_POPUP, (UINT_PTR)g, L"&Git");
    g_gitMenu = g;

    HMENU hm = CreatePopupMenu();
    AppendMenuW(hm, MF_STRING, ID_HELP_ABOUT, L"&About ditto");
    AppendMenuW(bar, MF_POPUP, (UINT_PTR)hm, L"&Help");
    return bar;
}

HACCEL BuildAccel() {
    ACCEL a[] = {
        {FCONTROL | FVIRTKEY, 'N', ID_FILE_NEW},
        {FCONTROL | FVIRTKEY, 'O', ID_FILE_OPEN},
        {FCONTROL | FSHIFT | FVIRTKEY, 'O', ID_FILE_OPENFOLDER},
        {FCONTROL | FVIRTKEY, 'S', ID_FILE_SAVE},
        {FCONTROL | FSHIFT | FVIRTKEY, 'S', ID_FILE_SAVEAS},
        {FCONTROL | FVIRTKEY, 'W', ID_FILE_CLOSETAB},
        {FCONTROL | FVIRTKEY, VK_F4, ID_FILE_CLOSETAB},
        {FCONTROL | FVIRTKEY, 'F', ID_FIND_SHOW},
        {FCONTROL | FVIRTKEY, 'H', ID_FIND_REPLACE},
        {FVIRTKEY, VK_F3, ID_FIND_NEXT},
        {FSHIFT | FVIRTKEY, VK_F3, ID_FIND_PREV},
        {FCONTROL | FVIRTKEY, 'G', ID_EDIT_GOTO},
        {FCONTROL | FSHIFT | FVIRTKEY, 'E', ID_VIEW_EXPLORER},
        {FCONTROL | FSHIFT | FVIRTKEY, 'G', ID_VIEW_SCM},
        {FCONTROL | FVIRTKEY, 'B', ID_VIEW_SIDEBAR},
        {FCONTROL | FVIRTKEY, VK_OEM_3, ID_VIEW_OUTPUT},
        {FALT | FVIRTKEY, 'Z', ID_VIEW_WRAP},
        {FCONTROL | FVIRTKEY, VK_OEM_PLUS, ID_VIEW_ZOOMIN},
        {FCONTROL | FVIRTKEY, VK_ADD, ID_VIEW_ZOOMIN},
        {FCONTROL | FVIRTKEY, VK_OEM_MINUS, ID_VIEW_ZOOMOUT},
        {FCONTROL | FVIRTKEY, VK_SUBTRACT, ID_VIEW_ZOOMOUT},
        {FCONTROL | FVIRTKEY, '0', ID_VIEW_ZOOMRESET},
        {FCONTROL | FVIRTKEY, VK_TAB, ID_VIEW_NEXTTAB},
        {FCONTROL | FSHIFT | FVIRTKEY, VK_TAB, ID_VIEW_PREVTAB},
        {FCONTROL | FVIRTKEY, VK_NEXT, ID_VIEW_NEXTTAB},
        {FCONTROL | FVIRTKEY, VK_PRIOR, ID_VIEW_PREVTAB},
        {FVIRTKEY, VK_F5, ID_GIT_REFRESH},
        {FVIRTKEY, VK_F1, ID_PALETTE_COMMANDS},
        {FCONTROL | FSHIFT | FVIRTKEY, 'P', ID_PALETTE_COMMANDS},
        {FCONTROL | FVIRTKEY, 'P', ID_PALETTE_FILES},
    };
    return CreateAcceleratorTableW(a, (int)(sizeof(a) / sizeof(a[0])));
}

void OnInitMenu(HMENU m) {
    if (m == g_fileMenu) {
        while (GetMenuItemCount(g_recentMenu) > 0) DeleteMenu(g_recentMenu, 0, MF_BYPOSITION);
        auto& r = g_settings.recentFolders;
        for (size_t i = 0; i < r.size(); ++i) AppendMenuW(g_recentMenu, MF_STRING, ID_FILE_RECENT0 + i, r[i].c_str());
        if (r.empty()) AppendMenuW(g_recentMenu, MF_STRING | MF_GRAYED, 0, L"(none)");
        EnableMenuItem(m, ID_FILE_CLOSEFOLDER, M.folder.empty() ? MF_GRAYED : MF_ENABLED);
        bool ed = ActiveEditor() != nullptr;
        EnableMenuItem(m, ID_FILE_SAVE, ed ? MF_ENABLED : MF_GRAYED);
        EnableMenuItem(m, ID_FILE_SAVEAS, ed ? MF_ENABLED : MF_GRAYED);
        EnableMenuItem(m, ID_FILE_CLOSETAB, M.active >= 0 ? MF_ENABLED : MF_GRAYED);
    } else if (m == g_editMenu) {
        EditorView* ed = ActiveEditor();
        UINT en = ed ? MF_ENABLED : MF_GRAYED;
        EnableMenuItem(m, ID_EDIT_UNDO, ed && ed->buf->CanUndo() ? MF_ENABLED : MF_GRAYED);
        EnableMenuItem(m, ID_EDIT_REDO, ed && ed->buf->CanRedo() ? MF_ENABLED : MF_GRAYED);
        for (int id : {ID_EDIT_CUT, ID_EDIT_COPY, ID_EDIT_PASTE, ID_EDIT_SELECTALL, ID_FIND_SHOW, ID_FIND_REPLACE, ID_FIND_NEXT,
                       ID_FIND_PREV, ID_EDIT_GOTO})
            EnableMenuItem(m, id, en);
    } else if (m == g_viewMenu) {
        CheckMenuItem(m, ID_VIEW_SIDEBAR, g_settings.showSidebar ? MF_CHECKED : MF_UNCHECKED);
        CheckMenuItem(m, ID_VIEW_OUTPUT, g_settings.showOutput ? MF_CHECKED : MF_UNCHECKED);
        CheckMenuItem(m, ID_VIEW_WRAP, g_settings.wrap ? MF_CHECKED : MF_UNCHECKED);
        CheckMenuItem(m, ID_VIEW_DARK, g_settings.dark ? MF_CHECKED : MF_UNCHECKED);
    } else if (m == g_gitMenu) {
        bool repo = M.status.isRepo;
        int repoCmds[] = {ID_GIT_COMMIT, ID_GIT_COMMIT_STAGED, ID_GIT_COMMIT_ALL, ID_GIT_COMMIT_AMEND, ID_GIT_UNDO_COMMIT,
                          ID_GIT_STAGE_ALL, ID_GIT_UNSTAGE_ALL, ID_GIT_DISCARD_ALL, ID_GIT_PULL, ID_GIT_PUSH, ID_GIT_SYNC,
                          ID_GIT_FETCH, ID_GIT_PUSH_FORCE, ID_GIT_CHECKOUT, ID_GIT_BRANCH_CREATE, ID_GIT_BRANCH_DELETE,
                          ID_GIT_MERGE, ID_GIT_STASH, ID_GIT_STASH_POP, ID_GIT_REMOTE_ADD, ID_GIT_REMOTE_REMOVE, ID_GIT_LOG};
        for (int id : repoCmds) EnableMenuItem(m, id, repo ? MF_ENABLED : MF_GRAYED);
        EnableMenuItem(m, ID_GIT_INIT, repo ? MF_GRAYED : MF_ENABLED);
        bool hasOrigin = std::find(M.status.remotes.begin(), M.status.remotes.end(), L"origin") != M.status.remotes.end();
        EnableMenuItem(m, ID_GIT_PUBLISH, (!M.folder.empty() && !hasOrigin) ? MF_ENABLED : MF_GRAYED);
    }
}

bool SaveTab(int i, bool saveAs) {
    if (i < 0 || i >= (int)M.tabs.size() || M.tabs[(size_t)i].kind != Tab::Editor) return false;
    Tab& t = M.tabs[(size_t)i];
    EditorView* ed = t.ed;
    std::wstring path = ed->buf->path;
    bool wasNew = path.empty();
    if (saveAs || path.empty()) {
        std::wstring p = path.empty() ? JoinPath(M.folder, ed->untitledName + L".md") : path;
        if (path.empty() && M.folder.empty()) p = ed->untitledName + L".md";
        if (!SaveFileDlg(M.hwnd, p)) return false;
        path = p;
    }
    std::wstring err;
    if (!ed->Save(path, err)) {
        MsgBox(M.hwnd, L"Could not save " + path + L"\n\n" + err, MB_ICONERROR);
        return false;
    }
    t.key = L"file:" + LowerW(ed->buf->path);
    if (wasNew || saveAs) Explorer::Refresh();
    UpdateTitle();
    InvalidateChrome();
    App::RefreshGit();
    return true;
}

bool ConfirmCloseAll() {
    for (size_t i = 0; i < M.tabs.size(); ++i) {
        Tab& t = M.tabs[i];
        if (t.kind != Tab::Editor || !t.ed->buf->Modified()) continue;
        ActivateTab((int)i);
        int r = MsgBox(M.hwnd, L"Do you want to save the changes you made to " + t.ed->Title() + L"?", MB_YESNOCANCEL | MB_ICONWARNING);
        if (r == IDCANCEL) return false;
        if (r == IDYES && !SaveTab((int)i, false)) return false;
    }
    return true;
}

static void SetZoom(int size) {
    g_settings.fontSize = std::max(6, std::min(72, size));
    CreateEditorFonts();
    for (auto& t : M.tabs) {
        if (t.kind == Tab::Editor) t.ed->FontChanged();
        else InvalidateRect(t.hwnd, nullptr, FALSE);
    }
    SaveSettings();
}

static void ChooseEditorFont() {
    LOGFONTW lf = {};
    GetObjectW(g_ef.normal, sizeof(lf), &lf);
    lf.lfHeight = -MulDiv(g_settings.fontSize, 96, 72);  // dialog works at 96 DPI logical
    HDC dc = GetDC(M.hwnd);
    lf.lfHeight = -MulDiv(g_settings.fontSize, GetDeviceCaps(dc, LOGPIXELSY), 72);
    CHOOSEFONTW cf = {sizeof(cf)};
    cf.hwndOwner = M.hwnd;
    cf.hDC = dc;
    cf.lpLogFont = &lf;
    cf.Flags = CF_SCREENFONTS | CF_FIXEDPITCHONLY | CF_INITTOLOGFONTSTRUCT | CF_NOVERTFONTS;
    BOOL ok = ChooseFontW(&cf);
    ReleaseDC(M.hwnd, dc);
    if (!ok) return;
    g_settings.fontName = lf.lfFaceName;
    SetZoom(cf.iPointSize / 10);
}

void OnCommand(int id) {
    EditorView* ed = ActiveEditor();
    if (id >= ID_FILE_RECENT0 && id < ID_FILE_RECENT0 + 10) {
        size_t i = (size_t)(id - ID_FILE_RECENT0);
        if (i < g_settings.recentFolders.size()) {
            std::wstring f = g_settings.recentFolders[i];
            App::OpenFolder(f);
        }
        return;
    }
    if (id >= ID_GIT_INIT && id <= ID_GIT_UNDO_COMMIT) {
        GitCommand(id);
        return;
    }
    switch (id) {
    case ID_FILE_NEW: {
        std::wstring err;
        EditorView* nv = EditorView::Create(M.hwnd, L"", err);
        if (!nv) return;
        nv->untitledName = L"Untitled-" + std::to_wstring(M.untitled++);
        Tab t;
        t.kind = Tab::Editor;
        t.hwnd = nv->hwnd;
        t.ed = nv;
        t.key = L"new:" + nv->untitledName;
        AddTab(t);
        break;
    }
    case ID_FILE_OPEN: {
        std::wstring p;
        if (OpenFileDlg(M.hwnd, p)) App::OpenFile(p);
        break;
    }
    case ID_FILE_OPENFOLDER: {
        std::wstring f = M.folder;
        if (BrowseFolder(M.hwnd, L"Open Folder", f)) App::OpenFolder(f);
        break;
    }
    case ID_FILE_SAVE: SaveTab(M.active, false); break;
    case ID_FILE_SAVEAS: SaveTab(M.active, true); break;
    case ID_FILE_SAVEALL:
        for (size_t i = 0; i < M.tabs.size(); ++i)
            if (M.tabs[i].kind == Tab::Editor && M.tabs[i].ed->buf->Modified()) SaveTab((int)i, false);
        break;
    case ID_FILE_CLOSETAB: CloseTab(M.active); break;
    case ID_FILE_CLOSEFOLDER:
        M.folder.clear();
        M.status = GitStatus();
        Explorer::SetRoot(L"");
        Scm::Update(M.status);
        UpdateTitle();
        InvalidateRect(M.hwnd, nullptr, FALSE);
        break;
    case ID_FILE_EXIT: PostMessageW(M.hwnd, WM_CLOSE, 0, 0); break;

    case ID_EDIT_UNDO: if (ed) ed->Undo(); break;
    case ID_EDIT_REDO: if (ed) ed->Redo(); break;
    case ID_EDIT_CUT: if (ed) ed->Cut(); break;
    case ID_EDIT_COPY: if (ed) ed->Copy(); break;
    case ID_EDIT_PASTE: if (ed) ed->Paste(); break;
    case ID_EDIT_SELECTALL: if (ed) ed->SelectAll(); break;
    case ID_EDIT_GOTO:
    case ID_PALETTE_GOTO:
        if (ed) Palette::Show(L":");
        break;
    case ID_PALETTE_COMMANDS: Palette::Show(L">"); break;
    case ID_PALETTE_FILES: Palette::Show(L""); break;
    case ID_FIND_SHOW:
    case ID_FIND_REPLACE:
        if (ed) FindBar::Show(id == ID_FIND_REPLACE);
        break;
    case ID_FIND_NEXT:
    case ID_FIND_PREV:
        if (!ed) break;
        if (FindBar::Query().needle.empty()) { FindBar::Show(false); break; }
        ed->Find(FindBar::Query(), id == ID_FIND_NEXT);
        break;
    case ID_FIND_REPLACEONE:
        if (ed) ed->ReplaceOne(FindBar::Query(), FindBar::Replacement());
        break;
    case ID_FIND_REPLACEALL:
        if (ed) ed->ReplaceAll(FindBar::Query(), FindBar::Replacement());
        break;
    case ID_FIND_CLOSE: FindBar::Hide(); break;

    case ID_VIEW_EXPLORER:
    case ID_VIEW_SCM: {
        int mode = id == ID_VIEW_EXPLORER ? 0 : 1;
        if (g_settings.showSidebar && M.sidebarMode == mode && GetKeyState(VK_SHIFT) < 0) break;
        M.sidebarMode = mode;
        g_settings.showSidebar = true;
        Layout();
        if (mode == 1) Scm::FocusMessage();
        else SetFocus(M.explorer);
        break;
    }
    case ID_VIEW_SIDEBAR:
        g_settings.showSidebar = !g_settings.showSidebar;
        Layout();
        SaveSettings();
        break;
    case ID_VIEW_OUTPUT:
        g_settings.showOutput = !g_settings.showOutput;
        Layout();
        SaveSettings();
        break;
    case ID_VIEW_WRAP:
        g_settings.wrap = !g_settings.wrap;
        for (auto& t : M.tabs)
            if (t.kind == Tab::Editor) t.ed->SetWrap(g_settings.wrap);
        if (ed && g_settings.wrap && !ed->WrapActive())
            App::SetStatusText(L"Word wrap is disabled for files larger than 64 MB");
        SaveSettings();
        break;
    case ID_VIEW_DARK:
        g_settings.dark = !g_settings.dark;
        ApplyThemeAll();
        SaveSettings();
        break;
    case ID_VIEW_FONT: ChooseEditorFont(); break;
    case ID_VIEW_ZOOMIN: SetZoom(g_settings.fontSize + 1); break;
    case ID_VIEW_ZOOMOUT: SetZoom(g_settings.fontSize - 1); break;
    case ID_VIEW_ZOOMRESET: SetZoom(11); break;
    case ID_VIEW_NEXTTAB:
    case ID_VIEW_PREVTAB:
        if (M.tabs.size() > 1) {
            int n = (int)M.tabs.size();
            ActivateTab((M.active + (id == ID_VIEW_NEXTTAB ? 1 : n - 1)) % n);
        }
        break;
    case ID_HELP_ABOUT:
        MsgBox(M.hwnd,
               L"ditto 1.0\n\nA lightweight text editor with Markdown highlighting and a built-in git client.\n\n"
               L"Large files are memory-mapped and indexed in the background, so multi-gigabyte files open instantly.\n\n"
               L"Git: " + GitExe(),
               MB_OK | MB_ICONINFORMATION);
        break;
    }
}
