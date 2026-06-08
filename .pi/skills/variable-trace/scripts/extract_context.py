#!/usr/bin/env python3
"""
Extract the enclosing block (function / struct / class / CSECT) around file:line.

Heuristic — returns a window the agent can read to find the controlling
condition, the full statement, and (for C++) the enclosing struct/class so a
field's `defined-in` can name its owner type.

Usage:
    extract_context.py PATH LINE [--lang auto|cpp|asm|mac] [--window N]

Emits JSON: {"path", "line", "lang", "start", "end", "text", "enclosing"?}
where `enclosing` is the line of the nearest containing struct/class/union/function
header (best-effort, C/C++ only).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LANG_BY_EXT = {
    ".asm": "asm", ".s": "asm", ".S": "asm",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c": "cpp",
    ".hpp": "cpp", ".hxx": "cpp", ".h": "cpp",
    ".mac": "mac",
}

CPP_TYPE_HEADER = re.compile(
    r"^\s*(?:template\s*<[^>]*>\s*)?"
    r"(?P<kind>struct|class|union|namespace|enum(?:\s+class)?)\s+"
    r"(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*:\s*[^\{]+)?"        # base list
    r"\s*\{?"
)
CPP_FUNC_HEADER = re.compile(
    r"^[^/].*\b(?P<name>[A-Za-z_][\w:]*)\s*\([^;]*\)\s*(?:const|noexcept|override|final|=\s*default|=\s*delete)?\s*\{?\s*$"
)


def detect_lang(path: Path) -> str:
    return LANG_BY_EXT.get(path.suffix.lower()) or "cpp"


def cpp_block(lines: list[str], target: int) -> tuple[int, int, str | None]:
    """Walk backward to the enclosing brace at depth 0 and forward to its close.

    Returns (start, end, enclosing_header_line).

    `enclosing_header_line` is the first non-empty line at the start of the
    block — typically the struct/class/function header that owns this scope.
    """
    # forward to matching close brace — only stop when depth drops BELOW 0
    # (i.e. we've seen a `}` that wasn't balanced by an earlier `{`)
    depth = 0
    end = len(lines) - 1
    for i in range(target, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth < 0:
            end = i
            break

    # backward to the line containing the unbalanced `{`
    depth = 0
    start = 0
    for i in range(target, -1, -1):
        depth += lines[i].count("}") - lines[i].count("{")
        if depth < 0:
            start = i
            break

    # If the `{` line is itself the brace-only continuation (e.g. function body
    # opens on its own line), the header is the line above. The lookup below
    # handles both cases — walk up from `start` until we find a struct/class/
    # function header (or run out of nearby non-blank lines).
    enclosing: str | None = None
    for j in range(start, max(0, start - 5) - 1, -1):
        s = lines[j].strip()
        if not s:
            continue
        if CPP_TYPE_HEADER.match(s) or CPP_FUNC_HEADER.match(s):
            enclosing = s
            start = j
            break

    return start, end, enclosing


def hlasm_block(lines: list[str], target: int) -> tuple[int, int, str | None]:
    boundary = re.compile(r"^\S+\s+(CSECT|DSECT|START|PROC|MACRO|MEND)\b", re.IGNORECASE)
    start = 0
    for i in range(target, -1, -1):
        if boundary.match(lines[i]):
            start = i
            break
    end = len(lines) - 1
    for i in range(target + 1, len(lines)):
        if boundary.match(lines[i]):
            end = i
            break
    return start, end, lines[start].strip() if 0 <= start < len(lines) else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("line", type=int)
    ap.add_argument("--lang", default="auto")
    ap.add_argument("--window", type=int, default=200,
                    help="Cap context window to this many lines (default 200).")
    args = ap.parse_args()

    path = Path(args.path)
    if not path.is_file():
        print(f"error: {path} not a file", file=sys.stderr)
        return 2

    lang = args.lang if args.lang != "auto" else detect_lang(path)
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    target = args.line - 1
    if not (0 <= target < len(text)):
        print(f"error: line {args.line} out of range (1..{len(text)})", file=sys.stderr)
        return 2

    enclosing: str | None = None
    if lang == "cpp":
        start, end, enclosing = cpp_block(text, target)
    elif lang in ("asm", "mac"):
        start, end, enclosing = hlasm_block(text, target)
    else:
        start, end = max(0, target - 20), min(len(text) - 1, target + 20)

    if end - start + 1 > args.window:
        half = args.window // 2
        start = max(start, target - half)
        end = min(end, target + half)

    block = "\n".join(text[start:end + 1])
    out: dict = {
        "path": str(path),
        "line": args.line,
        "lang": lang,
        "start": start + 1,
        "end": end + 1,
        "text": block,
    }
    if enclosing:
        out["enclosing"] = enclosing
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
