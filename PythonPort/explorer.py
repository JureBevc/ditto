# Folder tree (lazy loaded) in the sidebar. (port of explorer.cpp, namespace Explorer in app.h)
import functools
import itertools

import common as C
import win32 as W
from win32 import *  # noqa: F401,F403
import app
import dialogs

# ---- TreeView bits not in win32.py ----
WC_TREEVIEWW = "SysTreeView32"
TV_FIRST = 0x1100
TVM_INSERTITEMW = TV_FIRST + 50
TVM_DELETEITEM = TV_FIRST + 1
TVM_EXPAND = TV_FIRST + 2
TVM_GETITEMRECT = TV_FIRST + 4
TVM_SETIMAGELIST = TV_FIRST + 9
TVM_GETNEXTITEM = TV_FIRST + 10
TVM_SELECTITEM = TV_FIRST + 11
TVM_HITTEST = TV_FIRST + 17
TVM_SETITEMHEIGHT = TV_FIRST + 27
TVM_SETBKCOLOR = TV_FIRST + 29
TVM_SETTEXTCOLOR = TV_FIRST + 30
TVM_GETITEMSTATE = TV_FIRST + 39
TVM_SETLINECOLOR = TV_FIRST + 40
TVM_SETEXTENDEDSTYLE = TV_FIRST + 44
TVM_GETITEMW = TV_FIRST + 62
TVM_SETITEMW = TV_FIRST + 63
TVM_EDITLABELW = TV_FIRST + 65

TVGN_ROOT = 0x0
TVGN_NEXT = 0x1
TVGN_CHILD = 0x4
TVGN_FIRSTVISIBLE = 0x5
TVGN_NEXTVISIBLE = 0x6
TVGN_CARET = 0x9

TVI_ROOT = -0x10000
TVI_LAST = -0xFFFE

TVIF_TEXT = 0x1
TVIF_IMAGE = 0x2
TVIF_PARAM = 0x4
TVIF_STATE = 0x8
TVIF_SELECTEDIMAGE = 0x20
TVIF_CHILDREN = 0x40
TVIS_EXPANDED = 0x20
TVE_EXPAND = 0x2
TVSIL_NORMAL = 0
TVC_BYMOUSE = 0x1
TVHT_ONITEM = 0x46

TVS_HASBUTTONS = 0x1
TVS_EDITLABELS = 0x8
TVS_SHOWSELALWAYS = 0x20
TVS_TRACKSELECT = 0x200
TVS_FULLROWSELECT = 0x1000
TVS_EX_DOUBLEBUFFER = 0x4
TVS_EX_FADEINOUTEXPANDOS = 0x40

_U = 0xFFFFFFFF  # notification codes are UINT in NMHDR
NM_CLICK = -2 & _U
NM_RCLICK = -5 & _U
NM_CUSTOMDRAW = -12 & _U
TVN_FIRST = -400
TVN_SELCHANGEDW = (TVN_FIRST - 51) & _U
TVN_ITEMEXPANDINGW = (TVN_FIRST - 54) & _U
TVN_DELETEITEMW = (TVN_FIRST - 58) & _U
TVN_BEGINLABELEDITW = (TVN_FIRST - 59) & _U
TVN_ENDLABELEDITW = (TVN_FIRST - 60) & _U
TVN_KEYDOWN = (TVN_FIRST - 12) & _U


class TVITEMW(Structure):
    _fields_ = [("mask", UINT), ("hItem", c_void_p), ("state", UINT), ("stateMask", UINT), ("pszText", LPWSTR),
                ("cchTextMax", c_int), ("iImage", c_int), ("iSelectedImage", c_int), ("cChildren", c_int),
                ("lParam", LPARAM)]


class TVITEMEXW(Structure):
    _fields_ = TVITEMW._fields_ + [("iIntegral", c_int), ("uStateEx", UINT), ("hwnd", HWND),
                                   ("iExpandedImage", c_int), ("iReserved", c_int)]


class _TVINSERTITEMUNION(Union):
    _fields_ = [("itemex", TVITEMEXW), ("item", TVITEMW)]


class TVINSERTSTRUCTW(Structure):
    _anonymous_ = ("u",)
    _fields_ = [("hParent", c_void_p), ("hInsertAfter", c_void_p), ("u", _TVINSERTITEMUNION)]


class NMTREEVIEWW(Structure):
    _fields_ = [("hdr", NMHDR), ("action", UINT), ("itemOld", TVITEMW), ("itemNew", TVITEMW), ("ptDrag", POINT)]


