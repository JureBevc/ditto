#include "md_highlight.h"
#include "text_buffer.h"

static size_t LeadingSpaces(const char* s, size_t n) {
    size_t i = 0;
    while (i < n && s[i] == ' ') ++i;
    return i;
}

static bool OnlyWs(const char* s, size_t n) {
    for (size_t i = 0; i < n; ++i)
        if (s[i] != ' ' && s[i] != '\t' && s[i] != '\r') return false;
    return true;
}

// returns fence run length if the line opens/closes a fence (char in *ch), else 0
static size_t FenceRun(const char* s, size_t n, char* ch, size_t* after) {
    size_t i = LeadingSpaces(s, n);
    if (i > 3 || i >= n || (s[i] != '`' && s[i] != '~')) return 0;
    char c = s[i];
    size_t j = i;
    while (j < n && s[j] == c) ++j;
    if (j - i < 3) return 0;
    *ch = c;
    *after = j;
    return j - i;
}

uint16_t MdNextState(const char* s, size_t n, uint16_t state) {
    char c;
    size_t after;
    size_t run = FenceRun(s, n, &c, &after);
    if (!state) {
        if (!run) return 0;
        if (c == '`' && memchr(s + after, '`', n - after)) return 0;  // backticks in info string: not a fence
        return (uint16_t)(((uint8_t)c << 8) | std::min<size_t>(run, 255));
    }
    if (run && (char)(state >> 8) == c && run >= (state & 0xFF) && OnlyWs(s + after, n - after)) return 0;
    return state;
}

static void Push(std::vector<MdSpan>& out, size_t a, size_t b, uint8_t st) {
    if (b > a) out.push_back(MdSpan{(uint32_t)a, (uint32_t)b, st});
}

static bool IsRule(const char* s, size_t n) {
    size_t i = LeadingSpaces(s, n);
    if (i > 3 || i >= n) return false;
    char c = s[i];
    if (c != '-' && c != '*' && c != '_') return false;
    int cnt = 0;
    for (; i < n; ++i) {
        if (s[i] == c) ++cnt;
        else if (s[i] != ' ' && s[i] != '\t' && s[i] != '\r') return false;
    }
    return cnt >= 3;
}

static bool StartsWith(const char* s, size_t n, size_t i, const char* lit) {
    size_t l = strlen(lit);
    return i + l <= n && _strnicmp(s + i, lit, l) == 0;
}

// Inline spans for [a, b) with base style for plain text.
static void Inline(const char* s, size_t a, size_t b, uint8_t base, std::vector<MdSpan>& out) {
    size_t i = a, plain = a;
    auto flush = [&](size_t to) { Push(out, plain, to, base); };
    while (i < b) {
        char c = s[i];
        if (c == '\\' && i + 1 < b) { i += 2; continue; }
        if (c == '`') {
            size_t r = i;
            while (r < b && s[r] == '`') ++r;
            size_t run = r - i, j = r;
            size_t close = SIZE_MAX;
            while (j < b) {
                if (s[j] == '`') {
                    size_t k = j;
                    while (k < b && s[k] == '`') ++k;
                    if (k - j == run) { close = k; break; }
                    j = k;
                } else ++j;
            }
            if (close != SIZE_MAX) {
                flush(i);
                Push(out, i, close, MS_CODE);
                i = plain = close;
                continue;
            }
            i = r;
            continue;
        }
        if ((c == '*' || c == '_') ) {
            bool dbl = i + 1 < b && s[i + 1] == c;
            size_t w = dbl ? 2 : 1;
            if (i + w < b && s[i + w] != ' ' && (c == '*' || i == a || !isalnum((unsigned char)s[i - 1]))) {
                // find closing
                size_t j = i + w;
                size_t close = SIZE_MAX;
                while (j + w <= b) {
                    if (s[j] == '\\') { j += 2; continue; }
                    if (s[j] == '`') break;
                    if (s[j] == c && (!dbl || (j + 1 < b && s[j + 1] == c)) && s[j - 1] != ' ' &&
                        (dbl || j + 1 >= b || s[j + 1] != c)) {
                        close = j + w;
                        break;
                    }
                    ++j;
                }
                if (close != SIZE_MAX && close > i + 2 * w) {
                    flush(i);
                    Push(out, i, close, dbl ? MS_BOLD : MS_ITALIC);
                    i = plain = close;
                    continue;
                }
            }
            i += w;
            continue;
        }
        if (c == '[' || (c == '!' && i + 1 < b && s[i + 1] == '[')) {
            size_t open = c == '!' ? i + 1 : i;
            size_t j = open + 1;
            int depth = 1;
            while (j < b && depth) {
                if (s[j] == '[') ++depth;
                else if (s[j] == ']') --depth;
                if (depth) ++j;
            }
            if (j < b && j + 1 < b && s[j + 1] == '(') {
                const void* q = memchr(s + j + 1, ')', b - j - 1);
                if (q) {
                    size_t k = (const char*)q - s;
                    flush(i);
                    Push(out, i, j + 1, MS_LINK);
                    Push(out, j + 1, k + 1, MS_URL);
                    i = plain = k + 1;
                    continue;
                }
            } else if (j < b && j + 1 < b && s[j + 1] == '[') {
                const void* q = memchr(s + j + 2, ']', b - j - 2);
                if (q) {
                    size_t k = (const char*)q - s;
                    flush(i);
                    Push(out, i, k + 1, MS_LINK);
                    i = plain = k + 1;
                    continue;
                }
            }
            ++i;
            continue;
        }
        if (c == '<') {
            const void* q = memchr(s + i, '>', b - i);
            if (q) {
                size_t k = (const char*)q - s;
                bool url = StartsWith(s, b, i + 1, "http://") || StartsWith(s, b, i + 1, "https://") ||
                           StartsWith(s, b, i + 1, "mailto:");
                bool tag = i + 1 < b && (isalpha((unsigned char)s[i + 1]) || s[i + 1] == '/' || s[i + 1] == '!');
                if (url || tag) {
                    flush(i);
                    Push(out, i, k + 1, url ? MS_URL : MS_HTML);
                    i = plain = k + 1;
                    continue;
                }
            }
            ++i;
            continue;
        }
        if ((c == 'h' || c == 'H') && (StartsWith(s, b, i, "http://") || StartsWith(s, b, i, "https://")) &&
            (i == a || !isalnum((unsigned char)s[i - 1]))) {
            size_t j = i;
            while (j < b && s[j] != ' ' && s[j] != '\t' && s[j] != ')' && s[j] != '>' && s[j] != '\r') ++j;
            flush(i);
            Push(out, i, j, MS_URL);
            i = plain = j;
            continue;
        }
        ++i;
    }
    flush(b);
}

