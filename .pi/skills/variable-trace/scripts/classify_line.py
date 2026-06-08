#!/usr/bin/env python3
"""
Heuristic role classifier for a single source line.

Given a raw line + language + the target variable name, returns one of:
  definition | setter | reader | macro | unknown

Plus, when possible, the parsed LHS / RHS / qualifier (struct/class context),
mnemonic (HLASM), op (C++).

This is a CHEAP first pass. Borderline cases (pointer aliasing, macro expansion,
multi-line statements) will land in `unknown` — the agent must then read the
file directly. Never trust this script's output blindly for the chain.

Usage:
    classify_line.py --name VAR --lang LANG --line 'raw text'
    classify_line.py --name VAR --lang LANG --stdin   # reads JSONL from find_variable.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys

# ---------- C / C++ ----------

# Free-standing variable or static-class-member definition. Matches:
#   int foo;            extern int foo;     static const int foo;
#   int foo = 0;        std::string foo = "x";
#   int Foo::foo = 0;   (out-of-class static member definition)
# Will NOT match a struct field declaration on its own — that's CPP_FIELD below.
CPP_DEFN = re.compile(
    r"""
    ^\s*
    (?:extern\s+|static\s+|const\s+|volatile\s+|register\s+|inline\s+|constexpr\s+|thread_local\s+|mutable\s+)*
    (?:[A-Za-z_][\w:<>,\s\*&]*?\s+)?         # type (greedy until name); allows templates and qualified types
    \*?\s*
    (?:(?P<qualifier>[A-Za-z_][\w:]*)::)?    # optional Class:: qualifier for out-of-class definitions
    (?P<name>{name})\b
    (?:\s*\[[^\]]*\])*                       # array dims
    \s*(?:=\s*(?P<rhs>[^;]+?))?\s*;?\s*(?://.*|/\*.*)?$
    """,
    re.VERBOSE,
)

# Struct/class field declaration inside a `struct`/`class`/`union` body.
# Same shape as CPP_DEFN but we tag the role differently so the agent knows
# to also look at constructor init lists and member-access setters.
# Heuristic: any C-style definition line whose enclosing block is a struct/class
# is a field. The classifier itself can't see the enclosing block from one line,
# so we report this as a candidate `definition` and rely on the agent to confirm
# via extract_context.py — but we also expose a hint when the line obviously
# sits inside a class (e.g. leading "    Type name;" with no namespace/extern).
CPP_FIELD_HINT = re.compile(
    r"""
    ^\s+                                     # indented (suggests inside a block)
    (?:mutable\s+|static\s+|const\s+|volatile\s+|inline\s+|constexpr\s+)*
    (?:[A-Za-z_][\w:<>,\s\*&]*?\s+)
    \*?\s*
    (?P<name>{name})\b
    (?:\s*\[[^\]]*\])*
    \s*(?:=\s*(?P<rhs>[^;]+?))?\s*;?\s*(?://.*|/\*.*)?$
    """,
    re.VERBOSE,
)

# Plain assignment to bare name or any qualified form:
#   foo = …    obj.foo = …    ptr->foo = …    (*ptr).foo = …    Class::foo = …
# Capture the qualifier (everything before the dot/arrow/::) so the agent can
# tell "wrote to which instance" later if it matters.
CPP_ASSIGN = re.compile(
    r"""^\s*
        (?:(?P<qual>[A-Za-z_][\w\.\->:\(\)\*\[\]]*?)(?P<sep>\.|->|::))?
        \*?\s*
        (?P<name>{name})
        (?:\s*\[[^\]]*\])*                   # array index on LHS
        \s*(?P<op>=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<=|>>=)\s*
        (?P<rhs>[^;]+?)\s*;?\s*(?://.*)?$""",
    re.VERBOSE,
)

CPP_INCREMENT = re.compile(
    r"^\s*(?:\+\+|--)\s*(?:[A-Za-z_][\w\.\->:\(\)\*]*?(?:\.|->|::))?(?P<name>{name})\b"
    r"|^\s*(?:[A-Za-z_][\w\.\->:\(\)\*]*?(?:\.|->|::))?(?P<name2>{name})\s*(?:\+\+|--)"
)

# Constructor member-initializer list entry. Three shapes we accept:
#   1. starts with `:`  (first entry on the colon line)        :  name(expr)
#   2. preceded by `,`  (subsequent entry on the colon line)   ,  name(expr)
#   3. standalone line that is JUST `name(expr)` or `name{expr}` optionally
#      followed by `,` or `{` (continuation lines of a multi-line init list)
CPP_CTOR_INIT = re.compile(
    r"""(?:^\s*:\s*|,\s*)
        (?P<name>{name})\s*[\({]\s*(?P<rhs>[^){}]*)\s*[\)}]""",
    re.VERBOSE,
)
CPP_CTOR_INIT_CONT = re.compile(
    r"""^\s*
        (?P<name>{name})\s*[\({]\s*(?P<rhs>[^){}]*)\s*[\)}]
        \s*(?:,|\{)?\s*(?://.*)?$""",
    re.VERBOSE,
)

CPP_COND_HINT = re.compile(r"^\s*(if|else if|while|for|switch|case)\b")

# ---------- HLASM ----------

