import type { Skill } from "@/lib/api";
import type { Integration } from "@/lib/integration-api";
import { CATEGORIES } from "@/app/mcp/tool-meta";

export interface SelectionOption {
  value: string;
  label: string;
  description?: string;
  path: string[];
}

// Keep the existing CLI names as saved values; labels are presentation only.
const PERSONAL_TOOLS = [
  { value: "gmail", label: "Gmail", category: "Google" },
  { value: "google-drive", label: "Google Drive", category: "Google" },
  { value: "google-calendar", label: "Google Calendar", category: "Google" },
  { value: "google-docs", label: "Google Docs", category: "Google" },
  { value: "google-sheets", label: "Google Sheets", category: "Google" },
  { value: "slack", label: "Slack", category: "Messaging" },
  { value: "telegram", label: "Telegram", category: "Messaging" },
];

export function skillOptions(skills: Skill[]): SelectionOption[] {
  return skills.map((skill) => ({
    value: skill.slug || skill.name,
    label: skill.name,
    description: skill.description,
    path: [
      skill.scope === "workspace" || skill.scope === "system" ? "Organisation" : "Personal",
      ...(skill.scope === "system" ? ["System"] : []),
      ...(skill.folder ? [skill.folder] : []),
    ],
  }));
}

export function toolOptions(integrations: Integration[]): SelectionOption[] {
  return [
    ...PERSONAL_TOOLS.map(({ value, label, category }) => ({
      value, label, path: ["Personal", category],
    })),
    ...integrations.filter((integration) => integration.status === "connected").map((integration) => ({
      // Existing agents store display names. Do not silently migrate their values.
      value: integration.display_name || integration.provider,
      label: integration.display_name || integration.provider,
      description: integration.description,
      path: ["Organisation", CATEGORIES.find((category) => category.keys.includes(integration.provider))?.name || "Other"],
    })),
  ];
}

export function withSavedOptions(options: SelectionOption[], selected: string[]): SelectionOption[] {
  const unique = new Map(options.map((option) => [option.value, option]));
  for (const value of selected) {
    if (!unique.has(value)) unique.set(value, {
      value, label: value, path: ["Saved selections"],
      description: "Not in the current list. Kept until you deselect it.",
    });
  }
  return [...unique.values()];
}

export function toggleSelection(selected: string[], value: string): string[] {
  return selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value];
}
