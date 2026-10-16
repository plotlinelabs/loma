/** Runs take minutes, so people switch tabs. When a chat finishes while this
 * tab is in the background, say so in the tab title until they come back, and
 * with a desktop notification when they've allowed notifications for Loma
 * (task push already asks). Nothing happens while the tab is visible. */
export function alertIfAway(title: string | null) {
  if (typeof document === "undefined" || document.visibilityState === "visible") return;
  const label = title?.trim() || "your chat";
  const original = document.title.replace(/^Done: .*? · /, "");
  document.title = `Done: ${label} · ${original}`;
  const restore = () => {
    if (document.visibilityState !== "visible") return;
    document.title = original;
    document.removeEventListener("visibilitychange", restore);
  };
  document.addEventListener("visibilitychange", restore);

  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  try {
    const notification = new Notification("Loma finished", { body: label, tag: `loma-chat-${label}` });
    notification.onclick = () => {
      window.focus();
      notification.close();
    };
  } catch {
    // Some browsers only allow notifications from a service worker; the title still says it.
  }
}
