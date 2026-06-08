---
name: variable-trace
description: Trace where a variable is defined, set, and what controls its value across a C/C++ (.cpp/.cc/.cxx/.c/.hpp/.hxx/.h) and HLASM assembly (.asm/.s/.mac) codebase. Resolves struct/class fields in headers (the field declaration is the definition; writes happen via obj.field, ptr->field, Class::field, or constructor initializer lists). Builds a recursive dependency chain — for each setter, captures the controlling conditions and right-hand side, then recurses on every dependent variable. Use when the user asks "where does <var> get its value?", "what affects <var>?", or asks to explain a variable.
license: MIT
---

# variable-trace

A guided traversal for explaining a variable. Do NOT grep the whole tree blindly — follow the procedure below. Each phase narrows the search before the next.

## Inputs

- `target` — the variable name to explain (case-sensitive in C/C++; HLASM is case-insensitive but conventionally uppercase).
- `root` — directory to search. Default: the current working directory.
- `max_depth` — recursion depth for dependent variables. Default: 3. Stop sooner if a variable resolves to a literal, constant, or external input.

## Output

A dependency chain rendered as a tree. Every node has the schema:

```
<variable>
├── defined-in:     <file>:<line>      # declaration / field in struct / DS / DCL
│                                       # for a field, also note its owning type (e.g. "field of struct Policy")
├── set-at:                            # list of assignment sites
│   └── <file>:<line>
│       ├── qualifier:  <obj.|ptr->|Class::|none>   # only present for member writes
│       ├── condition:  <expr>         # IF/branch test guarding this write (omit if unconditional)
│       ├── rhs:        <expr>         # right-hand side as written
│       └── depends-on: [var1, var2…]  # union of identifiers in condition + rhs
└── children:                          # one subtree per dependent
    └── <var1> → …
```

When a node is a literal, parameter, external symbol (extern, function argument), or already-visited variable, mark it as a leaf and stop.

## The Procedure

### Phase 1 — Locate

Run `scripts/find_variable.py <target> --root <root>` to get every occurrence with its file type. The helper:
1. Walks `.asm .s .S .cpp .cc .cxx .c .hpp .hxx .h .mac` files.
2. Greps for the identifier with word boundaries appropriate to the language (C/C++ uses `\b` — which also matches the bare name after `.`, `->`, or `::`; HLASM allows `@#$_`).
3. Returns JSON: `{file, line, lang, raw_line}`.

If the helper returns no hits → report "variable not found" and stop. Do not guess synonyms; ask the user.

### Phase 2 — Classify each occurrence

For every hit, decide which **role** it plays. The role determines whether it contributes to the chain.

| Role | Counts toward chain? | Examples |
|---|---|---|
| `definition` | yes — record as `defined-in` | C/C++: `int FOO;`, `extern int FOO;`, `static int FOO = 0;`, struct/class field `int FOO;` inside a `struct X { … };` body, out-of-class static `int X::FOO = 0;`<br>HLASM: `FOO DS F`, `FOO DC F'0'`, `FOO EQU *` |
| `setter` | yes — add to `set-at` | C/C++: `FOO = …;`, `FOO += …;`, `obj.FOO = …;`, `ptr->FOO = …;`, `Class::FOO = …;`, ctor member-init `: FOO(…)` or `, FOO(…)`, `&FOO` passed to a function that writes it, `*p = …` where `p` aliases FOO<br>HLASM: `ST Rn,FOO`, `MVC FOO,X`, `MVI FOO,...`, `STH/STC/STM` |
| `reader` | no — skip | `if (FOO …)`, `MVC X,FOO`, `L Rn,FOO`, `return FOO;` |
| `parameter` | leaf — record as external input | function parameter, function pointer argument written through |
| `macro` | follow through | HLASM `.mac` macro substitution, C `#define` / `#include` |

When the classifier reports `likely_field: true` on a definition, the variable is most likely a struct/class field. Confirm by running `extract_context.py` on the definition line: the `enclosing` field of its output will say `struct …`, `class …`, or `union …`. Record the owning type in `defined-in`.

Reference cheat-sheet: `references/language-patterns.md`.

### Phase 3 — Build the setter graph

For each `setter` site:

