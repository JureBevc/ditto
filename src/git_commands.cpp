// User-facing git operations (menu / Source Control panel commands).
#include "app.h"
#include "dialogs.h"
#include "resource.h"

void RunGitOp(const std::wstring& title, const std::wstring& cwd, std::vector<std::vector<std::wstring>> cmds, bool reloadDocs,
              std::function<void(bool ok, const ProcResult& last)> after) {
    App::SetStatusText(title + L"...");
    BgRun([title, cwd, cmds, reloadDocs, after]() {
        ProcResult last;
        last.code = 0;
        bool ok = true;
        for (auto& c : cmds) {
            last = Git(cwd, c);
            if (last.code != 0) { ok = false; break; }
        }
        RunOnUi([title, ok, last, reloadDocs, after]() {
            App::SetStatusText(ok ? L"" : title + L" failed");
            if (reloadDocs) App::ReloadUnmodifiedDocs();
            App::RefreshGit();
            if (after) after(ok, last);
            else if (!ok) MsgBox(App::Main(), title + L" failed:\n\n" + GitErrorText(last), MB_ICONERROR);
        });
    });
}

namespace {

const GitStatus& St() { return App::Status(); }

bool RequireRepo() {
    if (St().isRepo) return true;
    if (App::Folder().empty()) MsgBox(App::Main(), L"Open a folder first (File > Open Folder).", MB_ICONINFORMATION);
    else MsgBox(App::Main(), L"The current folder is not a git repository.\nUse Git > Initialize Repository first.", MB_ICONINFORMATION);
    return false;
}

std::wstring DefaultRemote() {
    for (auto& r : St().remotes)
        if (r == L"origin") return r;
    return St().remotes.empty() ? L"" : St().remotes[0];
}

// Runs `git <args>` in the background and hands the output lines to fn on the UI thread.
void QueryLines(const std::vector<std::wstring>& args, std::function<void(std::vector<std::wstring>)> fn) {
    std::wstring root = St().root;
    BgRun([root, args, fn]() {
        ProcResult r = Git(root, args, nullptr, false);
        auto lines = std::make_shared<std::vector<std::wstring>>();
        for (auto& l : SplitLines(r.out)) {
            std::string t = TrimA(l);
            if (!t.empty()) lines->push_back(Utf8ToWide(t));
        }
        RunOnUi([fn, lines]() { fn(*lines); });
    });
}

void ListBranches(std::function<void(std::vector<std::wstring> locals, std::vector<std::wstring> remotes)> fn) {
    QueryLines({L"for-each-ref", L"--format=%(refname)", L"refs/heads", L"refs/remotes"}, [fn](std::vector<std::wstring> lines) {
        std::vector<std::wstring> loc, rem;
        for (auto& l : lines) {
            if (l.compare(0, 11, L"refs/heads/") == 0) loc.push_back(l.substr(11));
            else if (l.compare(0, 13, L"refs/remotes/") == 0) {
                std::wstring r = l.substr(13);
                if (r.size() < 5 || r.compare(r.size() - 5, 5, L"/HEAD") != 0) rem.push_back(r);
            }
        }
        fn(loc, rem);
    });
}

void Commit(int mode) {
    if (!RequireRepo()) return;
    std::wstring msg = Trim(Scm::Message());
    bool amend = mode == ID_GIT_COMMIT_AMEND;
    if (msg.empty() && !amend) {
        App::ShowScm();
        Scm::FocusMessage();
        MsgBox(App::Main(), L"Please enter a commit message.", MB_ICONINFORMATION);
        return;
    }
    int staged = 0, changes = 0;
    for (auto& f : St().files) {
        if (f.x != ' ' && f.x != '?') ++staged;
        if (f.y != ' ' || f.x == '?') ++changes;
    }
    bool stageAll = mode == ID_GIT_COMMIT_ALL;
    if (mode == ID_GIT_COMMIT && staged == 0) {
        if (changes == 0) { MsgBox(App::Main(), L"There are no changes to commit.", MB_ICONINFORMATION); return; }
        int r = MsgBox(App::Main(), L"There are no staged changes to commit.\n\nWould you like to stage all your changes and commit them directly?",
                       MB_YESNO | MB_ICONQUESTION);
        if (r != IDYES) return;
        stageAll = true;
    }
    if (mode == ID_GIT_COMMIT_STAGED && staged == 0) { MsgBox(App::Main(), L"There are no staged changes to commit.", MB_ICONINFORMATION); return; }
    if (mode == ID_GIT_COMMIT_ALL && staged == 0 && changes == 0) { MsgBox(App::Main(), L"There are no changes to commit.", MB_ICONINFORMATION); return; }
    std::string m = WideToUtf8(msg), norm;
    for (char c : m)
        if (c != '\r') norm += c;
    norm += '\n';
    std::wstring root = St().root;
    App::SetStatusText(L"Committing...");
    BgRun([root, stageAll, amend, norm, msg]() {
        ProcResult r;
        r.code = 0;
        if (stageAll) r = Git(root, {L"add", L"-A"});
        if (r.code == 0) {
            if (amend && msg.empty()) r = Git(root, {L"commit", L"--amend", L"--no-edit"});
            else {
                std::vector<std::wstring> args = {L"commit", L"-F", L"-"};
                if (amend) args.insert(args.begin() + 1, L"--amend");
                r = Git(root, args, &norm);
            }
        }
        RunOnUi([r]() {
            App::SetStatusText(r.code == 0 ? L"" : L"Commit failed");
            if (r.code == 0) Scm::SetMessage(L"");
            else MsgBox(App::Main(), L"Commit failed:\n\n" + GitErrorText(r), MB_ICONERROR);
            App::RefreshGit();
        });
    });
}

void Push(bool force) {
    if (!RequireRepo()) return;
    if (St().remotes.empty()) {
        if (MsgBox(App::Main(), L"This repository has no remotes configured.\n\nWould you like to publish it to GitHub?",
                   MB_YESNO | MB_ICONQUESTION) == IDYES)
            GitPublish();
        return;
    }
    if (St().detached) { MsgBox(App::Main(), L"Cannot push from a detached HEAD. Checkout a branch first.", MB_ICONWARNING); return; }
    std::vector<std::wstring> args = {L"push"};
    if (force) {
        if (MsgBox(App::Main(), L"Force push '" + St().branch + L"' (with lease)? This can overwrite commits on the remote.",
                   MB_YESNO | MB_ICONWARNING) != IDYES)
            return;
        args.push_back(L"--force-with-lease");
    }
    if (St().upstream.empty()) {
        args.push_back(L"-u");
        args.push_back(DefaultRemote());
        args.push_back(St().branch);
    }
    RunGitOp(L"Push", St().root, {args}, false);
}

void Pull() {
    if (!RequireRepo()) return;
    if (St().upstream.empty()) {
        MsgBox(App::Main(), L"The branch '" + St().branch + L"' has no upstream branch.\nPush it first to set one.", MB_ICONINFORMATION);
        return;
    }
    RunGitOp(L"Pull", St().root, {{L"pull"}}, true);
}

void Sync() {
    if (!RequireRepo()) return;
    if (St().upstream.empty()) { Push(false); return; }
    RunGitOp(L"Sync", St().root, {{L"pull"}, {L"push"}}, true);
}

void CreateBranch(const std::wstring& from) {
    std::wstring name;
    std::wstring prompt = from.empty() ? L"New branch name:" : L"New branch name (from " + from + L"):";
    if (!InputBox(App::Main(), L"Create Branch", prompt, name)) return;
    name = Trim(name);
    std::replace(name.begin(), name.end(), L' ', L'-');
    if (name.empty()) return;
    std::vector<std::wstring> args = {L"checkout", L"-b", name};
    if (!from.empty()) args.push_back(from);
    RunGitOp(L"Create branch", St().root, {args}, true);
}

void Checkout() {
    if (!RequireRepo()) return;
    ListBranches([](std::vector<std::wstring> loc, std::vector<std::wstring> rem) {
        std::vector<std::wstring> items = {L"+ Create new branch...", L"+ Create new branch from..."};
        size_t firstLocal = items.size();
        for (auto& b : loc) items.push_back(b == St().branch ? b + L"   (current)" : b);
        size_t firstRemote = items.size();
        for (auto& b : rem) items.push_back(b + L"   (remote)");
        int idx;
        if (!PickBox(App::Main(), L"Checkout", L"Select a branch to checkout:", items, idx)) return;
        if (idx == 0) { CreateBranch(L""); return; }
        if (idx == 1) {
            std::vector<std::wstring> refs = loc;
            refs.insert(refs.end(), rem.begin(), rem.end());
            int r;
            if (PickBox(App::Main(), L"Create Branch From", L"Select a ref to create the branch from:", refs, r)) CreateBranch(refs[(size_t)r]);
            return;
        }
        if ((size_t)idx < firstRemote) {
            std::wstring b = loc[(size_t)idx - firstLocal];
            if (b == St().branch) return;
            RunGitOp(L"Checkout " + b, St().root, {{L"checkout", b}}, true);
        } else {
            std::wstring r = rem[(size_t)idx - firstRemote];
            size_t sl = r.find(L'/');
            std::wstring local = sl == std::wstring::npos ? r : r.substr(sl + 1);
            if (std::find(loc.begin(), loc.end(), local) != loc.end())
                RunGitOp(L"Checkout " + local, St().root, {{L"checkout", local}}, true);
            else
                RunGitOp(L"Checkout " + r, St().root, {{L"checkout", L"-b", local, L"--track", r}}, true);
        }
    });
}

void DeleteBranch() {
    if (!RequireRepo()) return;
    ListBranches([](std::vector<std::wstring> loc, std::vector<std::wstring>) {
        std::vector<std::wstring> items;
        for (auto& b : loc)
            if (b != St().branch) items.push_back(b);
        if (items.empty()) { MsgBox(App::Main(), L"There are no other local branches.", MB_ICONINFORMATION); return; }
        int idx;
        if (!PickBox(App::Main(), L"Delete Branch", L"Select a branch to delete:", items, idx)) return;
        std::wstring b = items[(size_t)idx];
        RunGitOp(L"Delete branch", St().root, {{L"branch", L"-d", b}}, false, [b](bool ok, const ProcResult& r) {
            if (ok) return;
            std::wstring err = GitErrorText(r);
            if (err.find(L"not fully merged") != std::wstring::npos) {
                if (MsgBox(App::Main(), L"The branch '" + b + L"' is not fully merged. Delete it anyway?", MB_YESNO | MB_ICONWARNING) == IDYES)
                    RunGitOp(L"Delete branch", St().root, {{L"branch", L"-D", b}}, false);
            } else {
                MsgBox(App::Main(), L"Delete branch failed:\n\n" + err, MB_ICONERROR);
            }
        });
    });
}

void Merge() {
    if (!RequireRepo()) return;
    ListBranches([](std::vector<std::wstring> loc, std::vector<std::wstring> rem) {
        std::vector<std::wstring> items;
        for (auto& b : loc)
            if (b != St().branch) items.push_back(b);
        for (auto& b : rem) items.push_back(b);
        if (items.empty()) { MsgBox(App::Main(), L"There are no other branches to merge.", MB_ICONINFORMATION); return; }
        int idx;
        if (!PickBox(App::Main(), L"Merge Branch", L"Select a branch to merge into '" + St().branch + L"':", items, idx)) return;
        std::wstring b = items[(size_t)idx];
        RunGitOp(L"Merge " + b, St().root, {{L"merge", b}}, true, [](bool ok, const ProcResult& r) {
            if (ok) return;
            std::wstring err = GitErrorText(r);
            if (err.find(L"CONFLICT") != std::wstring::npos || GitErrorText(r).find(L"conflict") != std::wstring::npos)
                MsgBox(App::Main(), L"Merge produced conflicts. Resolve them (see 'Merge Changes' in Source Control), stage the files and commit.\n\n" + err, MB_ICONWARNING);
            else
                MsgBox(App::Main(), L"Merge failed:\n\n" + err, MB_ICONERROR);
        });
    });
}

void Stash() {
    if (!RequireRepo()) return;
    if (St().files.empty()) { MsgBox(App::Main(), L"There are no changes to stash.", MB_ICONINFORMATION); return; }
    std::wstring msg;
    if (!InputBox(App::Main(), L"Stash", L"Stash message (optional):", msg)) return;
    std::vector<std::wstring> args = {L"stash", L"push", L"--include-untracked"};
    if (!Trim(msg).empty()) { args.push_back(L"-m"); args.push_back(Trim(msg)); }
    RunGitOp(L"Stash", St().root, {args}, true);
}

void StashPop() {
    if (!RequireRepo()) return;
    QueryLines({L"stash", L"list"}, [](std::vector<std::wstring> lines) {
        if (lines.empty()) { MsgBox(App::Main(), L"There are no stashes.", MB_ICONINFORMATION); return; }
        int idx;
        if (!PickBox(App::Main(), L"Pop Stash", L"Select a stash to pop:", lines, idx)) return;
        std::wstring ref = lines[(size_t)idx].substr(0, lines[(size_t)idx].find(L':'));
        RunGitOp(L"Pop stash", St().root, {{L"stash", L"pop", ref}}, true);
    });
}

void AddRemote() {
    if (!RequireRepo()) return;
    std::wstring name = St().remotes.empty() ? L"origin" : L"", url;
    if (!InputBox(App::Main(), L"Add Remote", L"Remote name:", name) || Trim(name).empty()) return;
    if (!InputBox(App::Main(), L"Add Remote", L"Remote URL:", url) || Trim(url).empty()) return;
    RunGitOp(L"Add remote", St().root, {{L"remote", L"add", Trim(name), Trim(url)}}, false);
}

void RemoveRemote() {
    if (!RequireRepo()) return;
    if (St().remotes.empty()) { MsgBox(App::Main(), L"There are no remotes.", MB_ICONINFORMATION); return; }
    int idx;
    if (!PickBox(App::Main(), L"Remove Remote", L"Select a remote to remove:", St().remotes, idx)) return;
    RunGitOp(L"Remove remote", St().root, {{L"remote", L"remove", St().remotes[(size_t)idx]}}, false);
}

void Init() {
    std::wstring folder = App::Folder();
    if (folder.empty()) {
        if (!BrowseFolder(App::Main(), L"Choose a folder to initialize as a git repository", folder)) return;
        App::OpenFolder(folder);
    }
    if (St().isRepo && PathEqualsI(St().root, folder)) { MsgBox(App::Main(), L"This folder is already a git repository.", MB_ICONINFORMATION); return; }
    RunGitOp(L"Initialize repository", folder, {{L"init"}}, false);
}

void Clone() {
    std::wstring url, parent = App::Folder().empty() ? L"" : DirOf(App::Folder());
    if (parent.empty()) {
        PWSTR p = nullptr;
        if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_Documents, 0, nullptr, &p))) { parent = p; CoTaskMemFree(p); }
    }
    if (!CloneBox(App::Main(), url, parent)) return;
    std::wstring name = url;
    while (!name.empty() && (name.back() == L'/' || name.back() == L'\\')) name.pop_back();
    size_t sl = name.find_last_of(L"/\\:");
    if (sl != std::wstring::npos) name = name.substr(sl + 1);
    if (name.size() > 4 && LowerW(name.substr(name.size() - 4)) == L".git") name.resize(name.size() - 4);
    if (name.empty()) name = L"repo";
    std::wstring dest = JoinPath(parent, name);
    if (IsDirectory(dest)) { MsgBox(App::Main(), L"The folder already exists:\n" + dest, MB_ICONWARNING); return; }
    App::SetStatusText(L"Cloning " + url + L"...");
    App::ShowOutput();
    BgRun([url, parent, dest, name]() {
        ProcResult r = Git(parent, {L"clone", url, name});
        RunOnUi([r, dest]() {
            App::SetStatusText(L"");
            if (r.code != 0) { MsgBox(App::Main(), L"Clone failed:\n\n" + GitErrorText(r), MB_ICONERROR); return; }
            if (MsgBox(App::Main(), L"Repository cloned to:\n" + dest + L"\n\nOpen it now?", MB_YESNO | MB_ICONQUESTION) == IDYES)
                App::OpenFolder(dest);
        });
    });
}