void MdHighlightLine(const char* s, size_t n, uint16_t state, std::vector<MdSpan>& out) {
    out.clear();
    if (state) {  // inside fenced block (closing fence line included)
        Push(out, 0, n, MS_FENCE);
        return;
    }
    char fc;
    size_t after;
    if (FenceRun(s, n, &fc, &after)) {
        Push(out, 0, n, MS_FENCE);
        return;
    }
    size_t i = LeadingSpaces(s, n);
    if (i >= 4) {  // indented code block
        Push(out, 0, n, MS_FENCE);
        return;
    }
    if (i < n && s[i] == '#') {
        size_t j = i;
        while (j < n && s[j] == '#') ++j;
        if (j - i <= 6 && (j == n || s[j] == ' ' || s[j] == '\t' || s[j] == '\r')) {
            Push(out, 0, n, MS_HEADING);
            return;
        }
    }
    if (IsRule(s, n)) {
        Push(out, 0, n, MS_RULE);
        return;
    }
    if (i < n && s[i] == '>') {
        size_t j = i;
        while (j < n && (s[j] == '>' || s[j] == ' ')) ++j;
        Push(out, 0, j, MS_LIST);
        Inline(s, j, n, MS_QUOTE, out);
        return;
    }
    // list markers: -, *, + or 1. / 1)
    size_t j = i;
    if (j < n && (s[j] == '-' || s[j] == '*' || s[j] == '+') && j + 1 < n && s[j + 1] == ' ') {
        Push(out, 0, j + 1, MS_LIST);
        size_t k = j + 2;
        if (k + 3 <= n && s[k] == '[' && (s[k + 1] == ' ' || s[k + 1] == 'x' || s[k + 1] == 'X') && s[k + 2] == ']') {
            Push(out, j + 1, k + 3, MS_LIST);
            Inline(s, k + 3, n, MS_NORMAL, out);
        } else {
            Inline(s, j + 1, n, MS_NORMAL, out);
        }
        return;
    }
    while (j < n && isdigit((unsigned char)s[j]) && j - i < 9) ++j;
    if (j > i && j + 1 < n && (s[j] == '.' || s[j] == ')') && s[j + 1] == ' ') {
        Push(out, 0, j + 1, MS_LIST);
        Inline(s, j + 1, n, MS_NORMAL, out);
        return;
    }
    // setext underline (=== / ---) is shown as heading
    if (i < n && s[i] == '=') {
        size_t k = i;
        while (k < n && s[k] == '=') ++k;
        if (OnlyWs(s + k, n - k)) { Push(out, 0, n, MS_HEADING); return; }
    }
    Inline(s, 0, n, MS_NORMAL, out);
}

void MdStateCache::Invalidate(uint64_t fromLine) {
    size_t keep = (size_t)(fromLine / kStep) + 1;  // cp_[k] covers line k*kStep; valid if k*kStep <= fromLine
    if (cp_.size() > keep) cp_.resize(keep);
}

uint16_t MdStateCache::StateAt(const TextBuffer& buf, uint64_t line) {
    uint64_t k = std::min<uint64_t>(line / kStep, cp_.size() - 1);
    uint64_t cur = k * kStep;
    uint16_t st = cp_[(size_t)k];
    if (cur == line) return st;
    // sequential scan using large reads
    uint64_t off = buf.LineStart(cur);
    uint64_t total = buf.Length();
    std::string chunk, carry;
    const uint64_t kChunk = 1 << 20;
    while (cur < line && off < total) {
        buf.Read(off, kChunk, chunk);
        size_t p = 0;
        while (cur < line) {
            const char* nl = (const char*)memchr(chunk.data() + p, '\n', chunk.size() - p);
            if (!nl) {
                if (carry.size() < 512) carry.append(chunk.data() + p, std::min<size_t>(chunk.size() - p, 512));
                break;
            }
            size_t e = nl - chunk.data();
            if (!carry.empty()) {
                carry.append(chunk.data() + p, std::min<size_t>(e - p, 512));
                st = MdNextState(carry.data(), carry.size(), st);
                carry.clear();
            } else {
                st = MdNextState(chunk.data() + p, std::min<size_t>(e - p, 512), st);
            }
            p = e + 1;
            ++cur;
            if (cur % kStep == 0 && cur / kStep == cp_.size()) cp_.push_back(st);
        }
        off += chunk.size();
    }
    return st;
}
