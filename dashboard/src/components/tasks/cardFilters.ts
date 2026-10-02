import type {
  BoardField,
  BoardFieldType,
  CardFieldValue,
  CardFilter,
  CardFilterMatch,
  CardFilterOp,
  CardFilterValue,
  CardViewState,
  TaskCardItem,
} from "@/lib/api";

// Field filters for card boards. Pure functions only: the board applies them
// in the browser, since it already has every card and its field values.

/** Built-in filters that are not custom fields. */
export const STAGE_FIELD = "__stage";
export const ASSIGNEE_FIELD = "__assignee";

export type FilterKind = "choice" | "number" | "date" | "text" | "checkbox";

/** A filterable field: a board field, or one of the built-ins. */
export interface FilterField {
  id: string;
  name: string;
  kind: FilterKind;
  type: BoardFieldType | "stage" | "assignee";
}

const KIND_BY_TYPE: Record<BoardFieldType, FilterKind> = {
  select: "choice",
  multi_select: "choice",
  person: "choice",
  number: "number",
  date: "date",
  text: "text",
  link: "text",
  checkbox: "checkbox",
};

export const OPS_BY_KIND: Record<FilterKind, CardFilterOp[]> = {
  choice: ["any_of", "none_of", "empty", "not_empty"],
  number: ["gt", "lt", "eq", "between", "empty", "not_empty"],
  date: ["after", "before", "on", "between", "empty", "not_empty"],
  text: ["contains", "not_contains", "empty", "not_empty"],
  checkbox: ["checked", "not_checked"],
};

export const OP_LABELS: Record<CardFilterOp, string> = {
  any_of: "is any of",
  none_of: "is none of",
  eq: "equals",
  gt: "more than",
  lt: "less than",
  between: "between",
  before: "before",
  after: "after",
  on: "is on",
  contains: "contains",
  not_contains: "doesn't contain",
  checked: "is checked",
  not_checked: "is not checked",
  empty: "is empty",
  not_empty: "is not empty",
};

/** Conditions that need no value. */
const VALUELESS: CardFilterOp[] = ["checked", "not_checked", "empty", "not_empty"];

export const needsValue = (op: CardFilterOp) => !VALUELESS.includes(op);

export function filterFields(fields: BoardField[]): FilterField[] {
  return [
    { id: STAGE_FIELD, name: "Stage", kind: "choice", type: "stage" },
    { id: ASSIGNEE_FIELD, name: "Assignee", kind: "choice", type: "assignee" },
    ...fields.map((field) => ({ id: field.id, name: field.name, kind: KIND_BY_TYPE[field.type] ?? "text", type: field.type })),
  ];
}

export function defaultValue(op: CardFilterOp): CardFilterValue {
  if (op === "any_of" || op === "none_of") return [];
  if (op === "between") return [null, null];
  return null;
}

export function newFilter(field: FilterField): CardFilter {
  const op = OPS_BY_KIND[field.kind][0];
  return { id: Math.random().toString(36).slice(2, 10), field: field.id, op, value: defaultValue(op) };
}

/** What the board knows about a card beyond its own fields. */
export interface FilterContext {
  fields: BoardField[];
  /** People assigned to each card's open tasks. */
  assigneesByCard: Record<string, string[]>;
}

const isEmpty = (value: unknown) =>
  value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0);

const asNumber = (value: unknown): number | null => {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) return Number(value);
  return null;
};

const asDate = (value: unknown): string | null =>
  typeof value === "string" && /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : null;

/** The [low, high] bounds of a "between" filter; either side may be open. */
function bounds<T>(value: CardFilterValue, parse: (v: unknown) => T | null): [T | null, T | null] {
  const [low, high] = Array.isArray(value) ? value : [null, null];
  return [parse(low), parse(high)];
}

function rawValue(card: TaskCardItem, field: FilterField, ctx: FilterContext): CardFieldValue | string[] | undefined {
  if (field.type === "stage") return card.lane;
  if (field.type === "assignee") return ctx.assigneesByCard[card.card_id] ?? [];
  return card.fields[field.id];
}

/** Whether one card passes one filter; null when the filter is incomplete or its field is gone. */
export function matchFilter(card: TaskCardItem, filter: CardFilter, ctx: FilterContext): boolean | null {
  const field = filterFields(ctx.fields).find((f) => f.id === filter.field);
  if (!field || !OPS_BY_KIND[field.kind].includes(filter.op)) return null;
  const raw = rawValue(card, field, ctx);
  const { op, value } = filter;

  if (op === "empty") return isEmpty(raw);
  if (op === "not_empty") return !isEmpty(raw);
  if (op === "checked") return raw === true;
  if (op === "not_checked") return raw !== true;

  if (field.kind === "choice") {
    const wanted = (Array.isArray(value) ? value : []).filter((v) => !isEmpty(v)).map(String);
    if (wanted.length === 0) return null;
    const have = Array.isArray(raw) ? raw.map(String) : isEmpty(raw) ? [] : [String(raw)];
    const hit = have.some((v) => wanted.includes(v));
    return op === "any_of" ? hit : !hit;
  }

  if (field.kind === "text") {
    const query = typeof value === "string" ? value.trim().toLowerCase() : "";
    if (!query) return null;
    const hit = !isEmpty(raw) && String(raw).toLowerCase().includes(query);
    return op === "contains" ? hit : !hit;
  }

  if (field.kind === "number" || field.kind === "date") {
    const parse: (v: unknown) => number | string | null = field.kind === "number" ? asNumber : asDate;
    const have = parse(raw);
    if (op === "between") {
      const [low, high] = bounds(value, parse);
      if (low === null && high === null) return null;
      return have !== null && (low === null || have >= low) && (high === null || have <= high);
    }
    const target = parse(value);
    if (target === null) return null;
    if (have === null) return false;
    if (op === "eq" || op === "on") return have === target;
    if (op === "gt" || op === "after") return have > target;
    if (op === "lt" || op === "before") return have < target;
  }
  return null;
}

/** Whether a card passes the filters. Incomplete filters are ignored. */
export function matchesFilters(card: TaskCardItem, filters: CardFilter[], match: CardFilterMatch, ctx: FilterContext): boolean {
  const results = filters.map((f) => matchFilter(card, f, ctx)).filter((r): r is boolean => r !== null);
  if (results.length === 0) return true;
  return match === "any" ? results.some(Boolean) : results.every(Boolean);
}

/** Drop filters whose field no longer exists on the board. */
export function liveFilters(filters: CardFilter[], fields: BoardField[]): CardFilter[] {
  const ids = new Set(filterFields(fields).map((f) => f.id));
  return filters.filter((f) => ids.has(f.field));
}

export const EMPTY_VIEW_STATE: CardViewState = { filters: [], match: "all", search: "", assigned_to_me: false };

const comparable = (state: CardViewState) => JSON.stringify({
  filters: state.filters.map(({ field, op, value }) => ({ field, op, value })),
  match: state.filters.length > 1 ? state.match : "all",
  search: state.search.trim(),
  assigned_to_me: state.assigned_to_me,
});

/** Whether two filter states show the same cards (ignores filter ids). */
export const sameViewState = (a: CardViewState, b: CardViewState) => comparable(a) === comparable(b);
