#include "text_buffer.h"
#include <emmintrin.h>
#include <intrin.h>

static const char kEmpty[1] = {0};

uint64_t CountNewlines(const char* d, uint64_t a, uint64_t b) {
    uint64_t c = 0;
    const char* p = d + a;
    const char* e = d + b;
    const __m128i nl = _mm_set1_epi8('\n');
    while (p + 16 <= e) {
        c += __popcnt((unsigned)_mm_movemask_epi8(_mm_cmpeq_epi8(_mm_loadu_si128((const __m128i*)p), nl)));
        p += 16;
    }
    while (p < e) c += (*p++ == '\n');
    return c;
}

static uint64_t ScanNth(const char* d, uint64_t s, uint64_t e, uint64_t skip) {
    const char* p = d + s;
    const char* end = d + e;
    for (;;) {
        p = (const char*)memchr(p, '\n', (size_t)(end - p));
        if (!p) return UINT64_MAX;
        if (!skip) return (uint64_t)(p - d);
        --skip;
        ++p;
    }
}

void NlIndex::Reset(uint64_t expectedLen) {
    cum.assign((size_t)(expectedLen / kBlock + 2), 0);
    done_.store(0);
}

uint64_t NlIndex::Prefix(const char* d, uint64_t x) const {
    uint64_t k = std::min<uint64_t>(x / kBlock, Done());
    return cum[(size_t)k] + CountNewlines(d, k * kBlock, x);
}

uint64_t NlIndex::Count(const char* d, uint64_t a, uint64_t b) const {
    if (b <= a) return 0;
    if (b - a <= 2 * kBlock) return CountNewlines(d, a, b);
    return Prefix(d, b) - Prefix(d, a);
}

uint64_t NlIndex::FindNth(const char* d, uint64_t a, uint64_t b, uint64_t n) const {
    if (b - a <= 2 * kBlock) return ScanNth(d, a, b, n);
    uint64_t target = Prefix(d, a) + n;
    uint64_t dn = Done();
    size_t k = (size_t)(std::upper_bound(cum.begin(), cum.begin() + (size_t)dn + 1, target) - cum.begin()) - 1;
    uint64_t start = (uint64_t)k * kBlock, skip;
    if (start < a) { start = a; skip = n; }
    else skip = target - cum[k];
    return ScanNth(d, start, b, skip);
}

void NlIndex::Extend(const char* d, uint64_t newLen) {
    uint64_t full = newLen / kBlock;
    while (Done() < full) {
        uint64_t k = Done();
        if (cum.size() < k + 2) cum.resize((size_t)k + 2);
        cum[(size_t)k + 1] = cum[(size_t)k] + CountNewlines(d, k * kBlock, (k + 1) * kBlock);
        done_.store(k + 1, std::memory_order_release);
    }
}

// ------------------------------------------------------------------------------------

TextBuffer::TextBuffer() { LoadEmpty(); }
TextBuffer::~TextBuffer() { Close(); }

void TextBuffer::Close() {
    stop_ = true;
    if (indexer_.joinable()) indexer_.join();
    stop_ = false;
    if (view_) UnmapViewOfFile(view_);
    if (map_) CloseHandle(map_);
    if (file_ != INVALID_HANDLE_VALUE) CloseHandle(file_);
    view_ = nullptr; map_ = nullptr; file_ = INVALID_HANDLE_VALUE;
    for (auto& p : pendingDelete_) DeleteFileW(p.c_str());
    pendingDelete_.clear();
    mapped = false;
    owned_.clear(); owned_.shrink_to_fit();
    add_.clear(); add_.shrink_to_fit();
    pieces_.clear();
    undo_.clear();
    undoPos_ = savePos_ = 0;
    mergeOpen_ = false;
}

void TextBuffer::LoadEmpty() {
    Close();
    orig_ = kEmpty;
    origLen_ = 0;
    origIdx_.Reset(0);
    addIdx_.Reset(0);
    prefBytes_.assign(1, 0);
    prefNl_.assign(1, 0);
    total_ = 0;
    maxLineLen_ = 0;
    ready_ = true;
    indexDone_ = true;
    encoding = Encoding::Utf8;
    crlf = true;
    path.clear();
    ++version_;
}

