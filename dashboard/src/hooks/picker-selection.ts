import type { AvailableSkill, AvailableTool } from "@/lib/api";

export type PickerDomain = "tools" | "skills";
export interface PickerItem {
  id: string;
  name: string;
  description: string;
  path: string[];
  search: string;
  required: boolean;
}
export const REQUIRED_TOOLS = ["Bash", "Read"];
export const isRequiredTool = (id: string) => REQUIRED_TOOLS.includes(id);
export const isSelected = (selected: string[] | null, id: string) =>
  selected === null || selected.includes(id);

// Preserve unavailable saved IDs. Only the explicit root reset replaces the list.
export function setItems(
  selected: string[] | null,
  catalog: string[],
  ids: string[],
  enabled: boolean,
  required: string[] = [],
) {
  const next = new Set(selected ?? catalog);
  for (const id of ids) {
    if (enabled) next.add(id);
    else next.delete(id);
  }
  for (const id of required) next.add(id);
  return [...next];
}
export function checkState(
  items: PickerItem[],
  selected: string[] | null,
): boolean | "mixed" {
  const optional = items.filter((i) => !i.required);
  if (!optional.length) return true;
  const count = optional.filter((i) => isSelected(selected, i.id)).length;
  return count === optional.length ? true : count === 0 ? false : "mixed";
}
export function pickerItems(
  tools: AvailableTool[],
  skills: AvailableSkill[],
  domain: PickerDomain,
): PickerItem[] {
  const items =
    domain === "tools"
      ? tools.map((t) => ({
          id: t.id,
          name: t.name,
          description: t.description || "",
          required: isRequiredTool(t.id),
          path: [t.group === "built-in" ? "Built-in" : "Integrations"],
          search: "",
        }))
      : skills.map((s) => ({
          id: s.slug,
          name: s.name,
          description: s.description || "",
          required: false,
          path: [
            s.scope === "personal"
              ? "Personal"
              : s.scope === "system"
                ? "System"
                : "Workspace",
            ...(s.folder ? [s.folder] : []),
          ],
          search: (s.tags || []).join(" "),
        }));
  return items.map((i) => ({
    ...i,
    search: [i.id, i.name, i.description, ...i.path, i.search]
      .join(" ")
      .toLowerCase(),
  }));
}
