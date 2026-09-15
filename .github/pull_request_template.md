## Summary

## Testing
- Exact tested commit:
- Commands and results:
- Known failures or unverified paths:

### Live chat regression (required before merge)
- [ ] Isolated local stack; throwaway database; Slack/scheduler disabled.
- [ ] New message sent from the logged-in UI, real agent response received.
- [ ] Reply visible and persists after reload.
- [ ] Follow-up receives prior context, returns a real response and persists.
- [ ] `scripts/browser/chat-smoke.cjs` passes for each affected runtime.
- [ ] Model/runtime, feature-flag state, JSON result and safe screenshots attached.

Do not check these boxes for synthetic replies, login-only checks, HTTP 200 or
stream completion alone. Document blockers rather than claiming an untested pass.
See the live chat test instructions in `docs/history-recall.md`.
