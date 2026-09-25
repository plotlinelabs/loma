"use client";

import { useCallback, useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchAssets, type Asset } from "../../lib/assets-api";
import { LibrarySurface } from "./LibrarySurface";
import { LibraryDetail } from "./LibraryDetail";

export default function LibraryPage() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    fetchAssets()
      .then((data) => {
        setAssets(data.assets);
        setLoadError(false);
      })
      .catch(() => {
        setAssets([]);
        setLoadError(true);
      })
      .finally(() => setLoading(false));
  }, []);

  const handleSelect = useCallback((fileId: string) => {
    setSelectedId(fileId);
  }, []);

  const selected = assets.find((asset) => asset.file_id === selectedId) ?? null;

  return (
    <div className="flex h-full min-h-0 overflow-hidden">
      <div className="w-[280px] flex-shrink-0 border-r border-border bg-muted/50 flex flex-col min-h-0 overflow-y-auto">
        <div className="px-4 py-3 shrink-0 sticky top-0 z-10 bg-muted/50">
          <h1 className="text-lg font-semibold">Library</h1>
        </div>
        {loading ? (
          <div className="px-4 space-y-3">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-3 w-28" />
            <Skeleton className="h-3 w-36" />
          </div>
        ) : loadError ? (
          <p className="px-4 py-6 text-[13px] text-muted-foreground">Couldn't load</p>
        ) : (
          <LibrarySurface
            assets={assets}
            selectedId={selectedId}
            onSelect={handleSelect}
          />
        )}
      </div>
      <div className="flex-1 min-w-0 min-h-0">
        <LibraryDetail asset={selected} onClose={() => setSelectedId(null)} />
      </div>
    </div>
  );
}