HLASM_DEFN = re.compile(r"^\s*(?P<name>{name})\s+(?P<mnem>DS|DC|EQU)\s+(?P<rhs>\S.*?)\s*(?:\s+\S.*)?$", re.IGNORECASE)
HLASM_STORE = re.compile(
    r"""^\s*(?:[A-Za-z@#$_][\w@#$_]*\s+)?
        (?P<mnem>ST|STH|STC|STM|STD|STE|STG|MVC|MVI|MVCL|XC|OC|NC|TR)\s+
        (?P<args>.+?)\s*(?:\s{2,}.*)?$""",
    re.VERBOSE | re.IGNORECASE,
)
HLASM_BRANCH = re.compile(r"^\s*(?:\S+\s+)?(B|BC|BE|BNE|BL|BH|BNL|BNH|BZ|BNZ|BM|BP|BO|BNO|J|JE|JNE|JL|JH|JLE|JHE)\b", re.IGNORECASE)


def _sub(pat: re.Pattern, name: str) -> re.Pattern:
    return re.compile(pat.pattern.replace("{name}", re.escape(name)), pat.flags)


def classify_cpp(line: str, name: str) -> dict:
    # 1. Assignment first — it's the most common setter and avoids being shadowed
    #    by the broader definition regex.
    m = _sub(CPP_ASSIGN, name).match(line)
    if m and m.group("name") == name:
        out = {"role": "setter", "lhs": name, "op": m.group("op"), "rhs": m.group("rhs").strip()}
        if m.group("qual"):
            out["qualifier"] = m.group("qual") + m.group("sep")
        return out

    # 2. ++ / --
    m = _sub(CPP_INCREMENT, name).match(line)
    if m:
        return {"role": "setter", "lhs": name, "op": "++/--", "rhs": name}

    # 3. Constructor member-initializer list entry: `: foo(rhs)`, `, foo{rhs}`,
    #    or a continuation line that's just `foo(rhs)` / `foo{rhs}` followed by
    #    `,` or `{`. The continuation form is only a setter if the line truly
    #    looks like an init-list entry — we reject lines that look like
    #    function calls (e.g. `foo(x);` on its own would be ambiguous, but
    #    those end in `;` and the regex requires `,` or `{` or EOL).
    m = _sub(CPP_CTOR_INIT, name).search(line)
    if m:
        return {"role": "setter", "lhs": name, "op": "ctor-init", "rhs": m.group("rhs").strip()}
    m = _sub(CPP_CTOR_INIT_CONT, name).match(line)
    if m:
        return {"role": "setter", "lhs": name, "op": "ctor-init", "rhs": m.group("rhs").strip()}

    # 4. Definition — including struct/class field (the agent confirms via
    #    extract_context whether the enclosing block is a struct/class).
    m = _sub(CPP_DEFN, name).match(line)
    if m:
        rhs = m.group("rhs")
        role = "definition" if rhs is None else "setter"
        out: dict = {"role": role, "lhs": name}
        if m.group("qualifier"):
            out["qualifier"] = m.group("qualifier") + "::"
        if rhs is not None:
            out["rhs"] = rhs.strip()
        # Cheap heuristic: leading indent + no extern/storage-class hints suggests
        # a struct/class field. The agent should still confirm with extract_context.
        if _sub(CPP_FIELD_HINT, name).match(line) and not re.search(r"\b(extern|static)\b", line.split("=")[0]):
            out["likely_field"] = True
        return out

    # 5. Reader inside a condition.
    if CPP_COND_HINT.match(line) and re.search(rf"\b{re.escape(name)}\b", line):
        return {"role": "reader", "context": "condition"}

    return {"role": "reader" if re.search(rf"\b{re.escape(name)}\b", line) else "unknown"}


def classify_hlasm(line: str, name: str) -> dict:
    if (m := _sub(HLASM_DEFN, name).match(line)):
        mn = m.group("mnem").upper()
        return {"role": "definition", "lhs": name, "mnem": mn, "rhs": m.group("rhs").strip()}
    if (m := HLASM_STORE.match(line)):
        args = m.group("args")
        parts = [p.strip() for p in args.split(",", 1)]
        mn = m.group("mnem").upper()
        if mn in ("MVC", "MVI", "MVCL", "XC", "OC", "NC", "TR"):
            target, src = (parts + [""])[:2]
        else:
            src, target = (parts + [""])[:2]
        if re.search(rf"(?<![A-Za-z0-9@#$_]){re.escape(name)}(?![A-Za-z0-9@#$_])", target):
            return {"role": "setter", "lhs": name, "mnem": mn, "rhs": src}
        return {"role": "reader", "mnem": mn}
    if HLASM_BRANCH.match(line):
        return {"role": "reader", "context": "branch"}
    return {"role": "unknown"}


def classify(line: str, lang: str, name: str) -> dict:
    if lang == "cpp":
        return classify_cpp(line, name)
    if lang in ("asm", "mac"):
        return classify_hlasm(line, name)
    return {"role": "unknown"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--lang")
    ap.add_argument("--line")
    ap.add_argument("--stdin", action="store_true",
                    help="Read JSONL records from find_variable.py on stdin.")
    args = ap.parse_args()

    if args.stdin:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            rec = json.loads(raw)
            res = classify(rec["raw_line"], rec["lang"], args.name)
            print(json.dumps({**rec, **res}, ensure_ascii=False))
        return 0

    if not args.lang or args.line is None:
        print("error: provide --lang and --line, or use --stdin", file=sys.stderr)
        return 2
    print(json.dumps(classify(args.line, args.lang, args.name), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
