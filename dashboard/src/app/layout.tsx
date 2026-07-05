import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Red_Hat_Display, Roboto, Instrument_Serif } from "next/font/google";
import "./globals.css";
import Providers from "../components/Providers";
import LayoutShell from "../components/LayoutShell";
import ServiceWorkerRegistrar from "../components/ServiceWorkerRegistrar";
import { cn } from "@/lib/utils";

const instrumentSerifHeading = Instrument_Serif({subsets:['latin'],weight:['400'],variable:'--font-heading'});

const roboto = Roboto({subsets:['latin'],variable:'--font-sans'});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
});

const redHatDisplay = Red_Hat_Display({
  variable: "--font-logo",
  subsets: ["latin"],
  weight: ["700", "800", "900"],
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
    { media: "(prefers-color-scheme: light)", color: "#F5F5F4" },
    { media: "(prefers-color-scheme: dark)", color: "#292524" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning className={cn("font-sans", roboto.variable, instrumentSerifHeading.variable, jetbrainsMono.variable, redHatDisplay.variable)}>
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
