/**
 * Starting points for a crew. They only pre-fill the editor: every member,
 * link, permission and schedule can be changed or removed before saving.
 */
import type { CrewMember, CrewSchedule } from "./crew-api";

export interface CrewTemplate {
  id: string;
  name: string;
  description: string;
  members: CrewMember[];
  schedules: CrewSchedule[];
}

const WEEKDAYS = [0, 1, 2, 3, 4];

export function blankMember(id: string, name = "New member"): CrewMember {
  return {
    id, name, description: "", instructions: "", agent_id: null, model: "",
    delegates_to: [], reviewed_by: null, writes: "ask", can_escalate: true,
  };
}

const m = (id: string, name: string, extra: Partial<CrewMember>): CrewMember => ({ ...blankMember(id, name), ...extra });

export const CREW_TEMPLATES: CrewTemplate[] = [
  {
    id: "blank",
    name: "Blank",
    description: "Start with one member and build your own team.",
    members: [m("assistant", "Assistant", { description: "Does what you hand it." })],
    schedules: [],
  },
  {
    id: "chief-of-staff",
    name: "Chief of Staff team",
    description: "A planner hands goals to a PM or straight to workers. A reviewer checks the workers.",
    members: [
      m("chief", "Chief of Staff", {
        description: "Plans the day and hands out work.",
        instructions: "Read Gmail (read only), today's calendar and the taskboard. Pick the goals worth doing today and hand each one out. Never do long tasks yourself.",
        delegates_to: ["pm", "worker"],
      }),
      m("pm", "PM", {
        description: "Splits a goal into small tasks with acceptance criteria.",
        instructions: "Break your task into small worker tasks with clear acceptance criteria. Order them with depends_on when one needs another.",
        delegates_to: ["worker"],
      }),
      m("worker", "Worker", {
        description: "Does one clear job end to end.",
        reviewed_by: "reviewer",
      }),
      m("reviewer", "Reviewer", {
        description: "Checks a worker's result against the acceptance criteria.",
        instructions: "Be strict. Open the PR, read the file or check the record yourself.",
        writes: "deny",
      }),
    ],
    schedules: [
      {
        id: "morning-plan", name: "Morning plan", member_id: "chief", time: "08:30", days: WEEKDAYS,
        prompt: "Plan my day. Find what needs doing today, hand out the work, then send me a short brief: the plan, who is doing what, and the decisions only I can make.",
        include_activity: false, deliver: { inbox: true, slack_dm: true, updates: true }, enabled: true,
      },
      {
        id: "evening-report", name: "Evening report", member_id: "chief", time: "19:00", days: WEEKDAYS,
        prompt: "Write my evening report: what got done (with links and PRs), what is waiting on me, what failed and why, and what you suggest for tomorrow. Do not hand out new work.",
        include_activity: true, deliver: { inbox: true, slack_dm: true, updates: false }, enabled: true,
      },
    ],
  },
  {
    id: "builder-reviewer",
    name: "Builder and reviewer",
    description: "One agent writes code and opens draft PRs; another reviews every result.",
    members: [
      m("builder", "Builder", {
        description: "Implements changes and opens draft PRs.",
        reviewed_by: "code-reviewer",
      }),
      m("code-reviewer", "Code reviewer", {
        description: "Reviews the builder's PRs.",
        instructions: "Read the diff, run the tests where you can, and check the change matches the brief.",
        writes: "deny",
      }),
    ],
    schedules: [],
  },
  {
    id: "inbox",
    name: "Inbox assistant",
    description: "Triages your inbox every morning and drafts replies for you to approve.",
    members: [
      m("inbox", "Inbox assistant", {
        description: "Triages email and drafts replies.",
        instructions: "Read new email (read only). Draft replies as Gmail drafts. Ask me before anything is sent.",
      }),
    ],
    schedules: [
      {
        id: "inbox-triage", name: "Inbox triage", member_id: "inbox", time: "09:00", days: WEEKDAYS,
        prompt: "Triage my inbox since yesterday: what needs me, what you drafted, what can wait.",
        include_activity: false, deliver: { inbox: true, slack_dm: false, updates: false }, enabled: true,
      },
    ],
  },
];
