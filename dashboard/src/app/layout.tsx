import type { Metadata, Viewport } from "next";
import { JetBrains_Mono } from "next/font/google";
import localFont from "next/font/local";
import "./globals.css";
import Providers from "../components/Providers";
import LayoutShell from "../components/LayoutShell";
import ServiceWorkerRegistrar from "../components/ServiceWorkerRegistrar";
import { cn } from "@/lib/utils";

// Brand serif — Feature Deck (headings / display).
const featureDeck = localFont({
  src: [
    { path: "./fonts/FeatureDeck-Light.woff2", weight: "300", style: "normal" },
    { path: "./fonts/FeatureDeck-LightItalic.woff2", weight: "300", style: "italic" },
    { path: "./fonts/FeatureDeck-Regular.woff2", weight: "400", style: "normal" },
    { path: "./fonts/FeatureDeck-RegularItalic.woff2", weight: "400", style: "italic" },
    { path: "./fonts/FeatureDeck-Medium.woff2", weight: "500", style: "normal" },
    { path: "./fonts/FeatureDeck-MediumItalic.woff2", weight: "500", style: "italic" },
    { path: "./fonts/FeatureDeck-Bold.woff2", weight: "700", style: "normal" },
    { path: "./fonts/FeatureDeck-BoldItalic.woff2", weight: "700", style: "italic" },
  ],
  variable: "--font-display",
});

// Brand sans — Halyard Display (body / UI; also drives --font-logo via globals.css).
const halyard = localFont({
  src: [
    { path: "./fonts/HalyardDisplay-Regular.woff2", weight: "400", style: "normal" },
    { path: "./fonts/HalyardDisplay-Italic.woff2", weight: "400", style: "italic" },
    { path: "./fonts/HalyardDisplay-Medium.woff2", weight: "500", style: "normal" },
  ],
  variable: "--font-body",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Loma | AI Agent Factory for Companies",
  description: "Self-hosted AI agent factory for company teams",
  icons: {
    icon: "/favicon.svg",
    apple: "/icons/apple-touch-icon.png",
  },
  // Installed-PWA behavior on iOS (Add to Home Screen).
  appleWebApp: {
    capable: true,
    title: "Loma",
    statusBarStyle: "default",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Extend under the iPhone notch/home indicator; safe-area insets are
  // handled with env(safe-area-inset-*) padding where needed.
  viewportFit: "cover",
  // Where supported, resize the layout viewport when the on-screen keyboard
  // opens (ViewportHeightSync's --app-h covers browsers that ignore this).
  interactiveWidget: "resizes-content",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F7F4EF" },
    { media: "(prefers-color-scheme: dark)", color: "#061B20" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning className={cn("font-sans", halyard.variable, featureDeck.variable, jetbrainsMono.variable)}>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){var t=localStorage.getItem('loma-theme')||'light';if(t==='system'){t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}document.documentElement.setAttribute('data-theme',t)})()`,
          }}
        />
      </head>
      <body
        className="antialiased min-h-screen"
      >
        <Providers>
          <ServiceWorkerRegistrar />
          <LayoutShell>{children}</LayoutShell>
        </Providers>
      </body>
    </html>
  );
}
