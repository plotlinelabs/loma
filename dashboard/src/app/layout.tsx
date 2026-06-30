import type { Metadata } from "next";
import { Figtree, JetBrains_Mono, Outfit, Red_Hat_Display } from "next/font/google";
import "./globals.css";
import Providers from "../components/Providers";
import LayoutShell from "../components/LayoutShell";

const figtree = Figtree({
  variable: "--font-figtree",
  subsets: ["latin"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
});

const outfit = Outfit({
  variable: "--font-outfit",
  subsets: ["latin"],
  weight: ["600", "700", "800"],
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
    <html lang="en" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){var t=localStorage.getItem('loma-theme')||'light';if(t==='system'){t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}document.documentElement.setAttribute('data-theme',t)})()`,
          }}
        />
      </head>
      <body
        className={`${figtree.variable} ${jetbrainsMono.variable} ${outfit.variable} ${redHatDisplay.variable} antialiased bg-gray-50 text-gray-900 min-h-screen font-[family-name:var(--font-figtree)]`}
      >
        <Providers>
          <LayoutShell>{children}</LayoutShell>
        </Providers>
      </body>
    </html>
  );
}