void TextBuffer::StatDisk() {
    WIN32_FILE_ATTRIBUTE_DATA fa;
    if (GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &fa)) {
        diskTime_ = fa.ftLastWriteTime;
        diskSize_ = ((uint64_t)fa.nFileSizeHigh << 32) | fa.nFileSizeLow;
    }
}

bool TextBuffer::ChangedOnDisk() const {
    if (path.empty()) return false;
    WIN32_FILE_ATTRIBUTE_DATA fa;
    if (!GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &fa)) return false;
    uint64_t sz = ((uint64_t)fa.nFileSizeHigh << 32) | fa.nFileSizeLow;
    return CompareFileTime(&fa.ftLastWriteTime, &diskTime_) != 0 || sz != diskSize_;
}

bool TextBuffer::Load(const std::wstring& p, HWND notify, std::wstring& err) {
    HANDLE f = CreateFileW(p.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
                           FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE && GetLastError() == ERROR_SHARING_VIOLATION)
        f = CreateFileW(p.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
                        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) { err = FormatError(GetLastError()); return false; }
    LARGE_INTEGER sz;
    GetFileSizeEx(f, &sz);
    LoadEmpty();
    path = p;
    uint64_t size = (uint64_t)sz.QuadPart;
    const uint64_t kHeapLimit = 64ull << 20;
    const char* data = kEmpty;
    if (size == 0) {
        CloseHandle(f);
    } else if (size <= kHeapLimit) {
        owned_.resize((size_t)size);
        uint64_t got = 0;
        while (got < size) {
            DWORD rd = 0;
            DWORD want = (DWORD)std::min<uint64_t>(size - got, 1u << 30);
            if (!ReadFile(f, &owned_[(size_t)got], want, &rd, nullptr) || !rd) break;
            got += rd;
        }
        CloseHandle(f);
        owned_.resize((size_t)got);
        size = got;
        data = owned_.data();
    } else {
        map_ = CreateFileMappingW(f, nullptr, PAGE_READONLY, 0, 0, nullptr);
        if (!map_) { err = FormatError(GetLastError()); CloseHandle(f); return false; }
        view_ = (const char*)MapViewOfFile(map_, FILE_MAP_READ, 0, 0, 0);
        if (!view_) { err = FormatError(GetLastError()); CloseHandle(map_); map_ = nullptr; CloseHandle(f); return false; }
        file_ = f;
        mapped = true;
        data = view_;
    }
    // encoding detection
    const unsigned char* u = (const unsigned char*)data;
    if (size >= 3 && u[0] == 0xEF && u[1] == 0xBB && u[2] == 0xBF) {
        encoding = Encoding::Utf8Bom;
        data += 3; size -= 3;
    } else if (size >= 2 && ((u[0] == 0xFF && u[1] == 0xFE) || (u[0] == 0xFE && u[1] == 0xFF))) {
        bool be = u[0] == 0xFE;
        encoding = be ? Encoding::Utf16BE : Encoding::Utf16LE;
        std::wstring w((size_t)((size - 2) / 2), L'\0');
        memcpy(&w[0], data + 2, w.size() * 2);
        if (be) for (auto& c : w) c = (wchar_t)((c >> 8) | (c << 8));
        std::string conv = WideToUtf8(w);
        if (mapped) {
            UnmapViewOfFile(view_); CloseHandle(map_); CloseHandle(file_);
            view_ = nullptr; map_ = nullptr; file_ = INVALID_HANDLE_VALUE; mapped = false;
        }
        owned_.swap(conv);
        data = owned_.data();
        size = owned_.size();
    }
    orig_ = size ? data : kEmpty;
    origLen_ = size;
    const char* nlp = (const char*)memchr(orig_, '\n', (size_t)std::min<uint64_t>(size, 1 << 16));
    if (nlp) crlf = nlp > orig_ && nlp[-1] == '\r';
    StatDisk();
    if (!size) return true;

    pieces_.push_back(Piece{0, size, 0, 0});
    prefBytes_ = {0, size};
    prefNl_ = {0, 0};
    total_ = size;
    origIdx_.Reset(size);
    ready_ = false;
    indexDone_ = false;

    auto work = [this, notify]() {
        const char* d = orig_;
        uint64_t n = origLen_, nBlocks = n / NlIndex::kBlock, count = 0, lineStart = 0, maxLen = 0;
        const __m128i nl = _mm_set1_epi8('\n');
        for (uint64_t k = 0; k <= nBlocks; ++k) {
            if (stop_) return;
            uint64_t s = k * NlIndex::kBlock, e = std::min(n, s + NlIndex::kBlock), i = s;
            for (; i + 16 <= e; i += 16) {
                unsigned m = (unsigned)_mm_movemask_epi8(_mm_cmpeq_epi8(_mm_loadu_si128((const __m128i*)(d + i)), nl));
                while (m) {
                    unsigned long bit;
                    _BitScanForward(&bit, m);
                    uint64_t pos = i + bit;
                    if (pos - lineStart > maxLen) maxLen = pos - lineStart;
                    lineStart = pos + 1;
                    ++count;
                    m &= m - 1;
                }
            }
            for (; i < e; ++i) {
                if (d[i] == '\n') {
                    if (i - lineStart > maxLen) maxLen = i - lineStart;
                    lineStart = i + 1;
                    ++count;
                }
            }
            if (e - s == NlIndex::kBlock) {
                origIdx_.cum[(size_t)k + 1] = count;
                origIdx_.done_.store(k + 1, std::memory_order_release);
            }
        }
        maxLen = std::max(maxLen, n - lineStart);
        maxLineAtomic_ = maxLen;
        indexDone_ = true;
        if (notify) PostMessageW(notify, WM_APP_INDEXED, 0, 0);
    };
    if (size <= (4u << 20)) {
        work();
        OnIndexed();
    } else {
        indexer_ = std::thread([work]() { work(); });
    }
    return true;
}

