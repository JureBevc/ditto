# C++ → Python port conventions

The Python port in this folder is an **exact, behavior-for-behavior port** of `../src` (Win32 C++).
It uses only the Python standard library + `ctypes` calling Win32 directly. Run with `python ditto.py`
(or `pythonw ditto.py` for no console). Target: Python 3.11+ 64-bit on Windows 10/11.

## Module mapping

| C++ | Python |
|---|---|
| common.h, util.cpp | `common.py` (done) — import as `import common as C`; also does `from win32 import *` |
| (Win32 API) | `win32.py` (done) — structs, constants, functions, helpers |
| app.h | `app.py` (done): `DiffSpec`, `App::X` → `app.X(...)` (forwards to `main_window.App_X`) |
| app.h `Scm::`, scm_panel.cpp | `scm_panel.py` |
| app.h `Explorer::`, explorer.cpp | `explorer.py` |
| app.h `DiffView::`, diff_view.cpp | `diff_view.py` |
| app.h `LogView::`, log_view.cpp | `log_view.py` |
| app.h git_commands decls, git_commands.cpp | `git_commands.py` |
| app.h `GitPublish`, github.cpp | `github.py` |
| git.h, git.cpp | `git.py` |
| text_buffer.h/.cpp | `text_buffer.py` |
| md_highlight.h/.cpp | `md_highlight.py` |
| editor_view.h, editor_view.cpp, editor_input.cpp | `editor_view.py` (class `EditorView`, plus `FindQuery`, `g_ef`, `CreateEditorFonts`, `SearchBuffer`) |
| main_window.h (`Tab`, `MainState M`, free functions), main_window.cpp | `main_window.py` |
| main_window.h `Term::`, terminal.cpp | `terminal.py` |
| main_commands.cpp | `main_commands.py` |
| main_window.h `FindBar::`, find_bar.cpp | `find_bar.py` |
| main_window.h `Palette::`, palette.cpp | `palette.py` |
| dialogs.h/.cpp | `dialogs.py` |
| resource.h | `resource.py` (done) |
| main.cpp | `ditto.py` |

## Naming rules (so modules written in parallel agree)

* **Keep every C++ identifier name exactly**: functions, methods, classes, members (including trailing `_`),
  constants, enum values. `Scm::Update(s)` → `scm_panel.Update(s)`; `Term::New()` → `terminal.New()`;
  `FindBar::Show(r)` → `find_bar.Show(r)`; `Palette::Show(p)` → `palette.Show(p)`; `App::OpenFile` → `app.OpenFile`.
  Free functions declared in `main_window.h` live in `main_window.py` (`main_window.Layout()`, `main_window.M`, ...),
  except those whose comment says another file (`OnCommand/BuildMenu/BuildAccel/SaveTab/ConfirmCloseAll` →
  `main_commands.py`).
* Struct/class fields keep their names. Enums inside classes become class attributes
  (`Tab.Editor`, `DiffSpec.Commit`, `MainState.PanelOutput`, `MainState.PanelItem.OutputTab`).
* Default arguments keep their defaults.
* Import other project modules as modules (`import git`, `import app`, `import main_window as MW`) and access
  attributes at call time — circular imports are fine that way. Never `from X import name` for mutable globals.

## Types

* `std::wstring` → `str`. `std::string` (UTF-8 bytes / file bytes / git output) → `bytes`.
  `Utf8ToWide(bytes)->str`, `WideToUtf8(str)->bytes` in common.
* `uint64_t` sentinel `UINT64_MAX` → `W.UINT64_MAX` (`win32.UINT64_MAX`, also in `common`).
  Use plain Python ints; mask where C++ relies on unsigned wraparound.
* `HWND`/handles → int or None (ctypes `c_void_p` return values). Compare with `not h` for null.
* `RECT`/`POINT` → `win32.RECT`/`POINT` ctypes structs (RECT has `.copy()`). `PtInRect(rect, pt)` helper exists.
* `std::function` → Python callable. `std::atomic<bool>` → a `threading.Event` or a 1-element list / simple attribute
  (GIL makes simple attribute reads/writes atomic).
