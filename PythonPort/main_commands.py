# Menu bar, keyboard accelerators and command handling for the main window. (port of main_commands.cpp)
import common as C
import win32 as W
from win32 import *  # noqa: F401,F403
from resource import *  # noqa: F401,F403

import app
import git
import dialogs
import editor_view
import main_window as MW
import terminal as Term
import find_bar as FindBar
import palette as Palette
import explorer as Explorer
import scm_panel as Scm
import git_commands

g_recentMenu = None
g_fileMenu = None
g_editMenu = None
g_viewMenu = None
g_termMenu = None
g_gitMenu = None


def BuildMenu():
    global g_recentMenu, g_fileMenu, g_editMenu, g_viewMenu, g_termMenu, g_gitMenu
    bar = CreateMenu()
    f = CreatePopupMenu()
    AppendMenuW(f, MF_STRING, ID_FILE_NEW, "&New File\tCtrl+N")
    AppendMenuW(f, MF_STRING, ID_FILE_OPEN, "&Open File...\tCtrl+O")
    AppendMenuW(f, MF_STRING, ID_FILE_OPENFOLDER, "Open &Folder...\tCtrl+Shift+O")
    g_recentMenu = CreatePopupMenu()
    AppendMenuW(f, MF_POPUP, g_recentMenu, "Open &Recent")
    AppendMenuW(f, MF_STRING, ID_FILE_OPENRECENT, "Open Recent Folder...\tCtrl+R")
    AppendMenuW(f, MF_SEPARATOR, 0, None)
    AppendMenuW(f, MF_STRING, ID_FILE_SAVE, "&Save\tCtrl+S")
    AppendMenuW(f, MF_STRING, ID_FILE_SAVEAS, "Save &As...\tCtrl+Shift+S")
    AppendMenuW(f, MF_STRING, ID_FILE_SAVEALL, "Save A&ll")
    AppendMenuW(f, MF_SEPARATOR, 0, None)
    AppendMenuW(f, MF_STRING, ID_FILE_CLOSETAB, "&Close Tab\tCtrl+W")
    AppendMenuW(f, MF_STRING, ID_FILE_CLOSEFOLDER, "Close Fol&der")
    AppendMenuW(f, MF_STRING, ID_FILE_REVEAL, "Reveal in File Expl&orer\tAlt+Shift+R")
    AppendMenuW(f, MF_SEPARATOR, 0, None)
    AppendMenuW(f, MF_STRING, ID_FILE_EXIT, "E&xit\tAlt+F4")
    AppendMenuW(bar, MF_POPUP, f, "&File")
    g_fileMenu = f

    e = CreatePopupMenu()
    AppendMenuW(e, MF_STRING, ID_EDIT_UNDO, "&Undo\tCtrl+Z")
    AppendMenuW(e, MF_STRING, ID_EDIT_REDO, "&Redo\tCtrl+Y")
    AppendMenuW(e, MF_SEPARATOR, 0, None)
    AppendMenuW(e, MF_STRING, ID_EDIT_CUT, "Cu&t\tCtrl+X")
    AppendMenuW(e, MF_STRING, ID_EDIT_COPY, "&Copy\tCtrl+C")
    AppendMenuW(e, MF_STRING, ID_EDIT_PASTE, "&Paste\tCtrl+V")
    AppendMenuW(e, MF_STRING, ID_EDIT_SELECTALL, "Select &All\tCtrl+A")
    AppendMenuW(e, MF_SEPARATOR, 0, None)
    AppendMenuW(e, MF_STRING, ID_FIND_SHOW, "&Find...\tCtrl+F")
    AppendMenuW(e, MF_STRING, ID_FIND_REPLACE, "R&eplace...\tCtrl+H")
    AppendMenuW(e, MF_STRING, ID_FIND_NEXT, "Find &Next\tF3")
    AppendMenuW(e, MF_STRING, ID_FIND_PREV, "Find Pre&vious\tShift+F3")
    AppendMenuW(e, MF_SEPARATOR, 0, None)
    AppendMenuW(e, MF_STRING, ID_EDIT_GOTO, "&Go to Line...\tCtrl+G")
    AppendMenuW(bar, MF_POPUP, e, "&Edit")
    g_editMenu = e

    v = CreatePopupMenu()
    AppendMenuW(v, MF_STRING, ID_PALETTE_COMMANDS, "&Command Palette...\tF1")
    AppendMenuW(v, MF_STRING, ID_PALETTE_FILES, "&Go to File...\tCtrl+P")
    AppendMenuW(v, MF_SEPARATOR, 0, None)
    AppendMenuW(v, MF_STRING, ID_VIEW_EXPLORER, "&Explorer\tCtrl+Shift+E")
    AppendMenuW(v, MF_STRING, ID_VIEW_SCM, "&Source Control\tCtrl+Shift+G")
    AppendMenuW(v, MF_STRING, ID_VIEW_SIDEBAR, "Show Side &Bar\tCtrl+B")
    AppendMenuW(v, MF_STRING, ID_VIEW_OUTPUT, "Show &Output\tCtrl+Shift+U")
    AppendMenuW(v, MF_SEPARATOR, 0, None)
    AppendMenuW(v, MF_STRING, ID_VIEW_WRAP, "&Word Wrap\tAlt+Z")
    AppendMenuW(v, MF_STRING, ID_VIEW_DARK, "&Dark Theme")
    AppendMenuW(v, MF_STRING, ID_VIEW_FONT, "&Font...")
    AppendMenuW(v, MF_SEPARATOR, 0, None)
    AppendMenuW(v, MF_STRING, ID_VIEW_ZOOMIN, "Zoom &In\tCtrl+=")
    AppendMenuW(v, MF_STRING, ID_VIEW_ZOOMOUT, "Zoom O&ut\tCtrl+-")
    AppendMenuW(v, MF_STRING, ID_VIEW_ZOOMRESET, "&Reset Zoom\tCtrl+0")
    AppendMenuW(v, MF_SEPARATOR, 0, None)
    AppendMenuW(v, MF_STRING, ID_VIEW_NEXTTAB, "Ne&xt Tab\tCtrl+Tab")
    AppendMenuW(v, MF_STRING, ID_VIEW_PREVTAB, "Pre&vious Tab\tCtrl+Shift+Tab")
    AppendMenuW(bar, MF_POPUP, v, "&View")
    g_viewMenu = v

    tm = CreatePopupMenu()
    AppendMenuW(tm, MF_STRING, ID_TERM_NEW, "&New Terminal\tCtrl+Shift+`")
    AppendMenuW(tm, MF_STRING, ID_TERM_SPLIT, "&Split Terminal\tCtrl+Shift+5")
    AppendMenuW(tm, MF_STRING, ID_TERM_KILL, "&Kill Terminal")
    AppendMenuW(tm, MF_SEPARATOR, 0, None)
    AppendMenuW(tm, MF_STRING, ID_VIEW_TERMINAL, "&Toggle Terminal\tCtrl+`")
    AppendMenuW(bar, MF_POPUP, tm, "&Terminal")
    g_termMenu = tm

    g = CreatePopupMenu()
    AppendMenuW(g, MF_STRING, ID_GIT_INIT, "&Initialize Repository")
    AppendMenuW(g, MF_STRING, ID_GIT_CLONE, "C&lone Repository...")
    AppendMenuW(g, MF_STRING, ID_GIT_PUBLISH, "Publish to &GitHub...")
    AppendMenuW(g, MF_SEPARATOR, 0, None)
    AppendMenuW(g, MF_STRING, ID_GIT_COMMIT, "&Commit\tCtrl+Enter")
    AppendMenuW(g, MF_STRING, ID_GIT_COMMIT_STAGED, "Commit Staged")
    AppendMenuW(g, MF_STRING, ID_GIT_COMMIT_ALL, "Commit All")
    AppendMenuW(g, MF_STRING, ID_GIT_COMMIT_AMEND, "Commit (Amend)")
    AppendMenuW(g, MF_STRING, ID_GIT_UNDO_COMMIT, "Undo Last Commit")
    AppendMenuW(g, MF_SEPARATOR, 0, None)
    AppendMenuW(g, MF_STRING, ID_GIT_STAGE_ALL, "Stage All Changes")
    AppendMenuW(g, MF_STRING, ID_GIT_UNSTAGE_ALL, "Unstage All Changes")
    AppendMenuW(g, MF_STRING, ID_GIT_DISCARD_ALL, "Discard All Changes...")
    AppendMenuW(g, MF_SEPARATOR, 0, None)
    AppendMenuW(g, MF_STRING, ID_GIT_PULL, "P&ull")
    AppendMenuW(g, MF_STRING, ID_GIT_PUSH, "&Push")
    AppendMenuW(g, MF_STRING, ID_GIT_SYNC, "&Sync")
    AppendMenuW(g, MF_STRING, ID_GIT_FETCH, "&Fetch")
    AppendMenuW(g, MF_STRING, ID_GIT_PUSH_FORCE, "Force Push (with lease)...")
    AppendMenuW(g, MF_SEPARATOR, 0, None)
    AppendMenuW(g, MF_STRING, ID_GIT_CHECKOUT, "Check&out to...")
    AppendMenuW(g, MF_STRING, ID_GIT_BRANCH_CREATE, "Create &Branch...")
    AppendMenuW(g, MF_STRING, ID_GIT_BRANCH_DELETE, "&Delete Branch...")
    AppendMenuW(g, MF_STRING, ID_GIT_MERGE, "&Merge Branch...")
    AppendMenuW(g, MF_SEPARATOR, 0, None)
    AppendMenuW(g, MF_STRING, ID_GIT_STASH, "S&tash...")
    AppendMenuW(g, MF_STRING, ID_GIT_STASH_POP, "Pop St&ash...")
    AppendMenuW(g, MF_SEPARATOR, 0, None)
    AppendMenuW(g, MF_STRING, ID_GIT_REMOTE_ADD, "Add &Remote...")
    AppendMenuW(g, MF_STRING, ID_GIT_REMOTE_REMOVE, "Remove Remote...")
    AppendMenuW(g, MF_SEPARATOR, 0, None)
    AppendMenuW(g, MF_STRING, ID_GIT_LOG, "View &History")
    AppendMenuW(g, MF_STRING, ID_GIT_REFRESH, "&Refresh\tF5")
    AppendMenuW(bar, MF_POPUP, g, "&Git")
    g_gitMenu = g

    hm = CreatePopupMenu()
    AppendMenuW(hm, MF_STRING, ID_HELP_ABOUT, "&About ditto")
    AppendMenuW(bar, MF_POPUP, hm, "&Help")
    return bar


