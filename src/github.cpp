// Publish to GitHub: create the repository through the GitHub REST API (token from Git Credential
// Manager, or a personal access token typed by the user), add it as 'origin' and push.
#include "app.h"
#include "dialogs.h"
#include <winhttp.h>

namespace {

std::string JsonEscape(const std::string& s) {
    std::string o;
    for (unsigned char c : s) {
        switch (c) {
        case '"': o += "\\\""; break;
        case '\\': o += "\\\\"; break;
        case '\n': o += "\\n"; break;
        case '\r': o += "\\r"; break;
        case '\t': o += "\\t"; break;
        default:
            if (c < 0x20) { char b[8]; sprintf_s(b, "\\u%04x", c); o += b; }
            else o += (char)c;
        }
    }
    return o;
}

// Returns the first string value for "key" in a JSON document (simple scanner, enough for GitHub responses).
std::string JsonStr(const std::string& json, const char* key) {
    std::string pat = std::string("\"") + key + "\"";
    size_t p = json.find(pat);
    while (p != std::string::npos) {
        size_t q = p + pat.size();
        while (q < json.size() && (json[q] == ' ' || json[q] == '\n' || json[q] == '\r' || json[q] == '\t')) ++q;
        if (q < json.size() && json[q] == ':') {
            ++q;
            while (q < json.size() && (json[q] == ' ' || json[q] == '\n' || json[q] == '\r' || json[q] == '\t')) ++q;
            if (q < json.size() && json[q] == '"') {
                std::string out;
                for (++q; q < json.size() && json[q] != '"'; ++q) {
                    if (json[q] == '\\' && q + 1 < json.size()) {
                        char e = json[++q];
                        if (e == 'n') out += '\n';
                        else if (e == 't') out += '\t';
                        else if (e == 'u' && q + 4 < json.size()) {
                            unsigned v = (unsigned)strtoul(json.substr(q + 1, 4).c_str(), nullptr, 16);
                            wchar_t w = (wchar_t)v;
                            out += WideToUtf8(&w, 1);
                            q += 4;
                        } else out += e;
                    } else out += json[q];
                }
                return out;
            }
        }
        p = json.find(pat, p + 1);
    }
    return "";
}

int HttpRequest(const wchar_t* method, const wchar_t* path, const std::string& token, const std::string& body, std::string& resp,
                std::wstring& err) {
    resp.clear();
    HINTERNET s = WinHttpOpen(L"ditto/1.0", WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!s) s = WinHttpOpen(L"ditto/1.0", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!s) { err = FormatError(GetLastError()); return -1; }
    int status = -1;
    HINTERNET c = WinHttpConnect(s, L"api.github.com", INTERNET_DEFAULT_HTTPS_PORT, 0);
    HINTERNET r = c ? WinHttpOpenRequest(c, method, path, nullptr, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE) : nullptr;
    if (r) {
        std::wstring headers = L"Accept: application/vnd.github+json\r\nX-GitHub-Api-Version: 2022-11-28\r\nContent-Type: application/json\r\n"
                               L"Authorization: Bearer " + Utf8ToWide(token) + L"\r\n";
        BOOL ok = WinHttpSendRequest(r, headers.c_str(), (DWORD)-1, (LPVOID)(body.empty() ? nullptr : body.data()), (DWORD)body.size(),
                                     (DWORD)body.size(), 0);
        if (ok) ok = WinHttpReceiveResponse(r, nullptr);
        if (ok) {
            DWORD code = 0, sz = sizeof(code);
            WinHttpQueryHeaders(r, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_HEADER_NAME_BY_INDEX, &code, &sz,
                                WINHTTP_NO_HEADER_INDEX);
            status = (int)code;
            for (;;) {
                DWORD avail = 0;
                if (!WinHttpQueryDataAvailable(r, &avail) || !avail) break;
                std::string chunk(avail, '\0');
                DWORD rd = 0;
                if (!WinHttpReadData(r, &chunk[0], avail, &rd) || !rd) break;
                resp.append(chunk.data(), rd);
            }
        } else {
            err = FormatError(GetLastError());
        }
    } else {
        err = FormatError(GetLastError());
    }
    if (r) WinHttpCloseHandle(r);
    if (c) WinHttpCloseHandle(c);
    WinHttpCloseHandle(s);
    return status;
}

struct Ctx {
    std::wstring folder, root, branch, name, desc;
    bool priv = true, needInit = false, needCommit = false;
    std::string token, user;  // credential from GCM (user may be empty)
    bool manualToken = false;
};

void Fail(const std::wstring& msg) {
    RunOnUi([msg]() {
        App::SetStatusText(L"Publish failed");
        App::RefreshGit();
        MsgBox(App::Main(), msg, MB_ICONERROR);
    });
}

void Step2(std::shared_ptr<Ctx> c);

void AskToken(std::shared_ptr<Ctx> c, const std::wstring& why) {
    RunOnUi([c, why]() {
        std::wstring tok;
        std::wstring prompt = why + L"\nPaste a GitHub personal access token (scope 'repo') to continue:";
        if (!InputBox(App::Main(), L"GitHub Token", prompt, tok, true) || Trim(tok).empty()) {
            App::SetStatusText(L"Publish cancelled");
            App::RefreshGit();
            return;
        }
        c->token = WideToUtf8(Trim(tok));
        c->user.clear();
        c->manualToken = true;
        BgRun([c]() { Step2(c); });
    });
}

// Step 1 (worker): init/commit if needed, then fetch a token from the git credential helper.
void Step1(std::shared_ptr<Ctx> c) {
    std::wstring dir = c->root.empty() ? c->folder : c->root;
    if (c->needInit) {
        ProcResult r = Git(dir, {L"init"});
        if (r.code != 0) { Fail(L"git init failed:\n\n" + GitErrorText(r)); return; }
        c->root = dir;
    }
    if (c->needCommit) {
        ProcResult r = Git(dir, {L"add", L"-A"});
        if (r.code == 0) r = Git(dir, {L"commit", L"-m", L"Initial commit"});
        if (r.code != 0) { Fail(L"Creating the initial commit failed:\n\n" + GitErrorText(r)); return; }
    }
    ProcResult br = Git(dir, {L"rev-parse", L"--abbrev-ref", L"HEAD"}, nullptr, false);
    c->branch = Utf8ToWide(TrimA(br.out));
    std::string in = "protocol=https\nhost=github.com\n\n";
    ProcResult cr = Git(dir, {L"credential", L"fill"}, &in, false);
    if (cr.code == 0) {
        for (auto& l : SplitLines(cr.out)) {
            if (l.compare(0, 9, "password=") == 0) c->token = l.substr(9);
            else if (l.compare(0, 9, "username=") == 0) c->user = l.substr(9);
        }
    }
    if (c->token.empty()) { AskToken(c, L"No GitHub credentials were found (Git Credential Manager did not return a token)."); return; }
    Step2(c);
}

// Step 2 (worker): create the repository, add the remote and push.
void Step2(std::shared_ptr<Ctx> c) {
    std::string body = "{\"name\":\"" + JsonEscape(WideToUtf8(c->name)) + "\",\"description\":\"" + JsonEscape(WideToUtf8(c->desc)) +
                       "\",\"private\":" + (c->priv ? "true" : "false") + "}";
    std::string resp;
    std::wstring err;
    int status = HttpRequest(L"POST", L"/user/repos", c->token, body, resp, err);
    if (status == 401 || status == 403) {
        if (!c->manualToken) {
            std::string in = "protocol=https\nhost=github.com\nusername=" + c->user + "\npassword=" + c->token + "\n\n";
            if (status == 401) Git(c->root, {L"credential", L"reject"}, &in, false);
        }
        std::wstring msg = Utf8ToWide(JsonStr(resp, "message"));
        AskToken(c, L"GitHub rejected the credentials (HTTP " + std::to_wstring(status) + L": " + msg + L").");
        return;
    }
    if (status != 201) {
        std::wstring msg = status < 0 ? err : Utf8ToWide(JsonStr(resp, "message"));
        size_t em = resp.find("\"errors\"");
        if (em != std::string::npos) {
            std::string detail = JsonStr(resp.substr(em), "message");
            if (!detail.empty()) msg += L"\n" + Utf8ToWide(detail);
        }
        Fail(L"Could not create the GitHub repository (HTTP " + std::to_wstring(status) + L"):\n\n" + msg);
        return;
    }
    std::wstring cloneUrl = Utf8ToWide(JsonStr(resp, "clone_url"));
    std::wstring fullName = Utf8ToWide(JsonStr(resp, "full_name"));
    std::string login = JsonStr(resp, "login");
    if (cloneUrl.empty()) { Fail(L"Unexpected response from GitHub."); return; }
    ProcResult r = Git(c->root, {L"remote", L"add", L"origin", cloneUrl});
    if (r.code != 0) { Fail(L"The repository was created on GitHub, but adding the remote failed:\n\n" + GitErrorText(r)); return; }
    if (c->manualToken) {
        // store the token with the credential helper for future pushes
        std::string in = "protocol=https\nhost=github.com\nusername=" + (login.empty() ? std::string("x-access-token") : login) +
                         "\npassword=" + c->token + "\n\n";
        Git(c->root, {L"credential", L"approve"}, &in, false);
        std::string hdr = "AUTHORIZATION: basic " + Base64((login.empty() ? std::string("x-access-token") : login) + ":" + c->token);
        r = Git(c->root, {L"-c", L"http.https://github.com/.extraheader=" + Utf8ToWide(hdr), L"push", L"-u", L"origin", c->branch}, nullptr, false);
        RunOnUi([r]() { App::AppendOutput(L"> git push -u origin <branch>   (authenticated with the provided token)\r\n" +
                                          (r.code ? GitErrorText(r) + L"\r\n" : L"")); });
    } else {
        r = Git(c->root, {L"push", L"-u", L"origin", c->branch});
    }
    if (r.code != 0) { Fail(L"The repository was created on GitHub, but the push failed:\n\n" + GitErrorText(r)); return; }
    std::wstring url = L"https://github.com/" + fullName;
    std::wstring root = c->root;
    RunOnUi([url]() {
        App::SetStatusText(L"Published to " + url);
        App::RefreshGit();
        if (MsgBox(App::Main(), L"Successfully published to GitHub:\n" + url + L"\n\nOpen it in your browser?", MB_YESNO | MB_ICONINFORMATION) == IDYES)
            ShellExecuteW(nullptr, L"open", url.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
    });
}

}  // namespace

void GitPublish() {
    std::wstring folder = App::Folder();
    if (folder.empty()) { MsgBox(App::Main(), L"Open a folder first (File > Open Folder).", MB_ICONINFORMATION); return; }
    const GitStatus& st = App::Status();
    auto c = std::make_shared<Ctx>();
    c->folder = folder;
    c->root = st.isRepo ? st.root : folder;
    if (st.isRepo && std::find(st.remotes.begin(), st.remotes.end(), L"origin") != st.remotes.end()) {
        MsgBox(App::Main(), L"This repository already has a remote named 'origin'. Use Push instead.", MB_ICONINFORMATION);
        return;
    }
    if (st.isRepo && st.detached) { MsgBox(App::Main(), L"Checkout a branch before publishing.", MB_ICONWARNING); return; }
    c->needInit = !st.isRepo;
    if (!st.isRepo || st.noCommits) {
        if (MsgBox(App::Main(), L"The repository has no commits yet.\n\nStage all files and create an 'Initial commit' before publishing?",
                   MB_OKCANCEL | MB_ICONQUESTION) != IDOK)
            return;
        c->needCommit = true;
    }
    std::wstring name = FileNameOf(c->root);
    for (auto& ch : name)
        if (ch == L' ') ch = L'-';
    std::wstring desc;
    bool priv = true;
    if (!PublishBox(App::Main(), name, desc, priv)) return;
    c->name = name;
    c->desc = desc;
    c->priv = priv;
    App::SetStatusText(L"Publishing to GitHub...");
    App::ShowOutput();
    BgRun([c]() { Step1(c); });
}
