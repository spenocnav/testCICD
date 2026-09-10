/** @type {import('next').NextConfig} */
const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
  ...(process.env.NODE_ENV === 'production'
    ? [
        {
          key: 'Strict-Transport-Security',
          value: 'max-age=31536000; includeSubDomains',
        },
      ]
    : []),
];

const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  async headers() {
    return [
      {
        source: '/:path*',
        headers: securityHeaders,
      },
    ];
  },
  async redirects() {
    // Disponibilidad y Programación se fusionaron en /mantenimiento/informe
    // (Disponibilidad es el primer tab). Se conservan los enlaces guardados.
    return [
      {
        source: '/mantenimiento/disponibilidad',
        destination: '/mantenimiento/informe',
        permanent: true,
      },
      {
        source: '/mantenimiento/programacion',
        destination: '/mantenimiento/informe',
        permanent: true,
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.API_PROXY_TARGET ?? 'http://localhost:8000'}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