class NMTVKEYDOWN(Structure):
    _pack_ = 1  # commctrl.h wraps this struct in #include <pshpack1.h>
    _fields_ = [("hdr", NMHDR), ("wVKey", WORD), ("flags", UINT)]


class NMTVDISPINFOW(Structure):
    _fields_ = [("hdr", NMHDR), ("item", TVITEMW)]


class TVHITTESTINFO(Structure):
    _fields_ = [("pt", POINT), ("flags", UINT), ("hItem", c_void_p)]


# ---- TreeView_* macros ----
def _send(msg, wp, lp):
    return SendMessageW(g_tree, msg, wp, lp)


def TreeView_GetNextItem(it, code):
    return _send(TVM_GETNEXTITEM, code, it or 0) or None


def TreeView_GetRoot(t=None):
    return TreeView_GetNextItem(None, TVGN_ROOT)


def TreeView_GetChild(t, it):
    return TreeView_GetNextItem(it, TVGN_CHILD)


def TreeView_GetNextSibling(t, it):
    return TreeView_GetNextItem(it, TVGN_NEXT)


def TreeView_GetNextVisible(t, it):
    return TreeView_GetNextItem(it, TVGN_NEXTVISIBLE)


def TreeView_GetSelection(t=None):
    return TreeView_GetNextItem(None, TVGN_CARET)


def TreeView_DeleteItem(t, it):
    return _send(TVM_DELETEITEM, 0, it)


def TreeView_DeleteAllItems(t):
    return _send(TVM_DELETEITEM, 0, TVI_ROOT)


def TreeView_Expand(t, it, code):
    return _send(TVM_EXPAND, code, it)


def TreeView_Select(t, it, code):
    return _send(TVM_SELECTITEM, code, it or 0)


def TreeView_SelectItem(t, it):
    return TreeView_Select(t, it, TVGN_CARET)


def TreeView_EditLabel(t, it):
    return _send(TVM_EDITLABELW, 0, it)


def TreeView_GetItemState(t, it, mask):
    return _send(TVM_GETITEMSTATE, it, mask)


def TreeView_HitTest(t, hi):
    return _send(TVM_HITTEST, 0, addressof(hi)) or None


def TreeView_GetItemRect(t, it, r, textOnly):
    c_void_p.from_buffer(r).value = it
    return _send(TVM_GETITEMRECT, 1 if textOnly else 0, addressof(r))


# ---- state ----
class Node:
    __slots__ = ("path", "dir", "loaded")

    def __init__(self, path, dir):
        self.path = path
        self.dir = dir
        self.loaded = False


g_tree = None
g_root = ""
g_allowEdit = False  # label editing only via F2 / menu, not click-on-selected
_nodes = {}  # lParam id -> Node (the C++ stores Node* in the item's lParam)
_node_ids = itertools.count(1)


def NodeOf(it):
    tv = TVITEMW()
    tv.mask = TVIF_PARAM
    tv.hItem = it
    if not _send(TVM_GETITEMW, 0, addressof(tv)):
        return None
    return _nodes.get(tv.lParam)


def IconIndex(path, dir, open):
    fi = SHFILEINFOW()
    flags = SHGFI_SYSICONINDEX | SHGFI_SMALLICON | SHGFI_USEFILEATTRIBUTES | (SHGFI_OPENICON if open else 0)
    SHGetFileInfoW(path, FILE_ATTRIBUTE_DIRECTORY if dir else FILE_ATTRIBUTE_NORMAL, byref(fi), sizeof(fi), flags)
    return fi.iIcon


def Insert(parent, name, path, dir):
    key = next(_node_ids)
    _nodes[key] = Node(path, dir)
    is_ = TVINSERTSTRUCTW()
    is_.hParent = parent
    is_.hInsertAfter = TVI_LAST
    is_.item.mask = TVIF_TEXT | TVIF_PARAM | TVIF_CHILDREN | TVIF_IMAGE | TVIF_SELECTEDIMAGE
    is_.item.pszText = name
    is_.item.cChildren = 1 if dir else 0
    is_.item.lParam = key
    is_.item.iImage = IconIndex(path, dir, False)
    is_.item.iSelectedImage = is_.item.iImage
    r = _send(TVM_INSERTITEMW, 0, addressof(is_)) or None
    if not r:
        _nodes.pop(key, None)
    return r


def _LogicalCmp(a, b):
    if a[1] != b[1]:
        return -1 if a[1] else 1
    return StrCmpLogicalW(a[0], b[0])


