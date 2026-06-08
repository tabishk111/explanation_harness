#!/usr/bin/env python3
"""
Phase 1 locator for the variable-trace skill.

Walks a directory tree, finds every line referencing the target variable across
assembly, C/C++ (including struct/class fields in headers), and HLASM macros.
Emits JSONL records.

Each record: {"file": str, "line": int, "lang": str, "raw_line": str}

The agent is responsible for classifying role (definition / setter / reader / etc.)
in phase 2 — this script only narrows the search space.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

LANG_BY_EXT = {
    ".asm": "asm", ".s": "asm", ".S": "asm",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c": "cpp",
    ".hpp": "cpp", ".hxx": "cpp", ".h": "cpp",
    ".mac": "mac",
}

ALL_LANGS = {"asm", "cpp", "mac"}

SKIP_DIRS = {".git", "node_modules", ".pi", "build", "dist", "out", "target", ".venv", "__pycache__"}


def detect_lang(path: Path) -> str | None:
    return LANG_BY_EXT.get(path.suffix) or LANG_BY_EXT.get(path.suffix.lower())


def build_pattern(name: str, lang: str) -> re.Pattern[str]:
    """Word-boundary regex appropriate to the language.

    C/C++ identifiers: [A-Za-z_][A-Za-z0-9_]*
    HLASM identifiers: [A-Za-z@#$_][A-Za-z0-9@#$_]*
    """
    escaped = re.escape(name)
    if lang in ("asm", "mac"):
        return re.compile(rf"(?<![A-Za-z0-9@#$_]){escaped}(?![A-Za-z0-9@#$_])")
    # cpp default — \b handles member-access too (obj.foo, ptr->foo, Class::foo
    # all match because '.', '>', ':' are non-word characters)
    return re.compile(rf"\b{escaped}\b")


def is_comment(line: str, lang: str) -> bool:
    s = line.lstrip()
    if not s:
        return True
    if lang == "cpp":
        return s.startswith("//") or s.startswith("/*") or s.startswith("*")
    if lang in ("asm", "mac"):
        # HLASM: '*' in col 1 is a comment line; '.*' is a macro comment
        return line.startswith("*") or s.startswith(".*")
    return False


def scan_file(path: Path, pattern: re.Pattern[str], lang: str, include_comments: bool):
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for lineno, raw in enumerate(f, start=1):
                line = raw.rstrip("\n")
                if not include_comments and is_comment(line, lang):
                    continue
                if pattern.search(line):
                    yield {
                        "file": str(path),
                        "line": lineno,
                        "lang": lang,
                        "raw_line": line,
                    }
    except OSError as e:
        print(f"# could not read {path}: {e}", file=sys.stderr)


def walk(root: Path, langs: set[str]):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            p = Path(dirpath) / name
            lang = detect_lang(p)
            if lang and lang in langs:
                yield p, lang


def main() -> int:
    ap = argparse.ArgumentParser(description="Locate occurrences of a variable across the codebase.")
    ap.add_argument("name", help="Variable identifier to locate.")
    ap.add_argument("--root", default=".", help="Directory to search (default: cwd).")
    ap.add_argument("--lang", default=",".join(sorted(ALL_LANGS)),
                    help=f"Comma-separated language filter. Choices: {sorted(ALL_LANGS)}")
    ap.add_argument("--include-comments", action="store_true",
                    help="Include matches inside comment lines (default: skip).")
    ap.add_argument("--max-results", type=int, default=0,
                    help="Stop after N hits (0 = unlimited).")
    args = ap.parse_args()

    langs = {l.strip() for l in args.lang.split(",") if l.strip()}
    bad = langs - ALL_LANGS
    if bad:
        print(f"error: unknown lang(s) {sorted(bad)}; valid: {sorted(ALL_LANGS)}", file=sys.stderr)
        return 2

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    patterns = {lang: build_pattern(args.name, lang) for lang in langs}

    count = 0
    for path, lang in walk(root, langs):
        for hit in scan_file(path, patterns[lang], lang, args.include_comments):
            print(json.dumps(hit, ensure_ascii=False))
            count += 1
            if args.max_results and count >= args.max_results:
                return 0
    if count == 0:
        print(f"# no occurrences of {args.name!r} found under {root}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
