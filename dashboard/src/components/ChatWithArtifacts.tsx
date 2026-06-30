"use client";

import { useState, useCallback, useRef } from "react";
import ChatPanel from "./ChatPanel";
import type { ChatItem } from "./ChatPanel";
import ArtifactViewer from "./ArtifactViewer";
import type { Artifact } from "./ArtifactViewer";

// ── Draggable Resizer ───────────────────────────────────────────────────────

function PanelResizer({
  onResize,
  onDoubleClick,
}: {
  onResize: (deltaX: number) => void;
  onDoubleClick: () => void;
}) {
  const isDragging = useRef(false);
  const startX = useRef(0);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      isDragging.current = true;
      startX.current = e.clientX;
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";

      const handleMouseMove = (moveEvent: MouseEvent) => {
        if (!isDragging.current) return;
        const delta = moveEvent.clientX - startX.current;
        startX.current = moveEvent.clientX;
        onResize(delta);
      };

      const handleMouseUp = () => {
        isDragging.current = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        document.removeEventListener("mousemove", handleMouseMove);
        document.removeEventListener("mouseup", handleMouseUp);
      };

      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("mouseup", handleMouseUp);
    },
    [onResize]
  );

  return (
    <div
      className="w-1 hover:w-1.5 bg-gray-200 hover:bg-accent-300 cursor-col-resize flex-shrink-0 transition-all duration-100 relative group"
      onMouseDown={handleMouseDown}
      onDoubleClick={onDoubleClick}
      title="Drag to resize. Double-click to reset."
    >
      {/* Wider invisible hit target */}
      <div className="absolute inset-y-0 -left-1 -right-1" />
      {/* Visual grip dots */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity">
        <div className="flex flex-col gap-1">
          <div className="w-1 h-1 rounded-full bg-gray-400" />
          <div className="w-1 h-1 rounded-full bg-gray-400" />
          <div className="w-1 h-1 rounded-full bg-gray-400" />
        </div>
      </div>
    </div>
  );
}

// ── Chat + Artifact Split Pane ──────────────────────────────────────────────

export default function ChatWithArtifacts({
  initialItems,
  initialArtifacts,
  conversationId,
  initialPrompt,
  initialStatus,
  onConversationCreated,
}: {
  initialItems?: ChatItem[];
  initialArtifacts?: Artifact[];
  conversationId?: string;
  initialPrompt?: string;
  initialStatus?: string;
  onConversationCreated?: (conversationId: string) => void;
}) {
  // ── Artifact state ──────────────────────────────────────────────────────
  const [artifacts, setArtifacts] = useState<Artifact[]>(initialArtifacts || []);
  const [activeArtifactId, setActiveArtifactId] = useState<string | null>(null);
  const [chatPanelPercent, setChatPanelPercent] = useState(50);
  const containerRef = useRef<HTMLDivElement>(null);

  const activeArtifact = artifacts.find((a) => a.id === activeArtifactId) || null;

  const handleArtifactOpen = useCallback((artifact: Artifact) => {
    setArtifacts((prev) => {
      const existing = prev.findIndex((a) => a.id === artifact.id);
      if (existing >= 0) {
        const updated = [...prev];
        updated[existing] = artifact;
        return updated;
      }
      return [...prev, artifact];
    });
    setActiveArtifactId(artifact.id);
  }, []);

  const handleArtifactClose = useCallback(() => {
    setActiveArtifactId(null);
  }, []);

  const handleSelectArtifact = useCallback((id: string) => {
    setActiveArtifactId(id);
  }, []);

  const handleResize = useCallback((deltaX: number) => {
    if (!containerRef.current) return;
    const containerWidth = containerRef.current.offsetWidth;
    if (containerWidth === 0) return;
    const deltaPercent = (deltaX / containerWidth) * 100;
    setChatPanelPercent((prev) => {
      const newPercent = prev + deltaPercent;
      return Math.max(30, Math.min(75, newPercent));
    });
  }, []);

  const handleResetSplit = useCallback(() => {
    setChatPanelPercent(50);
  }, []);

  const showArtifactPanel = activeArtifact !== null;

  return (
    <div ref={containerRef} className="flex-1 overflow-hidden flex">
      {/* Chat panel (left) */}
      <div
        className="overflow-hidden bg-gray-50 flex-shrink-0"
        style={{
          width: showArtifactPanel ? `${chatPanelPercent}%` : "100%",
          transition: showArtifactPanel ? "none" : "width 0.3s ease-out",
        }}
      >
        <ChatPanel
          initialItems={initialItems}
          initialArtifacts={initialArtifacts}
          conversationId={conversationId}
          initialPrompt={initialPrompt}
          initialStatus={initialStatus}
          activeArtifactId={activeArtifactId}
          onArtifactOpen={handleArtifactOpen}
          onArtifactClose={handleArtifactClose}
          artifacts={artifacts}
          onConversationCreated={onConversationCreated}
        />
      </div>

      {/* Resizer + Artifact viewer (right) */}
      {showArtifactPanel && (
        <>
          {/* Draggable resizer — hidden on mobile */}
          <div className="hidden md:flex">
            <PanelResizer onResize={handleResize} onDoubleClick={handleResetSplit} />
          </div>

          {/* Artifact viewer panel */}
          <div
            className="flex-1 min-w-0 overflow-hidden
              fixed inset-0 z-[80] md:static md:z-auto
              animate-artifact-slide-in"
          >
            {/* Mobile close overlay */}
            <div
              className="absolute inset-0 bg-black/30 md:hidden"
              onClick={handleArtifactClose}
            />
            <div className="relative h-full md:h-full">
              <ArtifactViewer
                artifact={activeArtifact!}
                onClose={handleArtifactClose}
                allArtifacts={artifacts}
                onSelectArtifact={handleSelectArtifact}
              />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
