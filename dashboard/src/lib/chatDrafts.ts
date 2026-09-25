import type { ChatFile } from "./api";

export interface ChatDraft { text: string; files: ChatFile[]; updatedAt: number }
const MAX_AGE = 7 * 24 * 60 * 60 * 1000;
let database: Promise<IDBDatabase> | undefined;

function openDatabase(): Promise<IDBDatabase> {
  return database ??= new Promise((resolve, reject) => {
    const request = indexedDB.open("loma-chat-drafts", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("drafts");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => { database = undefined; reject(request.error); };
  });
}

export async function readChatDraft(key: string): Promise<ChatDraft | undefined> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("drafts", "readwrite");
    const store = tx.objectStore("drafts");
    // Prune abandoned drafts as well, not only the conversation being opened.
    const expired = store.openCursor();
    expired.onsuccess = () => {
      const cursor = expired.result;
      if (!cursor) return;
      if (Date.now() - cursor.value.updatedAt > MAX_AGE) cursor.delete();
      cursor.continue();
    };
    const request = store.get(key);
    let draft: ChatDraft | undefined;
    request.onsuccess = () => {
      draft = request.result;
      if (draft && Date.now() - draft.updatedAt > MAX_AGE) {
        store.delete(key);
        draft = undefined;
      }
    };
    tx.oncomplete = () => resolve(draft);
    tx.onabort = tx.onerror = () => reject(tx.error);
  });
}

export async function writeChatDraft(key: string, text: string, files: ChatFile[]): Promise<void> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("drafts", "readwrite");
    const store = tx.objectStore("drafts");
    if (text || files.length) store.put({ text, files, updatedAt: Date.now() }, key);
    else store.delete(key);
    tx.oncomplete = () => resolve();
    tx.onabort = tx.onerror = () => reject(tx.error);
  });
}
