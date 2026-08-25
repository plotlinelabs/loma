"use client";

import { useRef, useState } from "react";
import { RiUploadLine, RiFileTextLine } from "@remixicon/react";

import { type EvalSuite, uploadSuiteCasesCsv } from "@/lib/prompt-eval-api";

// Drag-and-drop + click-to-browse CSV upload for bulk test cases (the
// golden-dataset path) — adapted from ChatPanel.tsx's drag/drop handlers,
// not extracted into a shared hook there yet, so this is its own small copy
// rather than a premature shared abstraction across two different upload
// shapes (file attachments vs. a single CSV).
export function CsvUpload({
  ensureSuiteId,
  onUploaded,
}: {
  // Returns the suite_id to upload into, creating the suite first if one
  // doesn't exist yet — mirrors the existing ensureSuite()/saveCases()
  // pattern each surface already has for "Run".
  ensureSuiteId: () => Promise<string>;
  onUploaded: (suite: EvalSuite, added: number) => void;
}) {
  const [isDragOver, setIsDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFile = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".csv")) {
      setError("Only .csv files are accepted.");
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const suiteId = await ensureSuiteId();
      const result = await uploadSuiteCasesCsv(suiteId, file);
      onUploaded(result.suite, result.added);
    } catch (e) {
      setError(e instanceof Error ? e.message : "CSV upload failed");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragOver(true);
        }}
        onDragLeave={(e) => {
          e.preventDefault();
          setIsDragOver(false);
        }}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragOver(false);
          const file = e.dataTransfer.files?.[0];
          if (file) handleFile(file);
        }}
        className={`border border-dashed rounded-lg p-3 flex items-center gap-2 text-xs cursor-pointer transition-colors ${
          isDragOver ? "border-brand-400 bg-brand-50/50" : "border-border hover:border-muted-foreground"
        }`}
        onClick={() => fileInputRef.current?.click()}
      >
        {uploading ? (
          <span className="text-muted-foreground">Uploading…</span>
        ) : (
          <>
            <RiUploadLine size={14} className="text-muted-foreground" />
            <span className="text-muted-foreground">
              Drop a CSV here or click to browse — columns: <code>input</code> (required),{" "}
              <code>expected_contains</code>, <code>expected_not_contains</code>, <code>rubric</code>
            </span>
          </>
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFile(file);
            e.target.value = "";
          }}
        />
      </div>
      {error && (
        <p className="text-[11px] text-red-500 mt-1 flex items-center gap-1">
          <RiFileTextLine size={11} /> {error}
        </p>
      )}
    </div>
  );
}
