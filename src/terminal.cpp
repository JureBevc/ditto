// Integrated terminal: shells run under ConPTY and are rendered by a small xterm-compatible
// screen emulator. Terminals live in groups; the panes of a group sit side by side (splits),
// and the panel header shows one entry per group, like VS Code.
#include "main_window.h"
#include "resource.h"
#include <deque>

#ifndef PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE
#define PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE 0x00020016
#endif

namespace {

// ConPTY is resolved at runtime so ditto still starts on Windows versions without it.
typedef HRESULT(WINAPI* CreatePcFn)(COORD, HANDLE, HANDLE, DWORD, void**);
typedef HRESULT(WINAPI* ResizePcFn)(void*, COORD);
typedef void(WINAPI* ClosePcFn)(void*);
CreatePcFn pCreatePc;
ResizePcFn pResizePc;
ClosePcFn pClosePc;

bool LoadConPty() {
    static int state = -1;
    if (state < 0) {
        HMODULE k = GetModuleHandleW(L"kernel32.dll");
        pCreatePc = (CreatePcFn)(void*)GetProcAddress(k, "CreatePseudoConsole");
        pResizePc = (ResizePcFn)(void*)GetProcAddress(k, "ResizePseudoConsole");
        pClosePc = (ClosePcFn)(void*)GetProcAddress(k, "ClosePseudoConsole");
        state = pCreatePc && pResizePc && pClosePc ? 1 : 0;
    }
    return state == 1;
}

std::wstring ShellCommand(std::wstring& name) {
    wchar_t path[MAX_PATH];
    if (SearchPathW(nullptr, L"pwsh.exe", nullptr, MAX_PATH, path, nullptr)) {
        name = L"pwsh";
        return L"\"" + std::wstring(path) + L"\" -NoLogo";
    }
    wchar_t sys[MAX_PATH];
    GetSystemDirectoryW(sys, MAX_PATH);
    std::wstring ps = std::wstring(sys) + L"\\WindowsPowerShell\\v1.0\\powershell.exe";
    if (FileExists(ps)) {
        name = L"powershell";
        return L"\"" + ps + L"\" -NoLogo";
    }
    name = L"cmd";
    wchar_t comspec[MAX_PATH];
    if (GetEnvironmentVariableW(L"COMSPEC", comspec, MAX_PATH)) return comspec;
    return L"cmd.exe";
}

// Our own environment minus GIT_TERMINAL_PROMPT=0 (set for background git; shells should prompt).
std::vector<wchar_t> ShellEnvironment() {
    std::vector<wchar_t> env;
    wchar_t* block = GetEnvironmentStringsW();
    for (wchar_t* p = block; *p; p += wcslen(p) + 1)
        if (_wcsnicmp(p, L"GIT_TERMINAL_PROMPT=", 20)) env.insert(env.end(), p, p + wcslen(p) + 1);
    FreeEnvironmentStringsW(block);
    if (env.empty()) env.push_back(0);
    env.push_back(0);
    return env;
}

// ---- screen model ----

const uint32_t kDefColor = 0xFFFFFFFF;
const uint32_t kIndexed = 0x80000000;  // low byte = xterm 256-color index; otherwise a COLORREF
enum : uint8_t { A_BOLD = 1, A_DIM = 2, A_ITALIC = 4, A_UNDER = 8, A_INVERSE = 16, A_TAIL = 32 /* right half of a wide char */ };
struct Cell {
    uint32_t ch = L' ';
    uint32_t fg = kDefColor, bg = kDefColor;
    uint8_t attr = 0;
};
typedef std::vector<Cell> Line;
const size_t kMaxHistory = 5000;

COLORREF Indexed(int i) {
    static const COLORREF dark[16] = {RGB(0, 0, 0),       RGB(205, 49, 49),   RGB(13, 188, 121), RGB(229, 229, 16),
                                      RGB(36, 114, 200),  RGB(188, 63, 188),  RGB(17, 168, 205), RGB(229, 229, 229),
                                      RGB(102, 102, 102), RGB(241, 76, 76),   RGB(35, 209, 139), RGB(245, 245, 67),
                                      RGB(59, 142, 234),  RGB(214, 112, 214), RGB(41, 184, 219), RGB(229, 229, 229)};
    static const COLORREF light[16] = {RGB(0, 0, 0),       RGB(205, 49, 49), RGB(0, 188, 0),   RGB(148, 152, 0),
                                       RGB(4, 81, 165),    RGB(188, 5, 188), RGB(5, 152, 188), RGB(85, 85, 85),
                                       RGB(102, 102, 102), RGB(205, 49, 49), RGB(20, 206, 20), RGB(181, 186, 0),
                                       RGB(4, 81, 165),    RGB(188, 5, 188), RGB(5, 152, 188), RGB(165, 165, 165)};
    if (i < 16) return (g_settings.dark ? dark : light)[i];
    if (i < 232) {
        static const int lv[6] = {0, 95, 135, 175, 215, 255};
        i -= 16;
        return RGB(lv[i / 36], lv[(i / 6) % 6], lv[i % 6]);
    }
    int g = 8 + 10 * (i - 232);
    return RGB(g, g, g);
}

COLORREF ResolveColor(uint32_t v, COLORREF def, bool bright) {
    if (v == kDefColor) return def;
    if (v & kIndexed) {
        int i = (int)(v & 255);
        if (bright && i < 8) i += 8;
        return Indexed(i);
    }
    return (COLORREF)v;
}

void AppendCp(std::wstring& s, uint32_t cp) {
    if (cp >= 0x10000) {
        s += (wchar_t)(0xD800 + ((cp - 0x10000) >> 10));
        s += (wchar_t)(0xDC00 + ((cp - 0x10000) & 0x3FF));
    } else {
        s += (wchar_t)cp;
    }
}

int PadL() { return S(10); }
// Custom-drawn scrollbar, half the width of the system one.
int SbWidth() { return std::max(S(4), (int)GetSystemMetricsForDpi(SM_CXVSCROLL, (UINT)g_dpi) / 2); }
int PadT() { return S(4); }

struct Terminal {
    HWND hwnd = nullptr;
    std::wstring title;
    bool leftBorder = false;

    // ---- process ----
    void* pc = nullptr;
    HANDLE inW = nullptr, outR = nullptr, proc = nullptr;
    std::thread reader, waiter;
    std::mutex mu;
    std::string pending;  // output read by the reader thread, not yet parsed (guarded by mu)
    bool posted = false;  // a WM_APP_TERMDATA is in flight (guarded by mu)

