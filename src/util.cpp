#include "common.h"

HINSTANCE g_hinst;
HWND g_mainWnd;
HFONT g_uiFont;
HFONT g_uiFontBold;
HFONT g_iconFont;
int g_dpi = 96;
Theme g_theme;
Settings g_settings;

std::wstring Utf8ToWide(const char* s, size_t n) {
    if (!n) return std::wstring();
    std::wstring out;
    // chunk to stay within int limits
    size_t pos = 0;
    while (pos < n) {
        size_t take = std::min<size_t>(n - pos, 1u << 30);
        // don't split a UTF-8 sequence
        if (pos + take < n) {
            while (take > 0 && IsUtf8Cont((unsigned char)s[pos + take])) --take;
            if (!take) take = 1;
        }
        int len = MultiByteToWideChar(CP_UTF8, 0, s + pos, (int)take, nullptr, 0);
        size_t old = out.size();
        out.resize(old + len);
        MultiByteToWideChar(CP_UTF8, 0, s + pos, (int)take, &out[old], len);
        pos += take;
    }
    return out;
}

std::string WideToUtf8(const wchar_t* s, size_t n) {
    if (!n) return std::string();
    std::string out;
    size_t pos = 0;
    while (pos < n) {
        size_t take = std::min<size_t>(n - pos, 1u << 29);
        if (pos + take < n && IS_HIGH_SURROGATE(s[pos + take - 1])) --take;
        int len = WideCharToMultiByte(CP_UTF8, 0, s + pos, (int)take, nullptr, 0, nullptr, nullptr);
        size_t old = out.size();
        out.resize(old + len);
        WideCharToMultiByte(CP_UTF8, 0, s + pos, (int)take, &out[old], len, nullptr, nullptr);
        pos += take;
    }
    return out;
}

int Utf8Decode(const unsigned char* p, size_t n, uint32_t* cp) {
    unsigned c = p[0];
    if (c < 0x80) { *cp = c; return 1; }
    int len; uint32_t v;
    if ((c & 0xE0) == 0xC0) { len = 2; v = c & 0x1F; }
    else if ((c & 0xF0) == 0xE0) { len = 3; v = c & 0x0F; }
    else if ((c & 0xF8) == 0xF0) { len = 4; v = c & 0x07; }
    else { *cp = 0xFFFD; return 1; }
    if ((size_t)len > n) { *cp = 0xFFFD; return 1; }
    for (int i = 1; i < len; i++) {
        if ((p[i] & 0xC0) != 0x80) { *cp = 0xFFFD; return 1; }
        v = (v << 6) | (p[i] & 0x3F);
    }
    if ((len == 2 && v < 0x80) || (len == 3 && v < 0x800) || (len == 4 && (v < 0x10000 || v > 0x10FFFF)) ||
        (v >= 0xD800 && v <= 0xDFFF)) {
        *cp = 0xFFFD; return 1;
    }
    *cp = v;
    return len;
}

int CharColumns(uint32_t cp) {
    if (cp < 0x1100) return 1;
    if ((cp <= 0x115F) || (cp >= 0x2E80 && cp <= 0xA4CF && cp != 0x303F) || (cp >= 0xAC00 && cp <= 0xD7A3) ||
        (cp >= 0xF900 && cp <= 0xFAFF) || (cp >= 0xFE30 && cp <= 0xFE4F) || (cp >= 0xFF00 && cp <= 0xFF60) ||
        (cp >= 0xFFE0 && cp <= 0xFFE6) || (cp >= 0x1F300 && cp <= 0x1F64F) || (cp >= 0x1F900 && cp <= 0x1F9FF) ||
        (cp >= 0x20000 && cp <= 0x3FFFD))
        return 2;
    return 1;
}

