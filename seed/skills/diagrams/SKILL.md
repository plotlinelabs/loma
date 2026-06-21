---
name: diagrams
description: Generate visual diagrams (flowcharts, architecture, sequence) as PNG/SVG via Mermaid.js, upload to Google Drive, and embed in Google Docs — use when a user asks for a diagram, flowchart, architecture visualization, or visual representation of a flow
user-invocable: false
---

# Diagram Generation

Generate visual diagrams from Mermaid.js syntax, render them as PNG/SVG images via the mermaid.ink API, upload to Google Drive, and optionally embed them in Google Docs.

**Tool**: `tools/diagrams.py` — Python CLI called via Bash
**Rendering**: [mermaid.ink](https://mermaid.ink) public API (no API key required)
**Auth**: Google Drive/Docs operations require `--user-email` and `--auth-token` (user's personal Google OAuth)

---

## When to Use

- User asks for a diagram, flowchart, or architecture visualization
- User asks to visualize a process, flow, or system design
- User asks for a sequence diagram, state diagram, or ER diagram
- User wants a visual added to a Google Doc or slide deck
- User asks you to "draw" or "create a visual" of something

---

## Commands

### Render a Diagram

Writes Mermaid code to a temp file, then pipes it to the render command:

```bash
cat <<'MERMAID' > /tmp/diagram.mmd
graph LR
    A[Start] --> B[Process]
    B --> C{Decision}
    C -->|Yes| D[Action A]
    C -->|No| E[Action B]
MERMAID
python3 tools/diagrams.py render --output /tmp/diagram.png --type png < /tmp/diagram.mmd
```

**Flags:**
- `--output PATH` — Output file path (default: `/tmp/diagram.png`)
- `--type png|svg|jpeg|webp` — Image format (default: `png`)
- `--theme default|neutral|dark|forest` — Mermaid theme (default: `default`)
- `--width N` — Width in pixels (optional, sets rendered image width)
- `--height N` — Height in pixels (optional)
- `--scale N` — Scale multiplier 1-3 (requires width or height)
- `--bg-color HEX` — Background color as hex without `#` (e.g., `FFFFFF` for white, default: transparent)

**Response:**
```json
{"success": true, "output": "/tmp/diagram.png", "size_bytes": 43642, "type": "png"}
```

**IMPORTANT:** The `render` command reads Mermaid code from **stdin**. Always use file redirect (`< /tmp/diagram.mmd`) rather than inline piping with `echo` or `printf` for multi-line diagrams, because shell piping can silently drop content.

### Upload to Google Drive

```bash
python3 tools/diagrams.py upload --file /tmp/diagram.png --name "Architecture Diagram.png" --user-email EMAIL --auth-token TOKEN
```

**Flags:**
- `--file PATH` — Path to the rendered image file
- `--name NAME` — Display name on Google Drive
- `--user-email EMAIL` — User's email (from auth context)
- `--auth-token TOKEN` — User's auth token (from auth context)

**Response:**
```json
{
  "success": true,
  "file_id": "1abc...",
  "name": "Architecture Diagram.png",
  "web_view_link": "https://drive.google.com/file/d/1abc.../view",
  "embed_url": "https://lh3.googleusercontent.com/d/1abc..."
}
```

The file is automatically made publicly readable so it can be embedded in Google Docs.

### Embed in Google Doc

```bash
python3 tools/diagrams.py embed \
  --doc-id DOC_ID \
  --image-id DRIVE_FILE_ID \
  --replace-start "HEADING_BEFORE_DIAGRAM" \
  --replace-end "HEADING_AFTER_DIAGRAM" \
  --width 500 --height 250 \
  --user-email EMAIL --auth-token TOKEN
```

**Flags:**
- `--doc-id` — Google Doc document ID
- `--image-id` — Google Drive file ID of the uploaded image
- `--replace-start` — Text marker indicating the start of the area to replace (content AFTER this marker is deleted)
- `--replace-end` — Text marker indicating the end of the area to replace (content BEFORE this marker is deleted)
- `--width` — Image width in points (default: 500)
- `--height` — Image height in points (default: 250)
- `--user-email` / `--auth-token` — Auth context

**Response:**
```json
{"success": true, "doc_id": "...", "image_embedded": true, "replaced_range": "337-4100"}
```

---

## Common Workflows

> **Preview-first delivery**: When generating diagrams, always emit the Mermaid code as an inline code block FIRST — the chat UI renders Mermaid diagrams as interactive SVGs in the right-side preview panel. Then render a PNG and upload to Google Drive as a persistent backup.

### Create a Diagram (Primary Workflow)

1. Write the Mermaid code and **emit it as an inline mermaid code block** in the chat response — this gives the user an instant interactive preview in the right-side panel.
2. Also write the Mermaid code to a temp file and render a PNG: `python3 tools/diagrams.py render --output /tmp/diagram.png --type png --width 1200 < /tmp/diagram.mmd`
3. Upload the PNG to Google Drive: `python3 tools/diagrams.py upload --file /tmp/diagram.png --name "Diagram Name.png" --user-email EMAIL --auth-token TOKEN`
4. Share both the inline preview (already visible) and the Drive link for persistence/sharing

**Never skip the inline Mermaid preview** by jumping straight to a Google Drive PNG link. The user should see the diagram rendered in-chat first.

### Create a Diagram and Share as Drive Link (Legacy/Fallback)

Use this workflow only when the Mermaid diagram is too complex for inline rendering or the user specifically asks for a PNG/image file:

1. Write Mermaid code to a temp file based on the user's request
2. Render: `python3 tools/diagrams.py render --output /tmp/diagram.png --type png --width 1200 < /tmp/diagram.mmd`
3. Upload: `python3 tools/diagrams.py upload --file /tmp/diagram.png --name "Diagram Name.png" --user-email EMAIL --auth-token TOKEN`
4. Share the `web_view_link` with the user

### Create a Diagram and Embed in a Google Doc

1. Write Mermaid code to a temp file
2. Render as PNG with appropriate width (1200px works well for docs)
3. Upload to Drive
4. Use the `embed` command to insert the image between two section headings in the doc
5. The content between the two markers is replaced with the image

### Choosing the Right Diagram Type

| Use Case | Mermaid Syntax | Best Theme |
|----------|---------------|------------|
| Architecture / system design | `graph LR` or `graph TD` with subgraphs | `default` or `neutral` |
| API / integration flow | `sequenceDiagram` | `default` |
| Decision tree / logic flow | `graph TD` with `{Decision}` nodes | `neutral` |
| State machine | `stateDiagram-v2` | `default` |
| Database / ER diagram | `erDiagram` | `neutral` |
| Timeline | `timeline` | `default` |
| User journey | `journey` | `default` |

### Mermaid Tips for Better Diagrams

- Use `subgraph` to group related nodes visually
- Add descriptive labels on edges: `A -->|label| B`
- Use different node shapes: `[rectangle]`, `(rounded)`, `{diamond}`, `([stadium])`, `[[subroutine]]`, `((circle))`
- For architecture diagrams, prefer `graph LR` (left-to-right) for horizontal flows
- For process flows, prefer `graph TD` (top-down)
- Set `--width 1200` for diagrams that will be embedded in documents
- Use `--bg-color FFFFFF` (white background) when embedding in documents for clean appearance
- Use `--theme neutral` for professional/business diagrams

---

## Error Handling

- **400 (Mermaid syntax error)**: The diagram code has invalid Mermaid syntax. Check for missing arrows, unclosed brackets, or unsupported syntax.
- **431 (Header too large)**: Diagram is very large. The tool automatically falls back to base64 encoding.
- **503 (Rendering timeout)**: mermaid.ink took too long. Simplify the diagram.
- **Auth errors**: User needs to connect Google account at the Integrations page.

---

## Safety / Guardrails

- The `render` command does NOT require auth (uses free public mermaid.ink API)
- The `upload` and `embed` commands require user auth tokens
- Uploaded files are made publicly readable (anyone with link) so Google Docs can embed them
- Always use descriptive file names when uploading to Drive
- For the `embed` command, double-check marker text exists in the doc before running
