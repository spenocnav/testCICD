import { describe, expect, it } from 'vitest';

import {
  MODULE_LANDING,
  ROUTE_PERMISSIONS,
  landingPathFor,
  requiredPermission,
  routeGateState,
  safeRedirectPath,
} from './route-gate';

describe('requiredPermission', () => {
  it('resuelve la ruta exacta y sus subrutas', () => {
    expect(requiredPermission('/reportes')).toBe('reportes.view');
    expect(requiredPermission('/gestion/flotas/abc-123')).toBe('flotas.view');
  });

  it('prefiere la coincidencia más específica sobre su prefijo', () => {
    // `/novedades/nuevo` exige `edit` y aparece ANTES que `/novedades` en la
    // tabla. Si alguien reordena la lista, esta prueba lo caza: la ruta de
    // creación quedaría accesible con permiso de solo lectura.
    expect(requiredPermission('/novedades/nuevo')).toBe('novedades.edit');
    expect(requiredPermission('/novedades')).toBe('novedades.view');
    expect(requiredPermission('/novedades/abc-123')).toBe('novedades.view');
  });

  it('la bandeja de gestión de Navifault exige edit, no view', () => {
    // Mismo defecto que `/novedades/nuevo`: si alguien reordena la tabla, la
    // bandeja quedaría accesible con permiso de solo lectura y un perfil
    // cliente vería quién atendió qué dentro de la operación.
    expect(requiredPermission('/navifault/gestion')).toBe('navifault.edit');
    expect(requiredPermission('/navifault')).toBe('navifault.view');
  });

  it('no confunde un prefijo con otra ruta que empiece igual', () => {
    expect(requiredPermission('/reportes-viejos')).toBeNull();
  });

  it('la auditoría de uso exige el pseudo-permiso admin', () => {
    // 'admin' no existe en el catálogo: solo el bypass del rol admin lo
    // satisface, así que ningún permiso delegable abre la pantalla.
    expect(requiredPermission('/gestion/uso')).toBe('admin');
  });

  it('devuelve null para una ruta sin permiso declarado', () => {
    expect(requiredPermission('/inicio')).toBeNull();
  });

  it('la tabla no tiene una ruta que tape a otra más específica', () => {
    ROUTE_PERMISSIONS.forEach(([prefix], index) => {
      const tapaPrevio = ROUTE_PERMISSIONS.slice(0, index).some(([anterior]) =>
        prefix.startsWith(`${anterior}/`),
      );
      expect(tapaPrevio, `${prefix} queda tapado por un prefijo anterior`).toBe(false);
    });
  });
});

describe('routeGateState', () => {
  const base = { permission: 'reportes.view', isPending: false, hasSession: true, allowed: true };

  it('muestra el esqueleto mientras /me está en vuelo', () => {
    // Es el arreglo: antes esta rama devolvía `null` y el área de contenido
    // quedaba en blanco con la barra lateral ya pintada, que se lee como que
    // el clic no pasó nada.
    expect(routeGateState({ ...base, isPending: true })).toBe('loading');
  });

  it('no espera a /me en una ruta sin permiso declarado', () => {
    expect(routeGateState({ ...base, permission: null, isPending: true })).toBe('content');
    expect(routeGateState({ ...base, permission: null, hasSession: false })).toBe('content');
  });

  it('pinta el contenido cuando el permiso está concedido', () => {
    expect(routeGateState(base)).toBe('content');
  });

  it('niega cuando /me resolvió y el permiso no está', () => {
    expect(routeGateState({ ...base, allowed: false })).toBe('denied');
  });

  it('sin sesión resuelta deja pasar: de eso se ocupa el middleware', () => {
    // Bloquear aquí cambiaría una redirección al login por una pantalla en
    // blanco. Esta rama conserva el comportamiento que ya tenía.
    expect(routeGateState({ ...base, hasSession: false, allowed: false })).toBe('content');
  });

  it('nunca devuelve un estado que no pinte nada', () => {
    for (const permission of ['reportes.view', null]) {
      for (const isPending of [true, false]) {
        for (const hasSession of [true, false]) {
          for (const allowed of [true, false]) {
            const state = routeGateState({ permission, isPending, hasSession, allowed });
            expect(['content', 'loading', 'denied']).toContain(state);
          }
        }
      }
    }
  });
});

describe('landingPathFor', () => {
  it('el administrador aterriza en el inicio aunque tenga un solo permiso', () => {
    expect(landingPathFor({ permissions: ['novedades.view'], isAdmin: true })).toBe('/inicio');
  });

  it('un usuario de un solo módulo aterriza en ese módulo', () => {
    // El rol `reportante_novedades` (conductores, celular) es el caso que
    // motiva esta función: `/inicio` sería un clic de más para llegar a lo
    // único que puede hacer.
    expect(landingPathFor({ permissions: ['novedades.view'], isAdmin: false })).toBe('/novedades');
    expect(
      landingPathFor({ permissions: ['novedades.view', 'novedades.edit'], isAdmin: false }),
    ).toBe('/novedades');
    expect(landingPathFor({ permissions: ['reportes.view'], isAdmin: false })).toBe('/reportes');
  });

  it('con varios módulos, o ninguno, se queda en el inicio', () => {
    expect(
      landingPathFor({ permissions: ['novedades.view', 'reportes.view'], isAdmin: false }),
    ).toBe('/inicio');
    expect(landingPathFor({ permissions: [], isAdmin: false })).toBe('/inicio');
  });

  it('un módulo sin aterrizaje propio va al inicio', () => {
    expect(landingPathFor({ permissions: ['users.view'], isAdmin: false })).toBe('/inicio');
  });

  it('cada aterrizaje apunta a una ruta con permiso declarado', () => {
    // Si alguien renombra una ruta y olvida esta tabla, el usuario aterrizaría
    // en un 404 en vez de en su módulo.
    for (const path of Object.values(MODULE_LANDING)) {
      expect(requiredPermission(path), `${path} no tiene permiso declarado`).not.toBeNull();
    }
  });
});

describe('safeRedirectPath', () => {
  it('acepta rutas internas con query y hash', () => {
    expect(safeRedirectPath('/reportes')).toBe('/reportes');
    expect(safeRedirectPath('/novedades/nuevo?placa=ABC123#top')).toBe(
      '/novedades/nuevo?placa=ABC123#top',
    );
  });

  it('rechaza lo que no empieza por una sola barra', () => {
    expect(safeRedirectPath(null)).toBeNull();
    expect(safeRedirectPath('')).toBeNull();
    expect(safeRedirectPath('reportes')).toBeNull();
    expect(safeRedirectPath('//evil.example/x')).toBeNull();
    expect(safeRedirectPath('https://evil.example/x')).toBeNull();
  });

  it('rechaza la barra invertida que el parser convierte en host externo (SEC-019)', () => {
    expect(safeRedirectPath('/\\evil.example')).toBeNull();
    expect(safeRedirectPath('/\\/evil.example')).toBeNull();
    expect(safeRedirectPath('/reportes\\..\\x')).toBeNull();
  });

  it('rechaza caracteres de control y esquemas disfrazados', () => {
    expect(safeRedirectPath('/reportes\u0000')).toBeNull();
    expect(safeRedirectPath('/\tjavascript:alert(1)')).toBeNull();
    expect(safeRedirectPath('/%5C%5Cevil.example')).toBe('/%5C%5Cevil.example');
  });

  it('no vuelve a /login', () => {
    expect(safeRedirectPath('/login')).toBeNull();
    expect(safeRedirectPath('/login?redirect=/x')).toBeNull();
  });
});
