---
name: slack-reader
description: Slack channel history, listing, and messaging — use when reading Slack conversations, listing channels, checking channel activity, finding messages, sending or posting messages to Slack channels, or replying in Slack threads.
user-invocable: false
---

# Slack — Channel History, Listing & Messaging

Read Slack channel messages, list accessible channels, and send messages to channels. Use when asked about what was discussed in a channel, recent activity, finding a specific channel, or when needing to send/post a message to a Slack channel.

## Available Commands

This is a **CLI tool** — invoke via Bash. Do NOT look for MCP tools.

### `channels` — List accessible channels

```bash
python3 tools/slack_reader.py channels
python3 tools/slack_reader.py channels --query engineering
python3 tools/slack_reader.py channels --query bugs
```

Returns channel name, ID, member count, purpose, and whether it's private. Use `--query` to filter by name or purpose.

### `history` — Read recent messages from a channel

```bash
python3 tools/slack_reader.py history "#general"
python3 tools/slack_reader.py history "#general" --limit 20
python3 tools/slack_reader.py history C12345ABC --limit 100
```

- Accepts channel name (with or without `#`) or channel ID
- Default: 50 messages, max: 200
- Messages are returned in chronological order (oldest first)
- Includes: user names, timestamps, text, reactions, file attachments, thread reply counts

### `send` — Send a message to a channel

```bash
python3 tools/slack_reader.py send "#general" --text "Hello from the bot!"
python3 tools/slack_reader.py send C12345ABC --text "Message using channel ID"
python3 tools/slack_reader.py send "#general" --thread-ts 1234567890.123456 --text "Thread reply"
python3 tools/slack_reader.py send "#general" --text "Here's the report" --file /tmp/report.pdf --file-title "Monthly Report"
```

For long or multiline messages, pipe via stdin:

```bash
cat <<'EOF' | python3 tools/slack_reader.py send "#general"
This is a longer message
with multiple lines
and *Slack mrkdwn* formatting.
EOF
```

- Accepts channel name (with or without `#`) or channel ID
- Message text via `--text` flag or stdin (stdin is preferred for long/multiline content)
- Optional `--thread-ts` to reply in a thread (use a message timestamp from `history` output)
- Optional `--file /path/to/file` to upload and attach a file to the message
- Optional `--file-title TITLE` to set a custom title for the uploaded file
- Supports Slack mrkdwn formatting in message text
- Returns the sent message timestamp (useful for threading follow-up replies)
- Bot must be a member of the channel and have `chat:write` scope

---

## Sending Direct Messages (DMs)

The `send` command works with channels, not DMs directly. To send a DM via the bot, use the Slack API directly via curl:

### Step 1: Find the user's Slack ID

```bash
curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  "https://slack.com/api/users.list?limit=500" | \
  python3 -c "import json,sys; users=json.load(sys.stdin)['members']; [print(f\"{u['id']} {u['real_name']}\") for u in users if 'TARGET_NAME'.lower() in u.get('real_name','').lower()]"
```

Replace `TARGET_NAME` with the person's name (case-insensitive partial match).

### Step 2: Open a DM conversation

```bash
curl -s -X POST -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"users": "USER_ID"}' \
  "https://slack.com/api/conversations.open" | \
  python3 -c "import json,sys; r=json.load(sys.stdin); print(r['channel']['id'])"
```

Replace `USER_ID` with the Slack user ID from Step 1.

### Step 3: Send the DM using the channel ID

```bash
curl -s -X POST -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"channel": "DM_CHANNEL_ID", "text": "Your message here"}' \
  "https://slack.com/api/chat.postMessage"
```

Replace `DM_CHANNEL_ID` with the channel ID from Step 2.

**Important notes for DMs:**
- The DM channel ID looks like `D0XXXXXXXXX` (starts with `D`)
- DO NOT pass the DM channel ID to `slack_reader.py send` with a `#` prefix — use the raw ID with the curl approach above
- The bot must have `im:write` scope to send DMs
- You can also use `conversations.open` with multiple user IDs to create a group DM (MPIM)

---

## Common Workflows

### Check what was discussed in a channel
1. Read history: `python3 tools/slack_reader.py history "#product" --limit 50`
2. Summarize key topics and action items from the messages

### Find a specific channel
1. Search: `python3 tools/slack_reader.py channels --query onboarding`
2. Use the channel ID or name from results in follow-up commands

### Review recent activity in a channel
1. Read history with a limit: `python3 tools/slack_reader.py history "#bugs" --limit 30`
2. Identify patterns, recurring issues, or topics

### Send a message to a channel
1. Find the channel: `python3 tools/slack_reader.py channels --query alerts`
2. Send: `python3 tools/slack_reader.py send "#product-alerts" --text "Daily report is ready."`

### Reply in a thread
1. Read history to find the parent message timestamp: `python3 tools/slack_reader.py history "#bugs" --limit 10`
2. Reply in thread: `python3 tools/slack_reader.py send "#bugs" --thread-ts 1234567890.123456 --text "Following up on this."`

### Send a DM to a specific person
1. Find the user's Slack ID: `curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" "https://slack.com/api/users.list?limit=500" | python3 -c "..."`
2. Open a DM conversation: `curl -s -X POST ... conversations.open ...`
3. Send the message: `curl -s -X POST ... chat.postMessage ...`
4. See the **Sending Direct Messages (DMs)** section above for full commands.

### Post a formatted update
```bash
cat <<'EOF' | python3 tools/slack_reader.py send "#engineering"
*Daily Build Status* :white_check_mark:

• All tests passing
• Build deployed to staging
• No new issues found
EOF
```

## Important Notes

- **Verify automated post timestamps**: When a user reports that automated posts have stopped appearing in a channel, NEVER claim the posts are 'still flowing in consistently' without first verifying the actual timestamps of recent posts. Before responding, query the channel history with a filter for the specific bot/message type and check the dates of the most recent posts. If you cannot verify with data, say "let me check" rather than asserting everything is working.
- **Private channels**: The bot must be invited to a private channel to read or write to it. If you get a "not in channel" error, the bot hasn't been added.
- **Thread replies**: The `thread_replies` count is shown in history but individual thread messages are not fetched. If a specific thread is important, note this limitation.
- **Bot messages**: Bot messages are prefixed with `[bot]` and the bot's name.
- **Rate limits**: Slack has rate limits (~50 requests/min for most endpoints). Avoid reading many channels or sending many messages in rapid succession. When using channel names (not IDs), prefer channel IDs to avoid extra API calls for resolution.
- **chat:write scope**: The bot token must have `chat:write` scope to send messages. If you get a "missing_scope" error, the bot's OAuth permissions need updating.

## Error Handling
- **channel_not_found**: Channel doesn't exist or bot can't see it
- **not_in_channel**: Bot needs to be invited to the channel
- **missing_scope**: Bot needs `chat:write` permission (for send)
- **invalid_auth**: Bot token is invalid or expired
- **no_text**: Message text was empty
- **ratelimited**: Too many API calls — wait and retry
- **No results for query**: Try a broader search term with `channels --query`
- **Missing token**: Ensure `SLACK_BOT_TOKEN` is set in `.env`

## Configuration
Requires `SLACK_BOT_TOKEN` environment variable (already configured for the bot).
Bot needs `channels:read`, `channels:history`, `chat:write`, `im:write`, and `users:read` scopes.
