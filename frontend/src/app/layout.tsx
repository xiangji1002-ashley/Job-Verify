import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = { title: "JobVerify", description: "Real-time job posting verification" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-neutral-950 text-neutral-100 antialiased">{children}</body>
    </html>
  );
}
