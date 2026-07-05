// PWA environment detection (client-only — call from effects/handlers).

export function isIos(): boolean {
  return (
    /iP(hone|ad|od)/.test(navigator.userAgent) ||
    // iPadOS reports as MacIntel but has touch points.
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

/** True when running as an installed home-screen app. */
export function isStandalone(): boolean {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    (navigator as { standalone?: boolean }).standalone === true
  );
}
