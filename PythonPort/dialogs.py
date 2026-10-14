# Modal dialogs (port of dialogs.h / dialogs.cpp).
# The C++ build loads IDD_INPUT / IDD_PICK / IDD_PUBLISH / IDD_CLONE from res/app.rc; here the same
# DLGTEMPLATEEX is built in memory and shown with DialogBoxIndirectParamW.
import itertools
import struct

import common as C
import win32 as W
from win32 import *  # noqa: F401,F403
from resource import (IDD_INPUT, IDD_PICK, IDD_PUBLISH, IDD_CLONE, IDC_PROMPT, IDC_EDIT, IDC_EDIT2, IDC_LIST,
                      IDC_PRIVATE, IDC_PUBLIC, IDC_BROWSE)


# ---------------- in-memory dialog templates (res/app.rc) ----------------
_ATOM_BUTTON = 0x0080
_ATOM_EDIT = 0x0081
_ATOM_STATIC = 0x0082
_ATOM_LISTBOX = 0x0083

_DLG_STYLE = DS_SETFONT | DS_MODALFRAME | DS_CENTER | WS_POPUP | WS_CAPTION | WS_SYSMENU


def _wsz(s):
    return s.encode("utf-16-le") + b"\0\0"


def _align4(b):
    return b + b"\0" * ((-len(b)) % 4)


def _LTEXT(text, id, x, y, cx, cy):
    return (_ATOM_STATIC, text, id, x, y, cx, cy, SS_LEFT | WS_GROUP | WS_CHILD | WS_VISIBLE)


def _EDITTEXT(id, x, y, cx, cy, style):
    return (_ATOM_EDIT, "", id, x, y, cx, cy, ES_LEFT | WS_BORDER | WS_TABSTOP | WS_CHILD | WS_VISIBLE | style)


def _DEFPUSHBUTTON(text, id, x, y, cx, cy):
    return (_ATOM_BUTTON, text, id, x, y, cx, cy, BS_DEFPUSHBUTTON | WS_TABSTOP | WS_CHILD | WS_VISIBLE)


def _PUSHBUTTON(text, id, x, y, cx, cy):
    return (_ATOM_BUTTON, text, id, x, y, cx, cy, BS_PUSHBUTTON | WS_TABSTOP | WS_CHILD | WS_VISIBLE)


def _LISTBOX(id, x, y, cx, cy, style):
    return (_ATOM_LISTBOX, "", id, x, y, cx, cy, LBS_NOTIFY | WS_BORDER | WS_CHILD | WS_VISIBLE | style)


def _CONTROL(text, id, atom, style, x, y, cx, cy):
    return (atom, text, id, x, y, cx, cy, style | WS_CHILD | WS_VISIBLE)


def _BuildTemplate(caption, cx, cy, items):
    """DLGTEMPLATEEX + DLGITEMTEMPLATEEX array, FONT 9, "Segoe UI", 400, 0, 0x1."""
    b = struct.pack("<HHIIIHhhhh", 1, 0xFFFF, 0, 0, _DLG_STYLE, len(items), 0, 0, cx, cy)
    b += b"\0\0"  # no menu
    b += b"\0\0"  # default dialog class
    b += _wsz(caption)
    b += struct.pack("<HHBB", 9, 400, 0, 1) + _wsz("Segoe UI")
    for (atom, text, id, x, y, w, h, style) in items:
        b = _align4(b)
        b += struct.pack("<IIIhhhhI", 0, 0, style & 0xFFFFFFFF, x, y, w, h, id & 0xFFFFFFFF)
        b += struct.pack("<HH", 0xFFFF, atom)
        b += _wsz(text)
        b += struct.pack("<H", 0)  # no creation data
    return create_string_buffer(_align4(b), len(_align4(b)))


_templates = {}


