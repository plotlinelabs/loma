# Design Principles

Every element on screen must earn its place. If removing something doesn't hurt comprehension, remove it.

## Core Philosophy

**Cognitive load is the enemy.** The dashboard exists to surface answers, not to display data. Users should leave knowing what happened, not still scanning rows.

**Clean > Feature-rich.** A feature that adds clutter for 80% of users to help 20% belongs behind a hover, a tooltip, or a detail page — not on the default view.

**Quiet confidence.** The UI should feel like it already made the decisions for you. No visual noise competing for attention. The important thing should be obvious without the user having to look for it.

---

## Rules

### 1. Every word earns its place

- No filler copy ("Browse and manage your..."). If the page title says **Conversations**, the user knows what they're looking at.
- Labels should be 1-2 words. If you need a sentence, rethink the design.
- Prefer verbs over nouns for actions: "Continue", not "Continue Conversation".
- Status should be visual (dots, color), not textual ("completed", "running").

### 2. Icons over text

- Actions that have universally recognized icons get icons only. Add a tooltip for discoverability.
- Source/type indicators are icons with tooltips, not colored text badges.
- Buttons in table rows are always icon-only. Text labels go in toolbars, not inline.

### 3. Tables are for scanning, not reading

- **No row separators.** Whitespace and alignment create structure. Lines add noise.
- **No table borders.** The table container blends into the page.
- **No header backgrounds.** Column headers are uppercase, small, muted — they orient, they don't decorate.
- **Fewer columns.** If a column is useful <30% of the time, it doesn't belong in the default view. Put it in a tooltip on hover, or on the detail page.
- **Hover reveals actions.** Row actions (continue, delete, menu) are invisible until the user hovers. This keeps the resting state clean.
- **Hover reveals detail.** Secondary data (turns, duration, savings, confidence) lives in a tooltip on a related visible cell (e.g., cost).

### 4. Tuck away, don't remove

- Infrequent filters collapse or sit behind a "Filters" toggle.
- Bulk actions appear only when items are selected.
- Settings and configuration live on dedicated pages, not inline panels.
- "Power user" features (export, advanced filters, column customization) are behind a menu or secondary action.

### 5. One line, not two panels

- Search and filters share a single row. Search is narrow and left-aligned. Filters flow to the right.
- No wrapper cards around toolbars. Borders around controls add nothing.
- Action buttons (refresh, clear) are icon-only, right-aligned, and muted until hovered.

### 6. shadcn defaults, not custom styling

- Use shadcn/ui components as-is. Don't override their internals with random border/color classes.
- If shadcn's `Table` has no row borders by default, don't add them back.
- If shadcn's `Badge` has a style, use it. Don't invent new badge color schemes per-page.
- Custom styling is a bug unless it solves a real problem that the component library doesn't.

### 7. Color is meaning, not decoration

- **Green** = positive (savings, success, connected)
- **Blue** = active/running
- **Red** = error/destructive
- **Amber** = warning/pending
- **Gray/muted** = secondary information
- Don't use color to "make things pop." If everything pops, nothing does.

### 8. Status is a dot, not a badge

- Running = pulsing blue dot
- Completed = solid green dot
- Error = solid red dot
- A 2px circle communicates status faster than a 60px colored badge with text.

### 9. Typography hierarchy

- **Page title**: `text-lg font-semibold` — one per page, top-left.
- **Section headers**: `text-[13px] font-semibold` — inside cards or panels.
- **Table headers**: `text-[11px] uppercase tracking-wider text-muted-foreground` — orient, don't decorate.
- **Body text**: `text-[13px]` — the default. Readable without being large.
- **Secondary/metadata**: `text-xs text-muted-foreground` — timestamps, counts, subtle context.
- No `text-base` or larger in data views. This is a dashboard, not a marketing page.

### 10. Spacing is structure

- Use consistent `space-y-2` between page sections.
- Table cells use `px-3 py-1.5` — tight but readable.
- Don't add padding to "make things breathe." If something feels cramped, the content is too dense — simplify it.

---

## Anti-patterns

Things we explicitly avoid:

| Don't | Do instead |
|---|---|
| Add a border between every table row | Let whitespace separate rows |
| Wrap toolbars in bordered cards | Let them float in the page flow |
| Show 12 columns in a table | Show 5-6, tuck the rest in tooltips |
| Use text badges for status | Use colored dots |
| Show actions on every row by default | Show on hover |
| Write "Continue Conversation" | Write nothing — use a chat icon |
| Add a subtitle under every page title | Remove it unless it adds real context |
| Use `bg-muted/50` on table headers | Leave them transparent |
| Create custom badge colors per category | Use the existing shadcn palette |
| Add loading spinners everywhere | Use skeleton rows that match the layout |

---

## Decision framework

When adding any new UI element, ask:

1. **Does this help 80%+ of users on this page?** No → tuck it away.
2. **Can this be communicated with an icon instead of text?** Yes → use an icon + tooltip.
3. **Is this data needed at scan-time or only at investigation-time?** Investigation → put it on the detail page or in a hover tooltip.
4. **Am I adding a border, shadow, or background to create "structure"?** Stop. Use whitespace and alignment instead.
5. **Am I overriding a shadcn default?** Why? The defaults exist for consistency.