def BuildAccel():
    a = [
        (FCONTROL | FVIRTKEY, ord('N'), ID_FILE_NEW),
        (FCONTROL | FVIRTKEY, ord('O'), ID_FILE_OPEN),
        (FCONTROL | FSHIFT | FVIRTKEY, ord('O'), ID_FILE_OPENFOLDER),
        (FCONTROL | FVIRTKEY, ord('R'), ID_FILE_OPENRECENT),
        (FCONTROL | FVIRTKEY, ord('S'), ID_FILE_SAVE),
        (FCONTROL | FSHIFT | FVIRTKEY, ord('S'), ID_FILE_SAVEAS),
        (FALT | FSHIFT | FVIRTKEY, ord('R'), ID_FILE_REVEAL),
        (FCONTROL | FVIRTKEY, ord('W'), ID_FILE_CLOSETAB),
        (FCONTROL | FVIRTKEY, VK_F4, ID_FILE_CLOSETAB),
        (FCONTROL | FVIRTKEY, ord('F'), ID_FIND_SHOW),
        (FCONTROL | FVIRTKEY, ord('H'), ID_FIND_REPLACE),
        (FVIRTKEY, VK_F3, ID_FIND_NEXT),
        (FSHIFT | FVIRTKEY, VK_F3, ID_FIND_PREV),
        (FCONTROL | FVIRTKEY, ord('G'), ID_EDIT_GOTO),
        (FCONTROL | FSHIFT | FVIRTKEY, ord('E'), ID_VIEW_EXPLORER),
        (FCONTROL | FSHIFT | FVIRTKEY, ord('G'), ID_VIEW_SCM),
        (FCONTROL | FVIRTKEY, ord('B'), ID_VIEW_SIDEBAR),
        (FCONTROL | FSHIFT | FVIRTKEY, ord('U'), ID_VIEW_OUTPUT),
        (FCONTROL | FVIRTKEY, VK_OEM_3, ID_VIEW_TERMINAL),
        (FCONTROL | FSHIFT | FVIRTKEY, VK_OEM_3, ID_TERM_NEW),
        (FCONTROL | FSHIFT | FVIRTKEY, ord('5'), ID_TERM_SPLIT),
        (FALT | FVIRTKEY, ord('Z'), ID_VIEW_WRAP),
        (FCONTROL | FVIRTKEY, VK_OEM_PLUS, ID_VIEW_ZOOMIN),
        (FCONTROL | FVIRTKEY, VK_ADD, ID_VIEW_ZOOMIN),
        (FCONTROL | FVIRTKEY, VK_OEM_MINUS, ID_VIEW_ZOOMOUT),
        (FCONTROL | FVIRTKEY, VK_SUBTRACT, ID_VIEW_ZOOMOUT),
        (FCONTROL | FVIRTKEY, ord('0'), ID_VIEW_ZOOMRESET),
        (FCONTROL | FVIRTKEY, VK_TAB, ID_VIEW_NEXTTAB),
        (FCONTROL | FSHIFT | FVIRTKEY, VK_TAB, ID_VIEW_PREVTAB),
        (FCONTROL | FVIRTKEY, VK_NEXT, ID_VIEW_NEXTTAB),
        (FCONTROL | FVIRTKEY, VK_PRIOR, ID_VIEW_PREVTAB),
        (FVIRTKEY, VK_F5, ID_GIT_REFRESH),
        (FVIRTKEY, VK_F1, ID_PALETTE_COMMANDS),
        (FCONTROL | FSHIFT | FVIRTKEY, ord('P'), ID_PALETTE_COMMANDS),
        (FCONTROL | FVIRTKEY, ord('P'), ID_PALETTE_FILES),
    ]
    arr = (ACCEL * len(a))()
    for i, (fv, key, cmd) in enumerate(a):
        arr[i].fVirt = fv
        arr[i].key = key
        arr[i].cmd = cmd
    return CreateAcceleratorTableW(arr, len(a))


