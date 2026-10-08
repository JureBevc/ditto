// Internal state shared by main_window.cpp, main_commands.cpp and find_bar.cpp.
#pragma once
#include "app.h"
#include "editor_view.h"

struct Tab {
    enum Kind { Editor, Diff, Log } kind = Editor;
    HWND hwnd = nullptr;
    EditorView* ed = nullptr;
    std::wstring key;
};

struct MainState {
    HWND hwnd = nullptr;
    HWND explorer = nullptr, scm = nullptr, findBar = nullptr, output = nullptr;
    HMENU menu = nullptr;
    HACCEL accel = nullptr;
    HFONT symFont = nullptr;
    HBRUSH outputBrush = nullptr;

    std::vector<Tab> tabs;
    int active = -1;
    int untitled = 1;
    std::wstring folder;
    GitStatus status;
    bool gitPending = false, gitQueued = false;
    std::wstring statusText;
    int sidebarMode = 0;  // 0 explorer, 1 source control

    // geometry (client coordinates)
    RECT rcSideHeader{}, rcSideBody{}, rcSplitter{}, rcTabs{}, rcContent{}, rcOutputHeader{}, rcOutput{}, rcStatus{};
    RECT rcPanelSplit{};  // drag handle on the top edge of the bottom panel
    enum PanelTab { PanelOutput, PanelTerminal } panelTab = PanelOutput;
    struct PanelItem { RECT rc; enum Kind { OutputTab, TerminalTab, Group, NewTerm, SplitTerm, KillTerm, ClosePanel } kind; int group; };
    std::vector<PanelItem> panelItems;
    HWND lastFocus = nullptr;  // restored when the window is re-activated
    std::vector<RECT> tabRects;  // parallel to tabs (empty rect = not visible)
    RECT rcTabOverflow{};
    int tabFirst = 0;
    int hoverTab = -1;
    bool hoverClose = false;
    bool draggingSplit = false, draggingPanel = false;
    bool tracking = false;
    struct StatusItem { RECT rc; int cmd; };
    std::vector<StatusItem> statusItems;
    std::vector<std::pair<RECT, std::wstring>> welcomeLinks;
};
extern MainState M;

// main_window.cpp
void Layout();
void InvalidateChrome();  // tab strip + status bar + side header
void UpdateTitle();
void ActivateTab(int i);
void AddTab(const Tab& t);
bool CloseTab(int i);  // false if cancelled
EditorView* ActiveEditor();
void RecreateFonts();
void ApplyThemeAll();

void TerminalsChanged();  // terminal.cpp -> main: groups/panes changed, re-layout
void ShowPanel(MainState::PanelTab tab);
void HidePanel();

// terminal.cpp
namespace Term {
int GroupCount();
int ActiveGroup();
std::wstring GroupTitle(int i);
void SelectGroup(int i);
void New();         // new terminal in its own group
void Split();       // new terminal beside the active one
void KillActive();  // kill the active pane
void KillGroup(int i);
void Layout(const RECT& rc, bool visible);  // place the active group's panes (main-window client coordinates)
bool Focus();       // focus the active pane; false if there is none
bool HasFocus();
bool WantsKey(const MSG& msg);  // keystroke should go to a focused terminal instead of the accelerators
void FontChanged();
void ThemeChanged();
void CloseAll();
}

// main_commands.cpp
void OnCommand(int id);
HMENU BuildMenu();
HACCEL BuildAccel();
bool SaveTab(int i, bool saveAs);
bool ConfirmCloseAll();

// find_bar.cpp
namespace FindBar {
HWND Create(HWND parent);
void Show(bool replace);
void Hide();
bool Visible();
int Height();
FindQuery Query();
std::string Replacement();
void SetMessage(const std::wstring& m);
void ThemeChanged();
void FocusFind();
}

// palette.cpp
namespace Palette {
void Show(const std::wstring& prefix);  // ">" commands, "" files, ":" go to line
void ShowRecent();                      // pick a recent folder to open
void Hide(bool restoreFocus = true);
void Refresh();
}
