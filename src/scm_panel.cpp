// Source Control side panel: commit message box, commit button and a custom-drawn change list.
#include "app.h"
#include "resource.h"

namespace {

enum {
    IDC_MSG = 2001, IDC_COMMIT, IDC_COMMITMENU, IDC_QUICK, IDC_REFRESH, IDC_MORE, IDC_INIT, IDC_CLONE, IDC_OPENFOLDER, IDC_PUBLISH,
};
enum Group { G_MERGE, G_STAGED, G_CHANGES, G_COUNT };
const wchar_t* kGroupNames[G_COUNT] = {L"MERGE CHANGES", L"STAGED CHANGES", L"CHANGES"};

// glyphs (Segoe MDL2 Assets)
const wchar_t* kIcoAdd = L"\xE710";
const wchar_t* kIcoRemove = L"\xE738";
const wchar_t* kIcoUndo = L"\xE7A7";
const wchar_t* kIcoOpen = L"\xE8E5";
const wchar_t* kIcoRefresh = L"\xE72C";
const wchar_t* kIcoMore = L"\xE712";
const wchar_t* kIcoDown = L"\xE70D";
const wchar_t* kIcoRight = L"\xE76C";

struct Row {
    bool header;
    int group;
    int file;  // index in st.files
};

struct State {
    HWND hwnd = nullptr, msg, commit, commitMenu, quick, refresh, more, init, clone, openFolder, publish, list;
    HBRUSH inputBrush = nullptr;
    GitStatus st;
    std::vector<Row> rows;
    std::vector<bool> sel;
    int focus = -1, anchor = -1, hover = -1, hoverBtn = -1, top = 0;
    bool collapsed[G_COUNT] = {};
    bool tracking = false;
} P;

bool IsConflict(const GitFile& f) {
    char x = f.x, y = f.y;
    return (x == 'U' || y == 'U') || (x == 'A' && y == 'A') || (x == 'D' && y == 'D');
}

char Letter(const GitFile& f, int g) {
    if (g == G_MERGE) return '!';
    char c = g == G_STAGED ? f.x : f.y;
    return c == '?' ? 'U' : c;
}

COLORREF LetterColor(char c) {
    bool d = g_settings.dark;
    switch (c) {
    case 'M': return d ? RGB(226, 192, 141) : RGB(137, 92, 20);
    case 'A': case 'U': return d ? RGB(115, 201, 145) : RGB(56, 130, 70);
    case 'D': return d ? RGB(199, 78, 57) : RGB(173, 15, 15);
    case 'R': case 'C': return d ? RGB(115, 201, 145) : RGB(56, 130, 70);
    case '!': return d ? RGB(230, 100, 100) : RGB(200, 30, 30);
    default: return g_theme.panelFg;
    }
}

int RowH() { return S(22); }

std::vector<int> GroupFiles(int g) {
    std::vector<int> v;
    for (int i = 0; i < (int)P.st.files.size(); ++i) {
        const GitFile& f = P.st.files[(size_t)i];
        bool conflict = IsConflict(f);
        if (g == G_MERGE && conflict) v.push_back(i);
        if (g == G_STAGED && !conflict && f.x != ' ' && f.x != '?') v.push_back(i);
        if (g == G_CHANGES && !conflict && (f.y != ' ' || f.x == '?')) v.push_back(i);
    }
    return v;
}

std::wstring RowKey(const Row& r) {
    return std::to_wstring(r.group) + (r.header ? L"#" : L":" + P.st.files[(size_t)r.file].path);
}

void UpdateListScroll() {
    RECT rc;
    GetClientRect(P.list, &rc);
    int vis = std::max(1, (int)(rc.bottom / RowH()));
    int maxTop = std::max(0, (int)P.rows.size() - vis);
    P.top = std::max(0, std::min(P.top, maxTop));
    SCROLLINFO si = {sizeof(si), SIF_RANGE | SIF_PAGE | SIF_POS};
    si.nMax = (int)P.rows.size() - 1;
    si.nPage = (UINT)vis;
    si.nPos = P.top;
    SetScrollInfo(P.list, SB_VERT, &si, TRUE);
}

void RebuildRows() {
    std::vector<std::wstring> selKeys;
    std::wstring focusKey;
    for (size_t i = 0; i < P.rows.size(); ++i) {
        if (i < P.sel.size() && P.sel[i]) selKeys.push_back(RowKey(P.rows[i]));
        if ((int)i == P.focus) focusKey = RowKey(P.rows[i]);
    }
    P.rows.clear();
    for (int g = 0; g < G_COUNT; ++g) {
        auto files = GroupFiles(g);
        if (files.empty() && g != G_CHANGES) continue;
        P.rows.push_back(Row{true, g, -1});
        if (P.collapsed[g]) continue;
        for (int f : files) P.rows.push_back(Row{false, g, f});
    }
    P.sel.assign(P.rows.size(), false);
    P.focus = -1;
    for (size_t i = 0; i < P.rows.size(); ++i) {
        std::wstring k = RowKey(P.rows[i]);
        if (std::find(selKeys.begin(), selKeys.end(), k) != selKeys.end()) P.sel[i] = true;
        if (k == focusKey) P.focus = (int)i;
    }
    UpdateListScroll();
    InvalidateRect(P.list, nullptr, FALSE);
}

// action buttons for a row, right to left (excluding the status letter)
std::vector<int> RowActions(const Row& r) {
    // 1 = stage, 2 = unstage, 3 = discard, 4 = open file
    if (r.header) {
        if (r.group == G_STAGED) return {2};
        if (r.group == G_CHANGES) return {1, 3};
        return {1};
    }
    if (r.group == G_STAGED) return {2, 4};
    if (r.group == G_CHANGES) return {1, 3, 4};
    return {1, 4};
}
const wchar_t* ActionGlyph(int a) {
    switch (a) {
    case 1: return kIcoAdd;
    case 2: return kIcoRemove;
    case 3: return kIcoUndo;
    default: return kIcoOpen;
    }
}
const wchar_t* ActionTip(int a) {
    switch (a) {
    case 1: return L"Stage";
    case 2: return L"Unstage";
    case 3: return L"Discard Changes";
    default: return L"Open File";
    }
}

RECT ActionRect(const RECT& row, int idx, bool header) {
    int bw = S(22);
    int right = row.right - (header ? S(34) : S(22)) - idx * bw;
    return RECT{right - bw, row.top, right, row.bottom};
}

int HitAction(int rowIdx, int x, int y) {
    if (rowIdx < 0 || rowIdx >= (int)P.rows.size()) return -1;
    RECT rc;
    GetClientRect(P.list, &rc);
    RECT row = {0, (rowIdx - P.top) * RowH(), rc.right, (rowIdx - P.top + 1) * RowH()};
    auto acts = RowActions(P.rows[(size_t)rowIdx]);
    for (size_t i = 0; i < acts.size(); ++i) {
        RECT ar = ActionRect(row, (int)i, P.rows[(size_t)rowIdx].header);
        POINT pt = {x, y};
        if (PtInRect(&ar, pt)) return acts[i];
    }
    return -1;
}

std::vector<int> SelectedFileRows() {
    std::vector<int> v;
    for (size_t i = 0; i < P.rows.size(); ++i)
        if (P.sel[i] && !P.rows[i].header) v.push_back((int)i);
    return v;
}

void OpenRow(int i, bool file) {
    const Row& r = P.rows[(size_t)i];
    if (r.header) return;
    const GitFile& f = P.st.files[(size_t)r.file];
    std::wstring full = JoinPath(P.st.root, ToBackslashes(f.path));
    if (file || r.group == G_MERGE) {
        if (f.x == 'D' && r.group == G_STAGED) return;
        App::OpenFile(full);
        return;
    }
    DiffSpec d;
    d.root = P.st.root;
    d.path = f.path;
    d.origPath = f.origPath;
    if (r.group == G_STAGED) d.kind = DiffSpec::Staged;
    else d.kind = (f.x == '?') ? DiffSpec::Untracked : DiffSpec::Unstaged;
    App::OpenDiff(d);
}

void DoAction(int action, const std::vector<int>& rowIdx) {
    std::vector<std::wstring> paths;
    std::vector<GitFile> files;
    for (int i : rowIdx) {
        const Row& r = P.rows[(size_t)i];
        std::vector<int> fl;
        if (r.header) fl = GroupFiles(r.group);
        else fl.push_back(r.file);
        for (int fi : fl) {
            const GitFile& f = P.st.files[(size_t)fi];
            if (std::find(paths.begin(), paths.end(), f.path) == paths.end()) {
                paths.push_back(f.path);
                files.push_back(f);
                if (action == 2 && !f.origPath.empty()) paths.push_back(f.origPath);
            }
        }
    }
    if (paths.empty()) return;
    switch (action) {
    case 1: GitStage(paths, true); break;
    case 2: GitStage(paths, false); break;
    case 3: GitDiscard(files); break;
    case 4:
        for (int i : rowIdx) OpenRow(i, true);
        break;
    }
}

void ListContextMenu(int x, int y) {
    auto rows = SelectedFileRows();
    if (rows.empty()) return;
    bool anyStaged = false, anyChange = false;
    for (int i : rows) {
        if (P.rows[(size_t)i].group == G_STAGED) anyStaged = true;
        else anyChange = true;
    }
    HMENU m = CreatePopupMenu();
    AppendMenuW(m, MF_STRING, 10, L"Open Changes");
    AppendMenuW(m, MF_STRING, 4, L"Open File");
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    if (anyChange) AppendMenuW(m, MF_STRING, 1, L"Stage Changes");
    if (anyStaged) AppendMenuW(m, MF_STRING, 2, L"Unstage Changes");
    if (anyChange) AppendMenuW(m, MF_STRING, 3, L"Discard Changes");
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(m, MF_STRING, 11, L"Copy Path");
    AppendMenuW(m, MF_STRING, 12, L"Reveal in File Explorer\tAlt+Shift+R");
    int cmd = TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, 0, P.list, nullptr);
    DestroyMenu(m);
    if (cmd == 10) { for (int i : rows) OpenRow(i, false); }
    else if (cmd == 11 || cmd == 12) {
        const GitFile& f = P.st.files[(size_t)P.rows[(size_t)rows[0]].file];
        std::wstring full = JoinPath(P.st.root, ToBackslashes(f.path));
        if (cmd == 11) SetClipboardText(P.list, full);
        else RevealInExplorer(full);
    } else if (cmd >= 1 && cmd <= 4) {
        std::vector<int> filtered;
        for (int i : rows) {
            int g = P.rows[(size_t)i].group;
            if ((cmd == 1 || cmd == 3) && g == G_STAGED) continue;
            if (cmd == 2 && g != G_STAGED) continue;
            filtered.push_back(i);
        }
        DoAction(cmd, filtered);
    }
}

