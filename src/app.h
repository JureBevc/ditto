// Interfaces between the main window and the panels / git commands.
#pragma once
#include "common.h"
#include "git.h"

struct DiffSpec {
    enum Kind { Unstaged, Staged, Untracked, Commit } kind = Unstaged;
    std::wstring root;      // repository root
    std::wstring path;      // repo-relative, forward slashes
    std::wstring origPath;  // rename source (optional)
    std::wstring commit, parent;  // for Commit diffs (parent empty => root commit)
};

namespace App {
HWND Main();
void OpenFile(const std::wstring& path, int64_t line = -1);
void OpenDiff(const DiffSpec& spec);
void OpenLog();
void OpenFolder(const std::wstring& folder);
const std::wstring& Folder();
const GitStatus& Status();
void RefreshGit();
void SetStatusText(const std::wstring& s);
void AppendOutput(const std::wstring& s);
void ShowOutput();
void ReloadUnmodifiedDocs();
void ShowScm();
}

// git_commands.cpp
void GitCommand(int id);
void GitStage(const std::vector<std::wstring>& paths, bool stage);
void GitDiscard(const std::vector<GitFile>& files);
// Runs git commands sequentially in the background; stops at the first failure.
void RunGitOp(const std::wstring& title, const std::wstring& cwd, std::vector<std::vector<std::wstring>> cmds,
              bool reloadDocs, std::function<void(bool ok, const ProcResult& last)> after = nullptr);

// github.cpp
void GitPublish();

// scm_panel.cpp
namespace Scm {
HWND Create(HWND parent);
void Update(const GitStatus& s);
std::wstring Message();
void SetMessage(const std::wstring& m);
void FocusMessage();
void ThemeChanged();
}

// explorer.cpp
namespace Explorer {
HWND Create(HWND parent);
void SetRoot(const std::wstring& folder);
void Refresh();
void ThemeChanged();
}

// diff_view.cpp
namespace DiffView {
void Register();
HWND Create(HWND parent, const DiffSpec& spec);
std::wstring Title(HWND h);
std::wstring Key(const DiffSpec& spec);
void Reload(HWND h);
}

// log_view.cpp
namespace LogView {
void Register();
HWND Create(HWND parent, const std::wstring& root);
void Reload(HWND h);
void ThemeChanged(HWND h);
}
