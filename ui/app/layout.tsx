import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "vira · engine console",
  description: "Local inspection console for the vira video ad engine",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-zinc-950 text-zinc-200 antialiased">
        <Nav />
        <main className="mx-auto w-full max-w-[1600px] px-4 py-4">{children}</main>
      </body>
    </html>
  );
}