    // ---- screen ----
    int cols = 0, rows = 0;
    std::vector<Line> lines;
    std::deque<Line> history;
    std::vector<Line> mainLines;  // primary screen while the alternate screen is active
    bool alt = false;
    int cx = 0, cy = 0, top = 0, bot = 0;
    bool wrapPending = false;
    Cell pen;
    struct Saved { int cx = 0, cy = 0; Cell pen; } saved;
    bool cursorVisible = true, appCursor = false, bracketedPaste = false;

    // ---- parser ----
    enum State { Ground, Esc, Csi, Osc, OscEsc, Str, StrEsc, Charset } state = Ground;
    std::string seq;
    uint32_t u8 = 0;
    int u8need = 0;

    // ---- view ----
    int scroll = 0;  // lines scrolled back into history
    bool hasSel = false, selecting = false;
    int sbGrab = -1;  // while dragging the scrollbar thumb: mouse offset from the thumb top
    POINT selA{}, selB{};  // x = column boundary, y = absolute line (history + screen)
    bool eatChar = false;
    wchar_t hiSurrogate = 0;
    int wheelAcc = 0;

    ~Terminal() { Stop(); }

    // ---------------- process ----------------

    bool Start(const std::wstring& cwd, std::wstring& err) {
        if (!LoadConPty()) { err = L"The integrated terminal needs Windows 10 version 1809 or later."; return false; }
        HANDLE inR = nullptr, outW = nullptr;
        if (!CreatePipe(&inR, &inW, nullptr, 0) || !CreatePipe(&outR, &outW, nullptr, 0)) {
            err = FormatError(GetLastError());
            if (inR) CloseHandle(inR);
            return false;
        }
        HRESULT hr = pCreatePc(COORD{(SHORT)cols, (SHORT)rows}, inR, outW, 0, &pc);
        CloseHandle(inR);
        CloseHandle(outW);
        if (FAILED(hr)) { pc = nullptr; err = FormatError((DWORD)hr); return false; }

        SIZE_T size = 0;
        InitializeProcThreadAttributeList(nullptr, 1, 0, &size);
        std::vector<char> attrBuf(size);
        auto attrs = (LPPROC_THREAD_ATTRIBUTE_LIST)attrBuf.data();
        InitializeProcThreadAttributeList(attrs, 1, 0, &size);
        UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, pc, sizeof(pc), nullptr, nullptr);
        STARTUPINFOEXW si = {};
        si.StartupInfo.cb = sizeof(si);
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        si.lpAttributeList = attrs;
        std::wstring cmd = ShellCommand(title);
        std::vector<wchar_t> env = ShellEnvironment();
        PROCESS_INFORMATION pi = {};
        BOOL ok = CreateProcessW(nullptr, &cmd[0], nullptr, nullptr, FALSE, EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
                                 env.data(), cwd.empty() ? nullptr : cwd.c_str(), &si.StartupInfo, &pi);
        DWORD e = GetLastError();
        DeleteProcThreadAttributeList(attrs);
        if (!ok) { err = FormatError(e); return false; }
        CloseHandle(pi.hThread);
        proc = pi.hProcess;

        HWND h = hwnd;
        reader = std::thread([this, h] {
            static thread_local char buf[65536];
            DWORD n = 0;
            while (ReadFile(outR, buf, sizeof(buf), &n, nullptr) && n) {
                std::lock_guard<std::mutex> lk(mu);
                pending.append(buf, n);
                if (!posted) {
                    posted = true;
                    PostMessageW(h, WM_APP_TERMDATA, 0, 0);
                }
            }
        });
        waiter = std::thread([this, h] {
            WaitForSingleObject(proc, INFINITE);
            PostMessageW(h, WM_APP_TERMEXIT, 0, 0);
        });
        return true;
    }

    void Stop() {
        if (inW) { CloseHandle(inW); inW = nullptr; }
        if (pc) { pClosePc(pc); pc = nullptr; }  // ends conhost, which closes the output pipe
        if (proc) TerminateProcess(proc, 0);
        if (waiter.joinable()) waiter.join();
        if (reader.joinable()) reader.join();
        if (outR) { CloseHandle(outR); outR = nullptr; }
        if (proc) { CloseHandle(proc); proc = nullptr; }
    }

    void Send(const std::string& s) {
        if (!inW || s.empty()) return;
        DWORD n;
        WriteFile(inW, s.data(), (DWORD)s.size(), &n, nullptr);
    }

    // User input: jump back to the live screen and drop the selection.
    void Input(const std::string& s) {
        if (scroll || hasSel) {
            scroll = 0;
            hasSel = false;
            InvalidateRect(hwnd, nullptr, FALSE);
        }
        Send(s);
    }

    // ---------------- screen operations ----------------

    Cell Blank() const {
        Cell c;
        c.bg = pen.bg;
        return c;
    }
    Line BlankLine() const { return Line((size_t)cols, Blank()); }

    void Reset() {
        pen = Cell();
        lines.assign((size_t)rows, Line((size_t)cols));
        mainLines.clear();
        alt = false;
        cx = cy = 0;
        top = 0;
        bot = rows - 1;
        wrapPending = false;
        cursorVisible = true;
        appCursor = bracketedPaste = false;
        saved = Saved();
    }

    void Resize(int c, int r) {
        c = std::max(2, c);
        r = std::max(1, r);
        if (c == cols && r == rows && !lines.empty()) return;
        if (lines.empty()) {
            cols = c;
            rows = r;
            Reset();
            return;
        }
        // keep the cursor row on screen by moving top lines into history
        while (cy >= r) {
            PushHistory(std::move(lines.front()));
            lines.erase(lines.begin());
            --cy;
        }
        auto fit = [&](std::vector<Line>& ls) {
            ls.resize((size_t)r, Line((size_t)c));
            for (auto& l : ls) l.resize((size_t)c);
        };
        fit(lines);
        if (alt) fit(mainLines);
        cols = c;
        rows = r;
        top = 0;
        bot = rows - 1;
        cx = std::min(cx, cols - 1);
        wrapPending = false;
        if (pc) pResizePc(pc, COORD{(SHORT)cols, (SHORT)rows});
    }

    void PushHistory(Line&& l) {
        if (alt) return;
        history.push_back(std::move(l));
        if (history.size() > kMaxHistory) history.pop_front();
        else if (scroll > 0) ++scroll;  // keep the scrolled-back view still
    }