double TextBuffer::Progress() const {
    if (ready_ || !origLen_) return 1.0;
    return std::min(1.0, (double)(origIdx_.Done() * NlIndex::kBlock) / (double)origLen_);
}

void TextBuffer::OnIndexed() {
    if (ready_ || !indexDone_) return;
    if (indexer_.joinable()) indexer_.join();
    if (!pieces_.empty()) pieces_[0].nl = origIdx_.Count(orig_, 0, origLen_);
    RebuildPrefix(0);
    maxLineLen_ = maxLineAtomic_;
    ready_ = true;
    ++version_;
}

void TextBuffer::RebuildPrefix(size_t from) {
    size_t n = pieces_.size();
    prefBytes_.resize(n + 1);
    prefNl_.resize(n + 1);
    prefBytes_[0] = 0;
    prefNl_[0] = 0;
    for (size_t i = from; i < n; ++i) {
        prefBytes_[i + 1] = prefBytes_[i] + pieces_[i].len;
        prefNl_[i + 1] = prefNl_[i] + pieces_[i].nl;
    }
    total_ = prefBytes_[n];
}

uint64_t TextBuffer::LineCount() const {
    if (!ready_) return origIdx_.cum[(size_t)origIdx_.Done()] + 1;
    return prefNl_.back() + 1;
}

uint64_t TextBuffer::LineStart(uint64_t line) const {
    if (line == 0) return 0;
    if (line >= LineCount()) return total_;
    uint64_t n = line - 1;
    if (!ready_) {
        uint64_t pos = origIdx_.FindNth(orig_, 0, origLen_, n);
        return pos == UINT64_MAX ? total_ : pos + 1;
    }
    size_t i = (size_t)(std::upper_bound(prefNl_.begin(), prefNl_.end(), n) - prefNl_.begin()) - 1;
    if (i >= pieces_.size()) return total_;
    const Piece& p = pieces_[i];
    uint64_t pos = Idx(p.src).FindNth(Src(p.src), p.start, p.start + p.len, n - prefNl_[i]);
    if (pos == UINT64_MAX) return total_;
    return prefBytes_[i] + (pos - p.start) + 1;
}

uint64_t TextBuffer::LineNext(uint64_t line) const {
    if (line + 1 < LineCount()) return LineStart(line + 1);
    if (!ready_) {
        // last partially indexed line: look ahead a bounded distance
        uint64_t s = LineStart(line);
        uint64_t lim = std::min<uint64_t>(origLen_ - s, 1 << 20);
        const char* q = (const char*)memchr(orig_ + s, '\n', (size_t)lim);
        return q ? (uint64_t)(q - orig_) + 1 : s + lim;
    }
    return total_;
}

