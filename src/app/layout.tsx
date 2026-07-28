import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Palworld Server Dashboard",
  description: "Enhanced admin dashboard for Palworld dedicated servers — REST API monitoring, interactive map, save file viewer & editor",
  icons: { icon: "/favicon.ico" },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        {children}
      </body>
    </html>
  );
}
