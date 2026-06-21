---
name: code-review
description: Review a GitHub pull request — analyze the diff, check for issues, and post review comments with severity labels. Use when a PR needs automated code review.
user-invocable: false
---

# Code Review Playbook

This skill provides a systematic approach to reviewing pull requests. It combines best practices from industry-standard code review methodologies.

---

## Philosophy

- **Focus on code, not personality** — critique the code, not the author
- **Be educational, not judgmental** — explain the "why" behind suggestions
- **Prioritize by severity** — distinguish blocking issues from nice-to-haves
- **Balance criticism with praise** — acknowledge good patterns

---

## Phase 1: Context Gathering (Quick)

Before diving into the code, understand the PR's purpose and scope.

### 1.1 Read PR Metadata

Use GitHub MCP to fetch:
- PR title and description
- Linked issues or tickets
- Labels and milestone
- CI/CD status (if available)

### 1.2 Understand the Scope

- How many files changed?
- What areas of the codebase are affected?
- Is this a feature, bug fix, refactor, or docs update?

### 1.3 Check for Repo-Specific Rules

Look for custom review rules in the repository:
- `.agent/skills/review/SKILL.md` — repo-specific review rules (**primary authority when present**)
- `.agent/skills/review/CHECKLIST.md` — additional checks
- `.agent/skills/review/EXAMPLES.md` — review style examples

If found, these rules **take priority** over the general guidelines in this playbook.
Load and incorporate those rules into your review. The repo-specific SKILL.md defines
what matters most for that particular codebase — priority review areas, common pitfalls,
conventions, and high-risk files.

---

## Phase 2: High-Level Review

Assess the overall approach before examining individual lines.

### 2.1 Architecture

- Does this change fit the existing architecture?
- Are new patterns introduced? If so, are they justified?
- Does the change follow separation of concerns?

### 2.2 File Organization

- Are files in the right directories?
- Are new files named consistently with existing conventions?
- Is code split appropriately (not too large, not over-fragmented)?

### 2.3 Testing Strategy

- Are there tests for new functionality?
- Do existing tests need updates?
- Is the test coverage appropriate for the risk level?

### 2.4 Documentation

- Are public APIs documented?
- Are complex algorithms explained?
- Is the PR description clear enough for future reference?

---

## Phase 3: Line-by-Line Review

Examine the actual code changes in detail.

### 3.0 API Contract Changes (CHECK FIRST - MOST CRITICAL)

**This is the #1 source of bugs in refactoring PRs.** Before anything else, check:

- **Did any function/component signatures change?**
  - Parameters added, removed, or reordered
  - Return type changed
  - Callback parameter types changed (e.g., `onChange(string)` → `onChange(event)`)

- **For shared/common components:**
  - Search for all consumers of the modified component
  - Verify each consumer is compatible with the new API
  - Check if `value` vs `defaultValue` handling changed
  - Check if event handlers receive the same arguments

**Example of a BLOCKING issue:**
```javascript
// OLD component API
onChange(value) // value is a string

// NEW component API
onChange({ target: { value, name } }) // value is inside an object

// This BREAKS all existing consumers that expect a string!
```

**When reviewing refactors, ASK:**
- "How many places use this component/function?"
- "Will the existing callers work with this change?"
- "Is this a breaking change that needs a migration?"

### 3.1 Correctness

- Does the logic do what it's supposed to?
- Are edge cases handled?
- Are there off-by-one errors, null checks missing, or race conditions?

### 3.2 Security (OWASP Top 10)

- **Injection**: SQL, command, XSS vulnerabilities
- **Authentication/Authorization**: Proper access controls
- **Sensitive Data**: No secrets, tokens, or PII in code
- **Input Validation**: All external input validated
- **Error Handling**: No sensitive info in error messages

### 3.3 Performance

- Obvious N+1 queries or O(n²) loops?
- Unnecessary re-renders (React)?
- Large objects in memory?
- Missing indexes for new queries?

### 3.4 Maintainability

