"use client";

import type { ReactNode } from "react";
import { RiAddLine, RiArrowDownSLine, RiCloseLine, RiFilter3Line } from "@remixicon/react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { CardFilter, CardFilterMatch, CardFilterOp, CardFilterValue, TasksBoardResponse } from "@/lib/api";
import {
  ASSIGNEE_FIELD,
  OPS_BY_KIND,
  OP_LABELS,
  STAGE_FIELD,
  defaultValue,
  filterFields,
  needsValue,
  newFilter,
  type FilterField,
} from "./cardFilters";
import { FIELD_TYPE_LABELS } from "./cardDisplay";

interface Choice { value: string; label: string }

/** The values a "choice" filter can pick from. */
function choicesFor(field: FilterField, board: TasksBoardResponse, people: string[]): Choice[] {
  if (field.id === STAGE_FIELD) return board.lanes.map((lane) => ({ value: lane.id, label: lane.name }));
  if (field.id === ASSIGNEE_FIELD) {
    const assignees = board.tasks.map((t) => t.assignee).filter((a): a is string => !!a);
    return Array.from(new Set([...people, ...assignees])).map((email) => ({ value: email, label: email }));
  }
  const def = board.fields?.find((f) => f.id === field.id);
  if (def && def.type !== "person") return def.options.map((o) => ({ value: o, label: o }));
  // Person fields are free text: offer the values already on cards.
  const seen = (board.cards ?? []).flatMap((card) => {
    const v = card.fields[field.id];
    return Array.isArray(v) ? v.map(String) : v ? [String(v)] : [];
  });
  return Array.from(new Set(seen)).sort().map((v) => ({ value: v, label: v }));
}

