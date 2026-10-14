# User-facing git operations (menu / Source Control panel commands). Port of git_commands.cpp.
from ctypes import addressof, byref, c_void_p, create_string_buffer, wstring_at

import common as C
import win32 as W
import app
import dialogs
import scm_panel
import explorer
import github
import resource as R
from git import ProcResult, Git, BgRun, GitErrorText, SplitLines

MsgBox = C.MsgBox


def RunGitOp(title, cwd, cmds, reloadDocs, after=None):
    """Runs git commands sequentially in the background; stops at the first failure.
    after(ok: bool, last: ProcResult) runs on the UI thread."""
    app.SetStatusText(title + "...")
    cmds = [list(c) for c in cmds]

    def job():
        last = ProcResult()
        last.code = 0
        ok = True
        for c in cmds:
            last = Git(cwd, c)
            if last.code != 0:
                ok = False
                break

        def ui():
            app.SetStatusText("" if ok else title + " failed")
            if reloadDocs:
                app.ReloadUnmodifiedDocs()
            app.RefreshGit()
            if after:
                after(ok, last)
            elif not ok:
                MsgBox(app.Main(), title + " failed:\n\n" + GitErrorText(last), W.MB_ICONERROR)

        C.RunOnUi(ui)

    BgRun(job)


# ---- (anonymous namespace in C++) ----

def St():
    return app.Status()


def RequireRepo():
    if St().isRepo:
        return True
    if not app.Folder():
        MsgBox(app.Main(), "Open a folder first (File > Open Folder).", W.MB_ICONINFORMATION)
    else:
        MsgBox(app.Main(), "The current folder is not a git repository.\nUse Git > Initialize Repository first.",
               W.MB_ICONINFORMATION)
    return False


def DefaultRemote():
    for r in St().remotes:
        if r == "origin":
            return r
    return "" if not St().remotes else St().remotes[0]


def QueryLines(args, fn):
    """Runs `git <args>` in the background and hands the output lines to fn on the UI thread."""
    root = St().root
    args = list(args)

    def job():
        r = Git(root, args, None, False)
        lines = []
        for l in SplitLines(r.out):
            t = C.TrimA(l)
            if t:
                lines.append(C.Utf8ToWide(t))
        C.RunOnUi(lambda: fn(lines))

    BgRun(job)


def ListBranches(fn):
    def got(lines):
        loc, rem = [], []
        for l in lines:
            if l.startswith("refs/heads/"):
                loc.append(l[11:])
            elif l.startswith("refs/remotes/"):
                r = l[13:]
                if len(r) < 5 or r[-5:] != "/HEAD":
                    rem.append(r)
        fn(loc, rem)

    QueryLines(["for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes"], got)


def Commit(mode):
    if not RequireRepo():
        return
    msg = C.Trim(scm_panel.Message())
    amend = mode == R.ID_GIT_COMMIT_AMEND
    if not msg and not amend:
        app.ShowScm()
        scm_panel.FocusMessage()
        MsgBox(app.Main(), "Please enter a commit message.", W.MB_ICONINFORMATION)
        return
    staged = 0
    changes = 0
    for f in St().files:
        if f.x != " " and f.x != "?":
            staged += 1
        if f.y != " " or f.x == "?":
            changes += 1
    stageAll = mode == R.ID_GIT_COMMIT_ALL
    if mode == R.ID_GIT_COMMIT and staged == 0:
        if changes == 0:
            MsgBox(app.Main(), "There are no changes to commit.", W.MB_ICONINFORMATION)
            return
        r = MsgBox(app.Main(), "There are no staged changes to commit.\n\nWould you like to stage all your changes and commit them directly?",
                   W.MB_YESNO | W.MB_ICONQUESTION)
        if r != W.IDYES:
            return
        stageAll = True
    if mode == R.ID_GIT_COMMIT_STAGED and staged == 0:
        MsgBox(app.Main(), "There are no staged changes to commit.", W.MB_ICONINFORMATION)
        return
    if mode == R.ID_GIT_COMMIT_ALL and staged == 0 and changes == 0:
        MsgBox(app.Main(), "There are no changes to commit.", W.MB_ICONINFORMATION)
        return
    m = C.WideToUtf8(msg)
    norm = m.replace(b"\r", b"") + b"\n"
    root = St().root
    app.SetStatusText("Committing...")

    def job():
        r = ProcResult()
        r.code = 0
        if stageAll:
            r = Git(root, ["add", "-A"])
        if r.code == 0:
            if amend and not msg:
                r = Git(root, ["commit", "--amend", "--no-edit"])
            else:
                args = ["commit", "-F", "-"]
                if amend:
                    args.insert(1, "--amend")
                r = Git(root, args, norm)

        def ui():
            app.SetStatusText("" if r.code == 0 else "Commit failed")
            if r.code == 0:
                scm_panel.SetMessage("")
            else:
                MsgBox(app.Main(), "Commit failed:\n\n" + GitErrorText(r), W.MB_ICONERROR)
            app.RefreshGit()

        C.RunOnUi(ui)

    BgRun(job)


