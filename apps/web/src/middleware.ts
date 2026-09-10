import { NextResponse, type NextRequest } from 'next/server';

const PUBLIC_PATHS = ['/login'];
const ACCESS_COOKIE = 'access_token';
const REFRESH_COOKIE = 'refresh_token';

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

function isStaticAsset(pathname: string): boolean {
  return (
    pathname.startsWith('/_next/') ||
    pathname.startsWith('/api/') ||
    pathname === '/favicon.ico' ||
    /\.(svg|png|jpg|jpeg|webp|ico|css|js|map|woff2?)$/.test(pathname)
  );
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (isStaticAsset(pathname)) return NextResponse.next();

  const hasSession = request.cookies.has(ACCESS_COOKIE) || request.cookies.has(REFRESH_COOKIE);

  // Ruta pública: siempre accesible, incluso con sesión previa. El cliente
  // decide si redirigir al /inicio tras hidratar `useMe` (así evitamos un
  // bucle en el que el middleware expulsa al usuario a /inicio cuando el
  // backend aún no invalidó la cookie de la sesión anterior).
  if (isPublic(pathname)) {
    return NextResponse.next();
  }

  // Raíz: redirigir según sesión.
  if (pathname === '/') {
    const url = request.nextUrl.clone();
    url.pathname = hasSession ? '/inicio' : '/login';
    return NextResponse.redirect(url);
  }

  // Resto: requiere sesión.
  if (!hasSession) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    url.searchParams.set('redirect', pathname);
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    // Excluir assets y rutas internas.
    '/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)',
  ],
};
