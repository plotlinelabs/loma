"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
} from "react";
import {
  RiArrowDownSLine,
  RiArrowRightSLine,
  RiLockLine,
} from "@remixicon/react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
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

const mobileQuery = "(max-width: 639px)";
const subscribeMobile = (notify: () => void) => {
  const media = window.matchMedia(mobileQuery);
  media.addEventListener("change", notify);
  return () => media.removeEventListener("change", notify);
};
const getMobile = () => window.matchMedia(mobileQuery).matches;
const getServerMobile = () => false;

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
  descriptionId,
  disabled,
  onChange,
}: {
  state: boolean | "mixed";
  label: string;
  descriptionId?: string;
  disabled?: boolean;
  onChange: () => void;
}) {
  return (
    <input
      type="checkbox"
      aria-label={label}
      aria-describedby={descriptionId}
      aria-checked={state}
      checked={state === true}
      ref={(node) => {
        if (node) node.indeterminate = state === "mixed";
      }}
      disabled={disabled}
      onChange={onChange}
      className="size-5 shrink-0 cursor-pointer accent-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-default disabled:opacity-60"
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
  const mobile = useSyncExternalStore(
    subscribeMobile,
    getMobile,
    getServerMobile,
  );
  const descriptionPrefix = useId();
  const [undo, setUndo] = useState<{
    selection: string[] | null;
    message: string;
  } | null>(null);
  useEffect(() => {
    if (!undo) return;
    const timer = setTimeout(() => setUndo(null), 8000);
    return () => clearTimeout(timer);
  }, [undo]);
  const doneButton = useRef<HTMLButtonElement>(null);
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
  const remember = (message: string) =>
    setUndo({ selection: selected === null ? null : [...selected], message });
  const change = (rows: PickerItem[], value: boolean, bulk = true) => {
    if (bulk) remember(value ? "Selection added" : "Selection cleared");
    else setUndo(null);
    props.onSetEnabled(
      domain,
      rows.map((i) => i.id),
      value,
    );
  };
  const changeAll = () => {
    const value = checkState(items, selected) !== true;
    remember(
      value
        ? `All ${domain} selected`
        : domain === "tools"
          ? "Optional tools cleared"
          : "Skills cleared",
    );
    props.onSetAll(domain, value);
  };
  const restore = () => {
    if (!undo) return;
    // Restore this domain only, including all-mode and unavailable saved IDs.
    props.onSetAll(domain, undo.selection === null);
    if (undo.selection !== null)
      props.onSetEnabled(domain, undo.selection, true);
    setUndo(null);
  };
  const changeOpen = (next: boolean) => {
    if (!next) setUndo(null);
    if (next && trigger.current) {
      const bounds = trigger.current.getBoundingClientRect();
      setSide(
        bounds.top > window.innerHeight - bounds.bottom ? "top" : "bottom",
      );
    }
    onOpenChange(next);
  };

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
          const required = children.filter((i) => i.required).length;
          return (
            <div key={key}>
              <div
                className={`flex min-h-11 items-center gap-3 rounded-md px-2 hover:bg-muted/60 ${depth === 0 ? "mt-1 bg-muted/40" : ""}`}
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
                  className="flex min-h-11 min-w-0 flex-1 flex-wrap items-center gap-x-1 py-2 text-left text-sm font-semibold focus-visible:outline-2 focus-visible:outline-ring"
                >
                  {isOpen ? (
                    <RiArrowDownSLine size={15} />
                  ) : (
                    <RiArrowRightSLine size={15} />
                  )}
                  <span className="truncate">{name}</span>
                  <span className="ml-auto text-xs font-normal text-muted-foreground">
                    {required
                      ? `${required} required · ${children.filter((i) => !i.required && enabled(i)).length} optional selected`
                      : `${children.filter(enabled).length}/${children.length}`}
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
              tabIndex={i.required ? 0 : undefined}
              className="group flex min-h-11 cursor-pointer items-center gap-3 rounded-md py-2 pr-2 text-sm hover:bg-muted/60 focus-within:bg-muted/60"
              style={{ paddingLeft: 8 + depth * 16 }}
            >
              <Check
                label={i.name}
                descriptionId={`${descriptionPrefix}-${i.id}`}
                state={enabled(i)}
                disabled={i.required}
                onChange={() => change([i], !enabled(i), false)}
              />
              <span className="min-w-0 flex-1 break-words">
                {i.name}
                <span
                  id={`${descriptionPrefix}-${i.id}`}
                  className="sr-only group-hover:not-sr-only group-focus-within:not-sr-only group-hover:block group-focus-within:block text-xs font-normal text-muted-foreground"
                >
                  {i.required
                    ? "Required for core agent operation"
                    : i.description}
                </span>
              </span>
              {i.required && (
                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                  <RiLockLine size={14} aria-hidden="true" /> Required
                </span>
              )}
            </label>
          ))}
      </>
    );
  }
  const content = (
    <>
      <div className="space-y-2 border-b p-3">
        <div className="flex items-center justify-between">
          {mobile ? (
            <SheetTitle>
              {title}
              <span className="sr-only"> selection</span>
            </SheetTitle>
          ) : (
            <div className="text-base font-semibold">{title}</div>
          )}
          {mobile && (
            <button
              type="button"
              ref={doneButton}
              onClick={() => changeOpen(false)}
              className="min-h-11 px-3 text-sm font-medium text-primary"
            >
              Done
            </button>
          )}
        </div>
        <Input
          aria-label={`Search ${domain}`}
          placeholder={`Search ${domain}...`}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="h-10 text-base sm:text-sm"
        />
        <div
          role="group"
          aria-label={`${title} view`}
          className="flex rounded-lg bg-muted p-1"
        >
          {[false, true].map((only) => (
            <button
              key={String(only)}
              type="button"
              aria-pressed={selectedOnly === only}
              onClick={() => setSelectedOnly(only)}
              className={`min-h-9 flex-1 rounded-md px-3 text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring ${selectedOnly === only ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
            >
              {only ? `Selected${ready ? ` (${count})` : ""}` : "All"}
            </button>
          ))}
        </div>
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
          <div className="flex min-h-12 items-center gap-3 border-b px-3 py-2 text-sm">
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
                  disabled={!items.some((i) => !i.required)}
                  onChange={changeAll}
                />
                <span>All available {domain}</span>
                <button
                  type="button"
                  className="ml-auto text-xs text-muted-foreground"
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
            className="border-t p-3 text-xs text-muted-foreground"
            aria-live="polite"
          >
            {undo && (
              <div
                className="mb-2 flex items-center justify-between gap-2 text-sm text-foreground"
                role="status"
              >
                <span>{undo.message}</span>
                <button
                  type="button"
                  onClick={restore}
                  className="min-h-9 px-2 font-medium text-primary underline"
                >
                  Undo
                </button>
              </div>
            )}
            {count} enabled
            {requiredCount > 0 ? `, including ${requiredCount} required` : ""}
            {missingCount > 0 && (
              <div>{missingCount} saved unavailable selections retained.</div>
            )}
          </div>
        </>
      )}
    </>
  );
  const triggerButton = (
    <button
      ref={trigger}
      type="button"
      disabled={props.disabled}
      aria-label={`${title}: ${mode}`}
      className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-55"
    >
      {title}: {mode}
      <RiArrowDownSLine size={14} />
    </button>
  );
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    e.stopPropagation();
    navigate(e);
  };
  return mobile ? (
    <Sheet open={open} onOpenChange={changeOpen}>
      <SheetTrigger asChild>{triggerButton}</SheetTrigger>
      <SheetContent
        side="bottom"
        showCloseButton={false}
        aria-label={`${title} selection`}
        aria-describedby={undefined}
        onKeyDown={onKeyDown}
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          doneButton.current?.focus();
        }}
        className="h-[85dvh]! max-h-[85dvh] gap-0 overflow-hidden rounded-t-2xl p-0 pb-[env(safe-area-inset-bottom)]"
      >
        {content}
      </SheetContent>
    </Sheet>
  ) : (
    <Popover open={open} onOpenChange={changeOpen}>
      <PopoverTrigger asChild>{triggerButton}</PopoverTrigger>
      <PopoverContent
        side={side}
        align="start"
        aria-label={`${title} selection`}
        onKeyDown={onKeyDown}
        className="flex w-[min(90vw,400px)] max-h-[min(560px,var(--radix-popover-content-available-height))] flex-col gap-0 overflow-hidden rounded-xl p-0"
      >
        {content}
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
