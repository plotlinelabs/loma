import { useId } from "react";

export default function CrosscutIcon({ size = 28, className }: { size?: number; className?: string }) {
  const uid = useId();
  const id = (name: string) => `${uid}-${name}`;

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 160 160"
      className={className}
      style={{ borderRadius: size * 0.15, flexShrink: 0 }}
    >
      <defs>
        <radialGradient id={id("glassDark")} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#2a2a2a"/>
          <stop offset="80%" stopColor="#0A0A0A"/>
          <stop offset="100%" stopColor="#000000"/>
        </radialGradient>
        <radialGradient id={id("glassLight")} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#2a2a2a" stopOpacity="0.5"/>
          <stop offset="80%" stopColor="#0A0A0A" stopOpacity="0.35"/>
          <stop offset="100%" stopColor="#000000" stopOpacity="0.3"/>
        </radialGradient>
        <linearGradient id={id("glassReflect")} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.55"/>
          <stop offset="100%" stopColor="#ffffff" stopOpacity="0"/>
        </linearGradient>
        <linearGradient id={id("rimLight")} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0"/>
          <stop offset="85%" stopColor="#ffffff" stopOpacity="0"/>
          <stop offset="100%" stopColor="#ffffff" stopOpacity="0.15"/>
        </linearGradient>
        <filter id={id("glassShadow")} x="-20%" y="-10%" width="140%" height="140%">
          <feDropShadow dx="2" dy="3" stdDeviation="2.5" floodColor="#0A0A0A" floodOpacity="0.35"/>
        </filter>
        <clipPath id={id("topHalf60")}><rect x="38" y="38" width="44" height="20"/></clipPath>
        <clipPath id={id("topHalf100")}><rect x="78" y="38" width="44" height="20"/></clipPath>
        <clipPath id={id("topHalf60b")}><rect x="38" y="78" width="44" height="20"/></clipPath>
        <clipPath id={id("topHalf100b")}><rect x="78" y="78" width="44" height="20"/></clipPath>
      </defs>
      <rect width="160" height="160" fill="#E8FF5A" />
      <line x1="60" y1="20" x2="60" y2="140" stroke="#0A0A0A" strokeWidth="2" opacity="0.1" />
      <line x1="100" y1="20" x2="100" y2="140" stroke="#0A0A0A" strokeWidth="2" opacity="0.1" />
      <line x1="20" y1="60" x2="140" y2="60" stroke="#0A0A0A" strokeWidth="2" opacity="0.1" />
      <line x1="20" y1="100" x2="140" y2="100" stroke="#0A0A0A" strokeWidth="2" opacity="0.1" />
      {/* Top-left (dark) */}
      <circle cx="60" cy="60" r="18" fill={`url(#${id("glassDark")})`} filter={`url(#${id("glassShadow")})`} />
      <circle cx="60" cy="60" r="17" fill={`url(#${id("rimLight")})`} />
      <ellipse cx="60" cy="54" rx="13" ry="8" fill={`url(#${id("glassReflect")})`} clipPath={`url(#${id("topHalf60")})`} />
      {/* Top-right (light) */}
      <circle cx="100" cy="60" r="18" fill={`url(#${id("glassLight")})`} filter={`url(#${id("glassShadow")})`} />
      <circle cx="100" cy="60" r="17" fill={`url(#${id("rimLight")})`} opacity="0.5" />
      <ellipse cx="100" cy="54" rx="13" ry="8" fill={`url(#${id("glassReflect")})`} opacity="0.4" clipPath={`url(#${id("topHalf100")})`} />
      {/* Bottom-left (light) */}
      <circle cx="60" cy="100" r="18" fill={`url(#${id("glassLight")})`} filter={`url(#${id("glassShadow")})`} />
      <circle cx="60" cy="100" r="17" fill={`url(#${id("rimLight")})`} opacity="0.5" />
      <ellipse cx="60" cy="94" rx="13" ry="8" fill={`url(#${id("glassReflect")})`} opacity="0.4" clipPath={`url(#${id("topHalf60b")})`} />
      {/* Bottom-right (dark) */}
      <circle cx="100" cy="100" r="18" fill={`url(#${id("glassDark")})`} filter={`url(#${id("glassShadow")})`} />
      <circle cx="100" cy="100" r="17" fill={`url(#${id("rimLight")})`} />
      <ellipse cx="100" cy="94" rx="13" ry="8" fill={`url(#${id("glassReflect")})`} clipPath={`url(#${id("topHalf100b")})`} />
    </svg>
  );
}