def _Template(idd):
    t = _templates.get(idd)
    if t is not None:
        return t
    if idd == IDD_INPUT:
        t = _BuildTemplate("Input", 280, 74, [
            _LTEXT("", IDC_PROMPT, 7, 7, 266, 18),
            _EDITTEXT(IDC_EDIT, 7, 28, 266, 14, ES_AUTOHSCROLL),
            _DEFPUSHBUTTON("OK", IDOK, 169, 52, 50, 14),
            _PUSHBUTTON("Cancel", IDCANCEL, 223, 52, 50, 14),
        ])
    elif idd == IDD_PICK:
        t = _BuildTemplate("Select", 280, 200, [
            _LTEXT("", IDC_PROMPT, 7, 7, 266, 10),
            _EDITTEXT(IDC_EDIT, 7, 20, 266, 14, ES_AUTOHSCROLL),
            _LISTBOX(IDC_LIST, 7, 38, 266, 134, LBS_NOTIFY | LBS_NOINTEGRALHEIGHT | WS_VSCROLL | WS_TABSTOP),
            _DEFPUSHBUTTON("OK", IDOK, 169, 178, 50, 14),
            _PUSHBUTTON("Cancel", IDCANCEL, 223, 178, 50, 14),
        ])
    elif idd == IDD_PUBLISH:
        t = _BuildTemplate("Publish to GitHub", 280, 122, [
            _LTEXT("Repository name:", -1, 7, 7, 266, 10),
            _EDITTEXT(IDC_EDIT, 7, 18, 266, 14, ES_AUTOHSCROLL),
            _LTEXT("Description (optional):", -1, 7, 38, 266, 10),
            _EDITTEXT(IDC_EDIT2, 7, 49, 266, 14, ES_AUTOHSCROLL),
            _CONTROL("Private repository", IDC_PRIVATE, _ATOM_BUTTON, BS_AUTORADIOBUTTON | WS_GROUP | WS_TABSTOP,
                     7, 70, 140, 10),
            _CONTROL("Public repository", IDC_PUBLIC, _ATOM_BUTTON, BS_AUTORADIOBUTTON, 7, 83, 140, 10),
            _DEFPUSHBUTTON("Publish", IDOK, 169, 102, 50, 14),
            _PUSHBUTTON("Cancel", IDCANCEL, 223, 102, 50, 14),
        ])
    elif idd == IDD_CLONE:
        t = _BuildTemplate("Clone Repository", 280, 96, [
            _LTEXT("Repository URL:", -1, 7, 7, 266, 10),
            _EDITTEXT(IDC_EDIT, 7, 18, 266, 14, ES_AUTOHSCROLL),
            _LTEXT("Clone into parent folder:", -1, 7, 38, 266, 10),
            _EDITTEXT(IDC_EDIT2, 7, 49, 236, 14, ES_AUTOHSCROLL),
            _PUSHBUTTON("...", IDC_BROWSE, 247, 49, 26, 14),
            _DEFPUSHBUTTON("Clone", IDOK, 169, 74, 50, 14),
            _PUSHBUTTON("Cancel", IDCANCEL, 223, 74, 50, 14),
        ])
    _templates[idd] = t
    return t


# Per-dialog data: the C++ passes a struct pointer through lParam / DWLP_USER; here lParam is a key into this dict.
_data = {}
_data_ids = itertools.count(1)
_byHwnd = {}


def _MakeDlgProc(fn):
    def _wrap(d, m, w, l):
        try:
            if m == WM_INITDIALOG:
                _byHwnd[d] = _data.get(l)
            p = _byHwnd.get(d)
            if p is None:
                if m == WM_NCDESTROY:
                    _byHwnd.pop(d, None)
                return 0
            r = fn(d, m, w, l, p)
            if m == WM_NCDESTROY:
                _byHwnd.pop(d, None)
            return 0 if r is None else int(r)
        except Exception:
            C.ReportException()
            return 0
    return C.keep(DLGPROC(_wrap))


def _DialogBox(idd, owner, proc, data):
    k = next(_data_ids)
    _data[k] = data
    try:
        return DialogBoxIndirectParamW(C.g_hinst, _Template(idd), owner, proc, k)
    finally:
        _data.pop(k, None)


def GetText(dlg, id):
    h = GetDlgItem(dlg, id)
    return text_of(h)


# ---------------- input ----------------
class InputData:
    def __init__(self, title, prompt, value, password):
        self.title = title
        self.prompt = prompt
        self.value = value
        self.password = password


