import type { MetadataRoute } from 'next';

/**
 * Web app manifest — the half of "PWA support" this project ships.
 *
 * What it buys: a home-screen shortcut gets the paw icon and the name
 * instead of a generic globe, and over HTTPS (a reverse proxy — common) the
 * dashboard installs and launches standalone, without browser chrome. On a
 * plain-HTTP LAN address the browser only offers a shortcut, because install
 * requires a secure context; the manifest is correct either way.
 *
 * **There is deliberately NO service worker**, and this is the place that
 * records why so it is not re-litigated. Offline, everything this app shows
 * is live server state — a cached shell could only present stale numbers
 * dressed as current ones, exactly the "we could not ask" vs "nothing there"
 * distinction refused everywhere else here. And a service worker's classic
 * failure mode is serving a stale UI after an upgrade, which would fight the
 * version-change notifications and cache headers built in #66. On a LAN,
 * asset caching buys nothing worth that risk.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'paldash',
    short_name: 'paldash',
    description:
      'Self-hosted dashboard for Palworld dedicated servers — live map, save viewer & editor, breeding planner, backups.',
    start_url: '/',
    display: 'standalone',
    background_color: '#101214',
    theme_color: '#101214',
    icons: [
      { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
      {
        src: '/icon-512-maskable.png',
        sizes: '512x512',
        type: 'image/png',
        purpose: 'maskable',
      },
    ],
  };
}
