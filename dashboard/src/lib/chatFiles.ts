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
const BINARY_EXTENSIONS = new Set([
  "xlsx", "xlsm", "xls", "pdf", "docx", "pptx",
  "zip", "tar", "gz", "7z", "rar", "tgz",       // archives
]);

/** Read a File into a ChatFile object */
export async function readFileAsChatFile(file: File): Promise<ChatFile | null> {
  if (file.size > MAX_FILE_SIZE) return null;

  const ext = file.name.split(".").pop()?.toLowerCase() || "";
  const isImage = IMAGE_TYPES.has(file.type);
  const isText = TEXT_EXTENSIONS.has(ext) || file.type.startsWith("text/");
  const isBinary = BINARY_EXTENSIONS.has(ext);

  if (!isImage && !isText && !isBinary) {
    console.warn(`Unsupported file type: ${file.name} (${file.type})`);
    return null;
  }

  if (isImage) {
    const buffer = await file.arrayBuffer();
    const base64 = btoa(
      new Uint8Array(buffer).reduce((data, byte) => data + String.fromCharCode(byte), "")
    );
    return { name: file.name, mimetype: file.type, type: "image", data: base64 };
  }

  if (isBinary) {
    const buffer = await file.arrayBuffer();
    const base64 = btoa(
      new Uint8Array(buffer).reduce((data, byte) => data + String.fromCharCode(byte), "")
    );
    return { name: file.name, mimetype: file.type || "application/octet-stream", type: "binary", data: base64 };
  }

  // Text file
  let text = await file.text();
  if (text.length > MAX_TEXT_SIZE) {
    text = text.slice(0, MAX_TEXT_SIZE) + `\n\n... [truncated, file was ${(file.size / 1024).toFixed(0)} KB]`;
  }
  return { name: file.name, mimetype: file.type || "text/plain", type: "text", data: text };
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
