"use client";

import { useEffect, useState } from "react";

/** True while the on-screen keyboard is likely open — the visual viewport
 * is meaningfully shorter than the layout viewport. Used to hide chrome
 * (e.g. the bottom nav) so typing gets the full height back. */
export function useKeyboardVisible(): boolean {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const onResize = () => setVisible(window.innerHeight - vv.height > 140);
    vv.addEventListener("resize", onResize);
    onResize();
    return () => vv.removeEventListener("resize", onResize);
  }, []);

  return visible;
}
