# Language pattern cheat-sheet

Quick reference for classifying lines in phase 2 of `variable-trace`. When a
line looks ambiguous, find its pattern here. Anything not in this file is
"unknown" — read the file directly.

## C / C++ (`.c .cpp .cc .cxx .h .hpp .hxx`)

### Definitions

| Form | Where it lives | Role |
|---|---|---|
| `int foo;` `extern int foo;` `static int foo;` | namespace / file scope | definition (declaration without initializer) |
| `int foo = expr;` `static const int foo = 1;` `constexpr int foo = …;` | namespace / file scope | definition + setter (combined) |
| `int Class::foo = 0;` | out-of-class definition of a `static` member | definition |
| `int foo;` `double foo = 0.0;` | **inside `struct`/`class`/`union` body** | definition — this is a **field**. `classify_line.py` flags `likely_field: true` and `extract_context.py` returns the enclosing struct/class line. Record the owning type in `defined-in`. |
| `#define FOO …` | preprocessor | definition (macro) — expand at each use site |
| `extern "C" { … }` `namespace ns { … }` | scope marker | not a role; affects identifier lookup |

### Setters

| Form | Notes |
|---|---|
| `foo = expr;` | bare assignment |
| `foo += expr;` `foo -=` `foo *=` `foo /=` `foo %=` `foo &=` `foo \|=` `foo ^=` `foo <<=` `foo >>=` | compound op — RHS reads the prior value of `foo` (record a self-dep) |
| `++foo;` `foo--;` | self-dep |
| `obj.foo = expr;` `ptr->foo = expr;` `(*ptr).foo = expr;` | member write. `classify_line.py` captures the qualifier in the `qualifier` field |
| `Class::foo = expr;` | static member write |
| `obj.foo[i] = expr;` `obj.foo.sub = expr;` | aggregate write into a field — record the path |
| `: foo(rhs)` `, foo(rhs)` `, foo{rhs}` | constructor member-initializer-list entry. Continuation lines (just `foo(rhs)` followed by `,` or `{`) are also detected — `classify_line.py` reports `op: ctor-init`. Unconditional by construction. |
| `*p = expr;` where `p` aliases `&foo` | requires alias analysis; classifier marks `unknown` — agent must read |
| `func(&foo)` where `func` writes through the pointer | likewise — read the callee signature |
| `if (foo …)` `while (foo …)` `case foo:` `?:` | reader — the surrounding test is the *condition* for any setter inside that branch |

**Multi-line statements:** statements end at `;`; constructor initializer lists span many lines from `:` to `{`. `extract_context.py` returns the enclosing function/struct/class so you can find the `:` line and walk down from there.

**Aliasing pitfalls:**
- A `T*` or `T&` passed to any function with a non-const signature is potentially written.
- Templates and macros can hide assignments. If `foo` appears in a `#define`'d helper, expand it.
- A write to a parent object (`obj = other`) writes every field, including `foo`. The classifier won't catch that — read the file when you see a struct-typed assignment.

### Identifying the owning type for a field

When `classify_line.py` reports `likely_field: true` for a definition, run:

```
extract_context.py <file> <line>
```

The output's `enclosing` field is the header line of the nearest enclosing
block — e.g. `struct Policy {` or `class Engine : public Base {`. Use the type
name in `defined-in` so the user sees the field's owner.

If `enclosing` is a function header instead (e.g. inside a lambda or local
struct), the variable is *not* a member of a top-level type — record it as a
local definition.

## HLASM / IBM Assembler (`.asm .s .S .mac`)

Column layout: label cols 1–8 (or up to first space), opcode after, operands after that, optional comment after two spaces, continuation column 72.

### Definitions
| Form | Role |
|---|---|
| `NAME DS F` `NAME DS CL10` | definition (storage allocation, no initial value) |
| `NAME DC F'0'` `NAME DC C'X'` | definition + setter (initial constant) |
| `NAME EQU *` `NAME EQU 16` | definition (absolute / address alias — usually a constant) |
| `NAME CSECT` `NAME DSECT` | section symbol — not a variable |

### Setters (stores)
Memory-target operand position differs by mnemonic:

| Mnem | Operand order | Target | Source |
|---|---|---|---|
| `ST`, `STH`, `STC`, `STM`, `STD`, `STE`, `STG` | `reg, mem` | second | first |
| `MVC`, `MVCL`, `MVI`, `XC`, `OC`, `NC`, `TR` | `mem, src` | first | second |
| `LA Rn, NAME` | loads *address* into reg — neither setter nor reader of NAME's value |

### Conditions (branch chain)
HLASM doesn't have block-structured if. Walk backward from the setter looking for `CL`, `CLC`, `CLI`, `C`, `CH`, `LTR`, `CR`, `CLR` (the comparison that set the condition code) and the `B*` branch that gates the setter. Record both as the condition.

Examples:
```
         CL    R3,POLICY_TYPE
         BNE   SKIP                   <-- skips to SKIP if not equal
         ST    R5,PREMIUM_RATE        <-- this setter is guarded by R3 = POLICY_TYPE
SKIP     DS    0H
```

### Macros (`.mac`)
- `MACRO ... MEND` defines a macro; symbols starting with `&` are macro parameters.
- `&SYSPARM`, `&SYSDATE` etc. are external inputs.
- A `.mac` file referenced by `MACRO` or `COPY` substitutes textually; if a variable's only setter is inside a macro body, the macro expansion is the real setter site.

## Mixed-language identifier aliasing

The *same logical variable* may appear under different spellings:

| Convention | Example |
|---|---|
| C/C++ (camelCase or snake) | `wsPremiumRate` or `ws_premium_rate` |
| HLASM (underscores, no hyphens, often uppercase) | `WS_PREMIUM_RATE` |

When tracing across a C↔assembly boundary (inline `asm` blocks, linkage via
`extern "C"`, callable services), query phase 1 under all plausible spellings
and reconcile by data type and surrounding context. The skill should *not*
assume two spellings refer to the same value without evidence (a CALL site,
a marshal helper, a linker map, or a comment).
