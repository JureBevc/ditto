// Main frame: sidebar, tab strip, content area, bottom panel (output / terminal) and status bar.
#include "main_window.h"
#include "resource.h"
#include <dwmapi.h>

MainState M;
void OnInitMenu(HMENU m);  // main_commands.cpp

static const wchar_t* kIcoClose = L"\xE711";
static const UINT_PTR kTimerStatus = 1;

EditorView* ActiveEditor() {
    if (M.active >= 0 && M.active < (int)M.tabs.size() && M.tabs[(size_t)M.active].kind == Tab::Editor) return M.tabs[(size_t)M.active].ed;
    return nullptr;
}

static std::wstring TabTitle(const Tab& t) {
    switch (t.kind) {
    case Tab::Editor: return t.ed->Title();
    case Tab::Diff: return DiffView::Title(t.hwnd);
    default: return L"History";
    }
}

static bool TabModified(const Tab& t) { return t.kind == Tab::Editor && t.ed->buf->Modified(); }

// ---------------- fonts / theme ----------------

void RecreateFonts() {
    HFONT oldUi = g_uiFont, oldBold = g_uiFontBold, oldIcon = g_iconFont, oldSym = M.symFont;
    NONCLIENTMETRICSW ncm = {sizeof(ncm)};
    SystemParametersInfoForDpi(SPI_GETNONCLIENTMETRICS, sizeof(ncm), &ncm, 0, (UINT)g_dpi);
    LOGFONTW lf = ncm.lfMessageFont;
    g_uiFont = CreateFontIndirectW(&lf);
    lf.lfWeight = FW_BOLD;
    g_uiFontBold = CreateFontIndirectW(&lf);
    LOGFONTW li = {};
    li.lfHeight = -S(14);
    li.lfCharSet = DEFAULT_CHARSET;
    li.lfQuality = CLEARTYPE_QUALITY;
    wcscpy_s(li.lfFaceName, L"Segoe MDL2 Assets");
    g_iconFont = CreateFontIndirectW(&li);
    lf = ncm.lfMessageFont;
    wcscpy_s(lf.lfFaceName, L"Segoe UI Symbol");
    M.symFont = CreateFontIndirectW(&lf);
    CreateEditorFonts();
    if (M.hwnd) {
        EnumChildWindows(M.hwnd, [](HWND h, LPARAM) -> BOOL {
            wchar_t cls[64];
            GetClassNameW(h, cls, 64);
            if (!_wcsicmp(cls, L"EDIT") || !_wcsicmp(cls, L"BUTTON") || !_wcsicmp(cls, WC_TREEVIEWW) || !_wcsicmp(cls, WC_LISTVIEWW))
                SendMessageW(h, WM_SETFONT, (WPARAM)g_uiFont, TRUE);
            return TRUE;
        }, 0);
    }
    if (oldUi) DeleteObject(oldUi);
    if (oldBold) DeleteObject(oldBold);
    if (oldIcon) DeleteObject(oldIcon);
    if (oldSym) DeleteObject(oldSym);
}

void ApplyThemeAll() {
    ApplyTheme(g_settings.dark);
    BOOL dark = g_settings.dark;
    DwmSetWindowAttribute(M.hwnd, 20 /*DWMWA_USE_IMMERSIVE_DARK_MODE*/, &dark, sizeof(dark));
    if (M.outputBrush) DeleteObject(M.outputBrush);
    M.outputBrush = CreateSolidBrush(g_theme.bg);
    SetWindowTheme(M.output, g_settings.dark ? L"DarkMode_Explorer" : L"Explorer", nullptr);
    Explorer::ThemeChanged();
    Scm::ThemeChanged();
    FindBar::ThemeChanged();
    Term::ThemeChanged();
    for (auto& t : M.tabs) {
        SetWindowTheme(t.hwnd, g_settings.dark ? L"DarkMode_Explorer" : L"Explorer", nullptr);
        if (t.kind == Tab::Log) LogView::ThemeChanged(t.hwnd);
    }
    DrawMenuBar(M.hwnd);
    RedrawWindow(M.hwnd, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_FRAME);
}

// ---------------- layout ----------------

static void ComputeTabRects() {
    M.tabRects.assign(M.tabs.size(), RECT{});
    M.rcTabOverflow = RECT{};
    if (M.tabs.empty()) return;
    HDC dc = GetDC(M.hwnd);
    HGDIOBJ o = SelectObject(dc, g_uiFont);
    std::vector<int> w(M.tabs.size());
    for (size_t i = 0; i < M.tabs.size(); ++i) {
        std::wstring t = TabTitle(M.tabs[i]);
        SIZE sz;
        GetTextExtentPoint32W(dc, t.c_str(), (int)t.size(), &sz);
        w[i] = std::max(S(80), std::min(S(260), (int)sz.cx + S(48)));
    }
    SelectObject(dc, o);
    ReleaseDC(M.hwnd, dc);
    int avail = M.rcTabs.right - M.rcTabs.left - S(30);
    int total = 0;
    for (int x : w) total += x;
    bool overflow = total > avail + S(30);
    if (!overflow) M.tabFirst = 0;
    if (M.active >= 0) {
        if (M.active < M.tabFirst) M.tabFirst = M.active;
        for (;;) {
            int sum = 0;
            for (int i = M.tabFirst; i <= M.active; ++i) sum += w[(size_t)i];
            if (sum <= avail || M.tabFirst >= M.active) break;
            ++M.tabFirst;
        }
    }
    M.tabFirst = std::max(0, std::min(M.tabFirst, (int)M.tabs.size() - 1));
    int x = M.rcTabs.left;
    int limit = overflow ? M.rcTabs.right - S(30) : M.rcTabs.right;
    for (int i = M.tabFirst; i < (int)M.tabs.size(); ++i) {
        if (x + w[(size_t)i] > limit && i != M.tabFirst) break;
        M.tabRects[(size_t)i] = RECT{x, M.rcTabs.top, x + w[(size_t)i], M.rcTabs.bottom};
        x += w[(size_t)i];
    }
    if (overflow) M.rcTabOverflow = RECT{M.rcTabs.right - S(30), M.rcTabs.top, M.rcTabs.right, M.rcTabs.bottom};
}