    void ScrollUp(int n) {
        n = std::min(n, bot - top + 1);
        for (int i = 0; i < n; ++i) {
            if (top == 0) PushHistory(std::move(lines[(size_t)top]));
            lines.erase(lines.begin() + top);
            lines.insert(lines.begin() + bot, BlankLine());
        }
    }

    void ScrollDown(int n) {
        n = std::min(n, bot - top + 1);
        for (int i = 0; i < n; ++i) {
            lines.erase(lines.begin() + bot);
            lines.insert(lines.begin() + top, BlankLine());
        }
    }

    void LineFeed() {
        wrapPending = false;
        if (cy == bot) ScrollUp(1);
        else if (cy < rows - 1) ++cy;
    }

    void Put(uint32_t cp) {
        int w = CharColumns(cp);
        if (wrapPending) {
            cx = 0;
            LineFeed();
        }
        if (w == 2 && cx == cols - 1) {
            lines[(size_t)cy][(size_t)cx] = Blank();
            cx = 0;
            LineFeed();
        }
        Line& l = lines[(size_t)cy];
        if ((l[(size_t)cx].attr & A_TAIL) && cx > 0) l[(size_t)cx - 1] = Blank();  // broke a wide char in half
        Cell c = pen;
        c.ch = cp;
        l[(size_t)cx] = c;
        if (w == 2) {
            c.ch = 0;
            c.attr |= A_TAIL;
            l[(size_t)cx + 1] = c;
        } else if (cx + 1 < cols && (l[(size_t)cx + 1].attr & A_TAIL)) {
            l[(size_t)cx + 1] = Blank();
        }
        cx += w;
        if (cx >= cols) {
            cx = cols - 1;
            wrapPending = true;
        }
    }

    void EraseCells(int y, int x0, int x1) {
        Line& l = lines[(size_t)y];
        for (int x = std::max(0, x0); x < std::min(cols, x1); ++x) l[(size_t)x] = Blank();
    }

    void SaveCursor() { saved = Saved{cx, cy, pen}; }
    void RestoreCursor() {
        cx = std::min(saved.cx, cols - 1);
        cy = std::min(saved.cy, rows - 1);
        pen = saved.pen;
        wrapPending = false;
    }

    void SetMode(int m, bool on) {
        switch (m) {
        case 1: appCursor = on; break;
        case 25: cursorVisible = on; break;
        case 2004: bracketedPaste = on; break;
        case 47:
        case 1047:
        case 1049:
            if (on && !alt) {
                if (m == 1049) SaveCursor();
                mainLines = lines;
                lines.assign((size_t)rows, Line((size_t)cols));
                alt = true;
            } else if (!on && alt) {
                lines = mainLines;
                mainLines.clear();
                alt = false;
                if (m == 1049) RestoreCursor();
            }
            scroll = 0;
            hasSel = false;
            break;
        }
    }

    void Sgr(const std::vector<int>& p) {
        auto B = [](int v) { return (BYTE)std::max(0, std::min(255, v)); };
        for (size_t i = 0; i < p.size(); ++i) {
            int v = std::max(0, p[i]);
            if (v == 0) { pen.fg = pen.bg = kDefColor; pen.attr = 0; }
            else if (v == 1) pen.attr |= A_BOLD;
            else if (v == 2) pen.attr |= A_DIM;
            else if (v == 3) pen.attr |= A_ITALIC;
            else if (v == 4) pen.attr |= A_UNDER;
            else if (v == 7) pen.attr |= A_INVERSE;
            else if (v == 22) pen.attr &= (uint8_t)~(A_BOLD | A_DIM);
            else if (v == 23) pen.attr &= (uint8_t)~A_ITALIC;
            else if (v == 24) pen.attr &= (uint8_t)~A_UNDER;
            else if (v == 27) pen.attr &= (uint8_t)~A_INVERSE;
            else if (v >= 30 && v <= 37) pen.fg = kIndexed | (uint32_t)(v - 30);
            else if (v >= 40 && v <= 47) pen.bg = kIndexed | (uint32_t)(v - 40);
            else if (v >= 90 && v <= 97) pen.fg = kIndexed | (uint32_t)(v - 90 + 8);
            else if (v >= 100 && v <= 107) pen.bg = kIndexed | (uint32_t)(v - 100 + 8);
            else if (v == 39) pen.fg = kDefColor;
            else if (v == 49) pen.bg = kDefColor;
            else if (v == 38 || v == 48) {
                uint32_t c;
                if (i + 2 < p.size() && p[i + 1] == 5) { c = kIndexed | (uint32_t)(std::max(0, p[i + 2]) & 255); i += 2; }
                else if (i + 4 < p.size() && p[i + 1] == 2) { c = RGB(B(p[i + 2]), B(p[i + 3]), B(p[i + 4])); i += 4; }
                else break;
                (v == 38 ? pen.fg : pen.bg) = c;
            }
        }
    }

