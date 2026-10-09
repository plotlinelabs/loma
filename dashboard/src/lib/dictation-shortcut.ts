/** Keyboard shortcut that starts/stops dictation. Stored per browser, since
 * the right combo depends on the machine (Mac vs Windows, global hotkeys). */
export interface KeyShortcut {
  /** KeyboardEvent.code, e.g. "Space" or "KeyD" (layout independent). */
  code: string;
  alt: boolean;
  ctrl: boolean;
  meta: boolean;
  shift: boolean;
}

/** null means the shortcut is turned off. */
export type DictationShortcut = KeyShortcut | null;

export const DICTATION_SHORTCUT_STORAGE_KEY = "loma-dictation-shortcut";
export const DEFAULT_DICTATION_SHORTCUT: KeyShortcut = { code: "Space", alt: true, ctrl: false, meta: false, shift: false };

const MODIFIER_CODES = new Set([
  "AltLeft", "AltRight", "ControlLeft", "ControlRight", "MetaLeft", "MetaRight",
  "ShiftLeft", "ShiftRight", "CapsLock", "Fn", "FnLock", "OSLeft", "OSRight",
]);
// Editing shortcuts people rely on inside the composer.
const RESERVED_WITH_MOD = new Set(["KeyA", "KeyC", "KeyV", "KeyX", "KeyZ", "KeyY", "Enter", "Backspace", "Tab"]);

type KeyLike = Pick<KeyboardEvent, "code" | "altKey" | "ctrlKey" | "metaKey" | "shiftKey">;

export function shortcutFromEvent(event: KeyLike): KeyShortcut {
  return { code: event.code, alt: event.altKey, ctrl: event.ctrlKey, meta: event.metaKey, shift: event.shiftKey };
}

export function matchesShortcut(event: KeyLike, shortcut: DictationShortcut): boolean {
  return !!shortcut &&
    event.code === shortcut.code &&
    event.altKey === shortcut.alt &&
    event.ctrlKey === shortcut.ctrl &&
    event.metaKey === shortcut.meta &&
    event.shiftKey === shortcut.shift;
}

export const isModifierCode = (code: string) => MODIFIER_CODES.has(code);

/** Why a recorded combo can't be used, or null when it is fine. */
export function validateShortcut(s: KeyShortcut): string | null {
  if (!s.code || isModifierCode(s.code)) return "Press a key together with the modifiers.";
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(s.code)) return null;
  if (!s.alt && !s.ctrl && !s.meta) {
    return "Add Option/Alt, Ctrl or Cmd so normal typing isn't blocked.";
  }
  if ((s.ctrl || s.meta) && !s.alt && !s.shift && RESERVED_WITH_MOD.has(s.code)) {
    return "That combo is used for editing text. Pick another.";
  }
  return null;
}

export function parseShortcut(raw: string | null): DictationShortcut {
  if (raw === null) return DEFAULT_DICTATION_SHORTCUT;
  if (raw === "off") return null;
  try {
    const v = JSON.parse(raw);
    if (v && typeof v.code === "string") {
      const s = { code: v.code, alt: !!v.alt, ctrl: !!v.ctrl, meta: !!v.meta, shift: !!v.shift };
      return validateShortcut(s) ? DEFAULT_DICTATION_SHORTCUT : s;
    }
  } catch {
    // Corrupt value: fall back to the default below.
  }
  return DEFAULT_DICTATION_SHORTCUT;
}

export function serializeShortcut(s: DictationShortcut): string {
  return s ? JSON.stringify(s) : "off";
}

const NAMED_KEYS: Record<string, string> = {
  Space: "Space", Period: ".", Comma: ",", Slash: "/", Backslash: "\\", Semicolon: ";",
  Quote: "'", BracketLeft: "[", BracketRight: "]", Minus: "-", Equal: "=", Backquote: "`",
  Escape: "Esc", Enter: "Enter", ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→",
};

function keyLabel(code: string): string {
  if (code.startsWith("Key")) return code.slice(3);
  if (code.startsWith("Digit")) return code.slice(5);
  if (code.startsWith("Numpad")) return "Num " + code.slice(6);
  return NAMED_KEYS[code] ?? code;
}

/** Human label, e.g. "Option + Space" on Mac or "Alt + Space" elsewhere. */
export function formatShortcut(s: DictationShortcut, mac: boolean): string {
  if (!s) return "Off";
  const parts: string[] = [];
  if (s.ctrl) parts.push("Ctrl");
  if (s.alt) parts.push(mac ? "Option" : "Alt");
  if (s.shift) parts.push("Shift");
  if (s.meta) parts.push(mac ? "Cmd" : "Win");
  parts.push(keyLabel(s.code));
  return parts.join(" + ");
}

/** Value for aria-keyshortcuts, e.g. "Alt+Space". */
export function ariaShortcut(s: DictationShortcut): string | undefined {
  if (!s) return undefined;
  const parts: string[] = [];
  if (s.ctrl) parts.push("Control");
  if (s.alt) parts.push("Alt");
  if (s.shift) parts.push("Shift");
  if (s.meta) parts.push("Meta");
  parts.push(s.code.startsWith("Key") || s.code.startsWith("Digit") ? keyLabel(s.code) : s.code);
  return parts.join("+");
}