void ApplyTheme(bool dark) {
    Theme& t = g_theme;
    if (!dark) {
        t.bg = RGB(255, 255, 255); t.fg = RGB(31, 31, 31);
        t.gutterBg = RGB(255, 255, 255); t.gutterFg = RGB(160, 160, 160); t.gutterFgCur = RGB(40, 40, 40);
        t.curLine = RGB(245, 245, 245); t.selBg = RGB(173, 214, 255); t.caret = RGB(0, 0, 0);
        t.heading = RGB(0, 70, 160); t.bold = RGB(0, 0, 0); t.italic = RGB(60, 60, 60);
        t.code = RGB(163, 21, 21); t.link = RGB(0, 102, 204); t.url = RGB(0, 128, 128);
        t.quote = RGB(0, 128, 0); t.listMarker = RGB(4, 81, 165); t.rule = RGB(150, 150, 150);
        t.fence = RGB(128, 60, 0); t.html = RGB(128, 0, 128);
        t.addBg = RGB(220, 245, 220); t.delBg = RGB(255, 225, 225);
        t.addStrong = RGB(170, 230, 170); t.delStrong = RGB(255, 190, 190);
        t.emptyBg = RGB(242, 242, 242); t.hunkFg = RGB(120, 120, 180);
        t.panelBg = RGB(243, 243, 243); t.panelFg = RGB(30, 30, 30); t.panelFgDim = RGB(110, 110, 110);
        t.border = RGB(220, 220, 220); t.accent = RGB(0, 95, 184); t.hoverBg = RGB(228, 228, 228);
        t.activeBg = RGB(255, 255, 255); t.inputBg = RGB(255, 255, 255); t.buttonBg = RGB(0, 95, 184);
        t.buttonFg = RGB(255, 255, 255); t.statusBg = RGB(0, 95, 184); t.statusFg = RGB(255, 255, 255);
    } else {
        t.bg = RGB(30, 30, 30); t.fg = RGB(212, 212, 212);
        t.gutterBg = RGB(30, 30, 30); t.gutterFg = RGB(110, 118, 129); t.gutterFgCur = RGB(204, 204, 204);
        t.curLine = RGB(40, 40, 40); t.selBg = RGB(38, 79, 120); t.caret = RGB(230, 230, 230);
        t.heading = RGB(86, 156, 214); t.bold = RGB(240, 240, 240); t.italic = RGB(200, 200, 200);
        t.code = RGB(206, 145, 120); t.link = RGB(78, 201, 176); t.url = RGB(156, 220, 254);
        t.quote = RGB(106, 153, 85); t.listMarker = RGB(103, 150, 230); t.rule = RGB(128, 128, 128);
        t.fence = RGB(215, 186, 125); t.html = RGB(197, 134, 192);
        t.addBg = RGB(35, 60, 35); t.delBg = RGB(75, 35, 35);
        t.addStrong = RGB(45, 90, 45); t.delStrong = RGB(110, 45, 45);
        t.emptyBg = RGB(37, 37, 38); t.hunkFg = RGB(140, 140, 200);
        t.panelBg = RGB(37, 37, 38); t.panelFg = RGB(204, 204, 204); t.panelFgDim = RGB(140, 140, 140);
        t.border = RGB(60, 60, 60); t.accent = RGB(0, 122, 204); t.hoverBg = RGB(55, 55, 58);
        t.activeBg = RGB(30, 30, 30); t.inputBg = RGB(49, 49, 49); t.buttonBg = RGB(62, 70, 84);
        t.buttonFg = RGB(224, 224, 224); t.statusBg = RGB(45, 45, 48); t.statusFg = RGB(190, 190, 190);
    }
}

static std::wstring IniPath() { return JoinPath(ExeDir(), L"ditto.ini"); }

static int IniInt(const wchar_t* key, int def) {
    // GetPrivateProfileInt can't read negative numbers (window positions on left monitors, CW_USEDEFAULT)
    wchar_t buf[64];
    GetPrivateProfileStringW(L"ditto", key, L"", buf, 64, IniPath().c_str());
    if (!buf[0]) return def;
    return (int)wcstol(buf, nullptr, 10);
}
static std::wstring IniStr(const wchar_t* key, const wchar_t* def) {
    wchar_t buf[1024];
    GetPrivateProfileStringW(L"ditto", key, def, buf, 1024, IniPath().c_str());
    return buf;
}
static void IniSet(const wchar_t* key, const std::wstring& v) {
    WritePrivateProfileStringW(L"ditto", key, v.c_str(), IniPath().c_str());
}

void LoadSettings() {
    Settings& s = g_settings;
    s.fontName = IniStr(L"FontName", L"Consolas");
    s.fontSize = std::max(6, std::min(72, IniInt(L"FontSize", 11)));
    s.dark = IniInt(L"Dark", 0) != 0;
    s.wrap = IniInt(L"WordWrap", 0) != 0;
    s.tabSize = std::max(1, std::min(16, IniInt(L"TabSize", 4)));
    s.sidebarWidth = std::max(120, std::min(1200, IniInt(L"SidebarWidth", 260)));
    s.showOutput = IniInt(L"ShowOutput", 0) != 0;
    s.showSidebar = IniInt(L"ShowSidebar", 1) != 0;
    s.winX = IniInt(L"WinX", CW_USEDEFAULT);
    s.winY = IniInt(L"WinY", CW_USEDEFAULT);
    s.winW = IniInt(L"WinW", 1200);
    s.winH = IniInt(L"WinH", 800);
    s.winMax = IniInt(L"WinMax", 0) != 0;
    s.recentFolders.clear();
    for (int i = 0; i < 10; i++) {
        std::wstring v = IniStr((L"Recent" + std::to_wstring(i)).c_str(), L"");
        if (!v.empty()) s.recentFolders.push_back(v);
    }
}