    void DoCsi(char fin) {
        char priv = 0;
        size_t i = 0;
        if (!seq.empty() && strchr("?<=>", seq[0])) priv = seq[i++];
        std::vector<int> p;
        int cur = -1;  // -1 = omitted parameter
        for (; i < seq.size(); ++i) {
            char c = seq[i];
            if (c >= '0' && c <= '9') cur = std::min(65535, (cur < 0 ? 0 : cur) * 10 + (c - '0'));
            else if (c == ';' || c == ':') { p.push_back(cur); cur = -1; }
            else return;  // intermediate bytes: a sequence we don't support
        }
        p.push_back(cur);
        auto P = [&](size_t k, int def) { return k < p.size() && p[k] > 0 ? p[k] : def; };
        int n = P(0, 1);
        if (strchr("ABCDEFGHadef`LMru", fin)) wrapPending = false;
        switch (fin) {
        case 'A': cy = std::max(cy >= top ? top : 0, cy - n); break;
        case 'B': case 'e': cy = std::min(cy <= bot ? bot : rows - 1, cy + n); break;
        case 'C': case 'a': cx = std::min(cols - 1, cx + n); break;
        case 'D': cx = std::max(0, cx - n); break;
        case 'E': cy = std::min(cy <= bot ? bot : rows - 1, cy + n); cx = 0; break;
        case 'F': cy = std::max(cy >= top ? top : 0, cy - n); cx = 0; break;
        case 'G': case '`': cx = std::min(cols - 1, n - 1); break;
        case 'd': cy = std::min(rows - 1, n - 1); break;
        case 'H': case 'f':
            cy = std::min(rows - 1, P(0, 1) - 1);
            cx = std::min(cols - 1, P(1, 1) - 1);
            break;
        case 'J': {
            int m = std::max(0, p[0]);
            if (m == 0) {
                EraseCells(cy, cx, cols);
                for (int y = cy + 1; y < rows; ++y) EraseCells(y, 0, cols);
            } else if (m == 1) {
                for (int y = 0; y < cy; ++y) EraseCells(y, 0, cols);
                EraseCells(cy, 0, cx + 1);
            } else if (m == 2) {
                for (int y = 0; y < rows; ++y) EraseCells(y, 0, cols);
            } else if (m == 3) {
                history.clear();
                scroll = 0;
                hasSel = false;
            }
            break;
        }
        case 'K': {
            int m = std::max(0, p[0]);
            if (m == 0) EraseCells(cy, cx, cols);
            else if (m == 1) EraseCells(cy, 0, cx + 1);
            else EraseCells(cy, 0, cols);
            break;
        }
        case 'L':
        case 'M':
            if (cy >= top && cy <= bot) {
                n = std::min(n, bot - cy + 1);
                for (int k = 0; k < n; ++k) {
                    if (fin == 'L') {
                        lines.erase(lines.begin() + bot);
                        lines.insert(lines.begin() + cy, BlankLine());
                    } else {
                        lines.erase(lines.begin() + cy);
                        lines.insert(lines.begin() + bot, BlankLine());
                    }
                }
                cx = 0;
            }
            break;
        case '@': {
            Line& l = lines[(size_t)cy];
            n = std::min(n, cols - cx);
            l.insert(l.begin() + cx, (size_t)n, Blank());
            l.resize((size_t)cols);
            break;
        }
        case 'P': {
            Line& l = lines[(size_t)cy];
            n = std::min(n, cols - cx);
            l.erase(l.begin() + cx, l.begin() + cx + n);
            l.resize((size_t)cols, Blank());
            break;
        }
        case 'X': EraseCells(cy, cx, cx + n); break;
        case 'S': if (!priv) ScrollUp(n); break;
        case 'T': if (!priv) ScrollDown(n); break;
        case 'm': if (!priv) Sgr(p); break;
        case 'r':
            if (!priv) {
                int t = P(0, 1) - 1, b = P(1, rows) - 1;
                if (t < b && b < rows) { top = t; bot = b; }
                else { top = 0; bot = rows - 1; }
                cx = cy = 0;
            }
            break;
        case 'h':
        case 'l':
            if (priv == '?')
                for (int m : p) SetMode(m, fin == 'h');
            break;
        case 'n':
            if (!priv && p[0] == 6) Send("\x1b[" + std::to_string(cy + 1) + ";" + std::to_string(cx + 1) + "R");
            else if (!priv && p[0] == 5) Send("\x1b[0n");
            break;
        case 'c': if (!priv) Send("\x1b[?1;0c"); break;
        case 's': if (!priv) SaveCursor(); break;
        case 'u': if (!priv) RestoreCursor(); break;
        }
        cx = std::max(0, std::min(cols - 1, cx));
        cy = std::max(0, std::min(rows - 1, cy));
    }

    void DoOsc() {
        size_t semi = seq.find(';');
        if (semi == std::string::npos) return;
        std::string code = seq.substr(0, semi);
        if (code == "0" || code == "2") {
            title = Utf8ToWide(seq.substr(semi + 1));
            InvalidateRect(M.hwnd, &M.rcOutputHeader, FALSE);
        }
    }

    void EscByte(unsigned char c) {
        state = Ground;
        switch (c) {
        case '[': state = Csi; seq.clear(); break;
        case ']': state = Osc; seq.clear(); break;
        case 'P': case 'X': case '^': case '_': state = Str; break;  // DCS/SOS/PM/APC: ignored
        case '(': case ')': case '*': case '+': state = Charset; break;
        case '7': SaveCursor(); break;
        case '8': RestoreCursor(); break;
        case 'D': LineFeed(); break;
        case 'E': cx = 0; LineFeed(); break;
        case 'M':
            wrapPending = false;
            if (cy == top) ScrollDown(1);
            else if (cy > 0) --cy;
            break;
        case 'c': Reset(); break;
        case 0x1B: state = Esc; break;
        }
    }

    void Control(unsigned char c) {
        switch (c) {
        case 0x08: if (cx > 0) --cx; wrapPending = false; break;
        case 0x09: cx = std::min(cols - 1, (cx / 8 + 1) * 8); break;
        case 0x0A: case 0x0B: case 0x0C: LineFeed(); break;
        case 0x0D: cx = 0; wrapPending = false; break;
        case 0x1B: state = Esc; break;
        }
    }

    void Byte(unsigned char c) {
        switch (state) {
        case Ground: break;
        case Esc: EscByte(c); return;
        case Csi:
            if (c >= 0x40 && c <= 0x7E) { state = Ground; DoCsi((char)c); }
            else if (c >= 0x20) { if (seq.size() < 256) seq += (char)c; }
            else if (c == 0x18 || c == 0x1A) state = Ground;
            else Control(c);
            return;
        case Osc:
        case Str:
            if (c == 0x07) { if (state == Osc) DoOsc(); state = Ground; }
            else if (c == 0x1B) state = state == Osc ? OscEsc : StrEsc;
            else if (state == Osc && seq.size() < 4096) seq += (char)c;
            return;
        case OscEsc:
        case StrEsc:
            if (state == OscEsc) DoOsc();  // ESC \ ends the string; any other escape does too
            state = Ground;
            if (c != '\\') EscByte(c);
            return;
        case Charset: state = Ground; return;
        }
        if (u8need) {
            if ((c & 0xC0) == 0x80) {
                u8 = (u8 << 6) | (c & 0x3F);
                if (--u8need == 0) Put(u8 >= 0x110000 || (u8 >= 0xD800 && u8 < 0xE000) ? 0xFFFD : u8);
                return;
            }
            u8need = 0;
            Put(0xFFFD);
        }
        if (c < 0x20) Control(c);
        else if (c < 0x7F) Put(c);
        else if (c == 0x7F) {}
        else if (c >= 0xC2 && c <= 0xDF) { u8 = c & 0x1F; u8need = 1; }
        else if (c >= 0xE0 && c <= 0xEF) { u8 = c & 0x0F; u8need = 2; }
        else if (c >= 0xF0 && c <= 0xF4) { u8 = c & 0x07; u8need = 3; }
        else Put(0xFFFD);
    }