void Layout() {
    if (!M.hwnd) return;
    RECT rc;
    GetClientRect(M.hwnd, &rc);
    int sh = S(24);
    M.rcStatus = RECT{0, rc.bottom - sh, rc.right, rc.bottom};
    int bottom = rc.bottom - sh;
    int sideW = g_settings.showSidebar ? std::min(S(g_settings.sidebarWidth), (int)rc.right - S(200)) : 0;
    if (sideW < S(120) && g_settings.showSidebar) sideW = std::min(S(120), (int)rc.right / 2);
    if (g_settings.showSidebar) {
        M.rcSideHeader = RECT{0, 0, sideW, S(34)};
        M.rcSideBody = RECT{0, S(34), sideW, bottom};
        M.rcSplitter = RECT{sideW, 0, sideW + S(4), bottom};
        HWND show = M.sidebarMode == 0 ? M.explorer : M.scm;
        HWND hide = M.sidebarMode == 0 ? M.scm : M.explorer;
        MoveWindow(show, 0, M.rcSideBody.top, sideW, bottom - M.rcSideBody.top, TRUE);
        ShowWindow(show, SW_SHOW);
        ShowWindow(hide, SW_HIDE);
    } else {
        M.rcSideHeader = M.rcSideBody = M.rcSplitter = RECT{};
        ShowWindow(M.explorer, SW_HIDE);
        ShowWindow(M.scm, SW_HIDE);
    }
    int x0 = g_settings.showSidebar ? M.rcSplitter.right : 0;
    M.rcTabs = RECT{x0, 0, rc.right, S(34)};
    int y = M.rcTabs.bottom;
    int fh = FindBar::Height();
    if (fh) {
        MoveWindow(M.findBar, x0, y, rc.right - x0, fh, TRUE);
        y += fh;
    }
    int outH = 0;
    if (g_settings.showOutput) {
        int avail = bottom - y;
        outH = std::min(avail, std::max(S(60), std::min(S(g_settings.panelHeight), avail - S(80))));
    }
    if (outH) {
        M.rcOutputHeader = RECT{x0, bottom - outH, rc.right, bottom - outH + S(28)};
        M.rcOutput = RECT{x0, M.rcOutputHeader.bottom, rc.right, bottom};
        M.rcPanelSplit = RECT{x0, M.rcOutputHeader.top, rc.right, M.rcOutputHeader.top + S(4)};
        bool term = M.panelTab == MainState::PanelTerminal;
        if (!term) MoveWindow(M.output, x0 + S(8), M.rcOutput.top, rc.right - x0 - S(8), bottom - M.rcOutput.top, TRUE);
        ShowWindow(M.output, term ? SW_HIDE : SW_SHOW);
        Term::Layout(M.rcOutput, term);
    } else {
        M.rcOutputHeader = M.rcOutput = M.rcPanelSplit = RECT{};
        ShowWindow(M.output, SW_HIDE);
        Term::Layout(RECT{}, false);
    }
    M.rcContent = RECT{x0, y, rc.right, bottom - outH};
    if (M.active >= 0) {
        HWND h = M.tabs[(size_t)M.active].hwnd;
        MoveWindow(h, M.rcContent.left, M.rcContent.top, M.rcContent.right - M.rcContent.left, M.rcContent.bottom - M.rcContent.top, TRUE);
    }
    ComputeTabRects();
    InvalidateRect(M.hwnd, nullptr, FALSE);
}

void InvalidateChrome() {
    ComputeTabRects();
    InvalidateRect(M.hwnd, &M.rcTabs, FALSE);
    InvalidateRect(M.hwnd, &M.rcStatus, FALSE);
    InvalidateRect(M.hwnd, &M.rcSideHeader, FALSE);
}

void UpdateTitle() {
    std::wstring t;
    if (M.active >= 0) {
        const Tab& tab = M.tabs[(size_t)M.active];
        t = TabTitle(tab);
        if (TabModified(tab)) t = L"\x25CF " + t;
        t += L" - ";
    }
    if (!M.folder.empty()) t += FileNameOf(M.folder) + L" - ";
    t += L"ditto";
    SetWindowTextW(M.hwnd, t.c_str());
}

void ActivateTab(int i) {
    if (i < 0 || i >= (int)M.tabs.size()) return;
    if (M.active >= 0 && M.active != i && M.active < (int)M.tabs.size()) ShowWindow(M.tabs[(size_t)M.active].hwnd, SW_HIDE);
    M.active = i;
    HWND h = M.tabs[(size_t)i].hwnd;
    MoveWindow(h, M.rcContent.left, M.rcContent.top, M.rcContent.right - M.rcContent.left, M.rcContent.bottom - M.rcContent.top, TRUE);
    ShowWindow(h, SW_SHOW);
    SetFocus(h);
    EditorView* ed = ActiveEditor();
    FindBar::SetMessage(ed ? ed->findMessage : L"");
    UpdateTitle();
    InvalidateChrome();
    InvalidateRect(M.hwnd, nullptr, FALSE);
}

void AddTab(const Tab& t) {
    SetWindowTheme(t.hwnd, g_settings.dark ? L"DarkMode_Explorer" : L"Explorer", nullptr);
    M.tabs.push_back(t);
    ActivateTab((int)M.tabs.size() - 1);
}

bool CloseTab(int i) {
    if (i < 0 || i >= (int)M.tabs.size()) return true;
    Tab t = M.tabs[(size_t)i];
    if (TabModified(t)) {
        ActivateTab(i);
        int r = MsgBox(M.hwnd, L"Do you want to save the changes you made to " + t.ed->Title() + L"?\n\nYour changes will be lost if you don't save them.",
                       MB_YESNOCANCEL | MB_ICONWARNING);
        if (r == IDCANCEL) return false;
        if (r == IDYES && !SaveTab(i, false)) return false;
    }
    M.tabs.erase(M.tabs.begin() + i);
    DestroyWindow(t.hwnd);
    if (M.tabs.empty()) {
        M.active = -1;
    } else if (i == M.active) {
        M.active = -1;
        ActivateTab(std::min(i, (int)M.tabs.size() - 1));
    } else if (i < M.active) {
        --M.active;
    }
    if (M.tabs.empty()) FindBar::SetMessage(L"");
    UpdateTitle();
    Layout();
    return true;
}

// ---------------- painting ----------------

static void FillC(HDC dc, const RECT& r, COLORREF c) {
    SetBkColor(dc, c);
    ExtTextOutW(dc, 0, 0, ETO_OPAQUE, &r, nullptr, 0, nullptr);
}

