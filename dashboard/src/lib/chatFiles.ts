// File → ChatFile conversion, shared by the chat composer and the tasks
// quick-add composer.

import type { ChatFile } from "./api";

export const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10 MB
const MAX_TEXT_SIZE = 50 * 1024; // 50 KB

const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);
const TEXT_EXTENSIONS = new Set([
  "txt", "csv", "json", "py", "md", "js", "ts", "tsx", "jsx", "yml", "yaml",
  "xml", "html", "css", "log", "sh", "sql", "env", "cfg", "ini", "toml",
]);

/** Base64-encode a File's raw bytes. */
async function fileToBase64(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  return btoa(
    new Uint8Array(buffer).reduce((data, byte) => data + String.fromCharCode(byte), "")
  );
}

/**
 * Read a File into a ChatFile object.
 *
 * Any file extension is accepted — images and known text formats get their
 * dedicated handling, and everything else (documents, archives, or any
 * unrecognized extension) is attached as a binary blob. The backend writes
 * binary attachments to a temp file with their original extension, so the
 * agent can work with arbitrary file types.
 */
export async function readFileAsChatFile(file: File): Promise<ChatFile | null> {
  if (file.size > MAX_FILE_SIZE) return null;

  const ext = file.name.split(".").pop()?.toLowerCase() || "";
  const isImage = IMAGE_TYPES.has(file.type);
  const isText = TEXT_EXTENSIONS.has(ext) || file.type.startsWith("text/");

  if (isImage) {
    return { name: file.name, mimetype: file.type, type: "image", data: await fileToBase64(file) };
  }

  if (isText) {
    let text = await file.text();
    if (text.length > MAX_TEXT_SIZE) {
      text = text.slice(0, MAX_TEXT_SIZE) + `\n\n... [truncated, file was ${(file.size / 1024).toFixed(0)} KB]`;
    }
    return { name: file.name, mimetype: file.type || "text/plain", type: "text", data: text };
  }

  // Any other file type — attach as a binary blob (no allow-list gate).
  return {
    name: file.name,
    mimetype: file.type || "application/octet-stream",
    type: "binary",
    data: await fileToBase64(file),
  };
}

/** Convert a FileList/array into ChatFiles, reporting rejects by name. */
export async function filesToChatFiles(
  fileList: FileList | File[],
): Promise<{ files: ChatFile[]; rejected: string[] }> {
  const files: ChatFile[] = [];
  const rejected: string[] = [];
  for (const f of Array.from(fileList)) {
    if (f.size > MAX_FILE_SIZE) {
      rejected.push(`${f.name} (too large — max ${MAX_FILE_SIZE / 1024 / 1024}MB)`);
      continue;
    }
    const cf = await readFileAsChatFile(f);
    if (cf) files.push(cf);
    else rejected.push(f.name);
  }
  return { files, rejected };
}

/**
 * Extract every file from a clipboard paste (any type, not just images).
 * Clipboard files that arrive without a usable name (e.g. screenshots) are
 * given a timestamped name so they are distinguishable. Non-file clipboard
 * items (plain text, html) are ignored, so normal text paste is untouched.
 */
export function filesFromClipboard(clipboardData: DataTransfer | null): File[] {
  const items = clipboardData?.items;
  if (!items) return [];
  const out: File[] = [];
  for (const item of Array.from(items)) {
    if (item.kind !== "file") continue;
    const file = item.getAsFile();
    if (!file) continue;
    if (!file.name || file.name.toLowerCase() === "image.png" || file.name.toLowerCase() === "blob") {
      const ext = (file.type.split("/")[1] || "bin").split("+")[0];
      const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
      out.push(new File([file], `clipboard-${timestamp}.${ext}`, { type: file.type }));
    } else {
      out.push(file);
    }
  }
  return out;
}