def Push(force):
    if not RequireRepo():
        return
    if not St().remotes:
        if MsgBox(app.Main(), "This repository has no remotes configured.\n\nWould you like to publish it to GitHub?",
                  W.MB_YESNO | W.MB_ICONQUESTION) == W.IDYES:
            github.GitPublish()
        return
    if St().detached:
        MsgBox(app.Main(), "Cannot push from a detached HEAD. Checkout a branch first.", W.MB_ICONWARNING)
        return
    args = ["push"]
    if force:
        if MsgBox(app.Main(), "Force push '" + St().branch + "' (with lease)? This can overwrite commits on the remote.",
                  W.MB_YESNO | W.MB_ICONWARNING) != W.IDYES:
            return
        args.append("--force-with-lease")
    if not St().upstream:
        args.append("-u")
        args.append(DefaultRemote())
        args.append(St().branch)
    RunGitOp("Push", St().root, [args], False)


def QuickCommit():
    """Commits every change as "Update"; with a clean tree it pushes instead (matches the panel button)."""
    if not RequireRepo():
        return
    if not St().files:
        Push(False)
        return
    root = St().root
    app.SetStatusText("Committing...")

    def job():
        r = Git(root, ["add", "-A"])
        if r.code == 0:
            r = Git(root, ["commit", "-m", "Update"])

        def ui():
            app.SetStatusText("" if r.code == 0 else "Commit failed")
            if r.code != 0:
                MsgBox(app.Main(), "Commit failed:\n\n" + GitErrorText(r), W.MB_ICONERROR)
            app.RefreshGit()

        C.RunOnUi(ui)

    BgRun(job)


def Pull():
    if not RequireRepo():
        return
    if not St().upstream:
        MsgBox(app.Main(), "The branch '" + St().branch + "' has no upstream branch.\nPush it first to set one.",
               W.MB_ICONINFORMATION)
        return
    RunGitOp("Pull", St().root, [["pull"]], True)


def Sync():
    if not RequireRepo():
        return
    if not St().upstream:
        Push(False)
        return
    RunGitOp("Sync", St().root, [["pull"], ["push"]], True)


def CreateBranch(from_):
    name = ""
    prompt = "New branch name:" if not from_ else "New branch name (from " + from_ + "):"
    ok, name = dialogs.InputBox(app.Main(), "Create Branch", prompt, name)
    if not ok:
        return
    name = C.Trim(name)
    name = name.replace(" ", "-")
    if not name:
        return
    args = ["checkout", "-b", name]
    if from_:
        args.append(from_)
    RunGitOp("Create branch", St().root, [args], True)


def Checkout():
    if not RequireRepo():
        return

    def got(loc, rem):
        items = ["+ Create new branch...", "+ Create new branch from..."]
        firstLocal = len(items)
        for b in loc:
            items.append(b + "   (current)" if b == St().branch else b)
        firstRemote = len(items)
        for b in rem:
            items.append(b + "   (remote)")
        ok, idx = dialogs.PickBox(app.Main(), "Checkout", "Select a branch to checkout:", items, 0)
        if not ok:
            return
        if idx == 0:
            CreateBranch("")
            return
        if idx == 1:
            refs = list(loc) + list(rem)
            ok, r = dialogs.PickBox(app.Main(), "Create Branch From", "Select a ref to create the branch from:", refs, 0)
            if ok:
                CreateBranch(refs[r])
            return
        if idx < firstRemote:
            b = loc[idx - firstLocal]
            if b == St().branch:
                return
            RunGitOp("Checkout " + b, St().root, [["checkout", b]], True)
        else:
            r = rem[idx - firstRemote]
            sl = r.find("/")
            local = r if sl < 0 else r[sl + 1:]
            if local in loc:
                RunGitOp("Checkout " + local, St().root, [["checkout", local]], True)
            else:
                RunGitOp("Checkout " + r, St().root, [["checkout", "-b", local, "--track", r]], True)

    ListBranches(got)