function ChoicePicker({ choices, value, onChange }: {
  choices: Choice[];
  value: CardFilterValue;
  onChange: (value: CardFilterValue) => void;
}) {
  const selected = (Array.isArray(value) ? value : []).map(String);
  // Keep values that are no longer options visible so they can be removed.
  const all = [...choices, ...selected.filter((v) => !choices.some((c) => c.value === v)).map((v) => ({ value: v, label: v }))];
  const label = selected.map((v) => all.find((c) => c.value === v)?.label ?? v).join(", ");
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="h-7 min-w-0 flex-1 justify-between font-normal">
          <span className={cn("truncate", !label && "text-muted-foreground")}>{label || "Pick values"}</span>
          <RiArrowDownSLine className="text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-72 min-w-48 overflow-y-auto">
        {all.length === 0 && <DropdownMenuLabel className="font-normal text-muted-foreground">No values yet</DropdownMenuLabel>}
        {all.map((choice) => (
          <DropdownMenuCheckboxItem key={choice.value} checked={selected.includes(choice.value)}
            onSelect={(e) => e.preventDefault()}
            onCheckedChange={(checked) => onChange(checked
              ? [...selected, choice.value]
              : selected.filter((v) => v !== choice.value))}>
            <span className="truncate">{choice.label}</span>
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ValueInput({ field, filter, board, people, onChange }: {
  field: FilterField;
  filter: CardFilter;
  board: TasksBoardResponse;
  people: string[];
  onChange: (value: CardFilterValue) => void;
}) {
  if (!needsValue(filter.op)) return <div className="flex-1" />;
  if (field.kind === "choice") {
    return <ChoicePicker choices={choicesFor(field, board, people)} value={filter.value} onChange={onChange} />;
  }
  const type = field.kind === "number" ? "number" : field.kind === "date" ? "date" : "text";
  const parse = (raw: string): string | number | null =>
    raw === "" ? null : type === "number" ? Number(raw) : raw;
  const inputClass = "h-7 min-w-0 flex-1 text-[13px]";
  if (filter.op === "between") {
    const [low, high] = Array.isArray(filter.value) ? filter.value : [null, null];
    return (
      <div className="flex min-w-0 flex-1 items-center gap-1">
        <Input type={type} aria-label="From" placeholder="From" className={inputClass} value={low ?? ""}
          onChange={(e) => onChange([parse(e.target.value), high ?? null])} />
        <span className="text-muted-foreground">–</span>
        <Input type={type} aria-label="To" placeholder="To" className={inputClass} value={high ?? ""}
          onChange={(e) => onChange([low ?? null, parse(e.target.value)])} />
      </div>
    );
  }
  const value = typeof filter.value === "string" || typeof filter.value === "number" ? filter.value : "";
  return (
    <Input type={type} aria-label="Value" placeholder={type === "number" ? "0" : "Value"} className={inputClass}
      value={value} onChange={(e) => onChange(parse(e.target.value))} />
  );
}

/** "Filter" button and popover for a card board: filter cards by any field. */
export function CardFilterMenu({ board, people, filters, match, onChange, footer, compact = false }: {
  board: TasksBoardResponse;
  /** People who can be assigned on the board (Assignee filter options). */
  people: string[];
  filters: CardFilter[];
  match: CardFilterMatch;
  onChange: (filters: CardFilter[], match: CardFilterMatch) => void;
  /** View actions (Update view / Save as new). */
  footer?: ReactNode;
  /** Phones: icon-only trigger. */
  compact?: boolean;
}) {
  const fields = filterFields(board.fields ?? []);
  const builtIns = fields.slice(0, 2);
  const custom = fields.slice(2);
  const count = filters.length;
  const update = (id: string, patch: Partial<CardFilter>) =>
    onChange(filters.map((f) => (f.id === id ? { ...f, ...patch } : f)), match);

  return (
    <Popover>
      <PopoverTrigger asChild>
        {compact ? (
          <Button variant={count ? "secondary" : "ghost"} size="icon"
            className="relative size-10 rounded-full text-muted-foreground" aria-label="Filter cards">
            <RiFilter3Line size={18} />
            {count > 0 && (
              <span className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-brand-600 px-1 text-[10px] font-semibold text-white">
                {count}
              </span>
            )}
          </Button>
        ) : (
          <Button variant={count ? "secondary" : "ghost"} size="sm" className="h-9" aria-label="Filter cards">
            <RiFilter3Line className="h-4 w-4" /> Filter{count ? ` ${count}` : ""}
          </Button>
        )}
      </PopoverTrigger>
      <PopoverContent align="start" collisionPadding={8} className="w-[min(36rem,calc(100vw-1rem))] gap-2 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="font-medium">Filters</span>
          {count > 1 && (
            <div className="flex items-center gap-1.5 text-muted-foreground">
              Match
              <Select value={match} onValueChange={(next) => onChange(filters, next as CardFilterMatch)}>
                <SelectTrigger size="sm" className="w-20" aria-label="Match"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All</SelectItem>
                  <SelectItem value="any">Any</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
        </div>

        {count === 0 && <p className="text-muted-foreground">No filters. Showing every card.</p>}
        {filters.map((filter) => {
          const field = fields.find((f) => f.id === filter.field);
          if (!field) return null;
          return (
            <div key={filter.id} className="flex flex-wrap items-center gap-1.5 sm:flex-nowrap" data-filter-row>
              <Select value={filter.field} onValueChange={(next) => {
                const target = fields.find((f) => f.id === next);
                if (target) update(filter.id, { ...newFilter(target), id: filter.id });
              }}>
                <SelectTrigger size="sm" className="w-36 min-w-0" aria-label="Field"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectLabel>Built-in</SelectLabel>
                    {builtIns.map((f) => <SelectItem key={f.id} value={f.id}>{f.name}</SelectItem>)}
                  </SelectGroup>
                  {custom.length > 0 && (
                    <SelectGroup>
                      <SelectLabel>Board fields</SelectLabel>
                      {custom.map((f) => <SelectItem key={f.id} value={f.id}>{f.name}</SelectItem>)}
                    </SelectGroup>
                  )}
                </SelectContent>
              </Select>
              <Select value={filter.op} onValueChange={(next) => {
                const op = next as CardFilterOp;
                // Keep the value when the shape stays the same (e.g. any of -> none of).
                const keep = Array.isArray(filter.value) === Array.isArray(defaultValue(op)) && op !== "between" && filter.op !== "between";
                update(filter.id, { op, value: keep ? filter.value : defaultValue(op) });
              }}>
                <SelectTrigger size="sm" className="w-32" aria-label="Condition"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {OPS_BY_KIND[field.kind].map((op) => <SelectItem key={op} value={op}>{OP_LABELS[op]}</SelectItem>)}
                </SelectContent>
              </Select>
              <ValueInput field={field} filter={filter} board={board} people={people}
                onChange={(value) => update(filter.id, { value })} />
              <Button variant="ghost" size="icon-sm" className="text-muted-foreground" aria-label="Remove filter"
                onClick={() => onChange(filters.filter((f) => f.id !== filter.id), match)}>
                <RiCloseLine />
              </Button>
            </div>
          );
        })}

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="self-start text-muted-foreground">
              <RiAddLine /> Add filter
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="max-h-80 w-56 overflow-y-auto">
            <DropdownMenuLabel>Built-in</DropdownMenuLabel>
            {builtIns.map((f) => (
              <DropdownMenuItem key={f.id} onSelect={() => onChange([...filters, newFilter(f)], match)}>{f.name}</DropdownMenuItem>
            ))}
            {custom.length > 0 && <DropdownMenuSeparator />}
            {custom.length > 0 && <DropdownMenuLabel>Board fields</DropdownMenuLabel>}
            {custom.map((f) => (
              <DropdownMenuItem key={f.id} onSelect={() => onChange([...filters, newFilter(f)], match)}>
                <span className="flex-1 truncate">{f.name}</span>
                <span className="text-[11px] text-muted-foreground">
                  {FIELD_TYPE_LABELS[f.type as keyof typeof FIELD_TYPE_LABELS]}
                </span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        {(count > 0 || footer) && (
          <div className="flex items-center justify-between gap-2 border-t border-border pt-2">
            {count > 0 ? (
              <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={() => onChange([], "all")}>
                Clear all
              </Button>
            ) : <span />}
            <div className="flex items-center gap-1.5">{footer}</div>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
