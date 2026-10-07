// Find / replace bar shown above the editor.
#include "main_window.h"
#include "resource.h"

namespace {

enum { IDC_FIND = 3001, IDC_REPL };
HWND g_bar, g_find, g_repl, g_case, g_word, g_prev, g_next, g_close, g_replOne, g_replAll;
bool g_replace = false, g_visible = false, g_matchCase = false, g_wholeWord = false;
std::wstring g_msg;
HBRUSH g_inputBrush;
const UINT_PTR kTimerIncr = 1;

std::wstring GetText(HWND h) {
    int n = GetWindowTextLengthW(h);
    std::wstring s((size_t)n + 1, L'\0');
    GetWindowTextW(h, &s[0], n + 1);
    s.resize((size_t)n);
    return s;
}

int RowH() { return S(34); }

void LayoutBar() {
    RECT rc;
    GetClientRect(g_bar, &rc);
    int pad = S(6), h = S(24), bw = S(26), y = (RowH() - h) / 2;
    int ew = std::min(S(320), std::max(S(120), (int)rc.right / 2));
    MoveWindow(g_find, pad, y, ew, h, TRUE);
    int x = pad + ew + S(4);
    MoveWindow(g_case, x, y, bw, h, TRUE); x += bw + S(2);
    MoveWindow(g_word, x, y, bw, h, TRUE); x += bw + S(8);
    MoveWindow(g_prev, x, y, bw, h, TRUE); x += bw + S(2);
    MoveWindow(g_next, x, y, bw, h, TRUE);
    MoveWindow(g_close, rc.right - pad - bw, y, bw, h, TRUE);
    int y2 = RowH() + y - S(4);
    MoveWindow(g_repl, pad, y2, ew, h, TRUE);
    MoveWindow(g_replOne, pad + ew + S(4), y2, S(70), h, TRUE);
    MoveWindow(g_replAll, pad + ew + S(4) + S(74), y2, S(86), h, TRUE);
    int show = g_replace ? SW_SHOW : SW_HIDE;
    ShowWindow(g_repl, show);
    ShowWindow(g_replOne, show);
    ShowWindow(g_replAll, show);
    InvalidateRect(g_bar, nullptr, TRUE);
}

void RunFind(bool forward, bool incremental) {
    EditorView* ed = ActiveEditor();
    if (!ed) return;
    FindQuery q = FindBar::Query();
    if (q.needle.empty()) { FindBar::SetMessage(L""); return; }
    ed->Find(q, forward, incremental);
}

LRESULT CALLBACK EditSub(HWND h, UINT m, WPARAM w, LPARAM l, UINT_PTR, DWORD_PTR) {
    if (m == WM_KEYDOWN) {
        if (w == VK_ESCAPE) { SendMessageW(M.hwnd, WM_COMMAND, ID_FIND_CLOSE, 0); return 0; }
        if (w == VK_RETURN) {
            if (h == g_repl) {
                if (GetKeyState(VK_CONTROL) < 0) SendMessageW(M.hwnd, WM_COMMAND, ID_FIND_REPLACEALL, 0);
                else SendMessageW(M.hwnd, WM_COMMAND, ID_FIND_REPLACEONE, 0);
            } else {
                RunFind(GetKeyState(VK_SHIFT) >= 0, false);
            }
            return 0;
        }
        if (w == VK_TAB) {
            SetFocus(h == g_find && g_replace ? g_repl : g_find);
            return 0;
        }
        if (w == 'A' && GetKeyState(VK_CONTROL) < 0) { SendMessageW(h, EM_SETSEL, 0, -1); return 0; }
    }
    if (m == WM_CHAR && (w == VK_RETURN || w == VK_ESCAPE || w == VK_TAB || w == 1)) return 0;
    return DefSubclassProc(h, m, w, l);
}

LRESULT CALLBACK BarProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_SIZE: LayoutBar(); return 0;
    case WM_ERASEBKGND: {
        RECT rc;
        GetClientRect(h, &rc);
        HBRUSH b = CreateSolidBrush(g_theme.panelBg);
        FillRect((HDC)w, &rc, b);
        DeleteObject(b);
        RECT line = {0, rc.bottom - 1, rc.right, rc.bottom};
        HBRUSH lb = CreateSolidBrush(g_theme.border);
        FillRect((HDC)w, &line, lb);
        DeleteObject(lb);
        return 1;
    }
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(h, &ps);
        RECT nr;
        GetWindowRect(g_next, &nr);
        MapWindowPoints(nullptr, h, (POINT*)&nr, 2);
        RECT cr;
        GetWindowRect(g_close, &cr);
        MapWindowPoints(nullptr, h, (POINT*)&cr, 2);
        RECT tr = {nr.right + S(10), 0, cr.left - S(6), RowH()};
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, g_msg == L"No results" ? RGB(220, 80, 80) : g_theme.panelFgDim);
        HGDIOBJ o = SelectObject(dc, g_uiFont);
        DrawTextW(dc, g_msg.c_str(), -1, &tr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
        SelectObject(dc, o);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_CTLCOLOREDIT: {
        SetTextColor((HDC)w, g_theme.panelFg);
        SetBkColor((HDC)w, g_theme.inputBg);
        return (LRESULT)g_inputBrush;
    }
    case WM_DRAWITEM: {
        auto* dis = (DRAWITEMSTRUCT*)l;
        bool toggled = (dis->hwndItem == g_case && g_matchCase) || (dis->hwndItem == g_word && g_wholeWord);
        DrawFlatButton(dis, false, toggled);
        return TRUE;
    }
    case WM_TIMER:
        if (w == kTimerIncr) {
            KillTimer(h, kTimerIncr);
            RunFind(true, true);
        }
        return 0;
    case WM_COMMAND: {
        HWND c = (HWND)l;
        if (LOWORD(w) == IDC_FIND && HIWORD(w) == EN_CHANGE) { SetTimer(h, kTimerIncr, 250, nullptr); return 0; }
        if (c == g_case) { g_matchCase = !g_matchCase; InvalidateRect(g_case, nullptr, FALSE); RunFind(true, true); }
        else if (c == g_word) { g_wholeWord = !g_wholeWord; InvalidateRect(g_word, nullptr, FALSE); RunFind(true, true); }
        else if (c == g_prev) RunFind(false, false);
        else if (c == g_next) RunFind(true, false);
        else if (c == g_close) SendMessageW(M.hwnd, WM_COMMAND, ID_FIND_CLOSE, 0);
        else if (c == g_replOne) SendMessageW(M.hwnd, WM_COMMAND, ID_FIND_REPLACEONE, 0);
        else if (c == g_replAll) SendMessageW(M.hwnd, WM_COMMAND, ID_FIND_REPLACEALL, 0);
        return 0;
    }
    }
    return DefWindowProcW(h, m, w, l);
}