def _InputProc(d, m, w, l, p):
    if m == WM_INITDIALOG:
        SetWindowTextW(d, p.title)
        SetDlgItemTextW(d, IDC_PROMPT, p.prompt)
        SetDlgItemTextW(d, IDC_EDIT, p.value)
        if p.password:
            SendDlgItemMessageW(d, IDC_EDIT, EM_SETPASSWORDCHAR, 0x25CF, 0)
        SendDlgItemMessageW(d, IDC_EDIT, EM_SETSEL, 0, -1)
        SetFocus(GetDlgItem(d, IDC_EDIT))
        return False
    if m == WM_COMMAND:
        if LOWORD(w) == IDOK:
            p.value = GetText(d, IDC_EDIT)
            EndDialog(d, IDOK)
            return True
        if LOWORD(w) == IDCANCEL:
            EndDialog(d, IDCANCEL)
            return True
    return False


InputProc = _MakeDlgProc(_InputProc)


def InputBox(owner, title, prompt, value, password=False):
    """-> (ok, value)"""
    d = InputData(title, prompt, value, password)
    if _DialogBox(IDD_INPUT, owner, InputProc, d) != IDOK:
        return False, value
    return True, d.value


# ---------------- pick ----------------
class PickData:
    def __init__(self, title, prompt, items):
        self.title = title
        self.prompt = prompt
        self.items = items
        self.shown = []
        self.result = -1


def FillPick(d, p):
    f = C.LowerW(GetText(d, IDC_EDIT))
    lb = GetDlgItem(d, IDC_LIST)
    SendMessageW(lb, WM_SETREDRAW, False, 0)
    SendMessageW(lb, LB_RESETCONTENT, 0, 0)
    p.shown = []
    for i, it in enumerate(p.items):
        if f and C.LowerW(it).find(f) < 0:
            continue
        buf = create_unicode_buffer(it)
        SendMessageW(lb, LB_ADDSTRING, 0, addressof(buf))
        p.shown.append(i)
    SendMessageW(lb, LB_SETCURSEL, 0, 0)
    SendMessageW(lb, WM_SETREDRAW, True, 0)
    InvalidateRect(lb, None, True)


def _PickEditSub(h, m, w, l, uid, ref):
    try:
        if m == WM_KEYDOWN and w in (VK_DOWN, VK_UP, VK_NEXT, VK_PRIOR):
            SendMessageW(GetDlgItem(GetParent(h), IDC_LIST), m, w, l)
            return 0
    except Exception:
        C.ReportException()
    return DefSubclassProc(h, m, w, l)


PickEditSub = C.keep(SUBCLASSPROC(_PickEditSub))


def _PickProc(d, m, w, l, p):
    if m == WM_INITDIALOG:
        SetWindowTextW(d, p.title)
        SetDlgItemTextW(d, IDC_PROMPT, p.prompt)
        cue = create_unicode_buffer("Type to filter")
        SendDlgItemMessageW(d, IDC_EDIT, EM_SETCUEBANNER, True, addressof(cue))
        SetWindowSubclass(GetDlgItem(d, IDC_EDIT), PickEditSub, 1, 0)
        FillPick(d, p)
        SetFocus(GetDlgItem(d, IDC_EDIT))
        return False
    if m == WM_COMMAND:
        if LOWORD(w) == IDC_EDIT and HIWORD(w) == EN_CHANGE:
            FillPick(d, p)
            return True
        if (LOWORD(w) == IDC_LIST and HIWORD(w) == LBN_DBLCLK) or LOWORD(w) == IDOK:
            sel = SendDlgItemMessageW(d, IDC_LIST, LB_GETCURSEL, 0, 0)
            if 0 <= sel < len(p.shown):
                p.result = p.shown[sel]
                EndDialog(d, IDOK)
            return True
        if LOWORD(w) == IDCANCEL:
            EndDialog(d, IDCANCEL)
            return True
    return False


PickProc = _MakeDlgProc(_PickProc)