def DeleteBranch():
    if not RequireRepo():
        return

    def got(loc, _rem):
        items = [b for b in loc if b != St().branch]
        if not items:
            MsgBox(app.Main(), "There are no other local branches.", W.MB_ICONINFORMATION)
            return
        ok, idx = dialogs.PickBox(app.Main(), "Delete Branch", "Select a branch to delete:", items, 0)
        if not ok:
            return
        b = items[idx]

        def after(ok, r):
            if ok:
                return
            err = GitErrorText(r)
            if "not fully merged" in err:
                if MsgBox(app.Main(), "The branch '" + b + "' is not fully merged. Delete it anyway?",
                          W.MB_YESNO | W.MB_ICONWARNING) == W.IDYES:
                    RunGitOp("Delete branch", St().root, [["branch", "-D", b]], False)
            else:
                MsgBox(app.Main(), "Delete branch failed:\n\n" + err, W.MB_ICONERROR)

        RunGitOp("Delete branch", St().root, [["branch", "-d", b]], False, after)

    ListBranches(got)


def Merge():
    if not RequireRepo():
        return

    def got(loc, rem):
        items = [b for b in loc if b != St().branch]
        items.extend(rem)
        if not items:
            MsgBox(app.Main(), "There are no other branches to merge.", W.MB_ICONINFORMATION)
            return
        ok, idx = dialogs.PickBox(app.Main(), "Merge Branch", "Select a branch to merge into '" + St().branch + "':",
                                  items, 0)
        if not ok:
            return
        b = items[idx]

        def after(ok, r):
            if ok:
                return
            err = GitErrorText(r)
            if "CONFLICT" in err or "conflict" in GitErrorText(r):
                MsgBox(app.Main(), "Merge produced conflicts. Resolve them (see 'Merge Changes' in Source Control), stage the files and commit.\n\n" + err,
                       W.MB_ICONWARNING)
            else:
                MsgBox(app.Main(), "Merge failed:\n\n" + err, W.MB_ICONERROR)

        RunGitOp("Merge " + b, St().root, [["merge", b]], True, after)

    ListBranches(got)


def Stash():
    if not RequireRepo():
        return
    if not St().files:
        MsgBox(app.Main(), "There are no changes to stash.", W.MB_ICONINFORMATION)
        return
    msg = ""
    ok, msg = dialogs.InputBox(app.Main(), "Stash", "Stash message (optional):", msg)
    if not ok:
        return
    args = ["stash", "push", "--include-untracked"]
    if C.Trim(msg):
        args.append("-m")
        args.append(C.Trim(msg))
    RunGitOp("Stash", St().root, [args], True)


def StashPop():
    if not RequireRepo():
        return

    def got(lines):
        if not lines:
            MsgBox(app.Main(), "There are no stashes.", W.MB_ICONINFORMATION)
            return
        ok, idx = dialogs.PickBox(app.Main(), "Pop Stash", "Select a stash to pop:", lines, 0)
        if not ok:
            return
        line = lines[idx]
        colon = line.find(":")
        ref = line if colon < 0 else line[:colon]
        RunGitOp("Pop stash", St().root, [["stash", "pop", ref]], True)

    QueryLines(["stash", "list"], got)


def AddRemote():
    if not RequireRepo():
        return
    name = "origin" if not St().remotes else ""
    url = ""
    ok, name = dialogs.InputBox(app.Main(), "Add Remote", "Remote name:", name)
    if not ok or not C.Trim(name):
        return
    ok, url = dialogs.InputBox(app.Main(), "Add Remote", "Remote URL:", url)
    if not ok or not C.Trim(url):
        return
    RunGitOp("Add remote", St().root, [["remote", "add", C.Trim(name), C.Trim(url)]], False)