def OnInitMenu(m):
    M = MW.M
    if not m:
        return
    if m == g_fileMenu:
        while GetMenuItemCount(g_recentMenu) > 0:
            DeleteMenu(g_recentMenu, 0, MF_BYPOSITION)
        r = C.g_settings.recentFolders
        for i, f in enumerate(r):
            AppendMenuW(g_recentMenu, MF_STRING, ID_FILE_RECENT0 + i, f)
        if not r:
            AppendMenuW(g_recentMenu, MF_STRING | MF_GRAYED, 0, "(none)")
        EnableMenuItem(m, ID_FILE_CLOSEFOLDER, MF_GRAYED if not M.folder else MF_ENABLED)
        rv = MW.ActiveEditor()
        EnableMenuItem(m, ID_FILE_REVEAL, MF_ENABLED if (rv and rv.buf.path) else MF_GRAYED)
        ed = MW.ActiveEditor() is not None
        EnableMenuItem(m, ID_FILE_SAVE, MF_ENABLED if ed else MF_GRAYED)
        EnableMenuItem(m, ID_FILE_SAVEAS, MF_ENABLED if ed else MF_GRAYED)
        EnableMenuItem(m, ID_FILE_CLOSETAB, MF_ENABLED if M.active >= 0 else MF_GRAYED)
    elif m == g_editMenu:
        ed = MW.ActiveEditor()
        en = MF_ENABLED if ed else MF_GRAYED
        EnableMenuItem(m, ID_EDIT_UNDO, MF_ENABLED if ed and ed.buf.CanUndo() else MF_GRAYED)
        EnableMenuItem(m, ID_EDIT_REDO, MF_ENABLED if ed and ed.buf.CanRedo() else MF_GRAYED)
        for id in (ID_EDIT_CUT, ID_EDIT_COPY, ID_EDIT_PASTE, ID_EDIT_SELECTALL, ID_FIND_SHOW, ID_FIND_REPLACE,
                   ID_FIND_NEXT, ID_FIND_PREV, ID_EDIT_GOTO):
            EnableMenuItem(m, id, en)
    elif m == g_viewMenu:
        CheckMenuItem(m, ID_VIEW_SIDEBAR, MF_CHECKED if C.g_settings.showSidebar else MF_UNCHECKED)
        out = C.g_settings.showOutput and M.panelTab == MW.MainState.PanelOutput
        CheckMenuItem(m, ID_VIEW_OUTPUT, MF_CHECKED if out else MF_UNCHECKED)
        CheckMenuItem(m, ID_VIEW_WRAP, MF_CHECKED if C.g_settings.wrap else MF_UNCHECKED)
        CheckMenuItem(m, ID_VIEW_DARK, MF_CHECKED if C.g_settings.dark else MF_UNCHECKED)
    elif m == g_termMenu:
        EnableMenuItem(m, ID_TERM_KILL, MF_ENABLED if Term.GroupCount() else MF_GRAYED)
    elif m == g_gitMenu:
        repo = M.status.isRepo
        repoCmds = (ID_GIT_COMMIT, ID_GIT_COMMIT_STAGED, ID_GIT_COMMIT_ALL, ID_GIT_COMMIT_AMEND, ID_GIT_UNDO_COMMIT,
                    ID_GIT_STAGE_ALL, ID_GIT_UNSTAGE_ALL, ID_GIT_DISCARD_ALL, ID_GIT_PULL, ID_GIT_PUSH, ID_GIT_SYNC,
                    ID_GIT_FETCH, ID_GIT_PUSH_FORCE, ID_GIT_CHECKOUT, ID_GIT_BRANCH_CREATE, ID_GIT_BRANCH_DELETE,
                    ID_GIT_MERGE, ID_GIT_STASH, ID_GIT_STASH_POP, ID_GIT_REMOTE_ADD, ID_GIT_REMOTE_REMOVE, ID_GIT_LOG)
        for id in repoCmds:
            EnableMenuItem(m, id, MF_ENABLED if repo else MF_GRAYED)
        EnableMenuItem(m, ID_GIT_INIT, MF_GRAYED if repo else MF_ENABLED)
        hasOrigin = "origin" in M.status.remotes
        EnableMenuItem(m, ID_GIT_PUBLISH, MF_ENABLED if (M.folder and not hasOrigin) else MF_GRAYED)