1. **Extract the RHS** — everything to the right of the assignment operator (or the source field of MVC / source register of ST). Read enough surrounding lines to capture continuations (C/C++ statement until `;`; HLASM continuation column 72; constructor init lists may span many lines starting at `:`).
2. **Extract the controlling condition** — walk outward from the setter line to find the nearest enclosing predicate:
   - C/C++: nearest `if (…)`, `else if (…)`, `case`, `while (…)`, `for (…)`, ternary, with all `&&`/`||` operands intact. For setters inside a constructor body, the controlling condition is the constructor's logic (often unconditional); for ctor-init list entries, it's unconditional by construction.
   - HLASM: nearest `BC/BNE/BE/CLC/CL/CR…` branch sequence reaching this line; record the comparison plus the branch mnemonic.
   - If the setter sits at the top of a function with no guard, omit the condition.
3. **Union the identifiers** in (condition ∪ rhs). Strip literals, operators, intrinsic calls. The remaining symbols are this setter's dependents. For member writes like `p.premiumRate = p.baseRate * p.riskFactor`, the dependents are the *member names* (`baseRate`, `riskFactor`) — not the instance variable `p`, unless the user explicitly cares about which instance was written.

### Phase 4 — Recurse

For each dependent variable not yet visited and within `max_depth`:
1. Add it to the visited set (variable name + scope, so two locals named `i` in different functions are distinct).
2. Re-run Phase 1–3 with the dependent as the new `target`.
3. Attach the result as a child node.

Stop recursing on a branch when any of:
- The variable is a function parameter, `extern` whose definition isn't in the tree, or an argument passed by the caller → mark `external-input` and stop.
- The variable is a constant / `EQU` / `#define` / `constexpr` / `static const` initialised to a literal → mark `constant=<value>` and stop.
- `max_depth` reached → mark `truncated` and stop.
- Already-visited → mark `cycle → <node-id>` and stop.

### Phase 5 — Render

Emit the tree in the schema shown above. Always include file:line references so the user can jump. At the end, list:
- Every external input encountered (these are the true inputs to the variable).
- Every constant encountered.
- Any setter site you could not classify (be honest — note the file:line and why).

## Helper scripts

- `scripts/find_variable.py` — phase 1 locator. Usage: `find_variable.py NAME [--root DIR] [--lang asm,cpp,mac]`.
- `scripts/extract_context.py` — given `file:line`, returns the enclosing block (function / struct / class / CSECT) plus an `enclosing` field with the header line itself. Usage: `extract_context.py PATH LINE [--lang auto]`.
- `scripts/classify_line.py` — given a raw line + language, returns `{role, lhs, rhs, qualifier, op, likely_field}` using regex heuristics. Use it to bulk-filter; verify the borderline cases by reading the file.

These are heuristics — they will misclassify macro-heavy or pointer-aliased code. When `classify_line.py` returns role `unknown`, open the file and decide.

## When NOT to use this skill

- The user wants a one-line definition of a variable → just `read` the declaration file.
- The user wants every caller of a function → use a normal grep, not this skill.
- The variable is a database column / config key, not a code symbol → different problem.

## Worked example

User: "Where does `premiumRate` get its value?"

1. Run `find_variable.py premiumRate` → 6 hits across `policy.hpp` and `pricing.cpp`.
2. Classify:
   - `policy.hpp:7` — `double premiumRate;` → `definition`, `likely_field: true`. `extract_context.py policy.hpp 7` confirms `enclosing: struct Policy {`. Record **defined-in: policy.hpp:7 (field of struct Policy)**.
   - `pricing.cpp:9` — `premiumRate(0.0) {` → `setter, ctor-init, rhs="0.0"`. Unconditional. Deps: none (literal).
   - `pricing.cpp:15` — `p.premiumRate = p.baseRate * p.riskFactor` under `if (p.policyType == 1)` (from line 14). Deps: `baseRate`, `riskFactor`, `policyType`.
   - `pricing.cpp:17` — `p.premiumRate = p.defaultRate` (ELSE branch). Deps: `defaultRate`.
   - `pricing.cpp:20` — `p.premiumRate += 5.0` under `if (p.overrideEnabled)`. Self-dep on `premiumRate` (compound op reads prior value) + dep on `overrideEnabled`.
   - `pricing.cpp:25` — `p->premiumRate = 0.0` (unconditional, inside `resetPremium`). Deps: none.
3. Recurse on `baseRate`, `riskFactor`, `policyType`, `defaultRate`, `overrideEnabled` with `max_depth=3`. Most resolve to ctor-init constants in `policy.hpp` (literals / `100.0` / `false`) → leaves.
4. Render tree. Summary: external inputs = none (all initialised in the ctor or by `computePremium`'s caller); constants = `defaultRate = 100.0`, `overrideEnabled = false`.