def Populate(item):
    n = NodeOf(item)
    if not n or not n.dir:
        return
    # remove existing children
    while True:
        c = TreeView_GetChild(g_tree, item)
        if not c:
            break
        TreeView_DeleteItem(g_tree, c)
    n.loaded = True
    ents = []  # (name, dir)
    fd = WIN32_FIND_DATAW()
    h = FindFirstFileExW(C.JoinPath(n.path, "*"), FindExInfoBasic, byref(fd), FindExSearchNameMatch, None,
                         FIND_FIRST_EX_LARGE_FETCH)
    if h and h != INVALID_HANDLE_VALUE:
        while True:
            name = fd.cFileName
            if not (name == "." or name == ".." or name == ".git") and ".~mye-" not in name:
                ents.append((name, (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0))
            if not FindNextFileW(h, byref(fd)):
                break
        FindClose(h)
    ents.sort(key=functools.cmp_to_key(_LogicalCmp))
    for name, d in ents:
        Insert(item, name, C.JoinPath(n.path, name), d)
    if not ents:
        tv = TVITEMW()
        tv.mask = TVIF_CHILDREN
        tv.hItem = item
        tv.cChildren = 0
        _send(TVM_SETITEMW, 0, addressof(tv))


def CollectExpanded(it, out):
    while it:
        n = NodeOf(it)
        if n and n.dir and (TreeView_GetItemState(g_tree, it, TVIS_EXPANDED) & TVIS_EXPANDED):
            out.add(C.LowerW(n.path))
            CollectExpanded(TreeView_GetChild(g_tree, it), out)
        it = TreeView_GetNextSibling(g_tree, it)


def Reexpand(it, exp):
    while it:
        n = NodeOf(it)
        if n and n.dir and C.LowerW(n.path) in exp:
            TreeView_Expand(g_tree, it, TVE_EXPAND)
            Reexpand(TreeView_GetChild(g_tree, it), exp)
        it = TreeView_GetNextSibling(g_tree, it)


def TargetDir(it):
    n = NodeOf(it) if it else None
    if not n:
        return g_root
    return n.path if n.dir else C.DirOf(n.path)


def FindItem(it, path):
    while it:
        n = NodeOf(it)
        if n and C.PathEqualsI(n.path, path):
            return it
        if n and n.dir and n.loaded:
            r = FindItem(TreeView_GetChild(g_tree, it), path)
            if r:
                return r
        it = TreeView_GetNextSibling(g_tree, it)
    return None


def RefreshDir(dir):
    it = FindItem(TreeView_GetRoot(g_tree), dir)
    if not it:
        Refresh()
        return
    exp = set()
    CollectExpanded(TreeView_GetChild(g_tree, it), exp)
    Populate(it)
    TreeView_Expand(g_tree, it, TVE_EXPAND)
    Reexpand(TreeView_GetChild(g_tree, it), exp)


def _DeletePath(owner, path):
    """SHFileOperationW(FO_DELETE, FOF_ALLOWUNDO) on one path; returns True if it was deleted."""
    frm = create_unicode_buffer(path + "\0")  # double-NUL terminated list
    op = SHFILEOPSTRUCTW()
    op.hwnd = owner
    op.wFunc = FO_DELETE
    op.pFrom = addressof(frm)
    op.fFlags = FOF_ALLOWUNDO
    return SHFileOperationW(byref(op)) == 0 and not op.fAnyOperationsAborted


def ContextMenu(it, pt):
    global g_allowEdit
    n = NodeOf(it) if it else None
    m = CreatePopupMenu()
    if n and not n.dir:
        AppendMenuW(m, MF_STRING, 1, "Open")
    AppendMenuW(m, MF_STRING, 2, "New File...")
    AppendMenuW(m, MF_STRING, 3, "New Folder...")
    if n and it != TreeView_GetRoot(g_tree):
        AppendMenuW(m, MF_SEPARATOR, 0, None)
        AppendMenuW(m, MF_STRING, 4, "Rename...\tF2")
        AppendMenuW(m, MF_STRING, 5, "Delete\tDel")
    AppendMenuW(m, MF_SEPARATOR, 0, None)
    if n:
        AppendMenuW(m, MF_STRING, 6, "Copy Path")
        AppendMenuW(m, MF_STRING, 7, "Reveal in File Explorer\tAlt+Shift+R")
    AppendMenuW(m, MF_STRING, 8, "Refresh")
    cmd = TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTBUTTON, pt.x, pt.y, 0, GetParent(g_tree), None)
    DestroyMenu(m)
    owner = app.Main()
    if cmd == 1:
        app.OpenFile(n.path)
    elif cmd == 2 or cmd == 3:
        dir = TargetDir(it)
        name = ""
        ok, name = dialogs.InputBox(owner, "New File" if cmd == 2 else "New Folder", "Name (in " + dir + "):", name)
        if not ok:
            return
        name = C.Trim(name)
        if not name:
            return
        full = C.JoinPath(dir, C.ToBackslashes(name))
        if cmd == 2:
            SHCreateDirectoryExW(None, C.DirOf(full), None)
            h = CreateFileW(full, GENERIC_WRITE, 0, None, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, None)
            if not h or h == INVALID_HANDLE_VALUE:
                C.MsgBox(owner, "Could not create file:\n" + C.FormatError(GetLastError()), MB_ICONERROR)
                return
            CloseHandle(h)
            RefreshDir(dir)
            app.OpenFile(full)
        else:
            r = SHCreateDirectoryExW(None, full, None)
            if r != ERROR_SUCCESS:
                C.MsgBox(owner, "Could not create folder:\n" + C.FormatError(r & 0xFFFFFFFF), MB_ICONERROR)
                return
            RefreshDir(dir)
        app.RefreshGit()
    elif cmd == 4:
        g_allowEdit = True
        TreeView_EditLabel(g_tree, it)
    elif cmd == 5:
        dir = C.DirOf(n.path)
        if _DeletePath(owner, n.path):
            RefreshDir(dir)
            app.RefreshGit()
    elif cmd == 6:
        C.SetClipboardText(owner, n.path)
    elif cmd == 7:
        C.RevealInExplorer(n.path)
    elif cmd == 8:
        Refresh()