def SaveTab(i, saveAs):
    M = MW.M
    if i < 0 or i >= len(M.tabs) or M.tabs[i].kind != MW.Tab.Editor:
        return False
    t = M.tabs[i]
    ed = t.ed
    path = ed.buf.path
    wasNew = not path
    if saveAs or not path:
        p = C.JoinPath(M.folder, ed.untitledName + ".md") if not path else path
        if not path and not M.folder:
            p = ed.untitledName + ".md"
        ok, p = dialogs.SaveFileDlg(M.hwnd, p)
        if not ok:
            return False
        path = p
    ok, err = ed.Save(path)
    if not ok:
        C.MsgBox(M.hwnd, "Could not save " + path + "\n\n" + err, MB_ICONERROR)
        return False
    t.key = "file:" + C.LowerW(ed.buf.path)
    if wasNew or saveAs:
        Explorer.Refresh()
    MW.UpdateTitle()
    MW.InvalidateChrome()
    app.RefreshGit()
    return True


def ConfirmCloseAll():
    M = MW.M
    for i, t in enumerate(list(M.tabs)):
        if t.kind != MW.Tab.Editor or not t.ed.buf.Modified():
            continue
        MW.ActivateTab(i)
        r = C.MsgBox(M.hwnd, "Do you want to save the changes you made to " + t.ed.Title() + "?",
                     MB_YESNOCANCEL | MB_ICONWARNING)
        if r == IDCANCEL:
            return False
        if r == IDYES and not SaveTab(i, False):
            return False
    return True


