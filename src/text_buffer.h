// Piece-table text buffer over a memory-mapped (or heap-loaded) file with a
// block-based newline index, so multi-GB files open instantly and edits are cheap.
#pragma once
#include "common.h"

// Newline index over a byte array: cum[k] = number of '\n' in [0, k*kBlock).
// Only full blocks are indexed; the tail is scanned on demand.
class NlIndex {
public:
    static const uint64_t kBlock = 1 << 16;
    void Reset(uint64_t expectedLen);
    uint64_t Prefix(const char* d, uint64_t x) const;                 // newlines in [0, x)
    uint64_t Count(const char* d, uint64_t a, uint64_t b) const;      // newlines in [a, b)
    uint64_t FindNth(const char* d, uint64_t a, uint64_t b, uint64_t n) const;  // pos of n-th '\n' in [a,b)
    void Extend(const char* d, uint64_t newLen);                      // single-threaded growth
    uint64_t Done() const { return done_.load(std::memory_order_acquire); }
    // used by the background indexer
    std::vector<uint64_t> cum;
    std::atomic<uint64_t> done_{0};
};

uint64_t CountNewlines(const char* d, uint64_t a, uint64_t b);

enum class Encoding { Utf8, Utf8Bom, Utf16LE, Utf16BE };

struct Piece {
    uint64_t start, len, nl;
    uint32_t src;  // 0 = original, 1 = add buffer
};

struct EditInfo {
    uint64_t off = 0, delLen = 0, insLen = 0;
    uint64_t line = 0, linesDel = 0, linesIns = 0;  // first affected line and newline counts
    bool whole = false;                              // whole document replaced
};

class TextBuffer {
public:
    TextBuffer();
    ~TextBuffer();
    TextBuffer(const TextBuffer&) = delete;
    TextBuffer& operator=(const TextBuffer&) = delete;

    bool Load(const std::wstring& path, HWND notify, std::wstring& err);
    void LoadEmpty();
    // progress (optional) receives the number of bytes written so far; safe to read from another thread.
    bool Save(const std::wstring& path, std::wstring& err, std::atomic<uint64_t>* progress = nullptr);
    bool ChangedOnDisk() const;

    bool Ready() const { return ready_; }
    double Progress() const;
    void OnIndexed();  // UI thread: finalize after background indexing

    uint64_t Length() const { return total_; }
    uint64_t LineCount() const;
    uint64_t LineStart(uint64_t line) const;
    uint64_t LineEnd(uint64_t line) const;      // excluding line terminator
    uint64_t LineNext(uint64_t line) const;     // start of next line (or Length)
    uint64_t LineOf(uint64_t off) const;
    void Read(uint64_t off, uint64_t len, std::string& out) const;
    char At(uint64_t off) const;
    const char* Chunk(uint64_t off, uint64_t* avail) const;  // contiguous bytes at off
    uint64_t MaxLineLen() const { return maxLineLen_; }

    // Editing (only when Ready()). mergeTyping coalesces consecutive single inserts into one undo step.
    EditInfo Replace(uint64_t off, uint64_t delLen, const char* s, size_t n, uint64_t caretBefore, bool mergeTyping = false);
    EditInfo ReplaceMany(const std::vector<uint64_t>& offs, uint64_t len, const std::string& repl, uint64_t caretBefore);
    void BreakUndoMerge() { mergeOpen_ = false; }
    bool CanUndo() const { return undoPos_ > 0; }
    bool CanRedo() const { return undoPos_ < undo_.size(); }
    bool Undo(EditInfo& info, uint64_t& caret);
    bool Redo(EditInfo& info, uint64_t& caret);
    bool Modified() const { return undoPos_ != savePos_; }
    uint64_t Version() const { return version_; }

    Encoding encoding = Encoding::Utf8;
    bool crlf = true;
    bool mapped = false;
    std::wstring path;

private:
    struct Rec {
        uint64_t off;
        std::vector<Piece> removed, inserted;
        uint64_t insLen, delLen;
        uint64_t caretBefore, caretAfter;
    };
    const char* Src(uint32_t s) const { return s ? add_.data() : orig_; }
    const NlIndex& Idx(uint32_t s) const { return s ? addIdx_ : origIdx_; }
    Piece MakePiece(uint32_t src, uint64_t start, uint64_t len) const;
    size_t PieceAt(uint64_t off) const;  // index of piece containing off (pieces_.size() if off==total)
    size_t SplitAt(uint64_t off);        // ensure a piece boundary at off; returns index of piece starting there
    std::vector<Piece> Splice(uint64_t off, uint64_t delLen, const std::vector<Piece>& ins, EditInfo& info);
    std::vector<Piece> Slice(uint64_t a, uint64_t b) const;
    void RebuildPrefix(size_t from);
    void PushRec(Rec&& r);
    void Close();
    void StatDisk();

    const char* orig_ = nullptr;
    uint64_t origLen_ = 0;
    std::string owned_;  // heap copy for small / converted files
    std::string add_;
    NlIndex origIdx_, addIdx_;
    std::vector<Piece> pieces_;
    std::vector<uint64_t> prefBytes_, prefNl_;  // size pieces_.size()+1
    uint64_t total_ = 0;
    uint64_t version_ = 1;
    uint64_t maxLineLen_ = 0;
    std::atomic<uint64_t> maxLineAtomic_{0};
    bool ready_ = false;
    std::atomic<bool> indexDone_{false};
    std::atomic<bool> stop_{false};
    std::thread indexer_;

    std::vector<Rec> undo_;
    size_t undoPos_ = 0, savePos_ = 0;
    bool mergeOpen_ = false;

    HANDLE file_ = INVALID_HANDLE_VALUE, map_ = nullptr;
    const char* view_ = nullptr;
    std::vector<std::wstring> pendingDelete_;
    FILETIME diskTime_{};
    uint64_t diskSize_ = 0;
};
