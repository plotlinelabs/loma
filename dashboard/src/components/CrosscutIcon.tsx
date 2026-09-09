import { cn } from "@/lib/utils";

/** Four-leaf mark shared by the sidebar, login, and assistant surfaces. */
export default function CrosscutIcon({ size = 28, className }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true"
      className={cn("loma-mark shrink-0", className)} fill="currentColor">
      <circle cx="8" cy="8" r="6" />
      <circle cx="20" cy="8" r="6" />
      <circle cx="8" cy="20" r="6" />
      <circle cx="20" cy="20" r="6" />
    </svg>
  );
}
