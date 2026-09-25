"use client";

import type { Asset } from "../../lib/assets-api";
import ClientTimestamp from "@/components/ClientTimestamp";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  RiFileExcelLine,
  RiFileImageLine,
  RiFileLine,
  RiFilePdfLine,
  RiFileTextLine,
} from "@remixicon/react";

export function formatAssetSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatAssetType(mimeType: string, name = ""): string {
  const subtype = (mimeType.split("/")[1] || "").split("+")[0];
  if (subtype === "jpeg") return "JPG";
  if (subtype === "plain") return "TXT";
  if (subtype) return subtype.toUpperCase();
  const ext = name.split(".").pop();
  return ext ? ext.toUpperCase() : "FILE";
}

function typeIcon(mimeType: string, name: string) {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (mimeType.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) {
    return RiFileImageLine;
  }
  if (mimeType === "application/pdf" || ext === "pdf") return RiFilePdfLine;
  if (mimeType === "text/csv" || ext === "csv" || ["xlsx", "xls"].includes(ext)) {
    return RiFileExcelLine;
  }
  if (mimeType.startsWith("text/") || ["txt", "md"].includes(ext)) return RiFileTextLine;
  return RiFileLine;
}

function AssetTypeIcon({ mimeType, name }: { mimeType: string; name: string }) {
  const label = formatAssetType(mimeType, name);
  const Icon = typeIcon(mimeType, name);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span aria-label={label} className="text-muted-foreground">
          <Icon size={14} />
        </span>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

export function LibrarySurface({
  assets,
  selectedId,
  onSelect,
}: {
  assets: Asset[];
  selectedId?: string | null;
  onSelect?: (fileId: string) => void;
}) {
  if (assets.length === 0) {
    return (
      <p className="px-4 py-6 text-[13px] text-muted-foreground">No files yet</p>
    );
  }

  return (
    <TooltipProvider delayDuration={300}>
      <ul className="px-1 pb-3">
        {assets.map((asset) => {
          const selected = asset.file_id === selectedId;
          const unavailable = asset.status === "unavailable";
          return (
            <li key={asset.file_id}>
              <button
                type="button"
                onClick={() => onSelect?.(asset.file_id)}
                className={cn(
                  "flex w-full items-start gap-2 rounded-md px-3 py-1.5 text-left transition-colors",
                  selected
                    ? "bg-accent text-accent-foreground"
                    : "text-foreground/80 hover:bg-muted",
                )}
              >
                {unavailable ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span aria-label={asset.reason || "unavailable"} className="mt-1.5 shrink-0">
                        <span aria-hidden className="block h-1.5 w-1.5 rounded-full bg-red-500" />
                      </span>
                    </TooltipTrigger>
                    {asset.reason ? <TooltipContent>{asset.reason}</TooltipContent> : null}
                  </Tooltip>
                ) : null}
                <span className="mt-0.5 shrink-0">
                  <AssetTypeIcon mimeType={asset.mime_type} name={asset.name} />
                </span>
                <span className="min-w-0 flex flex-col items-start gap-0.5">
                  <span className="text-[13px] truncate w-full">{asset.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {formatAssetSize(asset.size_bytes)}
                    {" · "}
                    <time dateTime={asset.created_at}>
                      <ClientTimestamp iso={asset.created_at} variant="short" />
                    </time>
                  </span>
                  {unavailable && asset.reason ? (
                    <span className="text-xs text-muted-foreground">{asset.reason}</span>
                  ) : null}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </TooltipProvider>
  );
}
