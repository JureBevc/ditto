// ditto - shared declarations
#pragma once
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <windowsx.h>
#include <commctrl.h>
#include <commdlg.h>
#include <shellapi.h>
#include <shlobj.h>
#include <uxtheme.h>
#include <string>
#include <vector>
#include <memory>
#include <functional>
#include <atomic>
#include <mutex>
#include <thread>
#include <algorithm>
#include <cstdint>
#include <cstring>

// ---- custom window messages ----
enum : UINT {
    WM_APP_RUNUI = WM_APP + 1,   // lParam = std::function<void()>* (main window runs + deletes)
    WM_APP_INDEXED,              // TextBuffer -> editor view: background line index finished
    WM_APP_FINDDONE,             // editor search thread finished; lParam = FindResult*
    WM_APP_DOCCHANGED,           // view -> main: caret/modified/progress changed; wParam = HWND of view
    WM_APP_TERMDATA,             // terminal reader thread -> terminal view: new output is pending
    WM_APP_TERMEXIT,             // terminal shell process exited
};

// ---- UTF-8 helpers ----
std::wstring Utf8ToWide(const char* s, size_t n);
inline std::wstring Utf8ToWide(const std::string& s) { return Utf8ToWide(s.data(), s.size()); }
std::string WideToUtf8(const wchar_t* s, size_t n);
inline std::string WideToUtf8(const std::wstring& s) { return WideToUtf8(s.data(), s.size()); }
// Decode one code point; returns bytes consumed (1 for invalid -> U+FFFD).
int Utf8Decode(const unsigned char* p, size_t n, uint32_t* cp);
inline bool IsUtf8Cont(unsigned char c) { return (c & 0xC0) == 0x80; }
int CharColumns(uint32_t cp);  // display cells: 2 for wide East Asian / emoji, else 1

// ---- theme ----
struct Theme {
    COLORREF bg, fg, gutterBg, gutterFg, gutterFgCur, curLine, selBg, caret;
    COLORREF heading, bold, italic, code, link, url, quote, listMarker, rule, fence, html;
    COLORREF addBg, delBg, addStrong, delStrong, emptyBg, hunkFg;
    COLORREF panelBg, panelFg, panelFgDim, border, accent, hoverBg, activeBg, inputBg, buttonBg, buttonFg;
    COLORREF statusBg, statusFg;
};
extern Theme g_theme;
void ApplyTheme(bool dark);

// ---- settings (ditto.ini next to the exe) ----
struct Settings {
    std::wstring fontName = L"Consolas";
    int fontSize = 11;
    bool dark = false;
    bool wrap = false;
    int tabSize = 4;
    int sidebarWidth = 260;
    bool showOutput = false;  // bottom panel (output / terminal) visible
    int panelHeight = 240;
    bool showSidebar = true;
    int winX = CW_USEDEFAULT, winY = CW_USEDEFAULT, winW = 1200, winH = 800;
    bool winMax = false;
    std::vector<std::wstring> recentFolders;
};
extern Settings g_settings;
void LoadSettings();
void SaveSettings();
void AddRecentFolder(const std::wstring& path);

// ---- globals ----
extern HINSTANCE g_hinst;
extern HWND g_mainWnd;
extern HFONT g_uiFont;
extern HFONT g_uiFontBold;
extern HFONT g_iconFont;  // Segoe MDL2 Assets (glyphs in the U+E700 private range)
extern int g_dpi;
inline int S(int v) { return MulDiv(v, g_dpi, 96); }

// ---- misc helpers ----
std::wstring ExeDir();
std::wstring FileNameOf(const std::wstring& path);
std::wstring DirOf(const std::wstring& path);
std::wstring JoinPath(const std::wstring& a, const std::wstring& b);
std::wstring ToBackslashes(std::wstring s);
std::wstring ToForwardSlashes(std::wstring s);
bool IsDirectory(const std::wstring& p);
bool FileExists(const std::wstring& p);
bool PathEqualsI(const std::wstring& a, const std::wstring& b);
std::wstring Trim(const std::wstring& s);
std::string TrimA(const std::string& s);
std::wstring LowerW(std::wstring s);
std::wstring FormatError(DWORD err);
int MsgBox(HWND owner, const std::wstring& text, UINT flags = MB_OK | MB_ICONINFORMATION);
void RunOnUi(std::function<void()> fn);
bool SetClipboardText(HWND owner, const std::wstring& text);
// Opens Explorer with `path` selected.
void RevealInExplorer(const std::wstring& path);
bool GetClipboardText(HWND owner, std::wstring& text);
std::string Base64(const std::string& in);

// Flat owner-drawn button painting (used for BS_OWNERDRAW buttons in panels)
void DrawFlatButton(const DRAWITEMSTRUCT* dis, bool primary, bool toggled = false);
HWND MakeButton(HWND parent, int id, const wchar_t* text);