void SaveSettings() {
    Settings& s = g_settings;
    IniSet(L"FontName", s.fontName);
    IniSet(L"FontSize", std::to_wstring(s.fontSize));
    IniSet(L"Dark", s.dark ? L"1" : L"0");
    IniSet(L"WordWrap", s.wrap ? L"1" : L"0");
    IniSet(L"TabSize", std::to_wstring(s.tabSize));
    IniSet(L"SidebarWidth", std::to_wstring(s.sidebarWidth));
    IniSet(L"ShowOutput", s.showOutput ? L"1" : L"0");
    IniSet(L"ShowSidebar", s.showSidebar ? L"1" : L"0");
    IniSet(L"WinX", std::to_wstring(s.winX));
    IniSet(L"WinY", std::to_wstring(s.winY));
    IniSet(L"WinW", std::to_wstring(s.winW));
    IniSet(L"WinH", std::to_wstring(s.winH));
    IniSet(L"WinMax", s.winMax ? L"1" : L"0");
    for (int i = 0; i < 10; i++)
        IniSet((L"Recent" + std::to_wstring(i)).c_str(), i < (int)s.recentFolders.size() ? s.recentFolders[i] : L"");
}

void AddRecentFolder(const std::wstring& path) {
    auto& r = g_settings.recentFolders;
    r.erase(std::remove_if(r.begin(), r.end(), [&](const std::wstring& x) { return PathEqualsI(x, path); }), r.end());
    r.insert(r.begin(), path);
    if (r.size() > 10) r.resize(10);
    SaveSettings();
}

std::wstring ExeDir() {
    wchar_t buf[MAX_PATH * 2];
    DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH * 2);
    return DirOf(std::wstring(buf, n));
}

std::wstring FileNameOf(const std::wstring& path) {
    size_t p = path.find_last_of(L"\\/");
    return p == std::wstring::npos ? path : path.substr(p + 1);
}

std::wstring DirOf(const std::wstring& path) {
    size_t p = path.find_last_of(L"\\/");
    return p == std::wstring::npos ? std::wstring() : path.substr(0, p);
}

std::wstring JoinPath(const std::wstring& a, const std::wstring& b) {
    if (a.empty()) return b;
    if (a.back() == L'\\' || a.back() == L'/') return a + b;
    return a + L"\\" + b;
}

std::wstring ToBackslashes(std::wstring s) { std::replace(s.begin(), s.end(), L'/', L'\\'); return s; }
std::wstring ToForwardSlashes(std::wstring s) { std::replace(s.begin(), s.end(), L'\\', L'/'); return s; }

bool IsDirectory(const std::wstring& p) {
    DWORD a = GetFileAttributesW(p.c_str());
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}
bool FileExists(const std::wstring& p) {
    DWORD a = GetFileAttributesW(p.c_str());
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}
bool PathEqualsI(const std::wstring& a, const std::wstring& b) {
    return CompareStringOrdinal(ToBackslashes(a).c_str(), -1, ToBackslashes(b).c_str(), -1, TRUE) == CSTR_EQUAL;
}

std::wstring Trim(const std::wstring& s) {
    size_t a = s.find_first_not_of(L" \t\r\n");
    if (a == std::wstring::npos) return L"";
    size_t b = s.find_last_not_of(L" \t\r\n");
    return s.substr(a, b - a + 1);
}
std::string TrimA(const std::string& s) {
    size_t a = s.find_first_not_of(" \t\r\n");
    if (a == std::string::npos) return "";
    size_t b = s.find_last_not_of(" \t\r\n");
    return s.substr(a, b - a + 1);
}
std::wstring LowerW(std::wstring s) {
    if (!s.empty()) CharLowerBuffW(&s[0], (DWORD)s.size());
    return s;
}

std::wstring FormatError(DWORD err) {
    wchar_t* buf = nullptr;
    FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, nullptr,
                   err, 0, (LPWSTR)&buf, 0, nullptr);
    std::wstring s = buf ? Trim(buf) : L"Error " + std::to_wstring(err);
    if (buf) LocalFree(buf);
    return s;
}

int MsgBox(HWND owner, const std::wstring& text, UINT flags) {
    return MessageBoxW(owner ? owner : g_mainWnd, text.c_str(), L"ditto", flags);
}