def RemoveRemote():
    if not RequireRepo():
        return
    if not St().remotes:
        MsgBox(app.Main(), "There are no remotes.", W.MB_ICONINFORMATION)
        return
    ok, idx = dialogs.PickBox(app.Main(), "Remove Remote", "Select a remote to remove:", St().remotes, 0)
    if not ok:
        return
    RunGitOp("Remove remote", St().root, [["remote", "remove", St().remotes[idx]]], False)


def Init():
    folder = app.Folder()
    if not folder:
        ok, folder = dialogs.BrowseFolder(app.Main(), "Choose a folder to initialize as a git repository")
        if not ok:
            return
        app.OpenFolder(folder)
    if St().isRepo and C.PathEqualsI(St().root, folder):
        MsgBox(app.Main(), "This folder is already a git repository.", W.MB_ICONINFORMATION)
        return
    RunGitOp("Initialize repository", folder, [["init"]], False)


FOLDERID_Documents = W.GUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")


def Clone():
    url = ""
    parent = "" if not app.Folder() else C.DirOf(app.Folder())
    if not parent:
        p = c_void_p()
        if W.SUCCEEDED(W.SHGetKnownFolderPath(byref(FOLDERID_Documents), 0, None, byref(p))):
            parent = wstring_at(p.value)
            W.CoTaskMemFree(p)
    ok, url, parent = dialogs.CloneBox(app.Main(), url, parent)
    if not ok:
        return
    name = url
    while name and (name[-1] == "/" or name[-1] == "\\"):
        name = name[:-1]
    sl = max(name.rfind("/"), name.rfind("\\"), name.rfind(":"))
    if sl >= 0:
        name = name[sl + 1:]
    if len(name) > 4 and C.LowerW(name[-4:]) == ".git":
        name = name[:-4]
    if not name:
        name = "repo"
    dest = C.JoinPath(parent, name)
    if C.IsDirectory(dest):
        MsgBox(app.Main(), "The folder already exists:\n" + dest, W.MB_ICONWARNING)
        return
    app.SetStatusText("Cloning " + url + "...")
    app.ShowOutput()

    def job():
        r = Git(parent, ["clone", url, name])

        def ui():
            app.SetStatusText("")
            if r.code != 0:
                MsgBox(app.Main(), "Clone failed:\n\n" + GitErrorText(r), W.MB_ICONERROR)
                return
            if MsgBox(app.Main(), "Repository cloned to:\n" + dest + "\n\nOpen it now?",
                      W.MB_YESNO | W.MB_ICONQUESTION) == W.IDYES:
                app.OpenFolder(dest)

        C.RunOnUi(ui)

    BgRun(job)


def StageAll(stage):
    if not RequireRepo():
        return
    if stage:
        RunGitOp("Stage all", St().root, [["add", "-A"]], False)
    elif St().noCommits:
        RunGitOp("Unstage all", St().root, [["rm", "-r", "-q", "--cached", "--", "."]], False)
    else:
        RunGitOp("Unstage all", St().root, [["reset", "-q"]], False)


def DiscardAll():
    if not RequireRepo():
        return
    files = [f for f in St().files if f.y != " " or f.x == "?"]
    if not files:
        MsgBox(app.Main(), "There are no unstaged changes to discard.", W.MB_ICONINFORMATION)
        return
    GitDiscard(files)


def UndoCommit():
    if not RequireRepo():
        return
    if St().noCommits:
        return
    if MsgBox(app.Main(), "Undo the last commit? Its changes will be kept as staged changes.",
              W.MB_YESNO | W.MB_ICONQUESTION) != W.IDYES:
        return
    root = St().root

    def job():
        msg = Git(root, ["log", "-1", "--format=%B"], None, False)
        parent = Git(root, ["rev-parse", "--verify", "-q", "HEAD~1"], None, False)
        r = Git(root, ["reset", "--soft", "HEAD~1"]) if parent.code == 0 else Git(root, ["update-ref", "-d", "HEAD"])

        def ui():
            if r.code != 0:
                MsgBox(app.Main(), "Undo commit failed:\n\n" + GitErrorText(r), W.MB_ICONERROR)
            elif not C.Trim(scm_panel.Message()):
                scm_panel.SetMessage(C.Utf8ToWide(C.TrimA(msg.out)))
            app.RefreshGit()

        C.RunOnUi(ui)

    BgRun(job)


# ---- public ----

