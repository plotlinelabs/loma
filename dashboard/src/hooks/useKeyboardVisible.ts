"use client";

import { useEffect, useState } from "react";

/** Detect both visual-only (Safari) and layout+visual (Android resizes-content)
 * keyboard resizing. Focus alone is not enough: hardware keyboards also focus inputs. */
export function useKeyboardVisible(): boolean {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const touch = window.matchMedia("(pointer: coarse)");
    let baseline = window.innerHeight;
    let width = window.innerWidth;
    let frame = 0;
    const sync = () => {
      const el = document.activeElement as HTMLElement | null;
      const editable = el?.matches("textarea, input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]):not([type=range]):not([type=file])") || el?.isContentEditable;
      // A new width means rotation/window resizing, not a keyboard opening.
      if (window.innerWidth !== width) {
        width = window.innerWidth;
        baseline = window.innerHeight;
      }
      if (!editable) baseline = window.innerHeight;
      baseline = Math.max(baseline, window.innerHeight);
      const open = touch.matches && !!editable && Math.abs(vv.scale - 1) < 0.05 &&
        Math.max(window.innerHeight - vv.height, baseline - vv.height) > 140;
      setVisible(open);
    };
    const onFocusChange = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(sync);
    };
    vv.addEventListener("resize", sync);
    window.addEventListener("resize", sync);
    document.addEventListener("focusin", onFocusChange);
    document.addEventListener("focusout", onFocusChange);
    touch.addEventListener("change", sync);
    sync();
    return () => {
      cancelAnimationFrame(frame);
      vv.removeEventListener("resize", sync);
      window.removeEventListener("resize", sync);
      document.removeEventListener("focusin", onFocusChange);
      document.removeEventListener("focusout", onFocusChange);
      touch.removeEventListener("change", sync);
    };
  }, []);

  return visible;
}