def HostProc(h, m, w, l):
    global g_allowEdit
    if m == WM_SIZE:
        MoveWindow(g_tree, 0, 0, LOWORD(l), HIWORD(l), True)
        return 0
    if m == WM_NOTIFY:
        nm = NMHDR.from_address(l)
        if nm.hwndFrom != g_tree:
            return DefWindowProcW(h, m, w, l)
        code = nm.code
        if code == TVN_ITEMEXPANDINGW:
            tv = NMTREEVIEWW.from_address(l)
            n = _nodes.get(tv.itemNew.lParam)
            if tv.action == TVE_EXPAND and n and n.dir and not n.loaded:
                Populate(tv.itemNew.hItem)
            return 0
        if code == TVN_DELETEITEMW:
            tv = NMTREEVIEWW.from_address(l)
            _nodes.pop(tv.itemOld.lParam, None)
            return 0
        if code == TVN_SELCHANGEDW:
            tv = NMTREEVIEWW.from_address(l)
            n = _nodes.get(tv.itemNew.lParam)
            if tv.action == TVC_BYMOUSE and n and not n.dir:
                app.OpenFile(n.path)
            return 0
        if code == NM_CLICK:
            # clicking the already-selected file re-opens it (e.g. after its tab was closed)
            hi = TVHITTESTINFO()
            GetCursorPos(byref(hi.pt))
            ScreenToClient(g_tree, byref(hi.pt))
            it = TreeView_HitTest(g_tree, hi)
            if it and it == TreeView_GetSelection(g_tree) and (hi.flags & TVHT_ONITEM):
                n = NodeOf(it)
                if n and not n.dir:
                    app.OpenFile(n.path)
            return 0
        if code == TVN_KEYDOWN:
            kd = NMTVKEYDOWN.from_address(l)
            it = TreeView_GetSelection(g_tree)
            n = NodeOf(it) if it else None
            if kd.wVKey == VK_RETURN and n and not n.dir:
                app.OpenFile(n.path)
            elif kd.wVKey == VK_F2 and it:
                g_allowEdit = True
                TreeView_EditLabel(g_tree, it)
            elif kd.wVKey == VK_DELETE and it and it != TreeView_GetRoot(g_tree):
                r = RECT()
                TreeView_GetItemRect(g_tree, it, r, True)
                pt = POINT(r.left, r.bottom)
                ClientToScreen(g_tree, byref(pt))
                dir = C.DirOf(n.path)
                if _DeletePath(app.Main(), n.path):
                    RefreshDir(dir)
                    app.RefreshGit()
            elif kd.wVKey == VK_F5:
                Refresh()
            return 0
        if code == TVN_BEGINLABELEDITW:
            di = NMTVDISPINFOW.from_address(l)
            deny = not g_allowEdit or di.item.hItem == TreeView_GetRoot(g_tree)
            g_allowEdit = False
            return 1 if deny else 0
        if code == TVN_ENDLABELEDITW:
            di = NMTVDISPINFOW.from_address(l)
            text = di.item.pszText
            if text is None:
                return 0
            n = _nodes.get(di.item.lParam)
            name = C.Trim(text)
            if not n or not name:
                return 0
            to = C.JoinPath(C.DirOf(n.path), name)
            if not MoveFileExW(n.path, to, 0):
                C.MsgBox(app.Main(), "Rename failed:\n" + C.FormatError(GetLastError()), MB_ICONERROR)
                return 0
            dir = C.DirOf(n.path)

            def _after():
                RefreshDir(dir)
                app.RefreshGit()
            C.RunOnUi(_after)
            return 1
        if code == NM_RCLICK:
            hi = TVHITTESTINFO()
            GetCursorPos(byref(hi.pt))
            screen = POINT(hi.pt.x, hi.pt.y)
            ScreenToClient(g_tree, byref(hi.pt))
            it = TreeView_HitTest(g_tree, hi)
            if it:
                TreeView_SelectItem(g_tree, it)
            ContextMenu(it, screen)
            return 1
        # NM_CUSTOMDRAW: default
    return DefWindowProcW(h, m, w, l)


