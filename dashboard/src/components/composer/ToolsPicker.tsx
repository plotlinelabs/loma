"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import { RiArrowDownSLine, RiArrowRightSLine } from "@remixicon/react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Input } from "@/components/ui/input";
import type { AvailableTool, AvailableSkill } from "@/lib/api";
import type { ToolsLoadState, ToolsSelection } from "@/hooks/useToolsPicker";
import {
  checkState,
  isSelected,
  pickerItems,
  type PickerDomain,
  type PickerItem,
} from "@/hooks/picker-selection";

interface ToolsPickerProps {
  tools: AvailableTool[];
  skills: AvailableSkill[];
  selection: ToolsSelection;
  onSetEnabled: (domain: PickerDomain, ids: string[], enabled: boolean) => void;
  onSetAll: (domain: PickerDomain, enabled: boolean) => void;
  onOpen: () => void;
  loadState: ToolsLoadState;
  disabled?: boolean;
}

function Check({
  state,
  label,
  disabled,
  onChange,
}: {
  state: boolean | "mixed";
  label: string;
  disabled?: boolean;
  onChange: () => void;
}) {
  return (
    <input
      type="checkbox"
      aria-label={label}
      aria-checked={state}
      checked={state === true}
      ref={(node) => {
        if (node) node.indeterminate = state === "mixed";
      }}
      disabled={disabled}
      onChange={onChange}
      className="size-4 shrink-0 accent-primary disabled:opacity-50"
    />
  );
}

// Native controls provide Tab/Space support; arrows also move between visible tree controls.
function navigate(event: KeyboardEvent<HTMLDivElement>) {
  if (
    !["ArrowDown", "ArrowUp"].includes(event.key) ||
    ((event.target as HTMLElement).tagName === "INPUT" &&
      (event.target as HTMLInputElement).type === "text")
  )
    return;
  const elements = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>(
      "button:not(:disabled), input:not(:disabled)",
    ),
  );
  const index = elements.indexOf(event.target as HTMLElement);
  if (index < 0) return;
  event.preventDefault();
  elements[
    (index + (event.key === "ArrowDown" ? 1 : -1) + elements.length) %
      elements.length
  ]?.focus();
}

