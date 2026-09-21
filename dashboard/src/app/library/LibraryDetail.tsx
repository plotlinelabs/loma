"use client";

import { RiChat1Line, RiEyeLine } from "@remixicon/react";
import type { Asset } from "../../lib/assets-api";
import { basePath, getFileUrl } from "../../lib/api";
import ArtifactViewer from "../../components/ArtifactViewer";
import { Button } from "@/components/ui/button";

const DOCUMENT_PREVIEW: Record<string, string> = {
  "application/pdf": "pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
};

function previewLanguage(mimeType: string): string {
  const mime = mimeType.toLowerCase();
  if (DOCUMENT_PREVIEW[mime]) return DOCUMENT_PREVIEW[mime];
  const subtype = (mime.split("/")[1] || "file").split("+")[0];
  return subtype || "file";
}

export function LibraryDetail({
  asset,
  onClose,
}: {
  asset: Asset | null;
  onClose: () => void;
}) {
  if (!asset) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-[13px] text-muted-foreground">Select a file</p>
      </div>
    );
  }

  const conversationId = asset.conversation_id;
  const unavailable = asset.status === "unavailable";
  const fileUrl = getFileUrl(asset.file_id);
  const language = previewLanguage(asset.mime_type);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {conversationId ? (
        <div className="flex items-center gap-1.5 px-3 py-2">
          <Button asChild size="sm" variant="secondary">
            <a href={`${basePath}/chat?continue=${conversationId}`}>
              <RiChat1Line size={14} />
              Continue
            </a>
          </Button>
          <Button asChild size="sm" variant="outline">
            <a href={`${basePath}/conversations/${conversationId}`}>
              <RiEyeLine size={14} />
              View
            </a>
          </Button>
        </div>
      ) : null}
      <div className="min-h-0 flex-1">
        {unavailable ? (
          <div className="flex h-full items-center justify-center px-4">
            <p className="text-[13px] text-muted-foreground">{asset.reason}</p>
          </div>
        ) : (
          <ArtifactViewer
            artifact={{
              id: asset.file_id,
              title: asset.name,
              content: "",
              language,
              version: 1,
              timestamp: Date.parse(asset.created_at) || 0,
              file_url: fileUrl,
              file_size: asset.size_bytes,
              file_type: asset.mime_type,
            }}
            onClose={onClose}
          />
        )}
      </div>
    </div>
  );
}