uint64_t TextBuffer::LineEnd(uint64_t line) const {
    uint64_t s = LineStart(line);
    uint64_t nx = LineNext(line);
    uint64_t e = nx;
    if (e > s && At(e - 1) == '\n') {
        --e;
        if (e > s && At(e - 1) == '\r') --e;
    }
    return e;
}

uint64_t TextBuffer::LineOf(uint64_t off) const {
    if (off > total_) off = total_;
    if (!ready_) return origIdx_.Prefix(orig_, off);
    size_t i = PieceAt(off);
    if (i >= pieces_.size()) return prefNl_.back();
    const Piece& p = pieces_[i];
    return prefNl_[i] + Idx(p.src).Count(Src(p.src), p.start, p.start + (off - prefBytes_[i]));
}

size_t TextBuffer::PieceAt(uint64_t off) const {
    if (off >= total_) return pieces_.size();
    return (size_t)(std::upper_bound(prefBytes_.begin(), prefBytes_.end(), off) - prefBytes_.begin()) - 1;
}

void TextBuffer::Read(uint64_t off, uint64_t len, std::string& out) const {
    out.clear();
    if (off >= total_) return;
    len = std::min(len, total_ - off);
    out.reserve((size_t)len);
    size_t i = PieceAt(off);
    while (len && i < pieces_.size()) {
        const Piece& p = pieces_[i];
        uint64_t within = off - prefBytes_[i];
        uint64_t take = std::min(p.len - within, len);
        out.append(Src(p.src) + p.start + within, (size_t)take);
        off += take; len -= take; ++i;
    }
}

char TextBuffer::At(uint64_t off) const {
    size_t i = PieceAt(off);
    if (i >= pieces_.size()) return 0;
    const Piece& p = pieces_[i];
    return Src(p.src)[p.start + (off - prefBytes_[i])];
}

const char* TextBuffer::Chunk(uint64_t off, uint64_t* avail) const {
    size_t i = PieceAt(off);
    if (i >= pieces_.size()) { *avail = 0; return kEmpty; }
    const Piece& p = pieces_[i];
    uint64_t within = off - prefBytes_[i];
    *avail = p.len - within;
    return Src(p.src) + p.start + within;
}

Piece TextBuffer::MakePiece(uint32_t src, uint64_t start, uint64_t len) const {
    return Piece{start, len, Idx(src).Count(Src(src), start, start + len), src};
}

size_t TextBuffer::SplitAt(uint64_t off) {
    size_t i = PieceAt(off);
    if (i >= pieces_.size() || prefBytes_[i] == off) return i;
    Piece p = pieces_[i];
    uint64_t leftLen = off - prefBytes_[i];
    Piece L = MakePiece(p.src, p.start, leftLen);
    Piece R{p.start + leftLen, p.len - leftLen, p.nl - L.nl, p.src};
    pieces_[i] = L;
    pieces_.insert(pieces_.begin() + i + 1, R);
    prefBytes_.insert(prefBytes_.begin() + i + 1, off);
    prefNl_.insert(prefNl_.begin() + i + 1, prefNl_[i] + L.nl);
    return i + 1;
}

std::vector<Piece> TextBuffer::Splice(uint64_t off, uint64_t delLen, const std::vector<Piece>& ins, EditInfo& info) {
    info = EditInfo();
    info.off = off;
    info.delLen = delLen;
    info.line = LineOf(off);
    size_t a = SplitAt(off);
    size_t b = SplitAt(off + delLen);
    std::vector<Piece> removed(pieces_.begin() + a, pieces_.begin() + b);
    for (auto& p : removed) info.linesDel += p.nl;
    pieces_.erase(pieces_.begin() + a, pieces_.begin() + b);
    pieces_.insert(pieces_.begin() + a, ins.begin(), ins.end());
    for (auto& p : ins) { info.insLen += p.len; info.linesIns += p.nl; }
    // merge contiguous neighbours around the edit
    size_t lo = a > 0 ? a - 1 : 0;
    size_t hi = std::min(pieces_.size(), a + ins.size() + 1);
    for (size_t i = lo; i + 1 < hi;) {
        Piece& x = pieces_[i];
        Piece& y = pieces_[i + 1];
        if (x.src == y.src && x.start + x.len == y.start) {
            x.len += y.len;
            x.nl += y.nl;
            pieces_.erase(pieces_.begin() + i + 1);
            --hi;
        } else {
            ++i;
        }
    }
    RebuildPrefix(lo);
    info.whole = (off == 0 && delLen > 0 && off + info.insLen == total_ && removed.size() > 1);
    ++version_;
    return removed;
}