void StageAll(bool stage) {
    if (!RequireRepo()) return;
    if (stage) RunGitOp(L"Stage all", St().root, {{L"add", L"-A"}}, false);
    else if (St().noCommits) RunGitOp(L"Unstage all", St().root, {{L"rm", L"-r", L"-q", L"--cached", L"--", L"."}}, false);
    else RunGitOp(L"Unstage all", St().root, {{L"reset", L"-q"}}, false);
}

void DiscardAll() {
    if (!RequireRepo()) return;
    std::vector<GitFile> files;
    for (auto& f : St().files)
        if (f.y != ' ' || f.x == '?') files.push_back(f);
    if (files.empty()) { MsgBox(App::Main(), L"There are no unstaged changes to discard.", MB_ICONINFORMATION); return; }
    GitDiscard(files);
}

void UndoCommit() {
    if (!RequireRepo()) return;
    if (St().noCommits) return;
    if (MsgBox(App::Main(), L"Undo the last commit? Its changes will be kept as staged changes.", MB_YESNO | MB_ICONQUESTION) != IDYES) return;
    std::wstring root = St().root;
    BgRun([root]() {
        ProcResult msg = Git(root, {L"log", L"-1", L"--format=%B"}, nullptr, false);
        ProcResult parent = Git(root, {L"rev-parse", L"--verify", L"-q", L"HEAD~1"}, nullptr, false);
        ProcResult r = parent.code == 0 ? Git(root, {L"reset", L"--soft", L"HEAD~1"}) : Git(root, {L"update-ref", L"-d", L"HEAD"});
        RunOnUi([r, msg]() {
            if (r.code != 0) MsgBox(App::Main(), L"Undo commit failed:\n\n" + GitErrorText(r), MB_ICONERROR);
            else if (Trim(Scm::Message()).empty()) Scm::SetMessage(Utf8ToWide(TrimA(msg.out)));
            App::RefreshGit();
        });
    });
}

}  // namespace

