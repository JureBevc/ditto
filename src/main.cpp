// ditto entry point
#include "main_window.h"
#include <objbase.h>

HWND CreateMainWindow();

int WINAPI wWinMain(HINSTANCE hinst, HINSTANCE, PWSTR, int show) {
    g_hinst = hinst;
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
    INITCOMMONCONTROLSEX icc = {sizeof(icc), ICC_STANDARD_CLASSES | ICC_TREEVIEW_CLASSES | ICC_LISTVIEW_CLASSES | ICC_BAR_CLASSES};
    InitCommonControlsEx(&icc);
    // git must never block waiting for terminal input (there is no console)
    SetEnvironmentVariableW(L"GIT_TERMINAL_PROMPT", L"0");

    LoadSettings();
    if (g_settings.winX != CW_USEDEFAULT) {
        RECT r = {g_settings.winX, g_settings.winY, g_settings.winX + g_settings.winW, g_settings.winY + g_settings.winH};
        if (!MonitorFromRect(&r, MONITOR_DEFAULTTONULL) || g_settings.winW < 200 || g_settings.winH < 150) {
            g_settings.winX = g_settings.winY = CW_USEDEFAULT;
            g_settings.winW = 1200;
            g_settings.winH = 800;
        }
    }
    ApplyTheme(g_settings.dark);

    HWND wnd = CreateMainWindow();
    if (!wnd) return 1;
    ShowWindow(wnd, g_settings.winMax ? SW_SHOWMAXIMIZED : show);
    UpdateWindow(wnd);

    // command line: files and/or a folder
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    for (int i = 1; argv && i < argc; ++i) {
        std::wstring a = argv[i];
        if (IsDirectory(a)) App::OpenFolder(a);
        else App::OpenFile(a);
    }
    if (argv) LocalFree(argv);

    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        if (Term::WantsKey(msg) || !TranslateAcceleratorW(wnd, M.accel, &msg)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    CoUninitialize();
    return (int)msg.wParam;
}
