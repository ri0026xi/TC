 # Workflow Guidelines

 > Project-specific workflow principles for everything-claude-code. Complements
 > `rules/common/development-workflow.md` with planning-first discipline,
 > self-improvement loops, and minimal-impact engineering practices.

 ## Planning Discipline

 Default to `/plan` (invoking the `planner` agent) before writing code:

 - **Always plan**: 3 or more steps, multiple files, or any change to `hooks/`, `scripts/`, `agents/`, or `rules/`
 - **Act directly**: 1–2 isolated steps in a single file with clear intent
 - **Re-plan mid-task**: if the approach stops working, stop and re-plan rather than forcing a path forward
 - Write a concrete spec (what, why, acceptance criteria) before implementation begins

 ## Sub-Agent Strategy

 Protect the main context window by delegating to agents:

 - Use `planner` for feature breakdown and phase ordering
 - Use `code-reviewer` immediately after writing or modifying code
 - Launch independent analyses in parallel — see `rules/common/agents.md`
 - One task per agent; do not bundle planning + reviewing + implementing into a single agent turn

 Parallel launch example:
 ```text
 Analyzing a large change → launch in parallel:
   Agent 1: code-reviewer — correctness and quality
   Agent 2: security-reviewer — trust boundaries
   Agent 3: tdd-guide — test coverage gaps

 Self-Improvement Loop

 Record patterns so the same mistake does not repeat:

 - After resolving a non-trivial bug or discovering a reusable pattern: run /learn
 - /learn persists lessons to ~/.claude/skills/learned/ — available in future sessions
 - For session continuity: /save-session at end, /resume-session at start
 - For project-scoped notes: write to ~/.claude/projects/C--Users-ktm-a-everything-claude-code/memory/

 Mapping from traditional tasks/ approach:
 - tasks/todo.md → /plan output + /checkpoint create <name> for snapshots
 - tasks/lessons.md → /learn → ~/.claude/skills/learned/

 Verification Before Completion

 A task is complete only when behavior is proven, not when the code looks right:

 - Run node tests/run-all.js — all tests must pass
 - Run npx markdownlint-cli '**/*.md' --ignore node_modules for any .md changes
 - Run /verify to catch CRITICAL and HIGH issues before committing
 - Use /checkpoint verify to confirm known-good state

 Do not mark work done while any of these fail.

 Minimal Impact

 Prefer surgical changes over broad rewrites:

 - Touch the fewest files necessary
 - Extend existing modules before creating new ones — add helpers to scripts/lib/ rather than new top-level scripts
 - New hooks must use the run-with-flags.js wrapper; do not bypass ECC gating
 - No speculative abstractions: solve the stated problem, not an imagined future problem
 - Hook scripts must stay under 200 lines (project rule from node.md)

 Autonomous Bug Fixing

 When a test or build fails:

 1. Read the full error message before acting
 2. Find the root cause — do not patch symptoms
 3. Fix, then re-run the failing check to confirm resolution
 4. If the fix spans 3+ files, pause and create a sub-plan with /plan
 5. If the bug class is novel to this codebase, capture it with /learn

 CI failures are not someone else's problem — fix them proactively.

 Elegance Check

 Before implementing a non-trivial change, ask once: "Is there a simpler approach?"

 - If the fix feels hacky, step back: "Given everything I now know, what is the elegant solution?"
 - Skip this check for simple, obvious single-line fixes — do not over-engineer
 - Apply it before proposing a solution, not after the code is written

 ECC Command Reference

 ┌────────────────────────────┬────────────────────────────────────┐
 │            Need            │          Command / Agent           │
 ├────────────────────────────┼────────────────────────────────────┤
 │ Plan a feature or change   │ /plan → planner agent              │
 ├────────────────────────────┼────────────────────────────────────┤
 │ Track progress checkpoints │ /checkpoint create <name>          │
 ├────────────────────────────┼────────────────────────────────────┤
 │ Record a lesson            │ /learn                             │
 ├────────────────────────────┼────────────────────────────────────┤
 │ Save session state         │ /save-session                      │
 ├────────────────────────────┼────────────────────────────────────┤
 │ Resume previous session    │ /resume-session                    │
 ├────────────────────────────┼────────────────────────────────────┤
 │ Verify before commit       │ /verify                            │
 ├────────────────────────────┼────────────────────────────────────┤
 │ Fix build errors           │ /build-fix                         │
 ├────────────────────────────┼────────────────────────────────────┤
 │ Review written code        │ /code-review → code-reviewer agent │
 └────────────────────────────┴────────────────────────────────────┘

 ## Files to Reference (Read-Only)

 - `.claude/rules/node.md` — format reference for the new file
 - `.claude/rules/everything-claude-code-guardrails.md` — avoid duplication
 - `rules/common/development-workflow.md` — avoid duplication (baseline workflow)
 - `rules/common/agents.md` — agent strategy reference

 ## Verification

 After creating the file:
 1. Run `npx markdownlint-cli '.claude/rules/workflow-guidelines.md'` — must pass clean
 2. Spot-check that no section duplicates `rules/common/development-workflow.md`
 3. Confirm all referenced commands exist in `commands/` (plan, learn, checkpoint, verify, build-fix, code-review,
 save-session, resume-session — all confirmed present)
 4. Confirm all referenced agents exist in `agents/` (planner, code-reviewer, security-reviewer, tdd-guide — confirmed
 present)