std::vector<Piece> TextBuffer::Slice(uint64_t a, uint64_t b) const {
    std::vector<Piece> out;
    size_t i = PieceAt(a);
    while (a < b && i < pieces_.size()) {
        const Piece& p = pieces_[i];
        uint64_t within = a - prefBytes_[i];
        uint64_t take = std::min(p.len - within, b - a);
        if (within == 0 && take == p.len) out.push_back(p);
        else out.push_back(MakePiece(p.src, p.start + within, take));
        a += take;
        ++i;
    }
    return out;
}

void TextBuffer::PushRec(Rec&& r) {
    undo_.resize(undoPos_);
    if (savePos_ > undoPos_) savePos_ = SIZE_MAX;
    undo_.push_back(std::move(r));
    ++undoPos_;
}

EditInfo TextBuffer::Replace(uint64_t off, uint64_t delLen, const char* s, size_t n, uint64_t caretBefore, bool mergeTyping) {
    EditInfo info;
    if (!ready_) return info;
    off = std::min(off, total_);
    delLen = std::min(delLen, total_ - off);
    if (!delLen && !n) return info;
    std::vector<Piece> ins;
    if (n) {
        uint64_t st = add_.size();
        add_.append(s, n);
        addIdx_.Extend(add_.data(), add_.size());
        ins.push_back(MakePiece(1, st, n));
    }
    if (mergeTyping && mergeOpen_ && delLen == 0 && undoPos_ > 0 && undoPos_ == undo_.size()) {
        Rec& last = undo_.back();
        if (last.off + last.insLen == off) {
            Splice(off, 0, ins, info);
            if (!last.inserted.empty() && last.inserted.back().src == 1 &&
                last.inserted.back().start + last.inserted.back().len == ins[0].start) {
                last.inserted.back().len += ins[0].len;
                last.inserted.back().nl += ins[0].nl;
            } else {
                last.inserted.push_back(ins[0]);
            }
            last.insLen += n;
            last.caretAfter = off + n;
            if (savePos_ == undoPos_) savePos_ = SIZE_MAX;
            return info;
        }
    }
    Rec r;
    r.off = off;
    r.removed = Splice(off, delLen, ins, info);
    r.inserted = ins;
    r.insLen = n;
    r.delLen = delLen;
    r.caretBefore = caretBefore;
    r.caretAfter = off + n;
    PushRec(std::move(r));
    mergeOpen_ = mergeTyping;
    return info;
}

EditInfo TextBuffer::ReplaceMany(const std::vector<uint64_t>& offs, uint64_t len, const std::string& repl, uint64_t caretBefore) {
    EditInfo info;
    if (!ready_ || offs.empty()) return info;
    Piece R{};
    if (!repl.empty()) {
        uint64_t st = add_.size();
        add_ += repl;
        addIdx_.Extend(add_.data(), add_.size());
        R = MakePiece(1, st, repl.size());
    }
    std::vector<Piece> np;
    uint64_t cur = 0;
    for (uint64_t o : offs) {
        auto s = Slice(cur, o);
        np.insert(np.end(), s.begin(), s.end());
        if (!repl.empty()) np.push_back(R);
        cur = o + len;
    }
    auto tail = Slice(cur, total_);
    np.insert(np.end(), tail.begin(), tail.end());
    uint64_t oldTotal = total_;
    Rec r;
    r.off = 0;
    r.removed = Splice(0, total_, np, info);
    r.inserted = np;
    r.insLen = total_;
    r.delLen = oldTotal;
    r.caretBefore = caretBefore;
    r.caretAfter = std::min(caretBefore, total_);
    PushRec(std::move(r));
    mergeOpen_ = false;
    info.whole = true;
    return info;
}

