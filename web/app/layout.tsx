import type { ReactNode } from "react";
import Link from "next/link";

export const metadata = { title: "quiz-sift" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ja">
      <body style={{ fontFamily: "system-ui, sans-serif", maxWidth: 760, margin: "0 auto", padding: 16 }}>
        <header>
          <Link href="/">quiz-sift</Link>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