void RunOnUi(std::function<void()> fn) {
    auto* p = new std::function<void()>(std::move(fn));
    if (!g_mainWnd || !PostMessageW(g_mainWnd, WM_APP_RUNUI, 0, (LPARAM)p)) delete p;
}

bool SetClipboardText(HWND owner, const std::wstring& text) {
    if (!OpenClipboard(owner)) return false;
    EmptyClipboard();
    HGLOBAL h = GlobalAlloc(GMEM_MOVEABLE, (text.size() + 1) * sizeof(wchar_t));
    bool ok = false;
    if (h) {
        void* p = GlobalLock(h);
        memcpy(p, text.c_str(), (text.size() + 1) * sizeof(wchar_t));
        GlobalUnlock(h);
        ok = SetClipboardData(CF_UNICODETEXT, h) != nullptr;
        if (!ok) GlobalFree(h);
    }
    CloseClipboard();
    return ok;
}

bool GetClipboardText(HWND owner, std::wstring& text) {
    if (!IsClipboardFormatAvailable(CF_UNICODETEXT) || !OpenClipboard(owner)) return false;
    bool ok = false;
    HANDLE h = GetClipboardData(CF_UNICODETEXT);
    if (h) {
        const wchar_t* p = (const wchar_t*)GlobalLock(h);
        if (p) {
            size_t max = GlobalSize(h) / sizeof(wchar_t);
            size_t n = 0;
            while (n < max && p[n]) ++n;
            text.assign(p, n);
            ok = true;
            GlobalUnlock(h);
        }
    }
    CloseClipboard();
    return ok;
}

std::string Base64(const std::string& in) {
    static const char* tbl = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    size_t i = 0;
    while (i + 2 < in.size()) {
        uint32_t v = ((uint8_t)in[i] << 16) | ((uint8_t)in[i + 1] << 8) | (uint8_t)in[i + 2];
        out += tbl[v >> 18]; out += tbl[(v >> 12) & 63]; out += tbl[(v >> 6) & 63]; out += tbl[v & 63];
        i += 3;
    }
    if (i + 1 == in.size()) {
        uint32_t v = (uint8_t)in[i] << 16;
        out += tbl[v >> 18]; out += tbl[(v >> 12) & 63]; out += "==";
    } else if (i + 2 == in.size()) {
        uint32_t v = ((uint8_t)in[i] << 16) | ((uint8_t)in[i + 1] << 8);
        out += tbl[v >> 18]; out += tbl[(v >> 12) & 63]; out += tbl[(v >> 6) & 63]; out += '=';
    }
    return out;
}

void DrawFlatButton(const DRAWITEMSTRUCT* dis, bool primary, bool toggled) {
    HDC dc = dis->hDC;
    RECT r = dis->rcItem;
    bool pressed = (dis->itemState & ODS_SELECTED) != 0;
    bool disabled = (dis->itemState & ODS_DISABLED) != 0;
    COLORREF bg = primary ? g_theme.buttonBg : (toggled ? g_theme.hoverBg : g_theme.panelBg);
    COLORREF fg = primary ? g_theme.buttonFg : g_theme.panelFg;
    if (pressed) bg = primary ? RGB(GetRValue(bg) * 4 / 5, GetGValue(bg) * 4 / 5, GetBValue(bg) * 4 / 5) : g_theme.hoverBg;
    if (disabled) fg = g_theme.panelFgDim;
    HBRUSH br = CreateSolidBrush(bg);
    FillRect(dc, &r, br);
    DeleteObject(br);
    if (!primary || toggled) {
        HBRUSH bb = CreateSolidBrush(toggled ? g_theme.accent : g_theme.border);
        FrameRect(dc, &r, bb);
        DeleteObject(bb);
    }
    wchar_t text[256];
    int n = GetWindowTextW(dis->hwndItem, text, 256);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, fg);
    HGDIOBJ old = SelectObject(dc, (n > 0 && text[0] >= 0xE000 && text[0] < 0xF900 && g_iconFont) ? g_iconFont : g_uiFont);
    DrawTextW(dc, text, n, &r, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
    SelectObject(dc, old);
    if (dis->itemState & ODS_FOCUS) {
        RECT f = r;
        InflateRect(&f, -2, -2);
        DrawFocusRect(dc, &f);
    }
}

HWND MakeButton(HWND parent, int id, const wchar_t* text) {
    HWND h = CreateWindowExW(0, L"BUTTON", text, WS_CHILD | WS_VISIBLE | BS_OWNERDRAW, 0, 0, 10, 10, parent,
                             (HMENU)(INT_PTR)id, g_hinst, nullptr);
    SendMessageW(h, WM_SETFONT, (WPARAM)g_uiFont, FALSE);
    return h;
}
