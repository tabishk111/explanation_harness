# Output rendering

When phase 5 of the procedure runs, emit the chain as a tree. Two acceptable formats — pick by audience:

## Human-readable tree (default)

```
premiumRate
  defined-in: policy.hpp:7   (field of struct Policy — "double premiumRate;")
  set-at:
    [1] pricing.cpp:9   premiumRate(0.0) {
        qualifier: ctor-init (Policy::Policy)
        condition: unconditional
        rhs: 0.0
        depends-on: (none — literal)
    [2] pricing.cpp:15  p.premiumRate = p.baseRate * p.riskFactor
        qualifier: p.
        condition: if (p.policyType == 1)                (pricing.cpp:14)
        rhs:       p.baseRate * p.riskFactor
        depends-on: baseRate, riskFactor, policyType
    [3] pricing.cpp:17  p.premiumRate = p.defaultRate
        qualifier: p.
        condition: ELSE of pricing.cpp:14
        rhs:       p.defaultRate
        depends-on: defaultRate
    [4] pricing.cpp:20  p.premiumRate += 5.0
        qualifier: p.
        condition: if (p.overrideEnabled)                (pricing.cpp:19)
        rhs:       5.0
        depends-on: premiumRate (self), overrideEnabled
    [5] pricing.cpp:25  p->premiumRate = 0.0
        qualifier: p->
        condition: unconditional (inside resetPremium)
        rhs:       0.0
        depends-on: (none — literal)

  ├── baseRate        (field of struct Policy at policy.hpp:5)
  │     set-at:
  │       [1] pricing.cpp:7  : baseRate(0.0)   (ctor-init, unconditional)
  │     → constant=0.0 on every construction
  │
  ├── riskFactor      (field of struct Policy at policy.hpp:6)
  │     set-at:
  │       [1] pricing.cpp:8  : riskFactor(1.0) (ctor-init, unconditional)
  │     → constant=1.0
  │
  ├── policyType      (field of struct Policy at policy.hpp:4)
  │     set-at:
  │       [1] pricing.cpp:6  : policyType(0)   (ctor-init, unconditional)
  │     → constant=0 on construction; no other writes found → external-input (caller mutates)
  │
  ├── defaultRate     (field of struct Policy at policy.hpp:8)
  │     defined-in field has VALUE: defaultRate = 100.0
  │     → constant=100.0
  │
  └── overrideEnabled (field of struct Policy at policy.hpp:9)
        defined-in field has VALUE: overrideEnabled = false
        no other writes found → external-input (caller mutates)

External inputs: policyType (caller of computePremium), overrideEnabled (caller)
Constants:       defaultRate = 100.0, baseRate = 0.0 (ctor), riskFactor = 1.0 (ctor)
Unclassified:    (none)
```

## Machine-readable JSON

For piping into other tools:

```json
{
  "target": "premiumRate",
  "definedIn": {
    "file": "policy.hpp", "line": 7,
    "form": "double premiumRate;",
    "owner": {"kind": "struct", "name": "Policy"}
  },
  "setAt": [
    {
      "file": "pricing.cpp", "line": 15,
      "raw": "p.premiumRate = p.baseRate * p.riskFactor;",
      "qualifier": "p.",
      "condition": {"file": "pricing.cpp", "line": 14, "expr": "p.policyType == 1"},
      "rhs": "p.baseRate * p.riskFactor",
      "dependsOn": ["baseRate", "riskFactor", "policyType"]
    }
  ],
  "children": { "...": "recursive same shape" },
  "externalInputs": ["policyType", "overrideEnabled"],
  "constants":      [{"name": "defaultRate", "value": "100.0"}],
  "unclassified":   []
}
```

## Rules

- Always print absolute file paths or paths relative to the root the user supplied — never just the basename.
- Always include the line number.
- For a struct/class field, include the owning type in `defined-in` (e.g. "field of struct Policy"). Get it from `extract_context.py`'s `enclosing` output.
- For member writes (`obj.foo`, `ptr->foo`, `Class::foo`), include the `qualifier` field so the user can see *which* instance is being written.
- For `condition`, quote the source line verbatim. Do not paraphrase.
- When a setter is unconditional, say `unconditional` — do not omit the field silently.
- When recursion stopped early, say why: `external-input`, `constant=<v>`, `cycle → <node>`, `truncated (max_depth)`.
- The trailing `External inputs / Constants / Unclassified` summary is the user's actionable list. Keep it accurate even if it duplicates info already in the tree.
