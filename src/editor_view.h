// Custom-drawn text editor control. Only visible rows are measured/highlighted/drawn.
#pragma once
#include "common.h"
#include "text_buffer.h"
#include "md_highlight.h"

struct EditorFonts {
    HFONT normal = nullptr, bold = nullptr, italic = nullptr;
    int cw = 8, lh = 16, ascent = 12;
};
extern EditorFonts g_ef;
void CreateEditorFonts();  // from g_settings.fontName/fontSize and g_dpi

struct FindQuery {
    std::string needle;
    bool matchCase = false;
    bool wholeWord = false;
};
// Search helpers shared with other components; return UINT64_MAX when not found.
uint64_t SearchBuffer(const TextBuffer& buf, const FindQuery& q, uint64_t start, bool forward, const std::atomic<bool>* cancel);

class EditorView {
public:
    static void Register();
    // path empty => new untitled document. Returns nullptr (and err) if the file can't be opened.
    static EditorView* Create(HWND parent, const std::wstring& path, std::wstring& err);
    static EditorView* From(HWND h);
    ~EditorView();

    HWND hwnd = nullptr;
    std::unique_ptr<TextBuffer> buf;
    bool markdown = false;
    std::wstring untitledName;
    std::wstring findMessage;  // last find result message for the find bar

    std::wstring Title() const;
    void SetPath(const std::wstring& path);
    bool Save(const std::wstring& path, std::wstring& err);
    bool Reload(std::wstring& err);

    void Undo();
    void Redo();
    void Cut();
    void Copy();
    void Paste();
    void SelectAll();
    void GotoLine(uint64_t line, bool center = true);  // 0-based
    void SetWrap(bool on);
    bool WrapActive() const { return wrap_; }
    void FontChanged();

    void Find(const FindQuery& q, bool forward, bool incremental = false);  // incremental: search from selection start
    bool ReplaceOne(const FindQuery& q, const std::string& repl);
    uint64_t ReplaceAll(const FindQuery& q, const std::string& repl);
    std::string SelectionForFind() const;
    bool Searching() const { return searching_; }
    double SaveProgress() const { return saveProgress_; }  // -1 when not saving

    uint64_t CaretLine() const;
    uint64_t CaretCol() const;
    uint64_t SelectionLength() const { return caret_ > anchor_ ? caret_ - anchor_ : anchor_ - caret_; }

private:
    static LRESULT CALLBACK WndProc(HWND, UINT, WPARAM, LPARAM);
    LRESULT Handle(UINT, WPARAM, LPARAM);

    // geometry / rows
    int TextLeft() const { return gutterW_ + g_ef.cw / 2; }
    int PageRows() const { return std::max(1, clientH_ / g_ef.lh); }
    int VisibleCols() const { return std::max(1, (clientW_ - TextLeft()) / g_ef.cw); }
    uint64_t RowCount() const;
    void RowToLine(uint64_t row, uint64_t* line, uint64_t* sub) const;
    uint64_t LineFirstRow(uint64_t line) const;
    void Segments(uint64_t line, uint64_t s, uint64_t e, std::vector<uint64_t>& segs);  // absolute seg starts
    void OffToRowCol(uint64_t off, uint64_t* row, uint64_t* col);
    uint64_t RowColToOff(uint64_t row, uint64_t col);
    uint64_t HitTest(int x, int y);

    // columns
    uint64_t ScanCol(uint64_t lineEnd, uint64_t off, uint64_t col, uint64_t stopOff, uint64_t stopCol, uint64_t* outOff);
    void ColStart(uint64_t s, uint64_t e, uint64_t targetOff, uint64_t targetCol, uint64_t* off, uint64_t* col);
    uint64_t ColAt(uint64_t s, uint64_t e, uint64_t off);
    uint64_t OffAt(uint64_t s, uint64_t e, uint64_t col);

    // wrap
    void RecomputeWrapAll();
    void UpdateWrapAfterEdit(const EditInfo& info);
    void RebuildRowPrefix(uint64_t fromLine);
    void ForEachLine(uint64_t from, uint64_t to, const std::function<void(uint64_t, const char*, size_t, bool)>& fn);

    // painting / scrolling
    void Paint(HDC dc);
    void UpdateScrollbars();
    void UpdateCaretPos();
    void EnsureCaretVisible(bool center = false);
    void ScrollToRow(int64_t row);
    void Notify();
    void UpdateGutter();

    // editing (editor_input.cpp)
    bool CanEdit() const { return buf->Ready() && !searching_ && !saving_; }
    void InsertText(const std::string& s, bool typing);
    void DeleteRange(uint64_t a, uint64_t b);
    void ReplaceLines(uint64_t l0, uint64_t l1, const std::function<std::string(const std::string&)>& fn);
    void AfterEdit(const EditInfo& info);
    void OnChar(wchar_t ch);
    void OnKeyDown(WPARAM vk);
    void MoveCaret(uint64_t pos, bool extend);
    uint64_t PrevChar(uint64_t off) const;
    uint64_t NextChar(uint64_t off) const;
    uint64_t WordLeft(uint64_t off) const;
    uint64_t WordRight(uint64_t off) const;
    void SelectWordAt(uint64_t off);
    std::string EolStr() const { return buf->crlf ? "\r\n" : "\n"; }
    void ContextMenu(int x, int y);
    void OnFindDone(uint64_t result, size_t len);

    uint64_t caret_ = 0, anchor_ = 0;
    uint64_t firstRow_ = 0, scrollCol_ = 0;
    int64_t desiredCol_ = -1;
    int clientW_ = 0, clientH_ = 0, gutterW_ = 40;
    int wheelAccum_ = 0;
    int64_t sbScale_ = 1;
    bool dragging_ = false;
    bool dragLines_ = false;
    uint64_t dragOrigin_ = 0;
    uint64_t maxCols_ = 0;
    wchar_t pendingHigh_ = 0;

    bool wrapWanted_ = false, wrap_ = false;
    int wrapCols_ = 80;
    std::vector<uint32_t> rowsPerLine_;
    std::vector<uint64_t> rowPrefix_;

    MdStateCache md_;
    std::string scratch_;

    struct ColCp {
        uint64_t lineStart = UINT64_MAX, version = 0;
        std::vector<std::pair<uint64_t, uint64_t>> cps;
    };
    std::vector<ColCp> colCache_;
    size_t colCacheNext_ = 0;

    std::thread findThread_;
    std::atomic<bool> findCancel_{false};
    bool searching_ = false;
    bool saving_ = false;
    double saveProgress_ = -1;
    size_t findLen_ = 0;

    HBITMAP backBmp_ = nullptr;
    int backW_ = 0, backH_ = 0;
};
