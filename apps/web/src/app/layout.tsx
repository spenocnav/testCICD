import type { Metadata, Viewport } from 'next';
import { Manrope, Raleway } from 'next/font/google';

import { Providers } from '@/components/providers';
import '@/styles/globals.css';

const manrope = Manrope({
  subsets: ['latin'],
  variable: '--font-manrope',
  display: 'swap',
});

const raleway = Raleway({
  subsets: ['latin'],
  variable: '--font-raleway',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'Portal Clientes',
  description: 'Sistema bidireccional de mantenimiento predictivo',
  icons: { icon: '/favicon.png' },
};

// `viewportFit: 'cover'` es lo que hace que `env(safe-area-inset-*)` valga algo
// en teléfonos con notch. Sin `maximumScale`: bloquear el zoom es una barrera
// de accesibilidad y no evita nada.
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  themeColor: '#f7f9f8',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es" className={`${manrope.variable} ${raleway.variable}`}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
