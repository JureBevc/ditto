# Interfaces between the main window and the panels / git commands (port of app.h).
# The App namespace functions are implemented in main_window.py as App_<Name>; this module forwards to them
# so panels can `import app` without importing the main window at load time.
import common as C


class DiffSpec:
    Unstaged, Staged, Untracked, Commit = range(4)

    def __init__(self, kind=0, root="", path="", origPath="", commit="", parent=""):
        self.kind = kind
        self.root = root          # repository root
        self.path = path          # repo-relative, forward slashes
        self.origPath = origPath  # rename source (optional)
        self.commit = commit      # for Commit diffs (parent empty => root commit)
        self.parent = parent

    def copy(self):
        return DiffSpec(self.kind, self.root, self.path, self.origPath, self.commit, self.parent)


def _mw():
    import main_window
    return main_window


def Main():
    return _mw().App_Main()


def OpenFile(path, line=-1):
    return _mw().App_OpenFile(path, line)


def OpenDiff(spec):
    return _mw().App_OpenDiff(spec)


def OpenLog():
    return _mw().App_OpenLog()


def OpenFolder(folder):
    return _mw().App_OpenFolder(folder)


def Folder():
    return _mw().App_Folder()


def Status():
    return _mw().App_Status()


def RefreshGit():
    return _mw().App_RefreshGit()


def SetStatusText(s):
    return _mw().App_SetStatusText(s)


def AppendOutput(s):
    return _mw().App_AppendOutput(s)


def ShowOutput():
    return _mw().App_ShowOutput()


def ReloadUnmodifiedDocs():
    return _mw().App_ReloadUnmodifiedDocs()


def ShowScm():
    return _mw().App_ShowScm()
