"use client";

import {
  RiAddLine,
  RiChatHistoryLine,
  RiMoreLine,
  RiNotification3Line,
  RiNotificationOffLine,
  RiPriceTag3Line,
  RiSearchLine,
  RiSettings3Line,
  RiUserAddLine,
  RiUserLine,
} from "@remixicon/react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { TaskTag } from "@/lib/api";
import type { PushState } from "@/lib/push";

interface MobileBoardActionsProps {
  searchOpen: boolean;
  onToggleSearch: () => void;
  searchLabel: string;
  /** Undefined hides the item (view-only boards). */
  onNew?: () => void;
  newLabel: string;
  /** Undefined on personal boards. */
  assignedToMe?: boolean;
  onToggleAssigned?: () => void;
  tags: TaskTag[];
  includedTagIds: string[];
  excludedTagIds: string[];
  onIncludeTag: (id: string, on: boolean) => void;
  onExcludeTag: (id: string, on: boolean) => void;
  onClearTags: () => void;
  onShare?: () => void;
  onAddChat?: () => void;
  pushState: PushState | null;
  onTogglePush: () => void;
  onSettings?: () => void;
}

/** The task board's header actions on a phone: search stays one tap away in
 * the top bar, everything else (filters, share, notifications, settings)
 * lives in one overflow menu instead of a second header row. */
export function MobileBoardActions({
  searchOpen, onToggleSearch, searchLabel, onNew, newLabel,
  assignedToMe, onToggleAssigned,
  tags, includedTagIds, excludedTagIds, onIncludeTag, onExcludeTag, onClearTags,
  onShare, onAddChat, pushState, onTogglePush, onSettings,
}: MobileBoardActionsProps) {
  const tagFilterCount = includedTagIds.length + excludedTagIds.length;
  const filtering = !!assignedToMe || tagFilterCount > 0;
  const hasMenu = !!onNew || !!onToggleAssigned || tags.length > 0 || !!onShare || !!onAddChat || pushState !== null || !!onSettings;

  return (
    <>
      <Button
        variant={searchOpen ? "secondary" : "ghost"} size="icon"
        className="size-10 rounded-full text-muted-foreground"
        aria-label={searchLabel} aria-pressed={searchOpen}
        onClick={onToggleSearch}
      >
        <RiSearchLine size={18} />
      </Button>
      {hasMenu && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="relative size-10 rounded-full text-muted-foreground" aria-label="Board actions">
              <RiMoreLine size={20} />
              {filtering && <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-amber-500" />}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-60">
            {onNew && (
              <DropdownMenuItem onSelect={onNew}>
                <RiAddLine /> {newLabel}
              </DropdownMenuItem>
            )}
            {onAddChat && (
              <DropdownMenuItem onSelect={onAddChat}>
                <RiChatHistoryLine /> Add existing chat
              </DropdownMenuItem>
            )}
            {(onToggleAssigned || tags.length > 0) && <DropdownMenuSeparator />}
            {onToggleAssigned && (
              <DropdownMenuCheckboxItem checked={!!assignedToMe} onCheckedChange={onToggleAssigned}>
                <RiUserLine /> Assigned to me
              </DropdownMenuCheckboxItem>
            )}
            {tags.length > 0 && (
              <DropdownMenuSub>
                <DropdownMenuSubTrigger>
                  <RiPriceTag3Line />
                  <span className="flex-1">Tags</span>
                  {tagFilterCount > 0 && <span className="text-xs text-muted-foreground">{tagFilterCount}</span>}
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent className="max-h-80 min-w-48 overflow-y-auto">
                  <DropdownMenuLabel>Include</DropdownMenuLabel>
                  {tags.map((tag) => (
                    <DropdownMenuCheckboxItem key={`include-${tag.id}`} checked={includedTagIds.includes(tag.id)}
                      onCheckedChange={(checked) => onIncludeTag(tag.id, !!checked)}
                      onSelect={(e) => e.preventDefault()}>
                      {tag.name}
                    </DropdownMenuCheckboxItem>
                  ))}
                  <DropdownMenuSeparator />
                  <DropdownMenuLabel>Exclude</DropdownMenuLabel>
                  {tags.map((tag) => (
                    <DropdownMenuCheckboxItem key={`exclude-${tag.id}`} checked={excludedTagIds.includes(tag.id)}
                      onCheckedChange={(checked) => onExcludeTag(tag.id, !!checked)}
                      onSelect={(e) => e.preventDefault()}>
                      {tag.name}
                    </DropdownMenuCheckboxItem>
                  ))}
                  {tagFilterCount > 0 && (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem onSelect={onClearTags}>Clear filters</DropdownMenuItem>
                    </>
                  )}
                </DropdownMenuSubContent>
              </DropdownMenuSub>
            )}
            {(onShare || pushState !== null || onSettings) && <DropdownMenuSeparator />}
            {onShare && (
              <DropdownMenuItem onSelect={onShare}>
                <RiUserAddLine /> Share board
              </DropdownMenuItem>
            )}
            {pushState !== null && (
              <DropdownMenuItem disabled={pushState === "denied"} onSelect={onTogglePush}>
                {pushState === "subscribed" ? <RiNotification3Line /> : <RiNotificationOffLine />}
                {pushState === "denied"
                  ? "Notifications blocked"
                  : pushState === "subscribed" ? "Notifications on" : "Notify me on input"}
              </DropdownMenuItem>
            )}
            {onSettings && (
              <DropdownMenuItem onSelect={onSettings}>
                <RiSettings3Line /> Board settings
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </>
  );
}
