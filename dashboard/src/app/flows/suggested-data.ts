// Hardcoded demo data for the Suggested tab on the Flows page.
// Matches artboard 34I-0 — no backend wiring, pure presentation.

export type TriggerKind = "event" | "schedule";

export interface SuggestedStep {
  tool: "pylon" | "linear" | "github" | "slack" | "notion" | "clickhouse" | "mongodb" | "grain" | "hubspot";
  description: string;
  learned?: boolean;
}

export interface WorkThreadRef {
  title: string;
  relativeTime: string;
}

export interface SuggestedFlow {
  id: string;
  name: string;
  description: string;
  triggerKind: TriggerKind;
  detectedAgo: string;
  patternCount: string;
  patternWindow: string;
  avgTime: string;
  avgTimeSub: string;
  timeSaved: string;
  timeSavedSub: string;
  confidenceDots: number; // 0-5
  confidenceLabel: string;
  triggerDescription: string;
  triggerSource: string; // "Pylon webhook"
  steps: SuggestedStep[];
  learnedFromThreads: WorkThreadRef[];
}

export interface MoreSuggestion {
  id: string;
  name: string;
  triggerKind: TriggerKind;
  description: string;
  authors: string[]; // initials for avatar stack
  ranSummary: string; // "Sam ran this 4 Mondays in a row"
  timeSaved: string; // "~45 min/week saved"
  tools: Array<"pylon" | "linear" | "github" | "slack" | "notion" | "clickhouse" | "mongodb" | "grain" | "hubspot" | "bamboohr">;
}

export const FEATURED_SUGGESTED: SuggestedFlow = {
  id: "auto-triage-support",
  name: "Auto-triage incoming support tickets",
  description:
    "Whenever a new ticket lands in #customer-support from an external user, run the same triage sequence Loma just performed manually 7 times this week.",
  triggerKind: "event",
  detectedAgo: "12 hours ago",
  patternCount: "7 times",
  patternWindow: "in the last 14 days",
  avgTime: "12.4s",
  avgTimeSub: "across the 7 runs",
  timeSaved: "~3.5 hrs/wk",
  timeSavedSub: "human attention freed up",
  confidenceDots: 5,
  confidenceLabel: "Very high · same shape every time",
  triggerDescription: "When a new ticket arrives in Pylon channel 'customer-support'",
  triggerSource: "Pylon webhook",
  steps: [
    { tool: "notion", description: "Read the Bug Triage Playbook from Notion" },
    { tool: "pylon", description: "Read full ticket + customer context from Pylon" },
    { tool: "clickhouse", description: "Query ClickHouse for related metrics if a system is named" },
    { tool: "github", description: "Search your repositories for recent commits in implicated area" },
    {
      tool: "linear",
      description:
        "Create Linear ticket with reproduction, impact, suspect commit + android, deliverability labels (learned)",
      learned: true,
    },
    { tool: "pylon", description: "Reply on Pylon thread with the Linear ticket link + ETA" },
    { tool: "slack", description: "Notify #mobile-team in Slack with one-line summary" },
  ],
  learnedFromThreads: [
    { title: "Customer push notification bug", relativeTime: "today" },
    { title: "Customer checkout flow stuck on checkout provider", relativeTime: "2d" },
    { title: "Customer nudge events not firing on iOS 17", relativeTime: "3d" },
    { title: "Customer ratings widget missing avatars", relativeTime: "5d" },
  ],
};

export const MORE_SUGGESTIONS: MoreSuggestion[] = [
  {
    id: "weekly-customer-health",
    name: "Weekly customer health summary",
    triggerKind: "schedule",
    description:
      "Every Monday 9 AM, pull usage trends from ClickHouse, recent Pylon tickets, and meeting notes. Post a one-pager in #customer-success.",
    authors: ["I", "A", "V", "D"],
    ranSummary: "Sam ran this 4 Mondays in a row",
    timeSaved: "~45 min/week saved",
    tools: ["clickhouse", "pylon", "grain", "slack"],
  },
  {
    id: "onboard-new-hires",
    name: "Onboard new hires from BambooHR",
    triggerKind: "event",
    description:
      "When BambooHR fires a 'new employee' webhook, provision GitHub access, add to relevant Slack channels, and schedule the welcome huddle in Calendar.",
    authors: ["A", "V", "I"],
    ranSummary: "Alex ran this 3 times for new joiners",
    timeSaved: "~30 min per hire",
    tools: ["bamboohr", "github", "slack"],
  },
  {
    id: "contract-renewal-drafts",
    name: "Draft contract renewal emails 60 days out",
    triggerKind: "schedule",
    description:
      "Daily at 8 AM, check MonetizeNow for contracts expiring in 60 days. Draft a renewal email with usage stats from ClickHouse and post as draft in #renewals for review.",
    authors: ["V", "I", "S"],
    ranSummary: "Jordan ran this 5 times in last 30 days",
    timeSaved: "~2 hrs/week saved",
    tools: ["clickhouse", "hubspot", "slack"],
  },
];

/** Banner copy for the top of the Suggested tab. */
export const SUGGESTED_BANNER = {
  message:
    "Loma proposed 4 new flows this week based on repeating patterns across 47 work threads. Estimated 11 hours/week of work that could be automated.",
  link: "How does this work?",
};