def PickBox(owner, title, prompt, items, index):
    """Filterable list picker; index refers to the original items list. -> (ok, index)"""
    d = PickData(title, prompt, items)
    if _DialogBox(IDD_PICK, owner, PickProc, d) != IDOK:
        return False, index
    index = d.result
    return index >= 0, index


# ---------------- publish ----------------
class PublishData:
    def __init__(self, name, desc, priv):
        self.name = name
        self.desc = desc
        self.priv = priv


def _PublishProc(d, m, w, l, p):
    if m == WM_INITDIALOG:
        SetDlgItemTextW(d, IDC_EDIT, p.name)
        CheckRadioButton(d, IDC_PRIVATE, IDC_PUBLIC, IDC_PRIVATE if p.priv else IDC_PUBLIC)
        SendDlgItemMessageW(d, IDC_EDIT, EM_SETSEL, 0, -1)
        SetFocus(GetDlgItem(d, IDC_EDIT))
        return False
    if m == WM_COMMAND:
        if LOWORD(w) == IDOK:
            p.name = C.Trim(GetText(d, IDC_EDIT))
            p.desc = C.Trim(GetText(d, IDC_EDIT2))
            p.priv = IsDlgButtonChecked(d, IDC_PRIVATE) == BST_CHECKED
            if not p.name:
                MessageBeep(MB_ICONWARNING)
                return True
            EndDialog(d, IDOK)
            return True
        if LOWORD(w) == IDCANCEL:
            EndDialog(d, IDCANCEL)
            return True
    return False


PublishProc = _MakeDlgProc(_PublishProc)


def PublishBox(owner, name, desc, priv):
    """-> (ok, name, desc, priv)"""
    d = PublishData(name, desc, priv)
    if _DialogBox(IDD_PUBLISH, owner, PublishProc, d) != IDOK:
        return False, name, desc, priv
    return True, d.name, d.desc, d.priv


# ---------------- clone ----------------
class CloneData:
    def __init__(self, url, parent):
        self.url = url
        self.parent = parent


def _CloneProc(d, m, w, l, p):
    if m == WM_INITDIALOG:
        SetDlgItemTextW(d, IDC_EDIT, p.url)
        SetDlgItemTextW(d, IDC_EDIT2, p.parent)
        cue = create_unicode_buffer("https://github.com/user/repo.git")
        SendDlgItemMessageW(d, IDC_EDIT, EM_SETCUEBANNER, True, addressof(cue))
        SetFocus(GetDlgItem(d, IDC_EDIT))
        return False
    if m == WM_COMMAND:
        if LOWORD(w) == IDC_BROWSE:
            f = GetText(d, IDC_EDIT2)
            ok, f = BrowseFolder(d, "Choose parent folder", f)
            if ok:
                SetDlgItemTextW(d, IDC_EDIT2, f)
            return True
        if LOWORD(w) == IDOK:
            p.url = C.Trim(GetText(d, IDC_EDIT))
            p.parent = C.Trim(GetText(d, IDC_EDIT2))
            if not p.url or not C.IsDirectory(p.parent):
                MessageBeep(MB_ICONWARNING)
                return True
            EndDialog(d, IDOK)
            return True
        if LOWORD(w) == IDCANCEL:
            EndDialog(d, IDCANCEL)
            return True
    return False


CloneProc = _MakeDlgProc(_CloneProc)


def CloneBox(owner, url, parent):
    """-> (ok, url, parent)"""
    d = CloneData(url, parent)
    if _DialogBox(IDD_CLONE, owner, CloneProc, d) != IDOK:
        return False, url, parent
    return True, d.url, d.parent


# ---------------- shell dialogs ----------------
CLSID_FileOpenDialog = GUID("{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}")
IID_IFileOpenDialog = GUID("{D57C7288-D4AD-4768-BE02-9D969532D960}")
IID_IShellItem = GUID("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")

# vtable slots: IUnknown 0-2, IModalWindow::Show 3, IFileDialog 4-26, IFileOpenDialog 27-28
_IUnknown_Release = 2
_IModalWindow_Show = 3
_IFileDialog_SetOptions = 9
_IFileDialog_GetOptions = 10
_IFileDialog_SetFolder = 12
_IFileDialog_SetTitle = 17
_IFileDialog_GetResult = 20
_IShellItem_GetDisplayName = 5