HWND MakeEdit(int id, const wchar_t* cue) {
    HWND e = CreateWindowExW(0, L"EDIT", L"", WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL | WS_BORDER, 0, 0, 0, 0, g_bar,
                             (HMENU)(INT_PTR)id, g_hinst, nullptr);
    SendMessageW(e, WM_SETFONT, (WPARAM)g_uiFont, FALSE);
    SendMessageW(e, EM_SETCUEBANNER, TRUE, (LPARAM)cue);
    SetWindowSubclass(e, EditSub, 1, 0);
    return e;
}

void AddTip(HWND tip, HWND ctl, const wchar_t* text) {
    TOOLINFOW ti = {sizeof(ti)};
    ti.uFlags = TTF_IDISHWND | TTF_SUBCLASS;
    ti.hwnd = g_bar;
    ti.uId = (UINT_PTR)ctl;
    ti.lpszText = (LPWSTR)text;
    SendMessageW(tip, TTM_ADDTOOLW, 0, (LPARAM)&ti);
}

}  // namespace

namespace FindBar {

HWND Create(HWND parent) {
    WNDCLASSEXW wc = {sizeof(wc)};
    wc.lpfnWndProc = BarProc;
    wc.hInstance = g_hinst;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = L"MyFindBar";
    RegisterClassExW(&wc);
    g_inputBrush = CreateSolidBrush(g_theme.inputBg);
    g_bar = CreateWindowExW(0, L"MyFindBar", L"", WS_CHILD | WS_CLIPCHILDREN, 0, 0, 0, 0, parent, nullptr, g_hinst, nullptr);
    g_find = MakeEdit(IDC_FIND, L"Find");
    g_repl = MakeEdit(IDC_REPL, L"Replace");
    g_case = MakeButton(g_bar, 0, L"Aa");
    g_word = MakeButton(g_bar, 0, L"ab|");
    g_prev = MakeButton(g_bar, 0, L"\xE74A");
    g_next = MakeButton(g_bar, 0, L"\xE74B");
    g_close = MakeButton(g_bar, 0, L"\xE711");
    g_replOne = MakeButton(g_bar, 0, L"Replace");
    g_replAll = MakeButton(g_bar, 0, L"Replace All");
    HWND tip = CreateWindowExW(WS_EX_TOPMOST, TOOLTIPS_CLASSW, nullptr, WS_POPUP | TTS_ALWAYSTIP | TTS_NOPREFIX, 0, 0, 0, 0, g_bar,
                               nullptr, g_hinst, nullptr);
    AddTip(tip, g_case, L"Match Case");
    AddTip(tip, g_word, L"Match Whole Word");
    AddTip(tip, g_prev, L"Previous Match (Shift+Enter / Shift+F3)");
    AddTip(tip, g_next, L"Next Match (Enter / F3)");
    AddTip(tip, g_close, L"Close (Escape)");
    AddTip(tip, g_replOne, L"Replace (Enter)");
    AddTip(tip, g_replAll, L"Replace All (Ctrl+Enter)");
    return g_bar;
}

void Show(bool replace) {
    g_replace = replace;
    g_visible = true;
    EditorView* ed = ActiveEditor();
    if (ed) {
        std::string sel = ed->SelectionForFind();
        if (!sel.empty()) SetWindowTextW(g_find, Utf8ToWide(sel).c_str());
    }
    ShowWindow(g_bar, SW_SHOW);
    Layout();
    LayoutBar();
    SetFocus(replace && GetWindowTextLengthW(g_find) ? g_repl : g_find);
    SendMessageW(g_find, EM_SETSEL, 0, -1);
}

void Hide() {
    if (!g_visible) return;
    g_visible = false;
    ShowWindow(g_bar, SW_HIDE);
    Layout();
    EditorView* ed = ActiveEditor();
    if (ed) SetFocus(ed->hwnd);
}

bool Visible() { return g_visible; }
int Height() { return g_visible ? (g_replace ? RowH() * 2 - S(6) : RowH()) : 0; }

FindQuery Query() {
    FindQuery q;
    q.needle = WideToUtf8(GetText(g_find));
    q.matchCase = g_matchCase;
    q.wholeWord = g_wholeWord;
    return q;
}

std::string Replacement() { return WideToUtf8(GetText(g_repl)); }

void SetMessage(const std::wstring& m) {
    if (m == g_msg) return;
    g_msg = m;
    if (g_bar) InvalidateRect(g_bar, nullptr, TRUE);
}

void FocusFind() { SetFocus(g_find); }

void ThemeChanged() {
    if (g_inputBrush) DeleteObject(g_inputBrush);
    g_inputBrush = CreateSolidBrush(g_theme.inputBg);
    for (HWND e : {g_find, g_repl}) SetWindowTheme(e, g_settings.dark ? L"DarkMode_CFD" : L"Explorer", nullptr);
    RedrawWindow(g_bar, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_FRAME);
}

}  // namespace FindBar
