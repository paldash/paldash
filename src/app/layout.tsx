import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "paldash",
  description: "Enhanced admin dashboard for Palworld dedicated servers — REST API monitoring, interactive map, save file viewer & editor",
  icons: { icon: "/favicon.ico" },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // suppressHydrationWarning: the inline script below sets data-theme on
    // <html> BEFORE React hydrates, so the server-rendered attribute set and
    // the client's can legitimately differ for light-theme users. Without the
    // early script, an effect would apply the theme after first paint and a
    // light-theme user would get a dark flash on every load.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html:
              "try{if(localStorage.getItem('palworld-dashboard-theme')==='light')" +
              "document.documentElement.dataset.theme='light'}catch(e){}",
          }}
        />
      </head>
      <body>
        {children}
      </body>
    </html>
  );
}