    void Feed(const std::string& s) {
        for (char c : s) Byte((unsigned char)c);
    }

    // ---------------- view ----------------

    const Line& LineAt(int abs) const {
        return abs < (int)history.size() ? history[(size_t)abs] : lines[(size_t)(abs - (int)history.size())];
    }
    int ViewTop() const { return (int)history.size() - scroll; }

    void FitToWindow() {
        RECT rc;
        GetClientRect(hwnd, &rc);
        if (rc.right <= 0 || rc.bottom <= 0) return;  // hidden or minimized: keep the old size
        Resize((rc.right - SbWidth() - PadL() - S(4)) / g_ef.cw, (rc.bottom - PadT()) / g_ef.lh);
        InvalidateRect(hwnd, nullptr, FALSE);
    }

    void ScrollTo(int viewTop) {
        int h = (int)history.size();
        scroll = std::max(0, std::min(h, h - viewTop));
        InvalidateRect(hwnd, nullptr, FALSE);
    }

    // Scrollbar thumb in client coordinates; empty when there is no history to scroll.
    RECT ThumbRect() const {
        RECT rc;
        GetClientRect(hwnd, &rc);
        int hist = (int)history.size(), trackH = rc.bottom;
        if (!hist || trackH <= 0) return RECT{};
        int thumbH = std::min(trackH, std::max(S(20), trackH * rows / (hist + rows)));
        int y = (int)((int64_t)(trackH - thumbH) * ViewTop() / hist);
        return RECT{rc.right - SbWidth(), y, rc.right, y + thumbH};
    }

    bool OnScrollBar(LPARAM l) const {
        RECT rc;
        GetClientRect(hwnd, &rc);
        return GET_X_LPARAM(l) >= rc.right - SbWidth();
    }

    // Drag the thumb so its top sits at mouse y minus the grab offset.
    void DragThumb(int y) {
        RECT rc, th = ThumbRect();
        GetClientRect(hwnd, &rc);
        int span = rc.bottom - (th.bottom - th.top);
        if (span <= 0) return;
        int hist = (int)history.size();
        ScrollTo((int)(((int64_t)(y - sbGrab) * hist + span / 2) / span));
    }

    bool SelRange(POINT& a, POINT& b) const {
        if (!hasSel) return false;
        a = selA;
        b = selB;
        if (b.y < a.y || (b.y == a.y && b.x < a.x)) std::swap(a, b);
        return true;
    }

    // round: snap to the nearest column boundary (for drag selection) instead of the cell under the mouse
    POINT CellAt(LPARAM l, bool round) const {
        int x = GET_X_LPARAM(l) - PadL() + (round ? g_ef.cw / 2 : 0);
        int y = GET_Y_LPARAM(l) - PadT();
        int col = std::max(0, std::min(cols, x / g_ef.cw));
        int row = std::max(0, std::min(rows - 1, y < 0 ? 0 : y / g_ef.lh));
        return POINT{col, ViewTop() + row};
    }

    void SelectWord(POINT p) {
        const Line& l = LineAt(p.y);
        int x = std::min((int)p.x, (int)l.size() - 1);
        if (x < 0) return;
        auto isWord = [&](int i) { return l[(size_t)i].ch > L' ' || (l[(size_t)i].attr & A_TAIL); };
        if (!isWord(x)) return;
        int a = x, b = x + 1;
        while (a > 0 && isWord(a - 1)) --a;
        while (b < (int)l.size() && isWord(b)) ++b;
        selA = POINT{a, p.y};
        selB = POINT{b, p.y};
        hasSel = true;
        InvalidateRect(hwnd, nullptr, FALSE);
    }

    std::wstring SelectedText() const {
        POINT a, b;
        if (!SelRange(a, b)) return L"";
        std::wstring out;
        int total = (int)history.size() + rows;
        for (int y = std::max(0, (int)a.y); y <= b.y && y < total; ++y) {
            const Line& l = LineAt(y);
            int x0 = y == a.y ? a.x : 0, x1 = y == b.y ? b.x : (int)l.size();
            std::wstring s;
            for (int x = x0; x < x1 && x < (int)l.size(); ++x) {
                const Cell& c = l[(size_t)x];
                if (!(c.attr & A_TAIL)) AppendCp(s, c.ch < 0x20 ? L' ' : c.ch);
            }
            while (!s.empty() && s.back() == L' ') s.pop_back();
            out += s;
            if (y != b.y) out += L"\r\n";
        }
        return out;
    }

    void Copy() {
        std::wstring s = SelectedText();
        if (!s.empty()) SetClipboardText(hwnd, s);
        hasSel = false;
        InvalidateRect(hwnd, nullptr, FALSE);
    }

    void Paste() {
        std::wstring s;
        if (!GetClipboardText(hwnd, s) || s.empty()) return;
        std::wstring n;
        for (size_t i = 0; i < s.size(); ++i) {
            if (s[i] == L'\r') {
                if (i + 1 < s.size() && s[i + 1] == L'\n') ++i;
                n += L'\r';
            } else {
                n += s[i] == L'\n' ? L'\r' : s[i];
            }
        }
        std::string u = WideToUtf8(n);
        if (bracketedPaste) u = "\x1b[200~" + u + "\x1b[201~";
        Input(u);
    }

