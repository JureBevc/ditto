# Creates a "ditto" Start Menu shortcut (pythonw.exe + ditto.py, ditto icon) that can be pinned to Start and the
# taskbar. The shortcut carries the same AppUserModelID that ditto.py sets on its process, so the pinned icon and the
# running window share one taskbar button.
# Usage: python create_shortcut.py [--desktop]   (or double-click it)
import ctypes
import os
import sys
from ctypes import POINTER, Structure, byref, c_long, c_uint16, c_ulong, c_void_p, c_wchar_p, sizeof
from ctypes import wintypes as wt

import ditto  # APP_USER_MODEL_ID

HERE = os.path.dirname(os.path.abspath(__file__))
ole32 = ctypes.WinDLL("ole32")
ole32.CoInitializeEx.argtypes = [c_void_p, wt.DWORD]
ole32.CoCreateInstance.argtypes = [c_void_p, c_void_p, wt.DWORD, c_void_p, c_void_p]
ole32.CoCreateInstance.restype = c_long


class GUID(Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", wt.BYTE * 8)]

    def __init__(self, s):
        import uuid
        super().__init__()
        ctypes.memmove(byref(self), uuid.UUID(s).bytes_le, 16)


class PROPERTYKEY(Structure):
    _fields_ = [("fmtid", GUID), ("pid", wt.DWORD)]


class PROPVARIANT(Structure):
    _fields_ = [("vt", c_uint16), ("r1", c_uint16), ("r2", c_uint16), ("r3", c_uint16), ("pwszVal", c_wchar_p),
                ("pad", c_void_p)]


CLSID_ShellLink = GUID("00021401-0000-0000-C000-000000000046")
IID_IShellLinkW = GUID("000214F9-0000-0000-C000-000000000046")
IID_IPropertyStore = GUID("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")
IID_IPersistFile = GUID("0000010B-0000-0000-C000-000000000046")
PKEY_AppUserModel_ID = PROPERTYKEY(GUID("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5)
VT_LPWSTR = 31


def call(obj, index, *args, argtypes=()):
    """Calls vtable slot `index` of COM interface pointer `obj`; raises on a failed HRESULT."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(c_long, c_void_p, *argtypes)(vtbl[index])
    hr = fn(obj, *args)
    if hr < 0:
        raise OSError(f"COM call (slot {index}) failed: 0x{hr & 0xFFFFFFFF:08X}")
    return hr


def query(obj, iid):
    out = c_void_p()
    call(obj, 0, byref(iid), byref(out), argtypes=(c_void_p, c_void_p))
    return out


def release(obj):
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    ctypes.WINFUNCTYPE(c_ulong, c_void_p)(vtbl[2])(obj)


def create(lnk_path):
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pyw):
        pyw = sys.executable
    link = c_void_p()
    hr = ole32.CoCreateInstance(byref(CLSID_ShellLink), None, 1, byref(IID_IShellLinkW), byref(link))
    if hr < 0:
        raise OSError(f"CoCreateInstance failed: 0x{hr & 0xFFFFFFFF:08X}")
    w = (c_wchar_p,)
    call(link, 20, pyw, argtypes=w)                                        # SetPath
    call(link, 11, '"' + os.path.join(HERE, "ditto.py") + '"', argtypes=w)  # SetArguments
    call(link, 9, HERE, argtypes=w)                                        # SetWorkingDirectory
    call(link, 7, "ditto - lightweight text editor and git client", argtypes=w)  # SetDescription
    call(link, 17, os.path.join(HERE, "res", "app.ico"), 0, argtypes=(c_wchar_p, ctypes.c_int))  # SetIconLocation

    store = query(link, IID_IPropertyStore)
    pv = PROPVARIANT()
    pv.vt = VT_LPWSTR
    pv.pwszVal = ditto.APP_USER_MODEL_ID
    call(store, 6, byref(PKEY_AppUserModel_ID), byref(pv), argtypes=(c_void_p, c_void_p))  # SetValue
    call(store, 7)                                                                        # Commit
    release(store)

    pf = query(link, IID_IPersistFile)
    call(pf, 6, lnk_path, True, argtypes=(c_wchar_p, wt.BOOL))  # Save
    release(pf)
    release(link)


def main():
    ole32.CoInitializeEx(None, 2)
    targets = [os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs", "ditto.lnk")]
    if "--desktop" in sys.argv:
        desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
        targets.append(os.path.join(desktop, "ditto.lnk"))
    for t in targets:
        create(t)
        print("Created", t)
    print("\nOpen Start, find 'ditto', right-click it -> Pin to Start / Pin to taskbar.")


if __name__ == "__main__":
    main()
    if len(sys.argv) == 1 and sys.stdin and sys.stdin.isatty():  # keep a double-clicked console open
        try:
            input("Press Enter to close...")
        except EOFError:
            pass