void GitStage(const std::vector<std::wstring>& paths, bool stage) {
    if (!RequireRepo() || paths.empty()) return;
    std::vector<std::vector<std::wstring>> cmds;
    for (size_t i = 0; i < paths.size(); i += 100) {
        std::vector<std::wstring> args;
        if (stage) args = {L"add", L"-A", L"--"};
        else if (St().noCommits) args = {L"rm", L"-q", L"-r", L"--cached", L"--"};
        else args = {L"restore", L"--staged", L"--"};
        for (size_t k = i; k < paths.size() && k < i + 100; ++k) args.push_back(paths[k]);
        cmds.push_back(args);
    }
    RunGitOp(stage ? L"Stage" : L"Unstage", St().root, cmds, false);
}

void GitDiscard(const std::vector<GitFile>& files) {
    if (!RequireRepo() || files.empty()) return;
    std::vector<std::wstring> tracked;
    std::vector<std::wstring> untracked;
    for (auto& f : files) {
        if (f.x == '?') untracked.push_back(JoinPath(St().root, ToBackslashes(f.path)));
        else tracked.push_back(f.path);
    }
    std::wstring q = files.size() == 1 ? L"Are you sure you want to discard changes in '" + files[0].path + L"'?"
                                       : L"Are you sure you want to discard changes in " + std::to_wstring(files.size()) + L" files?";
    if (!untracked.empty()) q += L"\n\nUntracked files will be moved to the Recycle Bin.";
    else q += L"\n\nThis is irreversible: your current working tree changes will be lost.";
    if (MsgBox(App::Main(), q, MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) != IDYES) return;
    if (!untracked.empty()) {
        std::wstring from;
        for (auto& p : untracked) { from += p; from.push_back(L'\0'); }
        from.push_back(L'\0');
        SHFILEOPSTRUCTW op = {};
        op.hwnd = App::Main();
        op.wFunc = FO_DELETE;
        op.pFrom = from.c_str();
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT;
        SHFileOperationW(&op);
    }
    std::vector<std::vector<std::wstring>> cmds;
    for (size_t i = 0; i < tracked.size(); i += 100) {
        std::vector<std::wstring> args = {L"restore", L"--worktree", L"--"};
        for (size_t k = i; k < tracked.size() && k < i + 100; ++k) args.push_back(tracked[k]);
        cmds.push_back(args);
    }
    RunGitOp(L"Discard changes", St().root, cmds, true, [](bool ok, const ProcResult& r) {
        Explorer::Refresh();
        if (!ok) MsgBox(App::Main(), L"Discard failed:\n\n" + GitErrorText(r), MB_ICONERROR);
    });
}