    bool Key(WPARAM vk) {
        bool ctrl = GetKeyState(VK_CONTROL) < 0, shift = GetKeyState(VK_SHIFT) < 0, altKey = GetKeyState(VK_MENU) < 0;
        if (ctrl && vk == 'C' && (shift || hasSel)) { Copy(); eatChar = true; return true; }
        if (ctrl && vk == 'V') { Paste(); eatChar = true; return true; }
        if (shift && !ctrl && (vk == VK_PRIOR || vk == VK_NEXT)) {
            ScrollTo(ViewTop() + (vk == VK_PRIOR ? -(rows - 1) : rows - 1));
            return true;
        }
        int mod = 1 + (shift ? 1 : 0) + (altKey ? 2 : 0) + (ctrl ? 4 : 0);
        char letter = 0;
        int num = 0;
        bool ss3 = false;
        switch (vk) {
        case VK_UP: letter = 'A'; ss3 = appCursor; break;
        case VK_DOWN: letter = 'B'; ss3 = appCursor; break;
        case VK_RIGHT: letter = 'C'; ss3 = appCursor; break;
        case VK_LEFT: letter = 'D'; ss3 = appCursor; break;
        case VK_HOME: letter = 'H'; ss3 = appCursor; break;
        case VK_END: letter = 'F'; ss3 = appCursor; break;
        case VK_F1: letter = 'P'; ss3 = true; break;
        case VK_F2: letter = 'Q'; ss3 = true; break;
        case VK_F3: letter = 'R'; ss3 = true; break;
        case VK_F4: letter = 'S'; ss3 = true; break;
        case VK_INSERT: num = 2; break;
        case VK_DELETE: num = 3; break;
        case VK_PRIOR: num = 5; break;
        case VK_NEXT: num = 6; break;
        case VK_F5: num = 15; break;
        case VK_F6: num = 17; break;
        case VK_F7: num = 18; break;
        case VK_F8: num = 19; break;
        case VK_F9: num = 20; break;
        case VK_F11: num = 23; break;
        case VK_F12: num = 24; break;
        default: return false;
        }
        std::string out;
        if (letter) {
            if (mod > 1) out = "\x1b[1;" + std::to_string(mod) + letter;
            else out = std::string(ss3 ? "\x1bO" : "\x1b[") + letter;
        } else {
            out = "\x1b[" + std::to_string(num) + (mod > 1 ? ";" + std::to_string(mod) : "") + "~";
        }
        Input(out);
        return true;
    }

    void Char(wchar_t c) {
        if (eatChar) { eatChar = false; return; }
        std::wstring s;
        if (c >= 0xD800 && c < 0xDC00) { hiSurrogate = c; return; }
        if (c >= 0xDC00 && c < 0xE000) {
            if (!hiSurrogate) return;
            s = {hiSurrogate, c};
            hiSurrogate = 0;
        } else if (c == 0x08) {
            s = L"\x7f";  // Backspace sends DEL, Ctrl+Backspace sends ^H
        } else if (c == 0x7F) {
            s = L"\x08";
        } else {
            s = c;
        }
        Input(WideToUtf8(s));
    }

    void Paint(HDC hdc) {
        RECT rc;
        GetClientRect(hwnd, &rc);
        HDC dc = CreateCompatibleDC(hdc);
        HBITMAP bmp = CreateCompatibleBitmap(hdc, std::max(1, (int)rc.right), std::max(1, (int)rc.bottom));
        HGDIOBJ ob = SelectObject(dc, bmp);
        HGDIOBJ of = SelectObject(dc, g_ef.normal);
        auto fill = [&](const RECT& r, COLORREF c) {
            SetBkColor(dc, c);
            ExtTextOutW(dc, 0, 0, ETO_OPAQUE, &r, nullptr, 0, nullptr);
        };
        fill(rc, g_theme.bg);
        int cw = g_ef.cw, lh = g_ef.lh, padL = PadL(), padT = PadT(), ty = (lh - (g_ef.lh - S(3))) / 2;
        POINT sa{}, sb{};
        bool sel = SelRange(sa, sb);
        auto inSel = [&](int y, int x) {
            return sel && (y > sa.y || (y == sa.y && x >= sa.x)) && (y < sb.y || (y == sb.y && x < sb.x));
        };
        auto resolve = [&](const Cell& c, bool selected, COLORREF& fg, COLORREF& bg, HFONT& f, bool& under) {
            fg = ResolveColor(c.fg, g_theme.fg, (c.attr & A_BOLD) != 0);
            bg = ResolveColor(c.bg, g_theme.bg, false);
            if (c.attr & A_INVERSE) std::swap(fg, bg);
            if (c.attr & A_DIM) fg = RGB((GetRValue(fg) + GetRValue(bg)) / 2, (GetGValue(fg) + GetGValue(bg)) / 2, (GetBValue(fg) + GetBValue(bg)) / 2);
            if (selected) bg = g_theme.selBg;
            f = (c.attr & A_BOLD) ? g_ef.bold : (c.attr & A_ITALIC) ? g_ef.italic : g_ef.normal;
            under = (c.attr & A_UNDER) != 0;
        };
        std::wstring text;
        std::vector<int> dx;
        int viewTop = ViewTop();
        for (int r = 0; r < rows; ++r) {
            int abs = viewTop + r;
            const Line& l = LineAt(abs);
            int y = padT + r * lh;
            int ncols = std::min((int)l.size(), cols);
            int x = 0;
            while (x < ncols) {
                COLORREF fg, bg;
                HFONT f;
                bool under;
                resolve(l[(size_t)x], inSel(abs, x), fg, bg, f, under);
                int x0 = x;
                text.clear();
                dx.clear();
                while (x < ncols) {
                    const Cell& c = l[(size_t)x];
                    if (x > x0) {
                        COLORREF fg2, bg2;
                        HFONT f2;
                        bool u2;
                        resolve(c, inSel(abs, x), fg2, bg2, f2, u2);
                        if (fg2 != fg || bg2 != bg || f2 != f || u2 != under) break;
                    }
                    bool tail = (c.attr & A_TAIL) != 0;
                    bool wide = !tail && x + 1 < ncols && (l[(size_t)x + 1].attr & A_TAIL);
                    uint32_t ch = tail || c.ch < 0x20 ? L' ' : c.ch;
                    size_t before = text.size();
                    AppendCp(text, ch);
                    dx.push_back(wide ? 2 * cw : cw);
                    if (text.size() - before == 2) dx.push_back(0);
                    x += wide ? 2 : 1;
                }
                RECT cr = {padL + x0 * cw, y, padL + x * cw, y + lh};
                SelectObject(dc, f);
                SetTextColor(dc, fg);
                SetBkColor(dc, bg);
                ExtTextOutW(dc, cr.left, y + ty, ETO_OPAQUE | ETO_CLIPPED, &cr, text.c_str(), (UINT)text.size(), dx.data());
                if (under) {
                    RECT u = {cr.left, cr.bottom - S(2), cr.right, cr.bottom - S(1)};
                    fill(u, fg);
                }
            }
        }
        int curRow = cy + scroll;
        if (cursorVisible && curRow < rows) {
            RECT cr = {padL + cx * cw, padT + curRow * lh, padL + (cx + 1) * cw, padT + (curRow + 1) * lh};
            if (GetFocus() == hwnd) {
                fill(cr, g_theme.fg);
                const Cell& c = lines[(size_t)cy][(size_t)cx];
                if (c.ch > L' ' && !(c.attr & A_TAIL)) {
                    std::wstring s;
                    AppendCp(s, c.ch);
                    SelectObject(dc, g_ef.normal);
                    SetTextColor(dc, g_theme.bg);
                    SetBkMode(dc, TRANSPARENT);
                    ExtTextOutW(dc, cr.left, cr.top + ty, ETO_CLIPPED, &cr, s.c_str(), (UINT)s.size(), nullptr);
                    SetBkMode(dc, OPAQUE);
                }
            } else {
                HBRUSH b = CreateSolidBrush(g_theme.fg);
                FrameRect(dc, &cr, b);
                DeleteObject(b);
            }
        }
        RECT th = ThumbRect();
        if (th.bottom > th.top) {
            int pct = sbGrab >= 0 ? 45 : 30;
            COLORREF a = g_theme.fg, b = g_theme.bg;
            fill(th, RGB((GetRValue(a) * pct + GetRValue(b) * (100 - pct)) / 100, (GetGValue(a) * pct + GetGValue(b) * (100 - pct)) / 100,
                         (GetBValue(a) * pct + GetBValue(b) * (100 - pct)) / 100));
        }
        if (leftBorder) {
            RECT b = {0, 0, 1, rc.bottom};
            fill(b, g_theme.border);
        }
        BitBlt(hdc, 0, 0, rc.right, rc.bottom, dc, 0, 0, SRCCOPY);
        SelectObject(dc, of);
        SelectObject(dc, ob);
        DeleteObject(bmp);
        DeleteDC(dc);
    }
};

