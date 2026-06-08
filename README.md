# explanation_harness

A pi-based harness for explaining variables in mixed C/C++ + HLASM-assembly codebases — including variables defined as struct or class fields in `.hpp` headers and written via member-access (`obj.field`, `ptr->field`), qualified writes (`Class::field`), or constructor initializer lists.

The harness is one skill — `variable-trace` — plus three heuristic Python helpers. The skill encodes the traversal algorithm; the helpers narrow the search space so the agent doesn't grep blindly.

## Layout

```
.pi/
├── APPEND_SYSTEM.md                # auto-loaded: appended to pi's default coding-agent system prompt
├── settings.json                   # auto-loaded: sessionDir + timeout + retry tuning
├── sessions/                       # per-conversation JSONL transcripts (gitignored)
└── skills/
    └── variable-trace/
        ├── SKILL.md                # the procedure (5 phases: locate → classify → setter graph → recurse → render)
        ├── scripts/
        │   ├── find_variable.py    # phase 1: walk .asm/.cpp/.hpp/.mac + word-boundary grep
        │   ├── classify_line.py    # phase 2: heuristic role (definition / setter / reader / macro / unknown)
        │   │                        #  - definitions flagged `likely_field` when they look like struct/class fields
        │   │                        #  - setters carry a `qualifier` for member writes (obj.foo, ptr->foo, Class::foo)
        │   │                        #  - ctor-init list entries (multi-line) classified as `op: ctor-init`
        │   └── extract_context.py  # phase 3: return enclosing block + an `enclosing` header line
        │                            #  - for fields: the struct/class line (so `defined-in` can name the owner)
        │                            #  - for setters: the function header
        └── references/
            ├── language-patterns.md  # syntax cheat-sheet per language (incl. struct-field forms)
            └── output-format.md      # tree + JSON output schema
```

**Files pi auto-discovers in this layout:**

| Path | What pi does with it |
|------|----------------------|
| `.pi/APPEND_SYSTEM.md` | Appended to the default system prompt at startup |
| `.pi/SYSTEM.md` (if present) | *Replaces* the default system prompt entirely (we use APPEND_SYSTEM.md instead — safer) |
| `.pi/settings.json` | Merged on top of `~/.pi/agent/settings.json` |
| `.pi/skills/*/SKILL.md` | Skill discovery; visible in `pi config` |
| `.pi/prompts/*.md` | Prompt *templates* for slash-command expansion (not used here yet) |
| `.pi/sessions/` | JSONL transcript per conversation (because we set `sessionDir: "sessions"`) |

## How it works

When the user asks "where does `<var>` get its value?" or "what affects `<var>`?":

1. **Locate** — `find_variable.py NAME --root <repo>` lists every occurrence as JSONL.
2. **Classify** — pipe through `classify_line.py --name NAME --stdin` to label each line: definition, setter, reader, macro, unknown.
3. **For each setter** — open the file, find the controlling condition (IF / branch / WHEN), capture the RHS, union the identifiers in (condition ∪ RHS) as dependents.
4. **Recurse** — repeat 1–3 for each dependent variable, up to `max_depth` (default 3). Stop on external inputs, constants, cycles, or depth limit.
5. **Render** — emit a tree with every node carrying `defined-in`, `set-at`, `condition`, `rhs`, `depends-on`. End with a summary of external inputs and constants.

## Running

The skill targets [pi](https://pi.dev) but the same Agent Skills format works
with Claude Code and any agent that loads `.pi/skills/` or `.claude/skills/`.

To use with pi:

```bash
# install pi (npm or curl)
npm install -g --ignore-scripts @earendil-works/pi-coding-agent
# or
curl -fsSL https://pi.dev/install.sh | sh

# from this directory, just launch pi — everything is auto-discovered:
pi
> explain premiumRate in /path/to/codebase
```

When pi starts in this directory it picks up `.pi/APPEND_SYSTEM.md`,
`.pi/settings.json`, and the `.pi/skills/variable-trace/` skill automatically —
no CLI flags needed. The system prompt biases the agent toward loading and
following the skill for variable-explanation questions; it does not replace
the skill.

To use with Claude Code, symlink or copy the skill folder into `.claude/skills/`.

## Testing the helpers

A fixture lives at `/tmp/explain_test/` (created during setup). Try:

```bash
python3 .pi/skills/variable-trace/scripts/find_variable.py premiumRate --root /tmp/explain_test \
  | python3 .pi/skills/variable-trace/scripts/classify_line.py --name premiumRate --stdin
```

You should see one `definition (likely_field, owner: struct Policy)`, a `setter (op: ctor-init)`, multiple `setter (op: =/+=, qualifier: p./p->)` lines under controlling `if`s, and finally a `setter` in `resetPremium`. Fixture lives at `/tmp/explain_test/` (`policy.hpp`, `pricing.cpp`, `store.mac`).

## Limits

- The classifier is regex-based — pointer aliasing, whole-struct assignments (`*p = other`, `s1 = s2`), macro expansion, and multi-line statements other than ctor-init lists land in `unknown`. The skill instructs the agent to read the file directly on those cases.
- Identifier aliases across the C↔assembly boundary (`wsPremiumRate` ↔ `WS_PREMIUM_RATE`) are not auto-reconciled — the agent has to query each spelling and link them via linker symbols / `extern "C"` declarations / inline-asm references.
- No data-flow through function calls without reading the callee. The skill expects the agent to follow function invocations explicitly when a value is passed by reference / pointer or returned through an out-parameter.