void GitCommand(int id) {
    switch (id) {
    case ID_GIT_INIT: Init(); break;
    case ID_GIT_CLONE: Clone(); break;
    case ID_GIT_PUBLISH: GitPublish(); break;
    case ID_GIT_COMMIT:
    case ID_GIT_COMMIT_STAGED:
    case ID_GIT_COMMIT_ALL:
    case ID_GIT_COMMIT_AMEND: Commit(id); break;
    case ID_GIT_UNDO_COMMIT: UndoCommit(); break;
    case ID_GIT_PUSH: Push(false); break;
    case ID_GIT_PUSH_FORCE: Push(true); break;
    case ID_GIT_PULL: Pull(); break;
    case ID_GIT_SYNC: Sync(); break;
    case ID_GIT_FETCH:
        if (RequireRepo()) RunGitOp(L"Fetch", St().root, {{L"fetch", L"--all", L"--prune"}}, false);
        break;
    case ID_GIT_CHECKOUT: Checkout(); break;
    case ID_GIT_BRANCH_CREATE: if (RequireRepo()) CreateBranch(L""); break;
    case ID_GIT_BRANCH_DELETE: DeleteBranch(); break;
    case ID_GIT_MERGE: Merge(); break;
    case ID_GIT_STASH: Stash(); break;
    case ID_GIT_STASH_POP: StashPop(); break;
    case ID_GIT_REMOTE_ADD: AddRemote(); break;
    case ID_GIT_REMOTE_REMOVE: RemoveRemote(); break;
    case ID_GIT_LOG: if (RequireRepo()) App::OpenLog(); break;
    case ID_GIT_REFRESH: App::RefreshGit(); break;
    case ID_GIT_STAGE_ALL: StageAll(true); break;
    case ID_GIT_UNSTAGE_ALL: StageAll(false); break;
    case ID_GIT_DISCARD_ALL: DiscardAll(); break;
    }
}
