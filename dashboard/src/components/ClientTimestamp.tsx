"use client";

import {
  useClientTimestamp,
  useClientFullTimestamp,
  useClientTimeOnly,
  useClientShortTimestamp,
} from "../hooks/useClientTimestamp";

interface ClientTimestampProps {
  /** ISO-8601 timestamp string */
  iso: string | null | undefined;
  /** Formatting variant */
  variant?: "full" | "time" | "short" | "custom";
  /** Custom Intl.DateTimeFormatOptions (only used when variant="custom") */
  options?: Intl.DateTimeFormatOptions;
  /** Placeholder shown during SSR / before hydration */
  placeholder?: string;
  /** Extra class names for the wrapping <span> */
  className?: string;
}

/**
 * Renders a timestamp that is always formatted using the **browser's**
 * timezone and locale, avoiding the Next.js SSR hydration issue where
 * `toLocaleString()` runs on the server in UTC.
 *
 * Uses `suppressHydrationWarning` so React doesn't warn about the
 * placeholder-to-real-value switch.
 */
export default function ClientTimestamp({
  iso,
  variant = "full",
  options,
  placeholder = "\u2014",
  className,
}: ClientTimestampProps) {
  let formatted: string;

  switch (variant) {
    case "time":
      formatted = useClientTimeOnly(iso, placeholder);
      break;
    case "short":
      formatted = useClientShortTimestamp(iso, placeholder);
      break;
    case "custom":
      formatted = useClientTimestamp(iso, options, placeholder);
      break;
    case "full":
    default:
      formatted = useClientFullTimestamp(iso, placeholder);
      break;
  }

  return (
    <span className={className} suppressHydrationWarning>
      {formatted}
    </span>
  );
}