static void Text(HDC dc, const std::wstring& s, RECT r, COLORREF c, UINT fmt, HFONT f = nullptr) {
    SelectObject(dc, f ? f : g_uiFont);
    SetTextColor(dc, c);
    DrawTextW(dc, s.c_str(), (int)s.size(), &r, fmt | DT_NOPREFIX);
}

static std::wstring FormatSize(uint64_t n) {
    wchar_t b[64];
    if (n >= (1ull << 30)) swprintf_s(b, L"%.2f GB", n / 1073741824.0);
    else if (n >= (1ull << 20)) swprintf_s(b, L"%.1f MB", n / 1048576.0);
    else if (n >= 1024) swprintf_s(b, L"%.1f KB", n / 1024.0);
    else swprintf_s(b, L"%llu B", (unsigned long long)n);
    return b;
}

static void PaintStatus(HDC dc) {
    const Theme& t = g_theme;
    RECT r = M.rcStatus;
    FillC(dc, r, M.folder.empty() ? (g_settings.dark ? RGB(104, 33, 122) : RGB(104, 33, 122)) : t.statusBg);
    M.statusItems.clear();
    int pad = S(10);
    int x = r.left + S(6);
    HDC mdc = dc;
    auto addLeft = [&](const std::wstring& s, int cmd, HFONT f = nullptr) {
        if (s.empty()) return;
        SelectObject(mdc, f ? f : g_uiFont);
        SIZE sz;
        GetTextExtentPoint32W(mdc, s.c_str(), (int)s.size(), &sz);
        RECT ir = {x, r.top, x + sz.cx + pad, r.bottom};
        Text(mdc, s, ir, t.statusFg, DT_CENTER | DT_VCENTER | DT_SINGLELINE, f);
        if (cmd) M.statusItems.push_back({ir, cmd});
        x = ir.right;
    };
    const GitStatus& st = M.status;
    if (st.isRepo) {
        bool dirty = !st.files.empty();
        addLeft(L"\x2387 " + st.branch + (dirty ? L"*" : L""), ID_GIT_CHECKOUT, M.symFont);
        std::wstring sync;
        if (!st.upstream.empty()) sync = L"\x2193" + std::to_wstring(st.behind) + L" \x2191" + std::to_wstring(st.ahead);
        else if (!st.remotes.empty()) sync = L"\x2191 Publish branch";
        else sync = L"\x2601 Publish to GitHub";
        addLeft(sync, st.remotes.empty() ? ID_GIT_PUBLISH : ID_GIT_SYNC, M.symFont);
    }
    if (BgPending() > 0) addLeft(L"\x231B git...", 0, M.symFont);
    if (!M.statusText.empty()) addLeft(M.statusText, 0);

    int rx = r.right - S(8);
    auto addRight = [&](const std::wstring& s, int cmd) {
        if (s.empty()) return;
        SelectObject(mdc, g_uiFont);
        SIZE sz;
        GetTextExtentPoint32W(mdc, s.c_str(), (int)s.size(), &sz);
        RECT ir = {rx - sz.cx - pad, r.top, rx, r.bottom};
        if (ir.left < x) return;
        Text(mdc, s, ir, t.statusFg, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        if (cmd) M.statusItems.push_back({ir, cmd});
        rx = ir.left;
    };
    EditorView* ed = ActiveEditor();
    if (ed) {
        TextBuffer* b = ed->buf.get();
        addRight(ed->markdown ? L"Markdown" : L"Plain Text", 0);
        addRight(b->crlf ? L"CRLF" : L"LF", 0);
        const wchar_t* enc = L"UTF-8";
        if (b->encoding == Encoding::Utf8Bom) enc = L"UTF-8 with BOM";
        else if (b->encoding == Encoding::Utf16LE) enc = L"UTF-16 LE";
        else if (b->encoding == Encoding::Utf16BE) enc = L"UTF-16 BE";
        addRight(enc, 0);
        if (ed->SaveProgress() >= 0) {
            addRight(L"Saving " + std::to_wstring((int)(ed->SaveProgress() * 100)) + L"%", 0);
        } else if (!b->Ready()) {
            addRight(L"Indexing " + std::to_wstring((int)(b->Progress() * 100)) + L"%", 0);
        } else {
            addRight(FormatSize(b->Length()), 0);
        }
        std::wstring pos = L"Ln " + std::to_wstring(ed->CaretLine() + 1) + L", Col " + std::to_wstring(ed->CaretCol() + 1);
        uint64_t sel = ed->SelectionLength();
        if (sel) pos += L" (" + std::to_wstring(sel) + L" selected)";
        addRight(pos, ID_EDIT_GOTO);
    }
}

static void PaintTabs(HDC dc) {
    const Theme& t = g_theme;
    COLORREF stripBg = g_settings.dark ? RGB(37, 37, 38) : RGB(236, 236, 236);
    FillC(dc, M.rcTabs, stripBg);
    for (size_t i = 0; i < M.tabs.size(); ++i) {
        RECT r = M.tabRects[i];
        if (r.right <= r.left) continue;
        bool active = (int)i == M.active, hover = (int)i == M.hoverTab;
        FillC(dc, r, active ? t.bg : (hover ? t.hoverBg : stripBg));
        if (active) { RECT a = {r.left, r.top, r.right, r.top + S(2)}; FillC(dc, a, t.accent); }
        RECT sep = {r.right - 1, r.top, r.right, r.bottom};
        FillC(dc, sep, t.border);
        const Tab& tab = M.tabs[i];
        RECT tr = {r.left + S(12), r.top, r.right - S(30), r.bottom};
        Text(dc, TabTitle(tab), tr, active ? t.fg : t.panelFgDim, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
        RECT cr = {r.right - S(28), r.top + S(7), r.right - S(8), r.bottom - S(7)};
        bool closeHover = hover && M.hoverClose;
        if (closeHover) FillC(dc, cr, g_settings.dark ? RGB(70, 70, 74) : RGB(210, 210, 210));
        if (TabModified(tab) && !closeHover) Text(dc, L"\x25CF", cr, active ? t.fg : t.panelFgDim, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        else if (active || hover) Text(dc, kIcoClose, cr, active ? t.fg : t.panelFgDim, DT_CENTER | DT_VCENTER | DT_SINGLELINE, g_iconFont);
    }
    if (M.rcTabOverflow.right > M.rcTabOverflow.left)
        Text(dc, L"\xE712", M.rcTabOverflow, t.panelFg, DT_CENTER | DT_VCENTER | DT_SINGLELINE, g_iconFont);
    RECT line = {M.rcTabs.left, M.rcTabs.bottom - 1, M.rcTabs.right, M.rcTabs.bottom};
    if (M.tabs.empty()) FillC(dc, line, t.border);
}

static void PaintSideHeader(HDC dc) {
    const Theme& t = g_theme;
    RECT r = M.rcSideHeader;
    if (r.right <= r.left) return;
    FillC(dc, r, t.panelBg);
    int half = (r.right - r.left) / 2;
    RECT a = {r.left, r.top, r.left + half, r.bottom}, b = {r.left + half, r.top, r.right, r.bottom};
    int changes = (int)M.status.files.size();
    std::wstring scmLabel = L"SOURCE CONTROL";
    if (changes) scmLabel += L" (" + std::to_wstring(changes) + L")";
    Text(dc, L"EXPLORER", a, M.sidebarMode == 0 ? t.panelFg : t.panelFgDim, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS,
         M.sidebarMode == 0 ? g_uiFontBold : g_uiFont);
    Text(dc, scmLabel, b, M.sidebarMode == 1 ? t.panelFg : t.panelFgDim, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS,
         M.sidebarMode == 1 ? g_uiFontBold : g_uiFont);
    RECT u = M.sidebarMode == 0 ? a : b;
    RECT ul = {u.left + S(12), u.bottom - S(3), u.right - S(12), u.bottom - S(1)};
    FillC(dc, ul, t.accent);
}

static void PaintWelcome(HDC dc) {
    const Theme& t = g_theme;
    FillC(dc, M.rcContent, t.bg);
    M.welcomeLinks.clear();
    int x = M.rcContent.left + S(48), y = M.rcContent.top + S(40);
    RECT r = {x, y, M.rcContent.right - S(16), y + S(40)};
    LOGFONTW lf;
    GetObjectW(g_uiFont, sizeof(lf), &lf);
    lf.lfHeight = lf.lfHeight * 2;
    lf.lfWeight = FW_LIGHT;
    HFONT big = CreateFontIndirectW(&lf);
    Text(dc, L"ditto", r, t.fg, DT_LEFT | DT_TOP | DT_SINGLELINE, big);
    DeleteObject(big);
    y += S(56);
    const wchar_t* lines[] = {
        L"Ctrl+N\tNew file",           L"Ctrl+O\tOpen file",          L"Ctrl+Shift+O\tOpen folder",
        L"Ctrl+R\tOpen recent folder",
        L"Ctrl+Shift+E\tExplorer",     L"Ctrl+Shift+G\tSource control", L"Ctrl+F / Ctrl+H\tFind / replace",
        L"Ctrl+G\tGo to line",         L"Alt+Z\tToggle word wrap",     L"Ctrl+`\tToggle terminal",
    };
    for (auto l : lines) {
        std::wstring s = l;
        size_t tab = s.find(L'\t');
        RECT k = {x, y, x + S(130), y + S(22)}, v = {x + S(140), y, M.rcContent.right - S(16), y + S(22)};
        Text(dc, s.substr(0, tab), k, t.accent, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
        Text(dc, s.substr(tab + 1), v, t.panelFgDim, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
        y += S(22);
    }
    if (!g_settings.recentFolders.empty()) {
        y += S(18);
        RECT h = {x, y, M.rcContent.right - S(16), y + S(24)};
        Text(dc, L"Recent folders", h, t.fg, DT_LEFT | DT_VCENTER | DT_SINGLELINE, g_uiFontBold);
        y += S(26);
        for (auto& f : g_settings.recentFolders) {
            RECT lr = {x, y, M.rcContent.right - S(16), y + S(22)};
            Text(dc, f, lr, t.link, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_PATH_ELLIPSIS);
            M.welcomeLinks.push_back({lr, f});
            y += S(22);
        }
    }
}

// Bottom panel header: OUTPUT / TERMINAL tabs, terminal groups, and the terminal actions on the right.
static void PaintPanelHeader(HDC dc) {
    typedef MainState::PanelItem PI;
    const Theme& t = g_theme;
    M.panelItems.clear();
    RECT h = M.rcOutputHeader;
    if (h.bottom <= h.top) return;
    RECT o = {h.left, h.top, M.rcOutput.right, M.rcOutput.bottom};
    FillC(dc, o, t.bg);
    RECT l = {o.left, o.top, o.right, o.top + 1};
    FillC(dc, l, t.border);
    bool term = M.panelTab == MainState::PanelTerminal;
    int x = h.left + S(8);
    auto label = [&](const wchar_t* s, bool active, PI::Kind kind) {
        SelectObject(dc, g_uiFont);
        SIZE sz;
        GetTextExtentPoint32W(dc, s, (int)wcslen(s), &sz);
        RECT r = {x, h.top, x + sz.cx + S(16), h.bottom};
        Text(dc, s, r, active ? t.panelFg : t.panelFgDim, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        if (active) { RECT u = {r.left + S(6), r.bottom - S(3), r.right - S(6), r.bottom - S(1)}; FillC(dc, u, t.accent); }
        M.panelItems.push_back({r, kind, -1});
        x = r.right;
    };
    label(L"OUTPUT", !term, PI::OutputTab);
    label(L"TERMINAL", term, PI::TerminalTab);
    int rx = h.right - S(4);
    auto icon = [&](const wchar_t* glyph, PI::Kind kind) {
        RECT r = {rx - S(28), h.top, rx, h.bottom};
        Text(dc, glyph, r, t.panelFg, DT_CENTER | DT_VCENTER | DT_SINGLELINE, g_iconFont);
        M.panelItems.push_back({r, kind, -1});
        rx = r.left;
    };
    icon(kIcoClose, PI::ClosePanel);
    if (!term) return;
    icon(L"\xE74D", PI::KillTerm);   // Delete
    icon(L"\xE89A", PI::SplitTerm);  // TwoPage
    icon(L"\xE710", PI::NewTerm);    // Add
    x += S(16);
    for (int i = 0; i < Term::GroupCount() && x < rx - S(48); ++i) {
        std::wstring s = Term::GroupTitle(i);
        SelectObject(dc, g_uiFont);
        SIZE sz;
        GetTextExtentPoint32W(dc, s.c_str(), (int)s.size(), &sz);
        RECT r = {x, h.top + S(4), x + std::min((int)sz.cx + S(16), rx - S(8) - x), h.bottom - S(4)};
        bool active = i == Term::ActiveGroup();
        if (active) FillC(dc, r, t.activeBg);
        RECT tr = {r.left + S(8), r.top, r.right - S(8), r.bottom};
        Text(dc, s, tr, active ? t.panelFg : t.panelFgDim, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
        M.panelItems.push_back({r, PI::Group, i});
        x = r.right + S(4);
    }
}

static void Paint(HDC hdc, const RECT& clip) {
    RECT rc;
    GetClientRect(M.hwnd, &rc);
    HDC dc = CreateCompatibleDC(hdc);
    HBITMAP bmp = CreateCompatibleBitmap(hdc, std::max(1, (int)rc.right), std::max(1, (int)rc.bottom));
    HGDIOBJ ob = SelectObject(dc, bmp);
    HGDIOBJ of = SelectObject(dc, g_uiFont);
    SetBkMode(dc, TRANSPARENT);
    PaintSideHeader(dc);
    if (M.rcSplitter.right > M.rcSplitter.left) {
        FillC(dc, M.rcSplitter, g_theme.panelBg);
        RECT l = {M.rcSplitter.left, M.rcSplitter.top, M.rcSplitter.left + 1, M.rcSplitter.bottom};
        FillC(dc, l, g_theme.border);
    }
    PaintTabs(dc);
    if (M.tabs.empty()) PaintWelcome(dc);
    PaintPanelHeader(dc);
    PaintStatus(dc);
    BitBlt(hdc, clip.left, clip.top, clip.right - clip.left, clip.bottom - clip.top, dc, clip.left, clip.top, SRCCOPY);
    SelectObject(dc, of);
    SelectObject(dc, ob);
    DeleteObject(bmp);
    DeleteDC(dc);
}

// ---------------- mouse ----------------

static int TabAt(POINT pt, bool* onClose) {
    for (size_t i = 0; i < M.tabRects.size(); ++i) {
        RECT r = M.tabRects[i];
        if (r.right > r.left && PtInRect(&r, pt)) {
            RECT cr = {r.right - S(30), r.top, r.right - S(4), r.bottom};
            if (onClose) *onClose = PtInRect(&cr, pt) != 0;
            return (int)i;
        }
    }
    return -1;
}

static void TabMenu(int i, POINT screen) {
    HMENU m = CreatePopupMenu();
    AppendMenuW(m, MF_STRING, 1, L"Close");
    AppendMenuW(m, MF_STRING, 2, L"Close Others");
    AppendMenuW(m, MF_STRING, 3, L"Close All");
    const Tab& t = M.tabs[(size_t)i];
    bool hasPath = t.kind == Tab::Editor && !t.ed->buf->path.empty();
    if (hasPath) {
        AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(m, MF_STRING, 4, L"Copy Path");
        AppendMenuW(m, MF_STRING, 5, L"Reveal in File Explorer\tAlt+Shift+R");
    }
    int cmd = TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTBUTTON, screen.x, screen.y, 0, M.hwnd, nullptr);
    DestroyMenu(m);
    HWND keep = t.hwnd;
    std::wstring path = hasPath ? t.ed->buf->path : L"";
    if (cmd == 1) CloseTab(i);
    else if (cmd == 2 || cmd == 3) {
        for (int k = (int)M.tabs.size() - 1; k >= 0; --k) {
            if (cmd == 2 && M.tabs[(size_t)k].hwnd == keep) continue;
            if (!CloseTab(k)) break;
        }
    } else if (cmd == 4) SetClipboardText(M.hwnd, path);
    else if (cmd == 5) RevealInExplorer(path);
}

static void OverflowMenu() {
    HMENU m = CreatePopupMenu();
    for (size_t i = 0; i < M.tabs.size(); ++i)
        AppendMenuW(m, MF_STRING | ((int)i == M.active ? MF_CHECKED : 0), i + 1, TabTitle(M.tabs[i]).c_str());
    POINT pt = {M.rcTabOverflow.left, M.rcTabOverflow.bottom};
    ClientToScreen(M.hwnd, &pt);
    int cmd = TrackPopupMenu(m, TPM_RETURNCMD, pt.x, pt.y, 0, M.hwnd, nullptr);
    DestroyMenu(m);
    if (cmd > 0) ActivateTab(cmd - 1);
}

// ---------------- dark menu bar ----------------
// The native menu bar ignores DWM dark mode and stays white, so in dark mode it is painted by hand.
// WM_UAHDRAWMENU / WM_UAHDRAWMENUITEM are the undocumented messages Windows uses for that bar.
static const UINT kWmUahDrawMenu = 0x0091;
static const UINT kWmUahDrawMenuItem = 0x0092;

struct UahMenu { HMENU hmenu; HDC hdc; DWORD dwFlags; };
struct UahDrawMenuItem { DRAWITEMSTRUCT dis; UahMenu um; int iPosition; };

static void PaintMenuBar(HWND h, const UahMenu* um) {
    MENUBARINFO mbi = {sizeof(mbi)};
    if (!GetMenuBarInfo(h, OBJID_MENU, 0, &mbi)) return;
    RECT win;
    GetWindowRect(h, &win);
    RECT r = mbi.rcBar;  // screen coordinates, the DC is window-relative
    OffsetRect(&r, -win.left, -win.top);
    FillC(um->hdc, r, g_theme.panelBg);
}

static void PaintMenuItem(const UahDrawMenuItem* d) {
    wchar_t text[256] = {};
    GetMenuStringW(d->um.hmenu, (UINT)d->iPosition, text, 256, MF_BYPOSITION);
    HDC dc = d->dis.hDC;
    RECT r = d->dis.rcItem;
    bool hot = (d->dis.itemState & (ODS_HOTLIGHT | ODS_SELECTED)) != 0;
    FillC(dc, r, hot ? g_theme.hoverBg : g_theme.panelBg);
    HGDIOBJ old = SelectObject(dc, g_uiFont);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, (d->dis.itemState & ODS_GRAYED) ? g_theme.panelFgDim : g_theme.panelFg);
    DrawTextW(dc, text, -1, &r, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    SelectObject(dc, old);
}

static LRESULT CALLBACK MainProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_CREATE:
        M.hwnd = h;
        g_mainWnd = h;
        return 0;
    case WM_SIZE:
        Layout();
        return 0;
    case WM_ERASEBKGND:
        return 1;
    case kWmUahDrawMenu:
        if (g_settings.dark) { PaintMenuBar(h, (const UahMenu*)l); return 0; }
        break;
    case kWmUahDrawMenuItem:
        if (g_settings.dark) { PaintMenuItem((const UahDrawMenuItem*)l); return 0; }
        break;
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(h, &ps);
        Paint(dc, ps.rcPaint);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_APP_RUNUI: {
        auto* fn = (std::function<void()>*)l;
        (*fn)();
        delete fn;
        return 0;
    }
    case WM_APP_DOCCHANGED: {
        if (M.active >= 0 && M.tabs[(size_t)M.active].hwnd == (HWND)w) {
            EditorView* ed = ActiveEditor();
            if (ed) FindBar::SetMessage(ed->findMessage);
            UpdateTitle();
        }
        InvalidateChrome();
        return 0;
    }
    case WM_TIMER:
        if (w == kTimerStatus) {
            static int lastPending = -1;
            int p = BgPending();
            EditorView* ed = ActiveEditor();
            if (p != lastPending || (ed && !ed->buf->Ready())) {
                lastPending = p;
                InvalidateRect(h, &M.rcStatus, FALSE);
            }
        }
        return 0;
    case WM_COMMAND:
        OnCommand(LOWORD(w));
        return 0;
    case WM_INITMENUPOPUP:
        OnInitMenu((HMENU)w);
        return 0;
    case WM_ACTIVATE:
        if (LOWORD(w) == WA_INACTIVE) {
            M.lastFocus = GetFocus();
        } else if (M.lastFocus && IsChild(h, M.lastFocus) && IsWindowVisible(M.lastFocus)) {
            SetFocus(M.lastFocus);
            return 0;
        }
        break;
    case WM_SETFOCUS:
        if (M.active >= 0) SetFocus(M.tabs[(size_t)M.active].hwnd);
        return 0;
    case WM_ACTIVATEAPP:
        if (w) {
            App::ReloadUnmodifiedDocs();
            App::RefreshGit();
            Explorer::Refresh();
        }
        return 0;
    case WM_DROPFILES: {
        HDROP drop = (HDROP)w;
        UINT n = DragQueryFileW(drop, 0xFFFFFFFF, nullptr, 0);
        for (UINT i = 0; i < n; ++i) {
            UINT len = DragQueryFileW(drop, i, nullptr, 0);
            std::wstring p(len + 1, L'\0');
            DragQueryFileW(drop, i, &p[0], len + 1);
            p.resize(len);
            if (IsDirectory(p)) App::OpenFolder(p);
            else App::OpenFile(p);
        }
        DragFinish(drop);
        return 0;
    }
    case WM_CTLCOLORSTATIC:
    case WM_CTLCOLOREDIT:
        if ((HWND)l == M.output) {
            SetTextColor((HDC)w, g_theme.fg);
            SetBkColor((HDC)w, g_theme.bg);
            return (LRESULT)M.outputBrush;
        }
        break;
    case WM_SETCURSOR:
        if (LOWORD(l) == HTCLIENT) {
            POINT pt;
            GetCursorPos(&pt);
            ScreenToClient(h, &pt);
            RECT sp = M.rcSplitter;
            InflateRect(&sp, S(2), 0);
            if (PtInRect(&sp, pt) || M.draggingSplit) { SetCursor(LoadCursor(nullptr, IDC_SIZEWE)); return TRUE; }
            if (PtInRect(&M.rcPanelSplit, pt) || M.draggingPanel) { SetCursor(LoadCursor(nullptr, IDC_SIZENS)); return TRUE; }
            bool link = false;
            for (auto& it : M.statusItems) if (PtInRect(&it.rc, pt)) link = true;
            if (M.tabs.empty()) for (auto& wl : M.welcomeLinks) if (PtInRect(&wl.first, pt)) link = true;
            if (link) { SetCursor(LoadCursor(nullptr, IDC_HAND)); return TRUE; }
            SetCursor(LoadCursor(nullptr, IDC_ARROW));
            return TRUE;
        }
        break;
    case WM_LBUTTONDOWN: {
        POINT pt = {GET_X_LPARAM(l), GET_Y_LPARAM(l)};
        RECT sp = M.rcSplitter;
        InflateRect(&sp, S(2), 0);
        if (PtInRect(&sp, pt)) { M.draggingSplit = true; SetCapture(h); return 0; }
        if (PtInRect(&M.rcPanelSplit, pt)) { M.draggingPanel = true; SetCapture(h); return 0; }
        for (auto it : M.panelItems) {
            if (!PtInRect(&it.rc, pt)) continue;
            switch (it.kind) {
            case MainState::PanelItem::OutputTab: ShowPanel(MainState::PanelOutput); break;
            case MainState::PanelItem::TerminalTab:
                ShowPanel(MainState::PanelTerminal);
                if (!Term::Focus()) Term::New();
                break;
            case MainState::PanelItem::Group: Term::SelectGroup(it.group); break;
            case MainState::PanelItem::NewTerm: OnCommand(ID_TERM_NEW); break;
            case MainState::PanelItem::SplitTerm: OnCommand(ID_TERM_SPLIT); break;
            case MainState::PanelItem::KillTerm: OnCommand(ID_TERM_KILL); break;
            case MainState::PanelItem::ClosePanel: HidePanel(); break;
            }
            return 0;
        }
        if (PtInRect(&M.rcSideHeader, pt)) {
            int mode = pt.x < (M.rcSideHeader.left + M.rcSideHeader.right) / 2 ? 0 : 1;
            OnCommand(mode == 0 ? ID_VIEW_EXPLORER : ID_VIEW_SCM);
            return 0;
        }
        if (PtInRect(&M.rcTabOverflow, pt)) { OverflowMenu(); return 0; }
        bool onClose = false;
        int ti = TabAt(pt, &onClose);
        if (ti >= 0) {
            if (onClose) CloseTab(ti);
            else ActivateTab(ti);
            return 0;
        }
        for (auto& it : M.statusItems)
            if (PtInRect(&it.rc, pt)) { OnCommand(it.cmd); return 0; }
        if (M.tabs.empty())
            for (auto& wl : M.welcomeLinks)
                if (PtInRect(&wl.first, pt)) { std::wstring f = wl.second; App::OpenFolder(f); return 0; }
        return 0;
    }
    case WM_LBUTTONDBLCLK: {
        POINT pt = {GET_X_LPARAM(l), GET_Y_LPARAM(l)};
        if (PtInRect(&M.rcTabs, pt) && TabAt(pt, nullptr) < 0) OnCommand(ID_FILE_NEW);
        return 0;
    }
    case WM_MOUSEMOVE: {
        POINT pt = {GET_X_LPARAM(l), GET_Y_LPARAM(l)};
        if (M.draggingSplit) {
            g_settings.sidebarWidth = std::max(120, std::min(1200, MulDiv(pt.x, 96, g_dpi)));
            Layout();
            return 0;
        }
        if (M.draggingPanel) {
            g_settings.panelHeight = std::max(60, std::min(2000, MulDiv(M.rcStatus.top - pt.y, 96, g_dpi)));
            Layout();
            return 0;
        }
        bool onClose = false;
        int ti = TabAt(pt, &onClose);
        if (ti != M.hoverTab || onClose != M.hoverClose) {
            M.hoverTab = ti;
            M.hoverClose = onClose;
            InvalidateRect(h, &M.rcTabs, FALSE);
        }
        if (!M.tracking) {
            TRACKMOUSEEVENT tme = {sizeof(tme), TME_LEAVE, h, 0};
            TrackMouseEvent(&tme);
            M.tracking = true;
        }
        return 0;
    }
    case WM_MOUSELEAVE:
        M.tracking = false;
        if (M.hoverTab != -1) { M.hoverTab = -1; InvalidateRect(h, &M.rcTabs, FALSE); }
        return 0;
    case WM_LBUTTONUP:
        if (M.draggingSplit || M.draggingPanel) {
            M.draggingSplit = M.draggingPanel = false;
            ReleaseCapture();
            SaveSettings();
        }
        return 0;
    case WM_MBUTTONUP: {
        POINT pt = {GET_X_LPARAM(l), GET_Y_LPARAM(l)};
        int ti = TabAt(pt, nullptr);
        if (ti >= 0) CloseTab(ti);
        for (auto it : M.panelItems)
            if (it.kind == MainState::PanelItem::Group && PtInRect(&it.rc, pt)) { Term::KillGroup(it.group); break; }
        return 0;
    }
    case WM_RBUTTONUP: {
        POINT pt = {GET_X_LPARAM(l), GET_Y_LPARAM(l)};
        int ti = TabAt(pt, nullptr);
        if (ti >= 0) {
            ClientToScreen(h, &pt);
            TabMenu(ti, pt);
        }
        return 0;
    }
    case WM_MOUSEWHEEL: {
        POINT pt = {GET_X_LPARAM(l), GET_Y_LPARAM(l)};
        ScreenToClient(h, &pt);
        if (PtInRect(&M.rcTabs, pt) && !M.tabs.empty()) {
            int d = GET_WHEEL_DELTA_WPARAM(w) > 0 ? -1 : 1;
            int n = std::max(0, std::min((int)M.tabs.size() - 1, M.active + d));
            ActivateTab(n);
        }
        return 0;
    }
    case WM_DPICHANGED: {
        g_dpi = HIWORD(w);
        RecreateFonts();
        RECT* r = (RECT*)l;
        SetWindowPos(h, nullptr, r->left, r->top, r->right - r->left, r->bottom - r->top, SWP_NOZORDER | SWP_NOACTIVATE);
        for (auto& t : M.tabs)
            if (t.kind == Tab::Editor) t.ed->FontChanged();
        Explorer::ThemeChanged();
        Layout();
        Term::FontChanged();
        RedrawWindow(h, nullptr, nullptr, RDW_INVALIDATE | RDW_ALLCHILDREN);
        return 0;
    }
    case WM_CLOSE: {
        if (!ConfirmCloseAll()) return 0;
        WINDOWPLACEMENT wp = {sizeof(wp)};
        GetWindowPlacement(h, &wp);
        g_settings.winMax = wp.showCmd == SW_SHOWMAXIMIZED;
        g_settings.winX = wp.rcNormalPosition.left;
        g_settings.winY = wp.rcNormalPosition.top;
        g_settings.winW = wp.rcNormalPosition.right - wp.rcNormalPosition.left;
        g_settings.winH = wp.rcNormalPosition.bottom - wp.rcNormalPosition.top;
        SaveSettings();
        Term::CloseAll();
        DestroyWindow(h);
        return 0;
    }
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

// ---------------- bottom panel ----------------

void ShowPanel(MainState::PanelTab tab) {
    M.panelTab = tab;
    if (!g_settings.showOutput) {
        g_settings.showOutput = true;
        SaveSettings();
    }
    Layout();
}

void HidePanel() {
    bool hadFocus = Term::HasFocus() || GetFocus() == M.output;
    g_settings.showOutput = false;
    Layout();
    SaveSettings();
    if (hadFocus) SetFocus(M.hwnd);
}

void TerminalsChanged() {
    if (Term::GroupCount() == 0 && M.panelTab == MainState::PanelTerminal && g_settings.showOutput) {
        g_settings.showOutput = false;
        SaveSettings();
    }
    Layout();
}

// ---------------- App API ----------------

namespace App {

HWND Main() { return M.hwnd; }
const std::wstring& Folder() { return M.folder; }
const GitStatus& Status() { return M.status; }

static std::wstring FullPath(const std::wstring& p) {
    wchar_t buf[32768];
    DWORD n = GetFullPathNameW(p.c_str(), 32768, buf, nullptr);
    return n ? std::wstring(buf, n) : p;
}

void OpenFile(const std::wstring& path, int64_t line) {
    std::wstring full = FullPath(path);
    std::wstring key = L"file:" + LowerW(full);
    for (size_t i = 0; i < M.tabs.size(); ++i) {
        if (M.tabs[i].key == key) {
            ActivateTab((int)i);
            if (line >= 0) M.tabs[i].ed->GotoLine((uint64_t)line);
            return;
        }
    }
    std::wstring err;
    HCURSOR old = SetCursor(LoadCursor(nullptr, IDC_WAIT));
    EditorView* ed = EditorView::Create(M.hwnd, full, err);
    SetCursor(old);
    if (!ed) {
        MsgBox(M.hwnd, L"Could not open " + full + L"\n\n" + err, MB_ICONERROR);
        return;
    }
    Tab t;
    t.kind = Tab::Editor;
    t.hwnd = ed->hwnd;
    t.ed = ed;
    t.key = key;
    AddTab(t);
    if (line >= 0) ed->GotoLine((uint64_t)line);
}

void OpenDiff(const DiffSpec& spec) {
    std::wstring key = DiffView::Key(spec);
    for (size_t i = 0; i < M.tabs.size(); ++i) {
        if (M.tabs[i].key == key) {
            ActivateTab((int)i);
            DiffView::Reload(M.tabs[i].hwnd);
            return;
        }
    }
    HWND h = DiffView::Create(M.hwnd, spec);
    if (!h) return;
    Tab t;
    t.kind = Tab::Diff;
    t.hwnd = h;
    t.key = key;
    AddTab(t);
}

void OpenLog() {
    if (!M.status.isRepo) return;
    std::wstring key = L"log:" + LowerW(M.status.root);
    for (size_t i = 0; i < M.tabs.size(); ++i) {
        if (M.tabs[i].key == key) {
            ActivateTab((int)i);
            LogView::Reload(M.tabs[i].hwnd);
            return;
        }
    }
    HWND h = LogView::Create(M.hwnd, M.status.root);
    if (!h) return;
    Tab t;
    t.kind = Tab::Log;
    t.hwnd = h;
    t.key = key;
    AddTab(t);
}

void OpenFolder(const std::wstring& folder) {
    std::wstring f = FullPath(folder);
    while (f.size() > 3 && (f.back() == L'\\' || f.back() == L'/')) f.pop_back();
    if (!IsDirectory(f)) {
        MsgBox(M.hwnd, L"Folder not found:\n" + f, MB_ICONWARNING);
        auto& r = g_settings.recentFolders;
        r.erase(std::remove_if(r.begin(), r.end(), [&](const std::wstring& x) { return PathEqualsI(x, f); }), r.end());
        SaveSettings();
        InvalidateRect(M.hwnd, nullptr, FALSE);
        return;
    }
    M.folder = f;
    M.status = GitStatus();
    Explorer::SetRoot(f);
    AddRecentFolder(f);
    Scm::Update(M.status);
    RefreshGit();
    UpdateTitle();
    if (!g_settings.showSidebar) { g_settings.showSidebar = true; Layout(); }
    InvalidateRect(M.hwnd, nullptr, FALSE);
}

void RefreshGit() {
    if (M.gitPending) { M.gitQueued = true; return; }
    M.gitPending = true;
    std::wstring folder = M.folder;
    BgRun([folder]() {
        auto st = std::make_shared<GitStatus>(QueryGitStatus(folder));
        RunOnUi([st, folder]() {
            M.gitPending = false;
            if (folder == M.folder) {
                ReloadUnmodifiedDocs();  // pick up files changed by git or external tools
                M.status = *st;
                Scm::Update(M.status);
                for (auto& t : M.tabs)
                    if (t.kind == Tab::Diff) DiffView::Reload(t.hwnd);
                InvalidateChrome();
            }
            if (M.gitQueued) {
                M.gitQueued = false;
                RefreshGit();
            }
        });
    });
}

void SetStatusText(const std::wstring& s) {
    M.statusText = s;
    InvalidateRect(M.hwnd, &M.rcStatus, FALSE);
}

void AppendOutput(const std::wstring& s) {
    if (!M.output) return;
    int len = GetWindowTextLengthW(M.output);
    if (len > 400000) {
        SendMessageW(M.output, EM_SETSEL, 0, len / 2);
        SendMessageW(M.output, EM_REPLACESEL, FALSE, (LPARAM)L"");
        len = GetWindowTextLengthW(M.output);
    }
    SendMessageW(M.output, EM_SETSEL, len, len);
    SendMessageW(M.output, EM_REPLACESEL, FALSE, (LPARAM)s.c_str());
    SendMessageW(M.output, EM_SCROLLCARET, 0, 0);
}

void ShowOutput() {
    if (!g_settings.showOutput) ShowPanel(MainState::PanelOutput);  // don't take the panel away from a visible terminal
}

void ReloadUnmodifiedDocs() {
    for (auto& t : M.tabs) {
        if (t.kind != Tab::Editor) continue;
        TextBuffer* b = t.ed->buf.get();
        if (b->Modified() || b->path.empty() || !b->ChangedOnDisk()) continue;
        if (!FileExists(b->path)) continue;
        std::wstring err;
        t.ed->Reload(err);
    }
    InvalidateChrome();
}

void ShowScm() {
    if (M.sidebarMode != 1 || !g_settings.showSidebar) OnCommand(ID_VIEW_SCM);
}

}  // namespace App

// ---------------- creation ----------------

HWND CreateMainWindow() {
    WNDCLASSEXW wc = {sizeof(wc)};
    wc.style = CS_DBLCLKS;
    wc.lpfnWndProc = MainProc;
    wc.hInstance = g_hinst;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.hIcon = LoadIconW(g_hinst, MAKEINTRESOURCEW(IDI_APP));
    wc.hIconSm = (HICON)LoadImageW(g_hinst, MAKEINTRESOURCEW(IDI_APP), IMAGE_ICON, GetSystemMetrics(SM_CXSMICON),
                                   GetSystemMetrics(SM_CYSMICON), 0);
    wc.lpszClassName = L"DittoMain";
    RegisterClassExW(&wc);
    EditorView::Register();
    DiffView::Register();
    LogView::Register();

    M.menu = BuildMenu();
    M.accel = BuildAccel();
    HWND h = CreateWindowExW(WS_EX_ACCEPTFILES, L"DittoMain", L"ditto", WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
                             g_settings.winX, g_settings.winY, g_settings.winW, g_settings.winH, nullptr, M.menu, g_hinst, nullptr);
    if (!h) return nullptr;
    g_dpi = (int)GetDpiForWindow(h);
    RecreateFonts();
    ApplyTheme(g_settings.dark);
    M.explorer = Explorer::Create(h);
    M.scm = Scm::Create(h);
    M.findBar = FindBar::Create(h);
    M.output = CreateWindowExW(0, L"EDIT", L"", WS_CHILD | WS_VSCROLL | ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | ES_NOHIDESEL,
                               0, 0, 0, 0, h, nullptr, g_hinst, nullptr);
    SendMessageW(M.output, WM_SETFONT, (WPARAM)g_uiFont, FALSE);
    SendMessageW(M.output, EM_SETLIMITTEXT, 0, 0);
    SetGitLogger([](const std::wstring& s) {
        std::wstring copy = s;
        RunOnUi([copy]() { App::AppendOutput(copy); });
    });
    ApplyThemeAll();
    SetTimer(h, kTimerStatus, 500, nullptr);
    Layout();
    UpdateTitle();
    return h;
}
