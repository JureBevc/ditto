// Minimal line-based Markdown highlighter. Only fenced code blocks carry state across lines.
#pragma once
#include "common.h"

class TextBuffer;

enum MdStyle : uint8_t {
    MS_NORMAL, MS_HEADING, MS_BOLD, MS_ITALIC, MS_CODE, MS_LINK, MS_URL, MS_QUOTE, MS_LIST, MS_RULE, MS_FENCE, MS_HTML,
    MS_COUNT
};

struct MdSpan {
    uint32_t start, end;
    uint8_t style;
};

// state: 0 = normal, otherwise (fenceChar << 8) | fenceLen  (inside a fenced code block)
uint16_t MdNextState(const char* line, size_t n, uint16_t state);
void MdHighlightLine(const char* line, size_t n, uint16_t state, std::vector<MdSpan>& out);

// Caches the fence state at every kStep-th line so the state at any line is cheap to find.
class MdStateCache {
public:
    static const uint64_t kStep = 256;
    void Invalidate(uint64_t fromLine);
    uint16_t StateAt(const TextBuffer& buf, uint64_t line);
private:
    std::vector<uint16_t> cp_{0};  // cp_[k] = state at the start of line k*kStep
};