def GitStage(paths, stage):
    if not RequireRepo() or not paths:
        return
    cmds = []
    for i in range(0, len(paths), 100):
        if stage:
            args = ["add", "-A", "--"]
        elif St().noCommits:
            args = ["rm", "-q", "-r", "--cached", "--"]
        else:
            args = ["restore", "--staged", "--"]
        args.extend(paths[i:i + 100])
        cmds.append(args)
    RunGitOp("Stage" if stage else "Unstage", St().root, cmds, False)


def GitDiscard(files):
    if not RequireRepo() or not files:
        return
    tracked = []
    untracked = []
    for f in files:
        if f.x == "?":
            untracked.append(C.JoinPath(St().root, C.ToBackslashes(f.path)))
        else:
            tracked.append(f.path)
    q = ("Are you sure you want to discard changes in '" + files[0].path + "'?") if len(files) == 1 else \
        ("Are you sure you want to discard changes in " + str(len(files)) + " files?")
    if untracked:
        q += "\n\nUntracked files will be moved to the Recycle Bin."
    else:
        q += "\n\nThis is irreversible: your current working tree changes will be lost."
    if MsgBox(app.Main(), q, W.MB_YESNO | W.MB_ICONWARNING | W.MB_DEFBUTTON2) != W.IDYES:
        return
    if untracked:
        frm = "".join(p + "\0" for p in untracked) + "\0"
        buf = create_string_buffer(frm.encode("utf-16-le", "surrogatepass") + b"\0\0")
        op = W.SHFILEOPSTRUCTW()
        op.hwnd = app.Main()
        op.wFunc = W.FO_DELETE
        op.pFrom = addressof(buf)
        op.fFlags = W.FOF_ALLOWUNDO | W.FOF_NOCONFIRMATION | W.FOF_SILENT
        W.SHFileOperationW(byref(op))
    cmds = []
    for i in range(0, len(tracked), 100):
        args = ["restore", "--worktree", "--"]
        args.extend(tracked[i:i + 100])
        cmds.append(args)

    def after(ok, r):
        explorer.Refresh()
        if not ok:
            MsgBox(app.Main(), "Discard failed:\n\n" + GitErrorText(r), W.MB_ICONERROR)

    RunGitOp("Discard changes", St().root, cmds, True, after)


def GitCommand(id):
    if id == R.ID_GIT_INIT:
        Init()
    elif id == R.ID_GIT_CLONE:
        Clone()
    elif id == R.ID_GIT_PUBLISH:
        github.GitPublish()
    elif id in (R.ID_GIT_COMMIT, R.ID_GIT_COMMIT_STAGED, R.ID_GIT_COMMIT_ALL, R.ID_GIT_COMMIT_AMEND):
        Commit(id)
    elif id == R.ID_GIT_UNDO_COMMIT:
        UndoCommit()
    elif id == R.ID_GIT_QUICK_COMMIT:
        QuickCommit()
    elif id == R.ID_GIT_PUSH:
        Push(False)
    elif id == R.ID_GIT_PUSH_FORCE:
        Push(True)
    elif id == R.ID_GIT_PULL:
        Pull()
    elif id == R.ID_GIT_SYNC:
        Sync()
    elif id == R.ID_GIT_FETCH:
        if RequireRepo():
            RunGitOp("Fetch", St().root, [["fetch", "--all", "--prune"]], False)
    elif id == R.ID_GIT_CHECKOUT:
        Checkout()
    elif id == R.ID_GIT_BRANCH_CREATE:
        if RequireRepo():
            CreateBranch("")
    elif id == R.ID_GIT_BRANCH_DELETE:
        DeleteBranch()
    elif id == R.ID_GIT_MERGE:
        Merge()
    elif id == R.ID_GIT_STASH:
        Stash()
    elif id == R.ID_GIT_STASH_POP:
        StashPop()
    elif id == R.ID_GIT_REMOTE_ADD:
        AddRemote()
    elif id == R.ID_GIT_REMOTE_REMOVE:
        RemoveRemote()
    elif id == R.ID_GIT_LOG:
        if RequireRepo():
            app.OpenLog()
    elif id == R.ID_GIT_REFRESH:
        app.RefreshGit()
    elif id == R.ID_GIT_STAGE_ALL:
        StageAll(True)
    elif id == R.ID_GIT_UNSTAGE_ALL:
        StageAll(False)
    elif id == R.ID_GIT_DISCARD_ALL:
        DiscardAll()
