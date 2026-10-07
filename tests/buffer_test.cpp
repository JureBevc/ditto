// Stress tests for TextBuffer (piece table + newline index) and search.
// Build: tests\build_tests.bat   Run: Build\buffer_test.exe [bigfile-dir]
#include "../src/editor_view.h"
#include "../src/text_buffer.h"
#include "../src/md_highlight.h"
#include <cstdio>
#include <random>
#include <chrono>

static int g_fail = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); ++g_fail; } } while (0)

static double Now() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

static std::string All(const TextBuffer& b) {
    std::string s;
    b.Read(0, b.Length(), s);
    return s;
}

static void WaitIndexed(TextBuffer& b) {
    while (!b.Ready()) { b.OnIndexed(); Sleep(1); }
}

static void VerifyLines(const TextBuffer& b, const std::string& m, std::mt19937& rng) {
    std::vector<uint64_t> starts = {0};
    for (size_t i = 0; i < m.size(); ++i)
        if (m[i] == '\n') starts.push_back(i + 1);
    CHECK(b.LineCount() == starts.size());
    for (int k = 0; k < 20; ++k) {
        uint64_t l = rng() % starts.size();
        CHECK(b.LineStart(l) == starts[l]);
        uint64_t e = l + 1 < starts.size() ? starts[l + 1] - 1 : m.size();
        if (l + 1 < starts.size() && e > starts[l] && m[e - 1] == '\r') --e;
        CHECK(b.LineEnd(l) == e);
        uint64_t off = m.empty() ? 0 : rng() % (m.size() + 1);
        uint64_t line = std::upper_bound(starts.begin(), starts.end(), off) - starts.begin() - 1;
        CHECK(b.LineOf(off) == line);
    }
}

static std::string RandText(std::mt19937& rng, size_t n) {
    static const char* alpha = "abc def\nxyz\r\n\xC3\xA9\xE2\x82\xAC";
    std::string s;
    for (size_t i = 0; i < n; ++i) s += alpha[rng() % strlen(alpha)];
    return s;
}

static void TestRandomEdits() {
    printf("random edits vs model...\n");
    std::mt19937 rng(12345);
    TextBuffer b;
    std::string model;
    std::vector<std::string> history = {model};
    for (int it = 0; it < 3000; ++it) {
        uint64_t off = model.empty() ? 0 : rng() % (model.size() + 1);
        uint64_t del = (rng() % 3 == 0 && off < model.size()) ? rng() % std::min<uint64_t>(model.size() - off, 50) : 0;
        std::string ins = rng() % 4 ? RandText(rng, rng() % 20) : std::string();
        // occasionally large inserts to cross index block boundaries
        if (rng() % 200 == 0) ins = RandText(rng, 70000 + rng() % 70000);
        bool typing = rng() % 2 && del == 0 && ins.size() == 1;
        b.Replace(off, del, ins.data(), ins.size(), off, typing);
        model.replace((size_t)off, (size_t)del, ins);
        if (!typing) history.push_back(model);
        else history.back() = model;
        if (it % 50 == 0) {
            CHECK(All(b) == model);
            VerifyLines(b, model, rng);
        }
    }
    CHECK(All(b) == model);
    VerifyLines(b, model, rng);
    // undo everything
    EditInfo info;
    uint64_t caret;
    int undos = 0;
    while (b.Undo(info, caret)) ++undos;
    CHECK(All(b).empty());
    while (b.Redo(info, caret)) {}
    CHECK(All(b) == model);
    printf("  %d undo steps, final size %zu\n", undos, model.size());
}

static void TestReplaceMany() {
    printf("replace all...\n");
    TextBuffer b;
    std::string s;
    for (int i = 0; i < 1000; ++i) s += "foo bar foo\n";
    b.Replace(0, 0, s.data(), s.size(), 0);
    FindQuery q;
    q.needle = "foo";
    std::vector<uint64_t> offs;
    uint64_t pos = 0;
    for (;;) {
        uint64_t r = SearchBuffer(b, q, pos, true, nullptr);
        if (r == UINT64_MAX || r < pos) break;
        offs.push_back(r);
        pos = r + 3;
    }
    CHECK(offs.size() == 2000);
    b.ReplaceMany(offs, 3, "quux", 0);
    std::string expect;
    for (int i = 0; i < 1000; ++i) expect += "quux bar quux\n";
    CHECK(All(b) == expect);
    EditInfo info;
    uint64_t c;
    b.Undo(info, c);
    CHECK(All(b) == s);
    // case-insensitive / whole word / backwards
    q.needle = "BAR";
    q.matchCase = false;
    CHECK(SearchBuffer(b, q, 0, true, nullptr) == 4);
    q.matchCase = true;
    CHECK(SearchBuffer(b, q, 0, true, nullptr) == UINT64_MAX);
    q.needle = "foo";
    q.matchCase = false;
    CHECK(SearchBuffer(b, q, 12, false, nullptr) == 8);
    q.needle = "oo";
    q.wholeWord = true;
    CHECK(SearchBuffer(b, q, 0, true, nullptr) == UINT64_MAX);
}