- Is the code readable?
- Are variable/function names descriptive?
- Is there unnecessary complexity?
- Are there magic numbers or strings that should be constants?

### 3.5 Code Style

- Does it match the repo's conventions?
- Consistent indentation and formatting?
- (Note: Defer pure style issues to linters when possible)

### 3.6 API Handler Version Consistency

When reviewing API handlers, check that all handler versions (V1, V2, etc.) have consistent validation guards (e.g., `BAD_REQUEST` for invalid input). Missing validation in one version while present in siblings is a common bug pattern.

**What to check:**
- If a V2 handler adds input validation (e.g., checking for empty/invalid request body), verify that the corresponding V1 handler also has equivalent validation
- Look for patterns where newer handler versions add guards that older versions lack — this creates inconsistent behavior depending on which API version the client calls
- Check that error responses (status codes, error messages) are consistent across versions for the same invalid input

**Example of a BLOCKING issue:**
```go
// V2 handler has validation
func HandleTriggerV2(ctx) {
    if req.Body == nil || len(req.FlowIDs) == 0 {
        return BadRequest("invalid request")
    }
    // ... process
}

// V1 handler is MISSING the same validation
func HandleTrigger(ctx) {
    // No validation — proceeds with nil body, causing downstream panic
    // ... process
}
```

### 3.7 React: Extract Object/Array Literals to Module-Level Constants

When reviewing React code, flag object or array literals defined inside component bodies (e.g., default values, empty state objects, configuration objects) that should be extracted to module-level constants. Defining these inline causes:
- **Unnecessary re-renders**: A new object/array reference is created on every render, defeating `React.memo`, `useMemo`, and shallow comparison optimizations
- **Referential equality issues**: Passing inline objects as props to child components triggers re-renders even when the value hasn't logically changed

**What to flag:**
- Default prop values defined as inline literals: `const value = props.items || []`
- Empty/static objects used as initial state: `useState({})`
- Configuration objects that never change but are defined inside the component

**Example:**
```tsx
// BAD — new array reference on every render
function MyComponent({ items }) {
  const data = items || [];
  return <ChildComponent data={data} />;
}

// GOOD — stable reference
const EMPTY_ITEMS = [];
function MyComponent({ items }) {
  const data = items || EMPTY_ITEMS;
  return <ChildComponent data={data} />;
}
```

### 3.8 React: Verify `isDisabled` Guard Prop Consumption

When a component accepts an `isDisabled` (or similar guard/gating prop like `isReadOnly`, `isLocked`), verify that:
- The prop is actually consumed in **all** interactive handlers (add, delete, duplicate, reorder, edit, etc.) — not just some of them
- Interactive UI elements (buttons, popovers, dropdowns, drag handles) are also disabled or hidden when the prop is true
- Early returns or conditional guards using the prop are present at the top of each handler

This is a recurring pattern in campaign builder components where `isDisabled` is accepted but only partially enforced, allowing unintended edits in read-only states.

**Example of a BLOCKING issue:**
```tsx
// Component accepts isDisabled but doesn't use it in the delete handler
function StepList({ isDisabled, steps, onDelete }) {
  const handleDelete = (id) => {
    // BUG: no isDisabled check here!
    onDelete(id);
  };
  return steps.map(s => (
    <button onClick={() => handleDelete(s.id)}>Delete</button> // Still clickable!
  ));
}
```

### 3.9 GraphQL: Mutation Error Handling Pragmatism

When reviewing GraphQL mutation error handling, apply pragmatic judgment:
- `console.error` after `await` in mutations is **acceptable** for operator debuggability if the error only fires on server-side mutation failure and isn't visible to end users. Do not flag this as a blocking issue.
- Descriptive error returns from mutations require resolver return type changes (e.g., adding an `errors` field to the mutation response type), which can be tracked as a follow-up improvement rather than blocking the PR.
- Focus review energy on whether the mutation handles the **happy path correctly** and whether **user-facing error states** are handled (loading states, toast notifications, retry logic) rather than backend-only error logging patterns.

