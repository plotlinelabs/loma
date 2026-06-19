import { useState, useEffect } from "react";

type FormatOptions = Intl.DateTimeFormatOptions;

const DEFAULT_FULL: FormatOptions = {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
};

const DEFAULT_TIME_ONLY: FormatOptions = {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
};

const DEFAULT_SHORT: FormatOptions = {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
};

/**
 * Returns a formatted timestamp string that is guaranteed to use the
 * **browser's** locale and timezone.
 *
 * During SSR (and the very first client render before hydration) the hook
 * returns the `placeholder` value ("—" by default). After `useEffect` fires
 * (client-only), it re-renders with the correctly-localised string.
 *
 * This avoids the Next.js SSR hydration issue where `toLocaleString()` runs
 * on the server in UTC and that value gets stuck in the DOM.
 */
export function useClientTimestamp(
  iso: string | null | undefined,
  options?: FormatOptions,
  placeholder = "\u2014",
): string {
  const [formatted, setFormatted] = useState(placeholder);

  useEffect(() => {
    if (!iso) {
      setFormatted(placeholder);
      return;
    }
    const date = new Date(iso);
    if (isNaN(date.getTime())) {
      setFormatted(placeholder);
      return;
    }
    setFormatted(date.toLocaleString(undefined, options));
  }, [iso, placeholder]);

  return formatted;
}

/**
 * Convenience wrapper: full date + time.
 * e.g. "Feb 20, 2026, 12:37 PM"
 */
export function useClientFullTimestamp(
  iso: string | null | undefined,
  placeholder = "\u2014",
): string {
  return useClientTimestamp(iso, DEFAULT_FULL, placeholder);
}

/**
 * Convenience wrapper: time only.
 * e.g. "12:37:53 PM"
 */
export function useClientTimeOnly(
  iso: string | null | undefined,
  placeholder = "\u2014",
): string {
  return useClientTimestamp(iso, DEFAULT_TIME_ONLY, placeholder);
}

/**
 * Convenience wrapper: short date + time (no year).
 * e.g. "Feb 20, 12:37 PM"
 */
export function useClientShortTimestamp(
  iso: string | null | undefined,
  placeholder = "\u2014",
): string {
  return useClientTimestamp(iso, DEFAULT_SHORT, placeholder);
}