_vproto = {}


def _vcall(obj, index, argtypes, *args):
    """Calls COM method `index` of interface pointer obj (int) with HRESULT return."""
    key = tuple(argtypes)
    proto = _vproto.get(key)
    if proto is None:
        proto = WINFUNCTYPE(HRESULT, c_void_p, *argtypes)
        _vproto[key] = proto
    vtbl = cast(c_void_p(obj), POINTER(POINTER(c_void_p)))[0]
    return proto(vtbl[index])(obj, *args)


def _Release(obj):
    vtbl = cast(c_void_p(obj), POINTER(POINTER(c_void_p)))[0]
    WINFUNCTYPE(c_ulong, c_void_p)(vtbl[_IUnknown_Release])(obj)


def BrowseFolder(owner, title, out=""):
    """-> (ok, out)"""
    dlg = c_void_p()
    if FAILED(CoCreateInstance(byref(CLSID_FileOpenDialog), None, CLSCTX_INPROC_SERVER, byref(IID_IFileOpenDialog),
                               byref(dlg))) or not dlg.value:
        return False, out
    dlg = dlg.value
    opts = DWORD(0)
    _vcall(dlg, _IFileDialog_GetOptions, [c_void_p], byref(opts))
    _vcall(dlg, _IFileDialog_SetOptions, [DWORD], opts.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM)
    _vcall(dlg, _IFileDialog_SetTitle, [LPCWSTR], title)
    if out and C.IsDirectory(out):
        init = c_void_p()
        if SUCCEEDED(SHCreateItemFromParsingName(out, None, byref(IID_IShellItem), byref(init))) and init.value:
            _vcall(dlg, _IFileDialog_SetFolder, [c_void_p], init.value)
            _Release(init.value)
    ok = False
    if SUCCEEDED(_vcall(dlg, _IModalWindow_Show, [HWND], owner)):
        item = c_void_p()
        if SUCCEEDED(_vcall(dlg, _IFileDialog_GetResult, [c_void_p], byref(item))) and item.value:
            p = c_void_p()
            if SUCCEEDED(_vcall(item.value, _IShellItem_GetDisplayName, [c_int, c_void_p], SIGDN_FILESYSPATH,
                                byref(p))):
                out = wstring_at(p.value) if p.value else ""
                CoTaskMemFree(p)
                ok = True
            _Release(item.value)
    _Release(dlg)
    return ok, out


kFilter = "All Files (*.*)\0*.*\0Markdown (*.md)\0*.md;*.markdown\0Text (*.txt)\0*.txt\0"  # create_unicode_buffer adds the final terminator


def OpenFileDlg(owner, out=""):
    """-> (ok, out)"""
    buf = create_unicode_buffer(32768)
    flt = create_unicode_buffer(kFilter, len(kFilter) + 1)
    ofn = OPENFILENAMEW()
    ofn.lStructSize = sizeof(ofn)
    ofn.hwndOwner = owner
    ofn.lpstrFilter = cast(flt, c_wchar_p)
    ofn.lpstrFile = addressof(buf)
    ofn.nMaxFile = len(buf)
    ofn.Flags = OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR
    if not GetOpenFileNameW(byref(ofn)):
        return False, out
    return True, buf.value


def SaveFileDlg(owner, inout):
    """-> (ok, inout)"""
    buf = create_unicode_buffer(32768)
    init = (inout or "")[:32767]  # wcsncpy_s(..., _TRUNCATE)
    buf.value = init
    flt = create_unicode_buffer(kFilter, len(kFilter) + 1)
    ofn = OPENFILENAMEW()
    ofn.lStructSize = sizeof(ofn)
    ofn.hwndOwner = owner
    ofn.lpstrFilter = cast(flt, c_wchar_p)
    ofn.lpstrFile = addressof(buf)
    ofn.nMaxFile = len(buf)
    ofn.Flags = OFN_EXPLORER | OFN_OVERWRITEPROMPT | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR
    if not GetSaveFileNameW(byref(ofn)):
        return False, inout
    return True, buf.value