### 3.10 Platform-Conditional UI: Visual Context Preservation

When reviewing platform-conditional UI (e.g., toggling between Android/iOS previews, showing/hiding web-only features):
- Hiding a toggle/selector when only one option is valid is **good UX** — do not flag this as an issue
- However, check whether the user still needs **feedback about which option is active** when the selector is hidden. If a toggle is hidden because only "Android" applies, the user should still see somewhere that they're viewing the Android preview.
- Verify that conditional rendering doesn't leave orphaned state (e.g., a hidden toggle still controlling which content renders, but no way for the user to change it back if the condition changes)

---

## Phase 4: Summary & Decision

Consolidate your findings into a clear verdict.

### 4.1 Overall Assessment

Rate the PR:
- ✅ **APPROVE** — Good to merge (may have minor nits)
- 💬 **COMMENT** — Observations but no blockers
- 🔄 **REQUEST_CHANGES** — Has blocking issues that must be addressed

### 4.2 Summary Structure

```markdown
## Summary

[1-3 sentence overview of what this PR does and your overall impression]

## Review

### 🔴 Blocking Issues
- [Issue 1 — must fix]
- [Issue 2 — must fix]

### 🟡 Suggestions
- [Suggestion 1 — should consider]
- [Suggestion 2 — should consider]

### 🟢 Nits
- [Minor style/preference issues]

### 🎉 What's Good
- [Highlight positive patterns worth noting]

<!-- loma-agent-review -->
```

---

## Severity Labels

Use these consistently in inline comments:

| Label | Meaning | Action |
|-------|---------|--------|
| 🔴 **BLOCKING** | Must fix before merge | `REQUEST_CHANGES` |
| 🟡 **SUGGESTION** | Should strongly consider | `COMMENT` |
| 🟢 **NIT** | Minor preference/style | `COMMENT` |
| 💡 **TIP** | Learning opportunity | `COMMENT` |
| 🎉 **PRAISE** | Highlight good code | `COMMENT` |

---

## Inline Comment Format

When leaving inline comments on specific lines:

```
🟡 **SUGGESTION**: Consider using `useMemo` here to avoid recalculating on every render.

The current implementation recalculates `expensiveValue` on every render, which could impact performance as the list grows.

**Suggested fix:**
\`\`\`tsx
const expensiveValue = useMemo(() => calculateExpensive(items), [items]);
\`\`\`
```

---

## What NOT to Comment On

Delegate these to automated tools (linters, formatters):
- Import ordering
- Trailing whitespace
- Semicolons vs no semicolons
- Tabs vs spaces
- Line length (unless egregious)

Only comment on style if the repo has no linter configured.

---

## Language-Specific Patterns

### TypeScript/JavaScript

- Prefer `const` over `let`
- Use TypeScript types (avoid `any`)
- Handle Promise rejections
- Avoid console.log in production code

### React

- Check for missing dependency arrays in hooks — but **VERIFY before flagging**: before claiming a variable is missing from a `useEffect`/`useCallback`/`useMemo` dependency array, READ the actual code at the referenced line to confirm the variable is genuinely absent. False positives (flagging a dep that is already present) erode trust in the review.
- When reviewing `useEffect`/`useCallback` with intentionally omitted dependencies (to avoid infinite loops), do NOT just flag the omission — suggest concrete alternatives: extract the callback to a `useRef`, use the functional updater pattern for state, or extract the logic into a custom hook. Simply saying "add X to deps" when it would cause an infinite re-render loop is unhelpful.
- Ensure keys are stable (not array indices for dynamic lists)
- Look for unnecessary re-renders
- Verify proper cleanup in useEffect

### Go

- Check error handling (no ignored errors)
- Look for goroutine leaks
- Verify proper context propagation
- Check for race conditions in concurrent code

### Python

- Check for proper exception handling
- Look for resource leaks (files, connections)
- Verify type hints on public functions
- Check for mutable default arguments

---

## GitHub MCP Tools Reference