* `std::thread` → `threading.Thread(daemon=True)`.

## Out-parameters

A C++ function with output (or in/out) reference/pointer parameters returns a **tuple**
`(return_value, out1, out2, ...)` in parameter order; in/out params are also passed in as normal args.
`void` functions with outs return the single out (or a tuple of outs).
Examples:
* `bool InputBox(owner, title, prompt, std::wstring& value, bool password=false)` → `ok, value = dialogs.InputBox(owner, title, prompt, value, password=False)`
* `static EditorView* Create(HWND parent, const std::wstring& path, std::wstring& err)` → `ed, err = EditorView.Create(parent, path)`
* `bool TextBuffer::Load(path, notify, std::wstring& err)` → `ok, err = buf.Load(path, notify)`
* `bool Undo(EditInfo& info, uint64_t& caret)` → `ok, info, caret = buf.Undo()`
* `void RowToLine(row, uint64_t* line, uint64_t* sub)` → `line, sub = self.RowToLine(row)`
* `bool GetClipboardText(owner, std::wstring& text)` → `C.GetClipboardText(owner)` returns `str` or `None` (exception, already done).
* `int Utf8Decode(p, n, uint32_t* cp)` → `C.Utf8Decode(buf, i, n)` returns `(consumed, cp)` (already done).

## Win32 specifics

* Window procedures: register with `C.RegisterWindowClass(name, pyproc, style=..., cursor=IDC_xxx, background=...)`.
  The Python proc returns an int (None → 0). Exceptions are caught and printed, then `DefWindowProcW` is used.
* Per-window C++ objects stored via `GWLP_USERDATA` → keep a module-level `dict` hwnd → object instead
  (remove it on `WM_NCDESTROY`). Never store Python objects as raw pointers.
* Subclassing: `C.Subclass(hwnd, proc, uid, ref)`; proc signature `(h, m, wp, lp, uid, ref)` and call `DefSubclassProc`.
* Any callback type you hand to Windows (WNDPROC, DLGPROC, WNDENUMPROC, etc.) must be kept alive: `C.keep(cb)`.
* Messages that carry a pointer in C++ (`WM_APP_RUNUI`, `WM_APP_FINDDONE`, ...) carry an integer id instead:
  `C.RunOnUi(fn)` is done; for others use `k = C.PutHandoff(obj)` / `obj = C.TakeHandoff(k)`.
* Structs pointed to by `lParam`: `W.DRAWITEMSTRUCT.from_address(lp)` (or `lparam_struct(lp, cls)`).
* Win32 string length args count UTF-16 units: use `wstr_len(s)` / the `draw_text`, `text_extent`,
  `ext_text_out`, `text_of`, `fill_solid`, `frame_solid` helpers in win32.py.
* Struct pointer parameters are declared `c_void_p`: pass `byref(struct)`.
* If you need a Win32 function/constant/struct that win32.py lacks, **do not edit win32.py** (other agents are editing
  in parallel). Declare it at the top of your own module, e.g.
  `W._f(W.user32, "GetMenuItemRect", W.BOOL, W.HWND, W.HMENU, W.UINT, c_void_p)` (this also adds it to win32's namespace).
* Dialog resources (`IDD_*` in res/app.rc) don't exist without an .rc compile: `dialogs.py` builds the same
  DLGTEMPLATE in memory and uses `DialogBoxIndirectParamW`.
* Common Controls v6 is enabled by `ditto.py` activating `res/app.manifest` as an activation context
  before creating any window.
* ExeDir() is the folder containing ditto.py (ditto.ini lives there).

## Style

Match the C++ structure function-for-function, keep its comments, keep the same order of definitions where
practical. Don't "improve" behavior. Keep performance in mind for hot paths (painting, buffer scans): prefer
`bytes.find/count/rfind`, slicing, and `memoryview` over per-byte Python loops where it doesn't change behavior.
