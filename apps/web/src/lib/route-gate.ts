/**
 * Qué debe pintar el área de contenido para la ruta actual.
 *
 * Está separado del componente y sin dependencias de React para poder probarlo:
 * el defecto que motivó esta función era una sola rama mal resuelta dentro de
 * una condición encadenada, y una rama no se prueba mirándola.
 *
 * Esto NO es una frontera de autorización. El menú y esta compuerta son
 * experiencia de usuario; quien decide es FastAPI en cada petición.
 */

/** Permiso exigido por prefijo de ruta. El primero que casa gana, así que las
 *  rutas más específicas van antes que su prefijo (`/novedades/nuevo` antes que
 *  `/novedades`). */
export const ROUTE_PERMISSIONS: Array<[string, string]> = [
  ['/novedades/nuevo', 'novedades.edit'],
  ['/reportes', 'reportes.view'],
  ['/vehiculos', 'reportes.view'],
  // La bandeja de gestión va ANTES que `/navifault` y exige `edit`: es
  // seguimiento interno de quién atendió qué, no la lista de fallas que ve el
  // cliente. Con el orden invertido, `navifault.view` la abriría.
  ['/navifault/gestion', 'navifault.edit'],
  ['/navifault', 'navifault.view'],
  ['/novedades', 'novedades.view'],
  ['/mantenimiento', 'mantenimiento.view'],
  ['/gestion/calidad-datos', 'calidad_datos.view'],
  // 'admin' no es un permiso del catálogo: solo lo satisface el bypass del rol
  // admin. La auditoría de uso es exclusiva del administrador de plataforma.
  ['/gestion/uso', 'admin'],
  ['/gestion/usuarios', 'users.view'],
  ['/gestion/roles', 'roles.view'],
  ['/gestion/flotas', 'flotas.view'],
];

export function requiredPermission(pathname: string): string | null {
  return (
    ROUTE_PERMISSIONS.find(
      ([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`),
    )?.[1] ?? null
  );
}

export type RouteGateState = 'content' | 'loading' | 'denied';

/**
 * `loading` es el arreglo: antes, una ruta con permiso cuyo `/me` seguía en
 * vuelo devolvía `null`, es decir el área de contenido EN BLANCO, sin esqueleto
 * ni indicación de que algo estaba pasando. Con la barra lateral y la superior
 * ya pintadas, el resultado se lee como "el clic no hizo nada".
 *
 * El resto de las ramas conserva exactamente el comportamiento anterior:
 *
 * - una ruta sin permiso declarado pasa siempre, incluso mientras `/me` carga:
 *   no hay nada que esperar para decidir;
 * - si `/me` ya resolvió y aun así no hay sesión, se pinta el contenido. Ese
 *   caso lo ataja el middleware antes de llegar aquí; bloquearlo también acá
 *   solo cambiaría una redirección por una pantalla en blanco.
 */
export function routeGateState(args: {
  permission: string | null;
  isPending: boolean;
  hasSession: boolean;
  allowed: boolean;
}): RouteGateState {
  const { permission, isPending, hasSession, allowed } = args;
  if (!permission) return 'content';
  if (isPending) return 'loading';
  if (!hasSession) return 'content';
  return allowed ? 'content' : 'denied';
}

/** Ruta de aterrizaje por módulo cuando el usuario sólo tiene permisos de uno. */
export const MODULE_LANDING: Record<string, string> = {
  novedades: '/novedades',
  reportes: '/reportes',
  mantenimiento: '/mantenimiento/informe',
  navifault: '/navifault',
};

/**
 * A dónde ir tras iniciar sesión cuando la URL no pidió una ruta concreta.
 *
 * `/inicio` es un tablero pensado para quien ve reportes y mantenimiento. Un
 * usuario con permisos de UN solo módulo —el reportante de novedades, desde el
 * celular— aterriza directamente en ese módulo: `/inicio` le mostraría dos
 * tarjetas y un enlace, es decir, un clic más para llegar a lo único que puede
 * hacer. El administrador y quien tenga varios módulos siguen en `/inicio`.
 */
export function landingPathFor(args: { permissions: readonly string[]; isAdmin: boolean }): string {
  if (args.isAdmin) return '/inicio';
  const modules = new Set<string>();
  for (const code of args.permissions) {
    const [module, action] = code.split('.');
    if (module && (action === 'view' || action === 'edit')) modules.add(module);
  }
  if (modules.size !== 1) return '/inicio';
  const [only] = modules;
  return (only && MODULE_LANDING[only]) || '/inicio';
}

/**
 * Devuelve la ruta interna a la que volver tras el login, o `null` si el valor
 * de `?redirect` no es una ruta interna segura.
 *
 * Un patrón como `^/(?!/)` deja pasar `/\\evil.example`: satisface la regla y
 * el navegador lo normaliza como `//evil.example`, es decir, un host externo
 * (open redirect, SEC-019). Aquí la decisión no la toma una expresión regular
 * sino el parser de URL contra un origen fijo: si al resolverlo cambia de
 * origen, no es interno. Sólo se conservan pathname, query y hash.
 */
export function safeRedirectPath(raw: string | null | undefined): string | null {
  if (!raw) return null;
  // Barras invertidas y caracteres de control nunca forman una ruta interna
  // legítima y son justo lo que los parsers normalizan de forma sorprendente.
  if (/[\\\u0000-\u001f\u007f]/.test(raw)) return null;
  if (!raw.startsWith('/') || raw.startsWith('//')) return null;
  const base = 'http://portal.invalid';
  let parsed: URL;
  try {
    parsed = new URL(raw, base);
  } catch {
    return null;
  }
  if (parsed.origin !== base) return null;
  if (!parsed.pathname.startsWith('/') || parsed.pathname.startsWith('//')) return null;
  // Volver a /login después del login sería un bucle.
  if (parsed.pathname === '/login') return null;
  return `${parsed.pathname}${parsed.search}${parsed.hash}`;
}