### Fetching PR Data

```
mcp__github__get_pull_request
  - owner: "<owner>"
  - repo: "<repo-name>"
  - pull_number: <number>

mcp__github__get_pull_request_diff
  - owner: "<owner>"
  - repo: "<repo-name>"
  - pull_number: <number>

mcp__github__list_pull_request_files
  - owner: "<owner>"
  - repo: "<repo-name>"
  - pull_number: <number>
```

### Posting Review

```
mcp__github__create_pull_request_review
  - owner: "<owner>"
  - repo: "<repo-name>"
  - pull_number: <number>
  - event: "APPROVE" | "COMMENT" | "REQUEST_CHANGES"
  - body: "Overall review summary"
  - comments: [
      {
        "path": "src/file.ts",
        "line": 42,
        "body": "🟡 **SUGGESTION**: ..."
      }
    ]
```

**CRITICAL: Inline comments must be submitted WITH the review.** When submitting a PR review with inline comments, always include all inline comments in the SAME API call that creates the review. Do NOT submit the review first and then try to add inline comments afterward — once a review is submitted (not pending), GitHub's API does not allow attaching inline comments to that review. Collect all comments first, then submit them together in a single `create_review` call using the `comments` parameter.

### Fetching File Contents (for context)

```
mcp__github__get_file_contents
  - owner: "<owner>"
  - repo: "<repo-name>"
  - path: "src/file.ts"
  - branch: "<head-branch>"
```

---

## Review Style Guidelines

Based on real review examples from senior reviewers, follow these patterns:

### Be Concise for Obvious Issues

```
Wrap in useMemo
```

```
use apiVars constants here - FLOW_TYPE_MILESTONE etc
```

### Explain Consequences for Non-Obvious Issues

```
Do this for grouped widgets alone. Otherwise it might lead to some save
loop on all flow previews
```

```
Call this in FlowBuilder and pass allWidgets in context or as props. Otherwise
this GQL happens every time this component re-renders
```

### Suggest Architectural Alternatives

```
Just store `isGroupable` at the step level after creating from a template
instead of doing all this across multiple files just to see if a widget
needs to be allowed in grouping
```

```
All this extra prop drilling for stepTemplates is avoidable. Add a nullable
field in step typedef called `isGroupable`
```

### Ask Clarifying Questions

```
why are we not using the step level flag here?
```

```
Do we need preloadAspectRatio for streaks/milestones?
```

```
In which case will this isGroupable variable change from a user edit?
Can remove this from editStep if no such case exists
```

### Reference Existing Patterns

```
If `stepTemplates` is being added to flow builder context already, then avoid
passing it as prop to FlowLocation/FlowStyling. Fetch it from the context itself
```

```
No need to pass productId as prop, just use the useSelector in the child
component to get selectedProduct.id
```

---

## Loading Repo-Specific Examples

When reviewing a PR, also check for:
- `.agent/skills/review/EXAMPLES.md` — real review examples showing expected style

If found, study the examples to match the review tone and focus areas.

---

## Guardrails

- **Never approve without reading** — Always review the actual changes
- **Don't nitpick excessively** — Focus on what matters
- **Be constructive** — Every criticism should come with a suggestion
- **Acknowledge uncertainty** — If unsure, say so rather than guessing
- **Respect author's time** — Group related comments, don't spam notifications
- **Ask questions** — When reasoning isn't clear, ask "why" instead of assuming
- **Loop prevention** — Always include `<!-- loma-agent-review -->` marker in review body
- **Cross-repo PR porting** — When asked to make one PR match another PR across different repositories, NEVER assume the two repos have identical base states on main. Always compare the `main` branch of BOTH repos first. Replicate the INTENT of the source PR (what it changes relative to its own main), not the literal file contents.
- **Cross-repo PR comparison** — When comparing changes across two PRs in different repositories, NEVER conclude they are 'identical' after a single pass. Perform a file-by-file diff comparison. Account for different main baselines — the same logical change will produce different raw diffs when starting points differ.