# ---- Explorer namespace ----
def Create(parent):
    global g_tree
    C.RegisterWindowClass("MyExplorerHost", HostProc, cursor=IDC_ARROW)
    host = CreateWindowExW(0, "MyExplorerHost", "", WS_CHILD | WS_CLIPCHILDREN, 0, 0, 0, 0, parent, None,
                           C.g_hinst, None)
    g_tree = CreateWindowExW(0, WC_TREEVIEWW, "",
                             WS_CHILD | WS_VISIBLE | WS_TABSTOP | TVS_HASBUTTONS | TVS_SHOWSELALWAYS |
                             TVS_FULLROWSELECT | TVS_EDITLABELS | TVS_TRACKSELECT,
                             0, 0, 0, 0, host, None, C.g_hinst, None)
    _send(TVM_SETEXTENDEDSTYLE, TVS_EX_DOUBLEBUFFER | TVS_EX_FADEINOUTEXPANDOS,
          TVS_EX_DOUBLEBUFFER | TVS_EX_FADEINOUTEXPANDOS)
    SendMessageW(g_tree, WM_SETFONT, C.g_uiFont or 0, False)
    fi = SHFILEINFOW()
    il = SHGetFileInfoW("C:\\", 0, byref(fi), sizeof(fi), SHGFI_SYSICONINDEX | SHGFI_SMALLICON)
    _send(TVM_SETIMAGELIST, TVSIL_NORMAL, il)
    ThemeChanged()
    return host


def SetRoot(folder):
    global g_root
    g_root = folder
    TreeView_DeleteAllItems(g_tree)
    if not folder:
        return
    name = C.FileNameOf(folder)
    if not name:
        name = folder
    root = Insert(TVI_ROOT, name, folder, True)
    TreeView_Expand(g_tree, root, TVE_EXPAND)


def Refresh():
    if not g_root:
        return
    root = TreeView_GetRoot(g_tree)
    if not root:
        SetRoot(g_root)
        return
    exp = set()
    CollectExpanded(root, exp)
    selPath = ""
    sel = TreeView_GetSelection(g_tree)
    if sel and NodeOf(sel):
        selPath = NodeOf(sel).path
    scroll = GetScrollPos(g_tree, SB_VERT)
    SendMessageW(g_tree, WM_SETREDRAW, False, 0)
    Populate(root)
    TreeView_Expand(g_tree, root, TVE_EXPAND)
    Reexpand(TreeView_GetChild(g_tree, root), exp)
    if selPath:
        it = FindItem(root, selPath)
        if it:
            TreeView_Select(g_tree, it, TVGN_CARET)
    SendMessageW(g_tree, WM_SETREDRAW, True, 0)
    # restore approximate scroll position
    first = TreeView_GetRoot(g_tree)
    i = 0
    while i < scroll and first:
        first = TreeView_GetNextVisible(g_tree, first)
        i += 1
    if first:
        TreeView_Select(g_tree, first, TVGN_FIRSTVISIBLE)
    InvalidateRect(g_tree, None, True)


def ThemeChanged():
    _send(TVM_SETBKCOLOR, 0, C.g_theme.panelBg)
    _send(TVM_SETTEXTCOLOR, 0, C.g_theme.panelFg)
    _send(TVM_SETLINECOLOR, 0, C.g_theme.border)
    SetWindowTheme(g_tree, "DarkMode_Explorer" if C.g_settings.dark else "Explorer", None)
    _send(TVM_SETITEMHEIGHT, C.S(22), 0)
    InvalidateRect(g_tree, None, True)
