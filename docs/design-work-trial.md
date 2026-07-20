# Design Work Trial (ADA-77)

This note anchors the preview environment used for the design work trial
([ADA-77](https://linear.app/plotline/issue/ADA-77)): a designer is exploring a
Kanban-style "Work Manager" home for agent work in Loma.

Context for the trial:

- **Problem statement**: one place to see all in-flight agent work (conversations,
  flows, runs), fire off new work, and see what's done / stuck / needs a human.
- **Existing primitives** to build on: Conversations (ad-hoc chats), Flows
  (scheduled / webhook routines), and Runs/usage (status, tokens, cost, duration).
- **Base designs**: the current core screens (Home, Chat, Conversations,
  Conversation Detail, Flows) are recreated as artboards in the team's Paper
  Design file ("Loma") as the starting point.

The PR carrying this file exists to spin up a `preview`-labeled environment the
designer can click around in; it introduces no functional changes.