// ---------------- groups ----------------

struct Group {
    std::vector<Terminal*> panes;
    int active = 0;
};
std::vector<Group> g_groups;
int g_activeGroup = -1;
const wchar_t* kClass = L"DittoTerminal";

Terminal* ActivePane() {
    if (g_activeGroup < 0) return nullptr;
    Group& g = g_groups[(size_t)g_activeGroup];
    return g.panes.empty() ? nullptr : g.panes[(size_t)g.active];
}

Terminal* FindPane(HWND h) {
    if (!h) return nullptr;
    for (auto& g : g_groups)
        for (Terminal* t : g.panes)
            if (t->hwnd == h) return t;
    return nullptr;
}

void RemovePane(Terminal* t) {
    for (size_t gi = 0; gi < g_groups.size(); ++gi) {
        auto& ps = g_groups[gi].panes;
        auto it = std::find(ps.begin(), ps.end(), t);
        if (it == ps.end()) continue;
        bool hadFocus = GetFocus() == t->hwnd;
        ps.erase(it);
        if (ps.empty()) {
            g_groups.erase(g_groups.begin() + (ptrdiff_t)gi);
            if ((int)gi < g_activeGroup || g_activeGroup >= (int)g_groups.size()) --g_activeGroup;
        } else {
            g_groups[gi].active = std::min(g_groups[gi].active, (int)ps.size() - 1);
        }
        DestroyWindow(t->hwnd);
        delete t;  // stops the shell and joins its threads
        TerminalsChanged();
        if (hadFocus) {
            if (Terminal* a = ActivePane()) SetFocus(a->hwnd);
            else SetFocus(M.hwnd);
        }
        return;
    }
}

LRESULT CALLBACK TermProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    Terminal* t = (Terminal*)GetWindowLongPtrW(h, GWLP_USERDATA);
    if (!t) return DefWindowProcW(h, m, w, l);
    switch (m) {
    case WM_APP_TERMDATA: {
        std::string data;
        {
            std::lock_guard<std::mutex> lk(t->mu);
            data.swap(t->pending);
            t->posted = false;
        }
        t->Feed(data);
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    }
    case WM_APP_TERMEXIT:
        RemovePane(t);  // like VS Code: the pane closes when its shell exits
        return 0;
    case WM_SIZE:
        t->FitToWindow();
        return 0;
    case WM_ERASEBKGND:
        return 1;
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(h, &ps);
        t->Paint(dc);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_SETFOCUS:
        for (auto& g : g_groups)
            for (size_t i = 0; i < g.panes.size(); ++i)
                if (g.panes[i] == t) g.active = (int)i;
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    case WM_KILLFOCUS:
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    case WM_KEYDOWN:
        if (t->Key(w)) return 0;
        break;
    case WM_CHAR:
        t->Char((wchar_t)w);
        return 0;
    case WM_SETCURSOR:
        if (LOWORD(l) == HTCLIENT) {
            POINT pt;
            GetCursorPos(&pt);
            ScreenToClient(h, &pt);
            SetCursor(LoadCursor(nullptr, t->OnScrollBar(MAKELPARAM(pt.x, pt.y)) || t->sbGrab >= 0 ? IDC_ARROW : IDC_IBEAM));
            return TRUE;
        }
        break;
    case WM_LBUTTONDOWN:
        SetFocus(h);
        SetCapture(h);
        if (t->OnScrollBar(l)) {
            RECT th = t->ThumbRect();
            if (th.bottom <= th.top) { ReleaseCapture(); return 0; }
            int y = GET_Y_LPARAM(l);
            // grabbing the thumb keeps its offset; clicking the track centres the thumb on the mouse
            t->sbGrab = y >= th.top && y < th.bottom ? y - th.top : (th.bottom - th.top) / 2;
            t->DragThumb(y);
            return 0;
        }
        t->selA = t->selB = t->CellAt(l, true);
        t->hasSel = false;
        t->selecting = true;
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    case WM_MOUSEMOVE:
        if (t->sbGrab >= 0) {
            t->DragThumb(GET_Y_LPARAM(l));
        } else if (t->selecting) {
            t->selB = t->CellAt(l, true);
            t->hasSel = t->selA.x != t->selB.x || t->selA.y != t->selB.y;
            InvalidateRect(h, nullptr, FALSE);
        }
        return 0;
    case WM_LBUTTONUP:
        if (t->selecting || t->sbGrab >= 0) ReleaseCapture();
        return 0;
    case WM_CAPTURECHANGED:
        t->selecting = false;
        if (t->sbGrab >= 0) {
            t->sbGrab = -1;
            InvalidateRect(h, nullptr, FALSE);
        }
        return 0;
    case WM_LBUTTONDBLCLK:
        if (t->OnScrollBar(l)) return 0;
        t->SelectWord(t->CellAt(l, false));
        return 0;
    case WM_RBUTTONUP:  // Windows Terminal / VS Code style: copy the selection, or paste
        SetFocus(h);
        if (t->hasSel) t->Copy();
        else t->Paste();
        return 0;
    case WM_MOUSEWHEEL: {
        t->wheelAcc += GET_WHEEL_DELTA_WPARAM(w);
        int notches = t->wheelAcc / WHEEL_DELTA;
        t->wheelAcc -= notches * WHEEL_DELTA;
        if (!notches) return 0;
        if (t->alt) {  // full-screen apps (less, vim...) get arrow keys
            std::string key = t->appCursor ? (notches > 0 ? "\x1bOA" : "\x1bOB") : (notches > 0 ? "\x1b[A" : "\x1b[B");
            std::string s;
            for (int i = 0; i < 3 * std::abs(notches); ++i) s += key;
            t->Send(s);
        } else {
            t->ScrollTo(t->ViewTop() - 3 * notches);
        }
        return 0;
    }
    }
    return DefWindowProcW(h, m, w, l);
}