function DomainPicker({
  domain,
  open,
  onOpenChange,
  ...props
}: ToolsPickerProps & {
  domain: PickerDomain;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [side, setSide] = useState<"top" | "bottom">("top");
  const title = domain === "tools" ? "Tools" : "Skills";
  const selected =
    domain === "tools"
      ? props.selection.enabledTools
      : props.selection.enabledSkills;
  const items = pickerItems(props.tools, props.skills, domain);
  const [search, setSearch] = useState("");
  const [selectedOnly, setSelectedOnly] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(['["Workspace"]']),
  );
  const scrollTop = useRef(0);
  const ready = props.loadState === "ready";
  const query = search.trim().toLowerCase();
  const filtered = !!query || selectedOnly;
  const enabled = (i: PickerItem) => i.required || isSelected(selected, i.id);
  const visible = items.filter(
    (i) =>
      (!query || i.search.includes(query)) && (!selectedOnly || enabled(i)),
  );
  const count = items.filter(enabled).length;
  const requiredCount = items.filter((i) => i.required).length;
  const missingCount =
    selected?.filter((id) => !items.some((i) => i.id === id)).length || 0;
  const mode =
    selected === null
      ? "All"
      : !ready
        ? "Custom"
        : domain === "tools" && count === requiredCount
          ? "Required only"
          : count === 0
            ? "None"
            : String(count);
  const toggleExpanded = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const change = (rows: PickerItem[], value: boolean) =>
    props.onSetEnabled(
      domain,
      rows.map((i) => i.id),
      value,
    );

  function tree(rows: PickerItem[], path: string[] = []): React.ReactNode {
    const depth = path.length;
    const groupNames = [
      ...new Set(
        rows.filter((i) => i.path.length > depth).map((i) => i.path[depth]),
      ),
    ];
    if (depth === 0)
      groupNames.sort(
        (a, b) =>
          [
            "Built-in",
            "Integrations",
            "Workspace",
            "Personal",
            "System",
          ].indexOf(a) -
          [
            "Built-in",
            "Integrations",
            "Workspace",
            "Personal",
            "System",
          ].indexOf(b),
      );
    else groupNames.sort((a, b) => a.localeCompare(b));
    return (
      <>
        {groupNames.map((name) => {
          const children = rows.filter((i) => i.path[depth] === name);
          const childPath = [...path, name];
          const key = JSON.stringify(childPath);
          const isOpen = filtered || expanded.has(key);
          const state = checkState(children, selected);
          return (
            <div key={key}>
              <div
                className="flex min-h-9 items-center gap-2 rounded-md px-2 hover:bg-muted/60"
                style={{ paddingLeft: 8 + depth * 16 }}
              >
                {!filtered && (
                  <Check
                    label={`Select ${name}`}
                    state={state}
                    disabled={children.every((i) => i.required)}
                    onChange={() => change(children, state !== true)}
                  />
                )}
                <button
                  type="button"
                  aria-expanded={isOpen}
                  onClick={() => toggleExpanded(key)}
                  className="flex min-w-0 flex-1 items-center gap-1 py-1 text-left text-xs font-medium focus-visible:outline-2 focus-visible:outline-ring"
                >
                  {isOpen ? (
                    <RiArrowDownSLine size={15} />
                  ) : (
                    <RiArrowRightSLine size={15} />
                  )}
                  <span className="truncate">{name}</span>
                  <span className="ml-auto text-[11px] font-normal text-muted-foreground">
                    {children.filter(enabled).length}/{children.length}
                  </span>
                </button>
              </div>
              {isOpen && tree(children, childPath)}
            </div>
          );
        })}
        {rows
          .filter((i) => i.path.length === depth)
          .sort((a, b) => a.name.localeCompare(b.name))
          .map((i) => (
            <label
              key={i.id}
              title={
                i.required ? "Required for core agent operation" : i.description
              }
              className="flex min-h-9 cursor-pointer items-center gap-2 rounded-md py-1 pr-2 text-xs hover:bg-muted/60"
              style={{ paddingLeft: 8 + depth * 16 }}
            >
              <Check
                label={i.name}
                state={enabled(i)}
                disabled={i.required}
                onChange={() => change([i], !enabled(i))}
              />
              <span className="min-w-0 flex-1 break-words">{i.name}</span>
              {i.required && (
                <span className="text-[10px] text-muted-foreground">
                  Required
                </span>
              )}
            </label>
          ))}
      </>
    );
  }
  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next && trigger.current) {
          const bounds = trigger.current.getBoundingClientRect();
          setSide(
            bounds.top > window.innerHeight - bounds.bottom ? "top" : "bottom",
          );
        }
        onOpenChange(next);
      }}
    >
      <PopoverTrigger asChild>
        <button
          ref={trigger}
          type="button"
          disabled={props.disabled}
          aria-label={`${title}: ${mode}`}
          className="inline-flex h-7 items-center gap-1 rounded-md px-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-55"
        >
          {title}: {mode}
          <RiArrowDownSLine size={14} />
        </button>
      </PopoverTrigger>
      <PopoverContent
        side={side}
        align="start"
        aria-label={`${title} selection`}
        onKeyDown={(e) => {
          e.stopPropagation();
          navigate(e);
        }}
        className="flex w-[min(90vw,340px)] max-h-[min(480px,var(--radix-popover-content-available-height))] flex-col gap-0 overflow-hidden rounded-xl p-0"
      >
        <div className="space-y-2 border-b p-3">
          <div className="text-sm font-medium">{title}</div>
          <Input
            aria-label={`Search ${domain}`}
            placeholder={`Search ${domain}...`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 text-xs"
          />
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={selectedOnly}
              onChange={(e) => setSelectedOnly(e.target.checked)}
            />
            Selected only
          </label>
        </div>
        {!ready ? (
          <div className="p-4 text-xs text-muted-foreground" role="status">
            {props.loadState === "error" ? (
              <>
                Could not load {domain}. Saved selections are unchanged.{" "}
                <button
                  type="button"
                  onClick={props.onOpen}
                  className="underline"
                >
                  Retry
                </button>
              </>
            ) : (
              "Loading..."
            )}
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2 border-b px-3 py-2 text-xs">
              {filtered ? (
                <>
                  <button
                    type="button"
                    onClick={() => change(visible, true)}
                    className="underline"
                  >
                    Select results
                  </button>
                  <button
                    type="button"
                    onClick={() => change(visible, false)}
                    className="ml-auto underline"
                  >
                    Clear results
                  </button>
                </>
              ) : (
                <>
                  <Check
                    label={`All available ${domain}`}
                    state={checkState(items, selected)}
                    disabled={!items.length}
                    onChange={() =>
                      props.onSetAll(
                        domain,
                        checkState(items, selected) !== true,
                      )
                    }
                  />
                  <span>All available {domain}</span>
                  <button
                    type="button"
                    className="ml-auto text-muted-foreground"
                    onClick={() => setExpanded(new Set())}
                  >
                    Collapse all
                  </button>
                </>
              )}
            </div>
            <div
              className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1"
              ref={(node) => {
                if (node) node.scrollTop = scrollTop.current;
              }}
              onScroll={(e) => {
                scrollTop.current = e.currentTarget.scrollTop;
              }}
            >
              {visible.length ? (
                tree(visible)
              ) : (
                <div className="p-4 text-center text-xs text-muted-foreground">
                  {items.length ? "No matches." : `No ${domain} available.`}
                </div>
              )}
            </div>
            <div
              className="border-t p-3 text-[11px] text-muted-foreground"
              aria-live="polite"
            >
              {count} enabled
              {requiredCount > 0 ? `, including ${requiredCount} required` : ""}
              {missingCount > 0 && (
                <div>{missingCount} saved unavailable selections retained.</div>
              )}
            </div>
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}

export function ToolsPicker(props: ToolsPickerProps) {
  const [openDomain, setOpenDomain] = useState<PickerDomain | null>(null);
  return (
    <div className="flex flex-wrap items-center gap-1">
      {(["tools", "skills"] as const).map((domain) => (
        <DomainPicker
          key={domain}
          {...props}
          domain={domain}
          open={openDomain === domain}
          onOpenChange={(open) => {
            setOpenDomain((prev) =>
              open ? domain : prev === domain ? null : prev,
            );
            if (open) props.onOpen();
          }}
        />
      ))}
    </div>
  );
}
