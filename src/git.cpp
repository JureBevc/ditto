#include "git.h"
#include <condition_variable>
#include <deque>

static std::function<void(const std::wstring&)> g_gitLogger;
void SetGitLogger(std::function<void(const std::wstring&)> fn) { g_gitLogger = std::move(fn); }

std::wstring QuoteArg(const std::wstring& a) {
    if (!a.empty() && a.find_first_of(L" \t\n\v\"") == std::wstring::npos) return a;
    std::wstring r = L"\"";
    for (auto it = a.begin();; ++it) {
        size_t bs = 0;
        while (it != a.end() && *it == L'\\') { ++it; ++bs; }
        if (it == a.end()) { r.append(bs * 2, L'\\'); break; }
        if (*it == L'"') { r.append(bs * 2 + 1, L'\\'); r += L'"'; }
        else { r.append(bs, L'\\'); r += *it; }
    }
    r += L'"';
    return r;
}

static void ReadAll(HANDLE h, std::string& out) {
    char buf[65536];
    DWORD n;
    while (ReadFile(h, buf, sizeof(buf), &n, nullptr) && n) out.append(buf, n);
}

ProcResult RunProcess(const std::wstring& cmdline, const std::wstring& cwd, const std::string* input) {
    ProcResult r;
    SECURITY_ATTRIBUTES sa = {sizeof(sa), nullptr, TRUE};
    HANDLE outR, outW, errR, errW, inR, inW;
    if (!CreatePipe(&outR, &outW, &sa, 0)) { r.err = "CreatePipe failed"; return r; }
    CreatePipe(&errR, &errW, &sa, 0);
    CreatePipe(&inR, &inW, &sa, 0);
    SetHandleInformation(outR, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(errR, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(inW, HANDLE_FLAG_INHERIT, 0);
    STARTUPINFOW si = {sizeof(si)};
    si.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    si.hStdInput = inR;
    si.hStdOutput = outW;
    si.hStdError = errW;
    PROCESS_INFORMATION pi = {};
    std::wstring cl = cmdline;
    BOOL ok = CreateProcessW(nullptr, &cl[0], nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr,
                             cwd.empty() ? nullptr : cwd.c_str(), &si, &pi);
    DWORD createErr = GetLastError();
    CloseHandle(outW);
    CloseHandle(errW);
    CloseHandle(inR);
    if (!ok) {
        CloseHandle(outR); CloseHandle(errR); CloseHandle(inW);
        r.err = WideToUtf8(FormatError(createErr));
        return r;
    }
    std::thread errThread([&]() { ReadAll(errR, r.err); });
    if (input && !input->empty()) {
        DWORD wr;
        WriteFile(inW, input->data(), (DWORD)input->size(), &wr, nullptr);
    }
    CloseHandle(inW);
    ReadAll(outR, r.out);
    errThread.join();
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    r.code = (int)code;
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    CloseHandle(outR);
    CloseHandle(errR);
    return r;
}

std::wstring GitExe() {
    static std::wstring exe;
    if (!exe.empty()) return exe;
    wchar_t buf[MAX_PATH];
    if (SearchPathW(nullptr, L"git.exe", nullptr, MAX_PATH, buf, nullptr)) exe = buf;
    else {
        const wchar_t* cands[] = {L"C:\\Program Files\\Git\\cmd\\git.exe", L"C:\\Program Files (x86)\\Git\\cmd\\git.exe"};
        for (auto c : cands)
            if (FileExists(c)) { exe = c; break; }
        if (exe.empty()) exe = L"git.exe";
    }
    return exe;
}

ProcResult Git(const std::wstring& cwd, const std::vector<std::wstring>& args, const std::string* input, bool log) {
    std::wstring cmd = QuoteArg(GitExe()) + L" -c core.quotepath=off -c color.ui=never";
    std::wstring shown = L"> git";
    for (auto& a : args) {
        cmd += L" " + QuoteArg(a);
        shown += L" " + QuoteArg(a);
    }
    ProcResult r = RunProcess(cmd, cwd, input);
    if (g_gitLogger && (log || r.code != 0)) {
        std::wstring text = shown + L"\r\n";
        auto add = [&](const std::string& s) {
            std::string t = TrimA(s);
            if (t.empty()) return;
            if (t.size() > 8000) t = t.substr(0, 8000) + "\n...";
            std::wstring w = Utf8ToWide(t), o;
            for (wchar_t c : w) {
                if (c == L'\n') o += L"\r\n";
                else if (c != L'\r') o += c;
            }
            text += o + L"\r\n";
        };
        if (log) add(r.out);
        add(r.err);
        if (r.code != 0) text += L"(exit code " + std::to_wstring(r.code) + L")\r\n";
        g_gitLogger(text);
    }
    return r;
}

// ---------------- background worker ----------------

static std::mutex g_bgMutex;
static std::condition_variable g_bgCv;
static std::deque<std::function<void()>> g_bgQueue;
static std::atomic<int> g_bgPending{0};
static bool g_bgStarted = false;

void BgRun(std::function<void()> job) {
    std::lock_guard<std::mutex> lk(g_bgMutex);
    if (!g_bgStarted) {
        g_bgStarted = true;
        std::thread([]() {
            for (;;) {
                std::function<void()> j;
                {
                    std::unique_lock<std::mutex> lk2(g_bgMutex);
                    g_bgCv.wait(lk2, [] { return !g_bgQueue.empty(); });
                    j = std::move(g_bgQueue.front());
                    g_bgQueue.pop_front();
                }
                j();
                --g_bgPending;
            }
        }).detach();
    }
    ++g_bgPending;
    g_bgQueue.push_back(std::move(job));
    g_bgCv.notify_one();
}

int BgPending() { return g_bgPending.load(); }

// ---------------- status ----------------

std::vector<std::string> SplitNul(const std::string& s) {
    std::vector<std::string> v;
    size_t p = 0;
    while (p < s.size()) {
        size_t e = s.find('\0', p);
        if (e == std::string::npos) e = s.size();
        v.push_back(s.substr(p, e - p));
        p = e + 1;
    }
    return v;
}

std::vector<std::string> SplitLines(const std::string& s) {
    std::vector<std::string> v;
    size_t p = 0;
    while (p < s.size()) {
        size_t e = s.find('\n', p);
        if (e == std::string::npos) e = s.size();
        std::string line = s.substr(p, e - p);
        if (!line.empty() && line.back() == '\r') line.pop_back();
        v.push_back(line);
        p = e + 1;
    }
    return v;
}

std::wstring GitErrorText(const ProcResult& r) {
    std::string t = TrimA(r.err);
    if (t.empty()) t = TrimA(r.out);
    if (t.size() > 2000) t = t.substr(0, 2000) + "...";
    return Utf8ToWide(t);
}

GitStatus QueryGitStatus(const std::wstring& folder) {
    GitStatus s;
    s.folder = folder;
    s.ok = true;
    if (folder.empty()) return s;
    ProcResult r = Git(folder, {L"rev-parse", L"--show-toplevel"}, nullptr, false);
    if (r.code != 0) return s;
    s.root = ToBackslashes(Utf8ToWide(TrimA(r.out)));
    s.isRepo = true;
    r = Git(s.root, {L"status", L"--porcelain=v1", L"-b", L"-z", L"--untracked-files=all"}, nullptr, false);
    if (r.code != 0) {
        s.ok = false;
        s.error = GitErrorText(r);
        return s;
    }
    auto f = SplitNul(r.out);
    for (size_t i = 0; i < f.size(); ++i) {
        const std::string& e = f[i];
        if (e.size() >= 3 && e[0] == '#' && e[1] == '#') {
            std::string h = e.substr(3);
            auto starts = [&](const char* p) { return h.compare(0, strlen(p), p) == 0; };
            if (starts("No commits yet on ")) { s.branch = Utf8ToWide(h.substr(18)); s.noCommits = true; }
            else if (starts("Initial commit on ")) { s.branch = Utf8ToWide(h.substr(18)); s.noCommits = true; }
            else if (starts("HEAD (no branch)")) { s.detached = true; s.branch = L"HEAD"; }
            else {
                size_t br = h.find(" [");
                if (br != std::string::npos) {
                    std::string info = h.substr(br + 2);
                    size_t a = info.find("ahead ");
                    if (a != std::string::npos) s.ahead = atoi(info.c_str() + a + 6);
                    size_t b = info.find("behind ");
                    if (b != std::string::npos) s.behind = atoi(info.c_str() + b + 7);
                    h = h.substr(0, br);
                }
                size_t dots = h.find("...");
                if (dots != std::string::npos) {
                    s.branch = Utf8ToWide(h.substr(0, dots));
                    s.upstream = Utf8ToWide(h.substr(dots + 3));
                } else {
                    s.branch = Utf8ToWide(h);
                }
            }
            continue;
        }
        if (e.size() < 4) continue;
        GitFile gf;
        gf.x = e[0];
        gf.y = e[1];
        gf.path = Utf8ToWide(e.substr(3));
        if ((gf.x == 'R' || gf.x == 'C') && i + 1 < f.size()) gf.origPath = Utf8ToWide(f[++i]);
        s.files.push_back(gf);
    }
    if (s.detached) {
        r = Git(s.root, {L"rev-parse", L"--short", L"HEAD"}, nullptr, false);
        if (r.code == 0) s.branch = Utf8ToWide(TrimA(r.out));
    }
    r = Git(s.root, {L"remote"}, nullptr, false);
    for (auto& l : SplitLines(r.out))
        if (!TrimA(l).empty()) s.remotes.push_back(Utf8ToWide(TrimA(l)));
    return s;
}