def SetZoom(size):
    M = MW.M
    C.g_settings.fontSize = max(6, min(72, size))
    editor_view.CreateEditorFonts()
    for t in M.tabs:
        if t.kind == MW.Tab.Editor:
            t.ed.FontChanged()
        else:
            InvalidateRect(t.hwnd, None, False)
    Term.FontChanged()
    C.SaveSettings()


def ChooseEditorFont():
    M = MW.M
    lf = LOGFONTW()
    GetObjectW(editor_view.g_ef.normal, sizeof(lf), byref(lf))
    lf.lfHeight = -MulDiv(C.g_settings.fontSize, 96, 72)  # dialog works at 96 DPI logical
    dc = GetDC(M.hwnd)
    lf.lfHeight = -MulDiv(C.g_settings.fontSize, GetDeviceCaps(dc, LOGPIXELSY), 72)
    cf = CHOOSEFONTW()
    cf.lStructSize = sizeof(cf)
    cf.hwndOwner = M.hwnd
    cf.hDC = dc
    cf.lpLogFont = addressof(lf)
    cf.Flags = CF_SCREENFONTS | CF_FIXEDPITCHONLY | CF_INITTOLOGFONTSTRUCT | CF_NOVERTFONTS
    ok = ChooseFontW(byref(cf))
    ReleaseDC(M.hwnd, dc)
    if not ok:
        return
    C.g_settings.fontName = lf.lfFaceName
    SetZoom(cf.iPointSize // 10)


def OnCommand(id):
    M = MW.M
    ed = MW.ActiveEditor()
    if ID_FILE_RECENT0 <= id < ID_FILE_RECENT0 + 10:
        i = id - ID_FILE_RECENT0
        if i < len(C.g_settings.recentFolders):
            f = C.g_settings.recentFolders[i]
            app.OpenFolder(f)
        return
    if ID_GIT_INIT <= id <= ID_GIT_UNDO_COMMIT:
        git_commands.GitCommand(id)
        return
    if id == ID_FILE_NEW:
        nv, err = editor_view.EditorView.Create(M.hwnd, "")
        if not nv:
            return
        nv.untitledName = "Untitled-" + str(M.untitled)
        M.untitled += 1
        MW.AddTab(MW.Tab(MW.Tab.Editor, nv.hwnd, nv, "new:" + nv.untitledName))
    elif id == ID_FILE_OPEN:
        ok, p = dialogs.OpenFileDlg(M.hwnd)
        if ok:
            app.OpenFile(p)
    elif id == ID_FILE_OPENFOLDER:
        f = M.folder
        ok, f = dialogs.BrowseFolder(M.hwnd, "Open Folder", f)
        if ok:
            app.OpenFolder(f)
    elif id == ID_FILE_SAVE:
        SaveTab(M.active, False)
    elif id == ID_FILE_SAVEAS:
        SaveTab(M.active, True)
    elif id == ID_FILE_SAVEALL:
        for i in range(len(M.tabs)):
            if M.tabs[i].kind == MW.Tab.Editor and M.tabs[i].ed.buf.Modified():
                SaveTab(i, False)
    elif id == ID_FILE_CLOSETAB:
        MW.CloseTab(M.active)
    elif id == ID_FILE_CLOSEFOLDER:
        M.folder = ""
        M.status = git.GitStatus()
        Explorer.SetRoot("")
        Scm.Update(M.status)
        MW.UpdateTitle()
        InvalidateRect(M.hwnd, None, False)
    elif id == ID_FILE_EXIT:
        PostMessageW(M.hwnd, WM_CLOSE, 0, 0)
    elif id == ID_FILE_REVEAL:
        if ed:
            C.RevealInExplorer(ed.buf.path)

    elif id == ID_EDIT_UNDO:
        if ed:
            ed.Undo()
    elif id == ID_EDIT_REDO:
        if ed:
            ed.Redo()
    elif id == ID_EDIT_CUT:
        if ed:
            ed.Cut()
    elif id == ID_EDIT_COPY:
        if ed:
            ed.Copy()
    elif id == ID_EDIT_PASTE:
        if ed:
            ed.Paste()
    elif id == ID_EDIT_SELECTALL:
        if ed:
            ed.SelectAll()
    elif id == ID_EDIT_GOTO or id == ID_PALETTE_GOTO:
        if ed:
            Palette.Show(":")
    elif id == ID_PALETTE_COMMANDS:
        Palette.Show(">")
    elif id == ID_PALETTE_FILES:
        Palette.Show("")
    elif id == ID_FILE_OPENRECENT:
        Palette.ShowRecent()
    elif id == ID_FIND_SHOW or id == ID_FIND_REPLACE:
        if ed:
            FindBar.Show(id == ID_FIND_REPLACE)
    elif id == ID_FIND_NEXT or id == ID_FIND_PREV:
        if not ed:
            return
        if not FindBar.Query().needle:
            FindBar.Show(False)
            return
        ed.Find(FindBar.Query(), id == ID_FIND_NEXT)
    elif id == ID_FIND_REPLACEONE:
        if ed:
            ed.ReplaceOne(FindBar.Query(), FindBar.Replacement())
    elif id == ID_FIND_REPLACEALL:
        if ed:
            ed.ReplaceAll(FindBar.Query(), FindBar.Replacement())
    elif id == ID_FIND_CLOSE:
        FindBar.Hide()

    elif id == ID_VIEW_EXPLORER or id == ID_VIEW_SCM:
        mode = 0 if id == ID_VIEW_EXPLORER else 1
        if C.g_settings.showSidebar and M.sidebarMode == mode and GetKeyState(VK_SHIFT) < 0:
            return
        M.sidebarMode = mode
        C.g_settings.showSidebar = True
        MW.Layout()
        if mode == 1:
            Scm.FocusMessage()
        else:
            SetFocus(M.explorer)
    elif id == ID_VIEW_SIDEBAR:
        C.g_settings.showSidebar = not C.g_settings.showSidebar
        MW.Layout()
        C.SaveSettings()
    elif id == ID_VIEW_OUTPUT:
        if C.g_settings.showOutput and M.panelTab == MW.MainState.PanelOutput:
            MW.HidePanel()
        else:
            MW.ShowPanel(MW.MainState.PanelOutput)
    elif id == ID_VIEW_TERMINAL:
        if C.g_settings.showOutput and M.panelTab == MW.MainState.PanelTerminal and Term.HasFocus():
            MW.HidePanel()
        else:
            MW.ShowPanel(MW.MainState.PanelTerminal)
            if not Term.Focus():
                Term.New()
    elif id == ID_TERM_NEW:
        MW.ShowPanel(MW.MainState.PanelTerminal)
        Term.New()
    elif id == ID_TERM_SPLIT:
        MW.ShowPanel(MW.MainState.PanelTerminal)
        Term.Split()
    elif id == ID_TERM_KILL:
        Term.KillActive()
    elif id == ID_VIEW_WRAP:
        C.g_settings.wrap = not C.g_settings.wrap
        for t in M.tabs:
            if t.kind == MW.Tab.Editor:
                t.ed.SetWrap(C.g_settings.wrap)
        if ed and C.g_settings.wrap and not ed.WrapActive():
            app.SetStatusText("Word wrap is disabled for files larger than 64 MB")
        C.SaveSettings()
    elif id == ID_VIEW_DARK:
        C.g_settings.dark = not C.g_settings.dark
        MW.ApplyThemeAll()
        C.SaveSettings()
    elif id == ID_VIEW_FONT:
        ChooseEditorFont()
    elif id == ID_VIEW_ZOOMIN:
        SetZoom(C.g_settings.fontSize + 1)
    elif id == ID_VIEW_ZOOMOUT:
        SetZoom(C.g_settings.fontSize - 1)
    elif id == ID_VIEW_ZOOMRESET:
        SetZoom(11)
    elif id == ID_VIEW_NEXTTAB or id == ID_VIEW_PREVTAB:
        if len(M.tabs) > 1:
            n = len(M.tabs)
            MW.ActivateTab((M.active + (1 if id == ID_VIEW_NEXTTAB else n - 1)) % n)
    elif id == ID_HELP_ABOUT:
        C.MsgBox(M.hwnd,
                 "ditto 1.0\n\nA lightweight text editor with Markdown highlighting and a built-in git client.\n\n"
                 "Large files are memory-mapped and indexed in the background, so multi-gigabyte files open "
                 "instantly.\n\n"
                 "Git: " + git.GitExe(),
                 MB_OK | MB_ICONINFORMATION)