void PaintList(HDC hdc) {
    RECT rc;
    GetClientRect(P.list, &rc);
    HDC dc = CreateCompatibleDC(hdc);
    HBITMAP bmp = CreateCompatibleBitmap(hdc, std::max(1, (int)rc.right), std::max(1, (int)rc.bottom));
    HGDIOBJ ob = SelectObject(dc, bmp);
    const Theme& t = g_theme;
    HBRUSH bg = CreateSolidBrush(t.panelBg);
    FillRect(dc, &rc, bg);
    DeleteObject(bg);
    SetBkMode(dc, TRANSPARENT);
    int rh = RowH();
    bool focused = GetFocus() == P.list;
    for (int i = P.top; i < (int)P.rows.size(); ++i) {
        int y = (i - P.top) * rh;
        if (y > rc.bottom) break;
        const Row& r = P.rows[(size_t)i];
        RECT row = {0, y, rc.right, y + rh};
        if (P.sel[(size_t)i] || i == P.hover) {
            COLORREF c = P.sel[(size_t)i] ? (focused ? t.selBg : t.hoverBg) : t.hoverBg;
            HBRUSH b = CreateSolidBrush(c);
            FillRect(dc, &row, b);
            DeleteObject(b);
        }
        if (r.header) {
            RECT cr = {S(4), y, S(20), y + rh};
            SelectObject(dc, g_iconFont);
            SetTextColor(dc, t.panelFg);
            DrawTextW(dc, P.collapsed[r.group] ? kIcoRight : kIcoDown, 1, &cr, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            RECT tr = {S(22), y, rc.right - S(30), y + rh};
            SelectObject(dc, g_uiFontBold);
            DrawTextW(dc, kGroupNames[r.group], -1, &tr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
            // count badge
            std::wstring cnt = std::to_wstring(GroupFiles(r.group).size());
            SelectObject(dc, g_uiFont);
            SIZE sz;
            GetTextExtentPoint32W(dc, cnt.c_str(), (int)cnt.size(), &sz);
            int bw = std::max((int)sz.cx + S(10), S(18));
            RECT br = {rc.right - S(8) - bw, y + S(3), rc.right - S(8), y + rh - S(3)};
            HBRUSH bb = CreateSolidBrush(t.hoverBg == t.panelBg ? t.border : (g_settings.dark ? RGB(77, 77, 77) : RGB(196, 196, 196)));
            HGDIOBJ obr = SelectObject(dc, bb);
            HGDIOBJ op = SelectObject(dc, GetStockObject(NULL_PEN));
            RoundRect(dc, br.left, br.top, br.right, br.bottom, S(12), S(12));
            SelectObject(dc, op);
            SelectObject(dc, obr);
            DeleteObject(bb);
            DrawTextW(dc, cnt.c_str(), -1, &br, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        } else {
            const GitFile& f = P.st.files[(size_t)r.file];
            std::wstring path = f.path;
            size_t sl = path.find_last_of(L'/');
            std::wstring name = sl == std::wstring::npos ? path : path.substr(sl + 1);
            std::wstring dir = sl == std::wstring::npos ? L"" : path.substr(0, sl);
            char letter = Letter(f, r.group);
            int right = rc.right - S(26);
            if (i == P.hover || P.sel[(size_t)i]) right -= (int)RowActions(r).size() * S(22);
            RECT nr = {S(24), y, right, y + rh};
            SelectObject(dc, g_uiFont);
            SetTextColor(dc, letter == 'D' ? t.panelFgDim : t.panelFg);
            SIZE nsz;
            GetTextExtentPoint32W(dc, name.c_str(), (int)name.size(), &nsz);
            DrawTextW(dc, name.c_str(), -1, &nr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX);
            if (!dir.empty() && S(24) + nsz.cx + S(8) < right) {
                RECT dr = {S(24) + nsz.cx + S(6), y, right, y + rh};
                SetTextColor(dc, t.panelFgDim);
                DrawTextW(dc, dir.c_str(), -1, &dr, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX);
            }
            wchar_t ls[2] = {(wchar_t)letter, 0};
            RECT lr = {rc.right - S(22), y, rc.right - S(6), y + rh};
            SetTextColor(dc, LetterColor(letter));
            SelectObject(dc, g_uiFontBold);
            DrawTextW(dc, ls, 1, &lr, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        }
        if (i == P.hover || (!r.header && P.sel[(size_t)i])) {
            auto acts = RowActions(r);
            SelectObject(dc, g_iconFont);
            for (size_t a = 0; a < acts.size(); ++a) {
                RECT ar = ActionRect(row, (int)a, r.header);
                if (i == P.hover && acts[a] == P.hoverBtn) {
                    HBRUSH hb = CreateSolidBrush(g_settings.dark ? RGB(80, 80, 84) : RGB(205, 205, 205));
                    RECT hr = {ar.left + S(2), ar.top + S(2), ar.right - S(2), ar.bottom - S(2)};
                    FillRect(dc, &hr, hb);
                    DeleteObject(hb);
                }
                SetTextColor(dc, t.panelFg);
                DrawTextW(dc, ActionGlyph(acts[a]), 1, &ar, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            }
        }
    }
    if (P.rows.size() <= 1 && P.st.isRepo && P.st.files.empty()) {
        RECT mr = {S(24), rh + S(6), rc.right - S(8), rh * 3};
        SelectObject(dc, g_uiFont);
        SetTextColor(dc, t.panelFgDim);
        DrawTextW(dc, L"No changes.", -1, &mr, DT_LEFT | DT_TOP | DT_WORDBREAK);
    }
    BitBlt(hdc, 0, 0, rc.right, rc.bottom, dc, 0, 0, SRCCOPY);
    SelectObject(dc, ob);
    DeleteObject(bmp);
    DeleteDC(dc);
}

void SetSingleSel(int i) {
    std::fill(P.sel.begin(), P.sel.end(), false);
    if (i >= 0 && i < (int)P.sel.size()) P.sel[(size_t)i] = true;
    P.focus = P.anchor = i;
}

void EnsureVisible(int i) {
    RECT rc;
    GetClientRect(P.list, &rc);
    int vis = std::max(1, (int)(rc.bottom / RowH()));
    if (i < P.top) P.top = i;
    else if (i >= P.top + vis) P.top = i - vis + 1;
    UpdateListScroll();
}

LRESULT CALLBACK ListProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(h, &ps);
        PaintList(dc);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_ERASEBKGND: return 1;
    case WM_SIZE: UpdateListScroll(); InvalidateRect(h, nullptr, FALSE); return 0;
    case WM_SETFOCUS:
    case WM_KILLFOCUS: InvalidateRect(h, nullptr, FALSE); return 0;
    case WM_GETDLGCODE: return DLGC_WANTARROWS | DLGC_WANTCHARS;
    case WM_MOUSEMOVE: {
        int x = GET_X_LPARAM(l), y = GET_Y_LPARAM(l);
        int i = P.top + y / RowH();
        if (i >= (int)P.rows.size()) i = -1;
        int btn = HitAction(i, x, y);
        if (i != P.hover || btn != P.hoverBtn) {
            P.hover = i;
            P.hoverBtn = btn;
            InvalidateRect(h, nullptr, FALSE);
        }
        if (!P.tracking) {
            TRACKMOUSEEVENT tme = {sizeof(tme), TME_LEAVE, h, 0};
            TrackMouseEvent(&tme);
            P.tracking = true;
        }
        return 0;
    }
    case WM_MOUSELEAVE:
        P.tracking = false;
        P.hover = P.hoverBtn = -1;
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    case WM_LBUTTONDOWN:
    case WM_RBUTTONDOWN: {
        SetFocus(h);
        int x = GET_X_LPARAM(l), y = GET_Y_LPARAM(l);
        int i = P.top + y / RowH();
        if (i >= (int)P.rows.size()) return 0;
        if (m == WM_LBUTTONDOWN) {
            int act = HitAction(i, x, y);
            if (act > 0) {
                std::vector<int> target;
                if (!P.rows[(size_t)i].header && P.sel[(size_t)i]) {
                    for (int s : SelectedFileRows())
                        if (P.rows[(size_t)s].group == P.rows[(size_t)i].group) target.push_back(s);
                } else {
                    target.push_back(i);
                }
                DoAction(act, target);
                return 0;
            }
            if (P.rows[(size_t)i].header) {
                P.collapsed[P.rows[(size_t)i].group] = !P.collapsed[P.rows[(size_t)i].group];
                RebuildRows();
                return 0;
            }
            if (w & MK_CONTROL) {
                P.sel[(size_t)i] = !P.sel[(size_t)i];
                P.focus = P.anchor = i;
            } else if ((w & MK_SHIFT) && P.anchor >= 0) {
                std::fill(P.sel.begin(), P.sel.end(), false);
                for (int k = std::min(P.anchor, i); k <= std::max(P.anchor, i); ++k)
                    if (!P.rows[(size_t)k].header) P.sel[(size_t)k] = true;
                P.focus = i;
            } else {
                SetSingleSel(i);
                OpenRow(i, false);
            }
        } else if (!P.sel[(size_t)i]) {
            SetSingleSel(i);
        }
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    }
    case WM_LBUTTONDBLCLK: {
        int i = P.top + GET_Y_LPARAM(l) / RowH();
        if (i < (int)P.rows.size() && !P.rows[(size_t)i].header && HitAction(i, GET_X_LPARAM(l), GET_Y_LPARAM(l)) < 0) OpenRow(i, true);
        return 0;
    }
    case WM_CONTEXTMENU: {
        int x = GET_X_LPARAM(l), y = GET_Y_LPARAM(l);
        if (x == -1 && y == -1) {
            POINT pt = {S(30), (P.focus - P.top + 1) * RowH()};
            ClientToScreen(h, &pt);
            x = pt.x; y = pt.y;
        }
        ListContextMenu(x, y);
        return 0;
    }
    case WM_MOUSEWHEEL: {
        int d = GET_WHEEL_DELTA_WPARAM(w);
        P.top -= d / WHEEL_DELTA * 3;
        UpdateListScroll();
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    }
    case WM_VSCROLL: {
        SCROLLINFO si = {sizeof(si), SIF_ALL};
        GetScrollInfo(h, SB_VERT, &si);
        switch (LOWORD(w)) {
        case SB_LINEUP: P.top--; break;
        case SB_LINEDOWN: P.top++; break;
        case SB_PAGEUP: P.top -= (int)si.nPage; break;
        case SB_PAGEDOWN: P.top += (int)si.nPage; break;
        case SB_THUMBTRACK: case SB_THUMBPOSITION: P.top = si.nTrackPos; break;
        }
        UpdateListScroll();
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    }
    case WM_KEYDOWN: {
        int n = (int)P.rows.size();
        if (!n) return 0;
        int i = P.focus;
        if (w == VK_DOWN) i = std::min(n - 1, i + 1);
        else if (w == VK_UP) i = std::max(0, i - 1);
        else if (w == VK_HOME) i = 0;
        else if (w == VK_END) i = n - 1;
        else if (w == VK_RETURN && i >= 0) { OpenRow(i, false); return 0; }
        else if (w == VK_SPACE && i >= 0 && !P.rows[(size_t)i].header) {
            DoAction(P.rows[(size_t)i].group == G_STAGED ? 2 : 1, {i});
            return 0;
        } else if (w == 'A' && GetKeyState(VK_CONTROL) < 0) {
            for (int k = 0; k < n; ++k) P.sel[(size_t)k] = !P.rows[(size_t)k].header;
            InvalidateRect(h, nullptr, FALSE);
            return 0;
        } else return 0;
        if (GetKeyState(VK_SHIFT) < 0 && P.anchor >= 0) {
            std::fill(P.sel.begin(), P.sel.end(), false);
            for (int k = std::min(P.anchor, i); k <= std::max(P.anchor, i); ++k)
                if (!P.rows[(size_t)k].header) P.sel[(size_t)k] = true;
            P.focus = i;
        } else {
            SetSingleSel(i);
        }
        EnsureVisible(i);
        InvalidateRect(h, nullptr, FALSE);
        return 0;
    }
    }
    return DefWindowProcW(h, m, w, l);
}

LRESULT CALLBACK MsgEditSub(HWND h, UINT m, WPARAM w, LPARAM l, UINT_PTR, DWORD_PTR) {
    if (m == WM_KEYDOWN && w == VK_RETURN && GetKeyState(VK_CONTROL) < 0) {
        GitCommand(ID_GIT_COMMIT);
        return 0;
    }
    if (m == WM_KEYDOWN && w == 'A' && GetKeyState(VK_CONTROL) < 0) {
        SendMessageW(h, EM_SETSEL, 0, -1);
        return 0;
    }
    if (m == WM_CHAR && (w == 10 || w == 1) && GetKeyState(VK_CONTROL) < 0) return 0;
    LRESULT r = DefSubclassProc(h, m, w, l);
    if (m == WM_PAINT && GetWindowTextLengthW(h) == 0) {
        HDC dc = GetDC(h);
        RECT rc;
        SendMessageW(h, EM_GETRECT, 0, (LPARAM)&rc);
        HGDIOBJ o = SelectObject(dc, g_uiFont);
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, g_theme.panelFgDim);
        std::wstring ph = L"Message (Ctrl+Enter to commit";
        if (!P.st.branch.empty()) ph += L" on '" + P.st.branch + L"'";
        ph += L")";
        DrawTextW(dc, ph.c_str(), -1, &rc, DT_LEFT | DT_TOP | DT_WORDBREAK | DT_NOPREFIX);
        SelectObject(dc, o);
        ReleaseDC(h, dc);
    }
    if (m == WM_SETFOCUS || m == WM_KILLFOCUS || (m == WM_CHAR) || m == WM_KEYUP) InvalidateRect(h, nullptr, FALSE);
    return r;
}

void Layout() {
    RECT rc;
    GetClientRect(P.hwnd, &rc);
    int W = rc.right, pad = S(8), hh = S(30), bw = S(26);
    MoveWindow(P.refresh, W - pad - 2 * bw, S(3), bw, bw - S(2), TRUE);
    MoveWindow(P.more, W - pad - bw, S(3), bw, bw - S(2), TRUE);
    bool repo = P.st.isRepo;
    bool folder = !App::Folder().empty();
    int y = hh;
    ShowWindow(P.msg, repo ? SW_SHOW : SW_HIDE);
    ShowWindow(P.commit, repo ? SW_SHOW : SW_HIDE);
    ShowWindow(P.quick, repo ? SW_SHOW : SW_HIDE);
    ShowWindow(P.list, repo ? SW_SHOW : SW_HIDE);
    ShowWindow(P.init, !repo && folder ? SW_SHOW : SW_HIDE);
    ShowWindow(P.publish, !repo && folder ? SW_SHOW : SW_HIDE);
    ShowWindow(P.openFolder, !folder ? SW_SHOW : SW_HIDE);
    ShowWindow(P.clone, !repo ? SW_SHOW : SW_HIDE);
    if (repo) {
        int mh = S(66);
        MoveWindow(P.msg, pad, y, W - 2 * pad, mh, TRUE);
        y += mh + S(6);
        int ch = S(28), mw = S(28);
        MoveWindow(P.commit, pad, y, W - 2 * pad - mw - S(2), ch, TRUE);
        MoveWindow(P.commitMenu, W - pad - mw, y, mw, ch, TRUE);
        y += ch + S(8);
        MoveWindow(P.quick, pad, y, W - 2 * pad, ch, TRUE);
        y += ch + S(8);
        MoveWindow(P.list, 0, y, W, std::max(0, (int)rc.bottom - y), TRUE);
    } else {
        y += S(64);
        int bh = S(28);
        HWND order[] = {P.openFolder, P.init, P.publish, P.clone};
        for (HWND b : order) {
            if (!IsWindowVisible(b)) continue;
            MoveWindow(b, pad, y, W - 2 * pad, bh, TRUE);
            y += bh + S(8);
        }
    }
    InvalidateRect(P.hwnd, nullptr, TRUE);
}

void ShowMenu(HWND anchor, bool commitMenu) {
    HMENU m = CreatePopupMenu();
    if (commitMenu) {
        AppendMenuW(m, MF_STRING, ID_GIT_COMMIT, L"Commit");
        AppendMenuW(m, MF_STRING, ID_GIT_COMMIT_STAGED, L"Commit Staged");
        AppendMenuW(m, MF_STRING, ID_GIT_COMMIT_ALL, L"Commit All");
        AppendMenuW(m, MF_STRING, ID_GIT_COMMIT_AMEND, L"Commit (Amend)");
        AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(m, MF_STRING, ID_GIT_UNDO_COMMIT, L"Undo Last Commit");
        AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(m, MF_STRING, ID_GIT_PUSH, L"Push");
        AppendMenuW(m, MF_STRING, ID_GIT_SYNC, L"Sync (Pull + Push)");
    } else {
        AppendMenuW(m, MF_STRING, ID_GIT_PULL, L"Pull");
        AppendMenuW(m, MF_STRING, ID_GIT_PUSH, L"Push");
        AppendMenuW(m, MF_STRING, ID_GIT_SYNC, L"Sync");
        AppendMenuW(m, MF_STRING, ID_GIT_FETCH, L"Fetch");
        AppendMenuW(m, MF_STRING, ID_GIT_PUSH_FORCE, L"Force Push (with lease)...");
        AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(m, MF_STRING, ID_GIT_CHECKOUT, L"Checkout to...");
        AppendMenuW(m, MF_STRING, ID_GIT_BRANCH_CREATE, L"Create Branch...");
        AppendMenuW(m, MF_STRING, ID_GIT_BRANCH_DELETE, L"Delete Branch...");
        AppendMenuW(m, MF_STRING, ID_GIT_MERGE, L"Merge Branch...");
        AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(m, MF_STRING, ID_GIT_STAGE_ALL, L"Stage All Changes");
        AppendMenuW(m, MF_STRING, ID_GIT_UNSTAGE_ALL, L"Unstage All Changes");
        AppendMenuW(m, MF_STRING, ID_GIT_DISCARD_ALL, L"Discard All Changes...");
        AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(m, MF_STRING, ID_GIT_STASH, L"Stash...");
        AppendMenuW(m, MF_STRING, ID_GIT_STASH_POP, L"Pop Stash...");
        AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(m, MF_STRING, ID_GIT_REMOTE_ADD, L"Add Remote...");
        AppendMenuW(m, MF_STRING, ID_GIT_REMOTE_REMOVE, L"Remove Remote...");
        AppendMenuW(m, MF_STRING, ID_GIT_PUBLISH, L"Publish to GitHub...");
        AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(m, MF_STRING, ID_GIT_LOG, L"View History");
        AppendMenuW(m, MF_STRING, ID_VIEW_OUTPUT, L"Show Git Output");
    }
    RECT r;
    GetWindowRect(anchor, &r);
    int cmd = TrackPopupMenu(m, TPM_RETURNCMD | TPM_RIGHTALIGN, r.right, r.bottom, 0, P.hwnd, nullptr);
    DestroyMenu(m);
    if (cmd == ID_VIEW_OUTPUT) App::ShowOutput();
    else if (cmd) GitCommand(cmd);
}

LRESULT CALLBACK PanelProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_SIZE: Layout(); return 0;
    case WM_ERASEBKGND: {
        RECT rc;
        GetClientRect(h, &rc);
        HBRUSH b = CreateSolidBrush(g_theme.panelBg);
        FillRect((HDC)w, &rc, b);
        DeleteObject(b);
        return 1;
    }
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(h, &ps);
        RECT rc;
        GetClientRect(h, &rc);
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, g_theme.panelFgDim);
        HGDIOBJ o = SelectObject(dc, g_uiFont);
        RECT tr = {S(10), 0, rc.right - S(70), S(30)};
        DrawTextW(dc, L"SOURCE CONTROL", -1, &tr, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
        if (!P.st.isRepo) {
            RECT mr = {S(10), S(36), rc.right - S(10), S(36) + S(60)};
            SetTextColor(dc, g_theme.panelFg);
            const wchar_t* msg = App::Folder().empty()
                ? L"Open a folder to use git features, or clone a repository."
                : L"The current folder is not a git repository. Initialize a repository or publish it directly to GitHub.";
            DrawTextW(dc, msg, -1, &mr, DT_LEFT | DT_TOP | DT_WORDBREAK);
        }
        SelectObject(dc, o);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_CTLCOLOREDIT: {
        HDC dc = (HDC)w;
        SetTextColor(dc, g_theme.panelFg);
        SetBkColor(dc, g_theme.inputBg);
        return (LRESULT)P.inputBrush;
    }
    case WM_DRAWITEM: {
        auto* dis = (DRAWITEMSTRUCT*)l;
        DrawFlatButton(dis, dis->CtlID == IDC_COMMIT || dis->CtlID == IDC_COMMITMENU || dis->CtlID == IDC_QUICK ||
                                dis->CtlID == IDC_INIT || dis->CtlID == IDC_OPENFOLDER || dis->CtlID == IDC_PUBLISH ||
                                dis->CtlID == IDC_CLONE);
        return TRUE;
    }
    case WM_COMMAND:
        switch (LOWORD(w)) {
        case IDC_COMMIT: GitCommand(ID_GIT_COMMIT); break;
        case IDC_COMMITMENU: ShowMenu(P.commitMenu, true); break;
        case IDC_QUICK: GitCommand(ID_GIT_QUICK_COMMIT); break;
        case IDC_MORE: ShowMenu(P.more, false); break;
        case IDC_REFRESH: App::RefreshGit(); break;
        case IDC_INIT: GitCommand(ID_GIT_INIT); break;
        case IDC_CLONE: GitCommand(ID_GIT_CLONE); break;
        case IDC_PUBLISH: GitCommand(ID_GIT_PUBLISH); break;
        case IDC_OPENFOLDER: SendMessageW(App::Main(), WM_COMMAND, ID_FILE_OPENFOLDER, 0); break;
        }
        return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

void AddTip(HWND tip, HWND ctl, const wchar_t* text) {
    TOOLINFOW ti = {sizeof(ti)};
    ti.uFlags = TTF_IDISHWND | TTF_SUBCLASS;
    ti.hwnd = P.hwnd;
    ti.uId = (UINT_PTR)ctl;
    ti.lpszText = (LPWSTR)text;
    SendMessageW(tip, TTM_ADDTOOLW, 0, (LPARAM)&ti);
}

}  // namespace

namespace Scm {

HWND Create(HWND parent) {
    WNDCLASSEXW wc = {sizeof(wc)};
    wc.lpfnWndProc = PanelProc;
    wc.hInstance = g_hinst;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = L"MyScmPanel";
    RegisterClassExW(&wc);
    wc.lpfnWndProc = ListProc;
    wc.style = CS_DBLCLKS;
    wc.lpszClassName = L"MyScmList";
    RegisterClassExW(&wc);

    P.inputBrush = CreateSolidBrush(g_theme.inputBg);
    P.hwnd = CreateWindowExW(0, L"MyScmPanel", L"", WS_CHILD | WS_CLIPCHILDREN, 0, 0, 0, 0, parent, nullptr, g_hinst, nullptr);
    P.msg = CreateWindowExW(0, L"EDIT", L"", WS_CHILD | ES_MULTILINE | ES_AUTOVSCROLL | ES_WANTRETURN | WS_TABSTOP,
                            0, 0, 0, 0, P.hwnd, (HMENU)IDC_MSG, g_hinst, nullptr);
    SendMessageW(P.msg, WM_SETFONT, (WPARAM)g_uiFont, FALSE);
    SendMessageW(P.msg, EM_SETMARGINS, EC_LEFTMARGIN | EC_RIGHTMARGIN, MAKELPARAM(S(4), S(4)));
    SetWindowSubclass(P.msg, MsgEditSub, 1, 0);
    P.commit = MakeButton(P.hwnd, IDC_COMMIT, L"\x2713  Commit");
    P.commitMenu = MakeButton(P.hwnd, IDC_COMMITMENU, kIcoDown);
    P.quick = MakeButton(P.hwnd, IDC_QUICK, L"Quick Commit");
    P.refresh = MakeButton(P.hwnd, IDC_REFRESH, kIcoRefresh);
    P.more = MakeButton(P.hwnd, IDC_MORE, kIcoMore);
    P.init = MakeButton(P.hwnd, IDC_INIT, L"Initialize Repository");
    P.publish = MakeButton(P.hwnd, IDC_PUBLISH, L"Publish to GitHub");
    P.clone = MakeButton(P.hwnd, IDC_CLONE, L"Clone Repository");
    P.openFolder = MakeButton(P.hwnd, IDC_OPENFOLDER, L"Open Folder");
    P.list = CreateWindowExW(0, L"MyScmList", L"", WS_CHILD | WS_VSCROLL | WS_TABSTOP, 0, 0, 0, 0, P.hwnd, nullptr, g_hinst, nullptr);
    HWND tip = CreateWindowExW(WS_EX_TOPMOST, TOOLTIPS_CLASSW, nullptr, WS_POPUP | TTS_ALWAYSTIP | TTS_NOPREFIX, 0, 0, 0, 0,
                               P.hwnd, nullptr, g_hinst, nullptr);
    AddTip(tip, P.refresh, L"Refresh");
    AddTip(tip, P.more, L"More Actions...");
    AddTip(tip, P.commitMenu, L"More Commit Actions...");
    AddTip(tip, P.quick, L"Commit all changes as \"Update\", or push when there is nothing to commit");
    Layout();
    return P.hwnd;
}

void Update(const GitStatus& s) {
    bool wasRepo = P.st.isRepo;
    P.st = s;
    SetWindowTextW(P.commit, L"\x2713  Commit");
    SetWindowTextW(P.quick, s.files.empty() ? L"Push" : L"Quick Commit");
    RebuildRows();
    if (wasRepo != s.isRepo || true) Layout();
    InvalidateRect(P.msg, nullptr, FALSE);
}

std::wstring Message() {
    int n = GetWindowTextLengthW(P.msg);
    std::wstring s((size_t)n + 1, L'\0');
    GetWindowTextW(P.msg, &s[0], n + 1);
    s.resize((size_t)n);
    return s;
}

void SetMessage(const std::wstring& m) {
    SetWindowTextW(P.msg, m.c_str());
    InvalidateRect(P.msg, nullptr, TRUE);
}

void FocusMessage() { SetFocus(P.msg); }

void ThemeChanged() {
    if (P.inputBrush) DeleteObject(P.inputBrush);
    P.inputBrush = CreateSolidBrush(g_theme.inputBg);
    SetWindowTheme(P.list, g_settings.dark ? L"DarkMode_Explorer" : L"Explorer", nullptr);
    SetWindowTheme(P.msg, g_settings.dark ? L"DarkMode_Explorer" : L"Explorer", nullptr);
    RedrawWindow(P.hwnd, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_FRAME);
}

}  // namespace Scm
