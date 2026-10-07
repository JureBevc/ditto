// Runs git.exe in the background and parses its porcelain output.
#pragma once
#include "common.h"

struct ProcResult {
    int code = -1;
    std::string out, err;
};

std::wstring QuoteArg(const std::wstring& a);
ProcResult RunProcess(const std::wstring& cmdline, const std::wstring& cwd, const std::string* input);
std::wstring GitExe();
// log=true: the command line and its output are echoed to the Output panel.
ProcResult Git(const std::wstring& cwd, const std::vector<std::wstring>& args, const std::string* input = nullptr, bool log = true);
void SetGitLogger(std::function<void(const std::wstring&)> fn);  // invoked on worker threads

// Single background worker thread: jobs run one at a time in submission order (avoids index.lock races).
void BgRun(std::function<void()> job);
int BgPending();

struct GitFile {
    std::wstring path, origPath;  // repo-relative, forward slashes
    char x = ' ', y = ' ';
};

struct GitStatus {
    bool ok = false, isRepo = false;
    std::wstring folder, root, branch, upstream, error;
    bool detached = false, noCommits = false;
    int ahead = 0, behind = 0;
    std::vector<GitFile> files;
    std::vector<std::wstring> remotes;
};

GitStatus QueryGitStatus(const std::wstring& folder);
std::vector<std::string> SplitNul(const std::string& s);
std::vector<std::string> SplitLines(const std::string& s);
std::wstring GitErrorText(const ProcResult& r);  // stderr (or stdout) trimmed, for message boxes
