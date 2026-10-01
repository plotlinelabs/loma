import type { BoardField, CardFieldValue, TaskCardItem } from "@/lib/api";

// Display helpers for card boards (custom fields are defined per board).

export const FIELD_TYPE_LABELS: Record<BoardField["type"], string> = {
  text: "Text",
  number: "Number",
  date: "Date",
  person: "Person",
  select: "Select",
  multi_select: "Multi-select",
  link: "Link",
  checkbox: "Checkbox",
};

export const isEmptyValue = (value: CardFieldValue | undefined) =>
  value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0);

/** Short, human form of a card's value for one field ("" when empty). */
export function formatFieldValue(field: BoardField, value: CardFieldValue | undefined): string {
  if (isEmptyValue(value)) return "";
  if (field.type === "checkbox") return value ? "Yes" : "No";
  if (field.type === "number" && typeof value === "number") return value.toLocaleString();
  if (field.type === "date" && typeof value === "string") {
    const parsed = new Date(`${value}T00:00:00`);
    return Number.isNaN(parsed.getTime())
      ? value
      : parsed.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }
  if (field.type === "link" && typeof value === "string") {
    try {
      return new URL(value).hostname.replace(/^www\./, "");
    } catch {
      return value;
    }
  }
  return Array.isArray(value) ? value.join(", ") : String(value);
}

/** The number field a column totals in its header: the first one shown on cards. */
export function summaryField(fields: BoardField[]): BoardField | undefined {
  return fields.find((field) => field.type === "number" && field.show_on_card);
}

export function columnTotal(cards: TaskCardItem[], field: BoardField | undefined): string | null {
  if (!field) return null;
  const values = cards.map((card) => card.fields[field.id]).filter((v): v is number => typeof v === "number");
  if (values.length === 0) return null;
  const total = values.reduce((sum, v) => sum + v, 0);
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(total);
}