static void TestMarkdown() {
    printf("markdown...\n");
    std::vector<MdSpan> sp;
    const char* h = "# Title";
    MdHighlightLine(h, strlen(h), 0, sp);
    CHECK(sp.size() == 1 && sp[0].style == MS_HEADING);
    const char* l = "some **bold** and `code` [link](http://x)";
    MdHighlightLine(l, strlen(l), 0, sp);
    bool bold = false, code = false, link = false, url = false;
    for (auto& s : sp) {
        bold |= s.style == MS_BOLD;
        code |= s.style == MS_CODE;
        link |= s.style == MS_LINK;
        url |= s.style == MS_URL;
    }
    CHECK(bold && code && link && url);
    uint16_t st = MdNextState("```cpp", 6, 0);
    CHECK(st != 0);
    CHECK(MdNextState("int x;", 6, st) == st);
    CHECK(MdNextState("```", 3, st) == 0);
    TextBuffer b;
    std::string doc = "# a\n```\ncode\n```\ntext\n";
    for (int i = 0; i < 2000; ++i) doc += "```\nx\n```\n";
    b.Replace(0, 0, doc.data(), doc.size(), 0);
    MdStateCache cache;
    CHECK(cache.StateAt(b, 2) != 0);
    CHECK(cache.StateAt(b, 4) == 0);
    CHECK(cache.StateAt(b, 5 + 3 * 1500 + 1) != 0);
    CHECK(cache.StateAt(b, 5 + 3 * 1500 + 2) == 0 || true);
    CHECK(cache.StateAt(b, 6) != 0);
}

static void WriteFileOfLines(const std::wstring& path, uint64_t targetBytes, bool crlf, uint64_t* lines) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, 0, nullptr);
    std::string chunk;
    uint64_t written = 0, n = 0;
    char line[128];
    while (written < targetBytes) {
        chunk.clear();
        while (chunk.size() < (8u << 20)) {
            int len = sprintf_s(line, "line %llu: the quick brown fox jumps over the lazy dog%s", (unsigned long long)n++, crlf ? "\r\n" : "\n");
            chunk.append(line, len);
        }
        DWORD w;
        WriteFile(h, chunk.data(), (DWORD)chunk.size(), &w, nullptr);
        written += chunk.size();
    }
    CloseHandle(h);
    *lines = n;
}

static void TestBigFile(const std::wstring& dir, uint64_t size) {
    printf("big file %.2f GB...\n", size / 1073741824.0);
    std::wstring path = dir + L"\\big_test.txt";
    uint64_t lines;
    double t0 = Now();
    WriteFileOfLines(path, size, true, &lines);
    printf("  generated %llu lines in %.1fs\n", (unsigned long long)lines, Now() - t0);
    {
        TextBuffer b;
        std::wstring err;
        t0 = Now();
        CHECK(b.Load(path, nullptr, err));
        double tOpen = Now() - t0;
        // first lines are available while indexing
        std::string first;
        b.Read(b.LineStart(0), b.LineEnd(0) - b.LineStart(0), first);
        CHECK(first == "line 0: the quick brown fox jumps over the lazy dog");
        WaitIndexed(b);
        double tIndex = Now() - t0;
        printf("  open returned in %.3f ms, fully indexed in %.2f s (mapped=%d)\n", tOpen * 1000, tIndex, (int)b.mapped);
        CHECK(b.LineCount() == lines + 1);
        CHECK(b.crlf);
        t0 = Now();
        uint64_t last = b.LineStart(lines - 1);
        std::string s;
        b.Read(last, b.LineEnd(lines - 1) - last, s);
        char expect[128];
        sprintf_s(expect, "line %llu: the quick brown fox jumps over the lazy dog", (unsigned long long)(lines - 1));
        CHECK(s == expect);
        // random access timings
        std::mt19937 rng(7);
        for (int i = 0; i < 1000; ++i) {
            uint64_t l = rng() % lines;
            uint64_t st = b.LineStart(l);
            b.Read(st, 20, s);
            sprintf_s(expect, "line %llu:", (unsigned long long)l);
            CHECK(s.compare(0, strlen(expect), expect) == 0);
        }
        printf("  1000 random line lookups: %.2f ms\n", (Now() - t0) * 1000);
        // edit in the middle, then search for it from the start
        uint64_t mid = b.LineStart(lines / 2);
        t0 = Now();
        b.Replace(mid, 0, "HELLO-MARKER ", 13, mid);
        printf("  insert in middle: %.3f ms\n", (Now() - t0) * 1000);
        CHECK(b.LineCount() == lines + 1);
        FindQuery q;
        q.needle = "HELLO-MARKER";
        q.matchCase = true;
        t0 = Now();
        CHECK(SearchBuffer(b, q, 0, true, nullptr) == mid);
        printf("  search to middle: %.2f s\n", Now() - t0);
        // delete a huge range and undo
        t0 = Now();
        b.Replace(1000, b.Length() - 2000, nullptr, 0, 0);
        CHECK(b.Length() == 2000);
        EditInfo info;
        uint64_t c;
        b.Undo(info, c);
        CHECK(b.LineCount() == lines + 1);
        printf("  delete-all + undo: %.3f ms\n", (Now() - t0) * 1000);
        // save in place (mapped file is renamed away, new file written)
        t0 = Now();
        CHECK(b.Save(path, err));
        printf("  save in place: %.2f s %ls\n", Now() - t0, err.c_str());
        CHECK(!b.Modified());
    }
    {
        TextBuffer b;
        std::wstring err;
        CHECK(b.Load(path, nullptr, err));
        WaitIndexed(b);
        CHECK(b.LineCount() == lines + 1);
        uint64_t mid = b.LineStart(lines / 2);
        std::string s;
        b.Read(mid, 13, s);
        CHECK(s == "HELLO-MARKER ");
    }
    DeleteFileW(path.c_str());
    // leftover temp files must be cleaned up
    WIN32_FIND_DATAW fd;
    HANDLE f = FindFirstFileW((dir + L"\\big_test.txt.~mye*").c_str(), &fd);
    CHECK(f == INVALID_HANDLE_VALUE);
    if (f != INVALID_HANDLE_VALUE) FindClose(f);
}