void AddPane(bool split) {
    static bool registered = false;
    if (!registered) {
        WNDCLASSEXW wc = {sizeof(wc)};
        wc.style = CS_DBLCLKS;
        wc.lpfnWndProc = TermProc;
        wc.hInstance = g_hinst;
        wc.hCursor = LoadCursor(nullptr, IDC_IBEAM);
        wc.lpszClassName = kClass;
        RegisterClassExW(&wc);
        registered = true;
    }
    Terminal* t = new Terminal;
    t->hwnd = CreateWindowExW(0, kClass, L"", WS_CHILD | WS_CLIPSIBLINGS, 0, 0, 0, 0, M.hwnd, nullptr, g_hinst, nullptr);
    SetWindowLongPtrW(t->hwnd, GWLP_USERDATA, (LONG_PTR)t);
    t->Resize(80, 24);
    if (split && g_activeGroup >= 0) {
        Group& g = g_groups[(size_t)g_activeGroup];
        g.panes.insert(g.panes.begin() + g.active + 1, t);
        ++g.active;
    } else {
        g_groups.push_back(Group{{t}, 0});
        g_activeGroup = (int)g_groups.size() - 1;
    }
    TerminalsChanged();  // lays the pane out first so the shell starts with the right size
    std::wstring err;
    std::wstring cwd = M.folder;
    if (cwd.empty()) {
        wchar_t home[MAX_PATH];
        if (GetEnvironmentVariableW(L"USERPROFILE", home, MAX_PATH)) cwd = home;
    }
    if (!t->Start(cwd, err)) {
        RemovePane(t);
        MsgBox(M.hwnd, L"Could not start the terminal.\n\n" + err, MB_ICONERROR);
        return;
    }
    SetFocus(t->hwnd);
}

std::wstring PaneName(const Terminal* t) {
    std::wstring s = t->title;
    if (s.find_first_of(L"\\/") != std::wstring::npos) s = FileNameOf(s);
    if (s.size() > 4 && !_wcsicmp(s.c_str() + s.size() - 4, L".exe")) s.resize(s.size() - 4);
    return s.empty() ? L"terminal" : s;
}

}  // namespace

namespace Term {

int GroupCount() { return (int)g_groups.size(); }
int ActiveGroup() { return g_activeGroup; }

std::wstring GroupTitle(int i) {
    std::wstring s;
    for (Terminal* t : g_groups[(size_t)i].panes) s += (s.empty() ? L"" : L", ") + PaneName(t);
    return std::to_wstring(i + 1) + L": " + s;
}

void SelectGroup(int i) {
    if (i < 0 || i >= (int)g_groups.size()) return;
    g_activeGroup = i;
    TerminalsChanged();
    Focus();
}

void New() { AddPane(false); }
void Split() { AddPane(g_activeGroup >= 0); }

void KillActive() {
    if (Terminal* t = ActivePane()) RemovePane(t);
}

void KillGroup(int i) {
    if (i < 0 || i >= (int)g_groups.size()) return;
    std::vector<Terminal*> ps = g_groups[(size_t)i].panes;
    for (Terminal* t : ps) RemovePane(t);
}

void Layout(const RECT& rc, bool visible) {
    for (size_t gi = 0; gi < g_groups.size(); ++gi) {
        auto& ps = g_groups[gi].panes;
        for (size_t pi = 0; pi < ps.size(); ++pi) {
            Terminal* t = ps[pi];
            if (visible && (int)gi == g_activeGroup) {
                int w = rc.right - rc.left, n = (int)ps.size();
                int x0 = rc.left + w * (int)pi / n, x1 = rc.left + w * ((int)pi + 1) / n;
                t->leftBorder = pi > 0;
                MoveWindow(t->hwnd, x0, rc.top, x1 - x0, rc.bottom - rc.top, TRUE);
                ShowWindow(t->hwnd, SW_SHOW);
            } else {
                ShowWindow(t->hwnd, SW_HIDE);
            }
        }
    }
}

bool Focus() {
    Terminal* t = ActivePane();
    if (!t) return false;
    SetFocus(t->hwnd);
    return true;
}

bool HasFocus() { return FindPane(GetFocus()) != nullptr; }

bool WantsKey(const MSG& msg) {
    if ((msg.message != WM_KEYDOWN && msg.message != WM_SYSKEYDOWN) || !FindPane(msg.hwnd)) return false;
    bool ctrl = GetKeyState(VK_CONTROL) < 0, shift = GetKeyState(VK_SHIFT) < 0;
    WPARAM vk = msg.wParam;
    // workbench shortcuts that still work while a terminal has focus; everything else goes to the shell
    bool global = (ctrl && vk == VK_OEM_3) || (ctrl && shift && vk == '5') || vk == VK_F1 || (ctrl && vk == 'P') ||
                  (ctrl && (vk == VK_TAB || vk == VK_PRIOR || vk == VK_NEXT)) || (ctrl && shift && (vk == 'E' || vk == 'G'));
    return !global;
}

void FontChanged() {
    for (auto& g : g_groups)
        for (Terminal* t : g.panes) t->FitToWindow();
}

void ThemeChanged() {
    for (auto& g : g_groups)
        for (Terminal* t : g.panes) InvalidateRect(t->hwnd, nullptr, FALSE);
}

void CloseAll() {
    for (auto& g : g_groups)
        for (Terminal* t : g.panes) {
            DestroyWindow(t->hwnd);
            delete t;
        }
    g_groups.clear();
    g_activeGroup = -1;
}

}  // namespace Term
