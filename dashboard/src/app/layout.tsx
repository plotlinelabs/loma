import type { Metadata } from "next";
import { JetBrains_Mono, Red_Hat_Display, Roboto, Instrument_Serif } from "next/font/google";
import "./globals.css";
import Providers from "../components/Providers";
import LayoutShell from "../components/LayoutShell";
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
  },
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
          <LayoutShell>{children}</LayoutShell>
        </Providers>
      </body>
    </html>
  );
}