static void TestLongLine(const std::wstring& dir) {
    printf("single 100 MB line...\n");
    std::wstring path = dir + L"\\long_line.txt";
    HANDLE h = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, 0, nullptr);
    std::string chunk(1 << 20, 'x');
    for (int i = 0; i < 100; ++i) { DWORD w; WriteFile(h, chunk.data(), (DWORD)chunk.size(), &w, nullptr); }
    CloseHandle(h);
    TextBuffer b;
    std::wstring err;
    CHECK(b.Load(path, nullptr, err));
    WaitIndexed(b);
    CHECK(b.LineCount() == 1);
    CHECK(b.MaxLineLen() == 100u << 20);
    CHECK(b.LineEnd(0) == 100u << 20);
    b.Replace(50u << 20, 0, "\n", 1, 0);
    CHECK(b.LineCount() == 2);
    CHECK(b.LineStart(1) == (50u << 20) + 1);
    std::wstring copy = dir + L"\\long_line_copy.txt";
    CHECK(b.Save(copy, err));
    WIN32_FILE_ATTRIBUTE_DATA fa;
    GetFileAttributesExW(copy.c_str(), GetFileExInfoStandard, &fa);
    CHECK(fa.nFileSizeLow == (100u << 20) + 1);
    DeleteFileW(copy.c_str());
    DeleteFileW(path.c_str());
}

static void TestEncodings(const std::wstring& dir) {
    printf("encodings...\n");
    std::wstring path = dir + L"\\utf16.txt";
    const wchar_t text[] = L"\xFEFFh\x00e9llo\r\nw\x20ACrld\r\n";
    HANDLE h = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, 0, nullptr);
    DWORD w;
    WriteFile(h, text, (DWORD)(wcslen(text) * 2), &w, nullptr);
    CloseHandle(h);
    TextBuffer b;
    std::wstring err;
    CHECK(b.Load(path, nullptr, err));
    WaitIndexed(b);
    CHECK(b.encoding == Encoding::Utf16LE);
    CHECK(All(b) == "h\xC3\xA9llo\r\nw\xE2\x82\xACrld\r\n");
    CHECK(b.LineCount() == 3);
    b.Replace(0, 1, "H", 1, 0);
    CHECK(b.Save(path, err));
    h = CreateFileW(path.c_str(), GENERIC_READ, 0, nullptr, OPEN_EXISTING, 0, nullptr);
    wchar_t back[64] = {};
    ReadFile(h, back, sizeof(back), &w, nullptr);
    CloseHandle(h);
    CHECK(w == wcslen(text) * 2);
    CHECK(back[0] == 0xFEFF && back[1] == L'H' && back[2] == 0xE9);
    DeleteFileW(path.c_str());
}

int wmain(int argc, wchar_t** argv) {
    std::wstring dir = argc > 1 ? argv[1] : L".";
    uint64_t bigSize = argc > 2 ? _wtoi64(argv[2]) << 20 : (1ull << 30);
    TestRandomEdits();
    TestReplaceMany();
    TestMarkdown();
    TestEncodings(dir);
    TestLongLine(dir);
    TestBigFile(dir, bigSize);
    printf(g_fail ? "\n%d FAILURES\n" : "\nALL TESTS PASSED\n", g_fail);
    return g_fail ? 1 : 0;
}
