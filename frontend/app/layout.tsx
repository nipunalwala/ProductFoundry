import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";

export const metadata: Metadata = {
  title: "ProductFoundry",
  description: "Turn a software product idea into an evidence-backed build plan.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <Link href="/" className="brand">
            ProductFoundry
          </Link>
          <nav>
            <Link href="/">Runs</Link>
            <Link href="/new">New run</Link>
          </nav>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