bool TextBuffer::Undo(EditInfo& info, uint64_t& caret) {
    if (!CanUndo()) return false;
    Rec& r = undo_[--undoPos_];
    Splice(r.off, r.insLen, r.removed, info);
    caret = r.caretBefore;
    mergeOpen_ = false;
    if (r.removed.size() + r.inserted.size() > 64) info.whole = true;
    return true;
}

bool TextBuffer::Redo(EditInfo& info, uint64_t& caret) {
    if (!CanRedo()) return false;
    Rec& r = undo_[undoPos_++];
    Splice(r.off, r.delLen, r.inserted, info);
    caret = r.caretAfter;
    mergeOpen_ = false;
    if (r.removed.size() + r.inserted.size() > 64) info.whole = true;
    return true;
}

static bool WriteAll(HANDLE h, const char* p, uint64_t n, std::atomic<uint64_t>* progress = nullptr) {
    while (n) {
        DWORD w = 0;
        DWORD want = (DWORD)std::min<uint64_t>(n, 64u << 20);
        if (!WriteFile(h, p, want, &w, nullptr) || w != want) return false;
        p += w; n -= w;
        if (progress) *progress += w;
    }
    return true;
}

bool TextBuffer::Save(const std::wstring& target, std::wstring& err, std::atomic<uint64_t>* progress) {
    bool samePath = !path.empty() && PathEqualsI(target, path);
    bool viaTemp = mapped && samePath;
    std::wstring dest = viaTemp ? target + L".~mye-tmp" : target;
    HANDLE h = CreateFileW(dest.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) { err = FormatError(GetLastError()); return false; }
    bool ok = true;
    if (encoding == Encoding::Utf8Bom) ok = WriteAll(h, "\xEF\xBB\xBF", 3);
    if (encoding == Encoding::Utf16LE || encoding == Encoding::Utf16BE) {
        bool be = encoding == Encoding::Utf16BE;
        ok = WriteAll(h, be ? "\xFE\xFF" : "\xFF\xFE", 2);
        uint64_t off = 0;
        std::string chunk;
        while (ok && off < total_) {
            uint64_t take = std::min<uint64_t>(total_ - off, 1 << 20);
            Read(off, take, chunk);
            // don't cut a UTF-8 sequence
            size_t n = chunk.size();
            if (off + n < total_) {
                while (n > 0 && IsUtf8Cont((unsigned char)chunk[n - 1])) --n;
                if (n > 0 && (unsigned char)chunk[n - 1] >= 0xC0) --n;
                if (!n) n = chunk.size();
            }
            std::wstring w = Utf8ToWide(chunk.data(), n);
            if (be) for (auto& c : w) c = (wchar_t)((c >> 8) | (c << 8));
            ok = WriteAll(h, (const char*)w.data(), w.size() * 2);
            if (progress) *progress = off + n;
            off += n;
        }
    } else {
        for (size_t i = 0; ok && i < pieces_.size(); ++i)
            ok = WriteAll(h, Src(pieces_[i].src) + pieces_[i].start, pieces_[i].len, progress);
    }
    if (ok) ok = SetEndOfFile(h) != 0;
    DWORD e = GetLastError();
    CloseHandle(h);
    if (!ok) {
        err = FormatError(e);
        if (viaTemp) DeleteFileW(dest.c_str());
        return false;
    }
    if (viaTemp) {
        std::wstring old = target + L".~mye-old" + std::to_wstring(GetTickCount64());
        if (!MoveFileExW(target.c_str(), old.c_str(), 0)) {
            err = FormatError(GetLastError());
            DeleteFileW(dest.c_str());
            return false;
        }
        SetFileAttributesW(old.c_str(), FILE_ATTRIBUTE_HIDDEN);
        if (!MoveFileExW(dest.c_str(), target.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_COPY_ALLOWED)) {
            err = FormatError(GetLastError());
            MoveFileExW(old.c_str(), target.c_str(), 0);
            return false;
        }
        pendingDelete_.push_back(old);
    }
    path = target;
    savePos_ = undoPos_;
    mergeOpen_ = false;
    StatDisk();
    return true;
}
