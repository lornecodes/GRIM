import type { Metadata } from "next";
import { JetBrains_Mono } from "next/font/google";
import "./globals.css";

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
});

export const metadata: Metadata = {
  title: "GRIM",
  description: "General Recursive Intelligence Machine",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={jetbrains.variable}>
      <head>
        {/* Polyfill crypto.randomUUID for non-secure contexts (plain HTTP over LAN) */}
        <script
          dangerouslySetInnerHTML={{
            __html: `if(typeof crypto!=="undefined"&&typeof crypto.randomUUID!=="function"){crypto.randomUUID=function(){var b=new Uint8Array(16);crypto.getRandomValues(b);b[6]=(b[6]&0x0f)|0x40;b[8]=(b[8]&0x3f)|0x80;var h=Array.from(b,function(x){return x.toString(16).padStart(2,"0")}).join("");return h.slice(0,8)+"-"+h.slice(8,12)+"-"+h.slice(12,16)+"-"+h.slice(16,20)+"-"+h.slice(20)}}`,
          }}
        />
      </head>
      <body className="font-mono">{children}</body>
    </html>
  );
}
