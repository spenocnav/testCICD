import { afterEach, describe, expect, it, vi } from 'vitest';

import { cn, newIdempotencyKey } from './utils';

describe('cn', () => {
  it('combina clases condicionales', () => {
    expect(cn('base', false && 'hidden', true && 'visible')).toBe('base visible');
  });

  it('resuelve conflictos de Tailwind con la última clase', () => {
    expect(cn('px-2 text-sm', 'px-4 text-lg')).toBe('px-4 text-lg');
  });

  it('acepta arreglos y objetos de clsx', () => {
    expect(cn(['rounded', { block: false, shadow: true }])).toBe('rounded shadow');
  });
});

describe('newIdempotencyKey', () => {
  const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('usa randomUUID cuando el contexto es seguro', () => {
    const randomUUID = vi.fn(() => '11111111-2222-4333-8444-555555555555');
    vi.stubGlobal('crypto', { randomUUID });
    expect(newIdempotencyKey()).toBe('11111111-2222-4333-8444-555555555555');
    expect(randomUUID).toHaveBeenCalledOnce();
  });

  it('sin randomUUID —HTTP, contexto no seguro— sigue produciendo un UUID v4', () => {
    // Es el caso real: el navegador expone `crypto` pero no `randomUUID`.
    vi.stubGlobal('crypto', {
      getRandomValues: (buffer: Uint8Array) => {
        for (let i = 0; i < buffer.length; i++) buffer[i] = i * 7 + 3;
        return buffer;
      },
    });
    expect(newIdempotencyKey()).toMatch(UUID_V4);
  });

  it('sin crypto en absoluto cae a Math.random y no lanza', () => {
    vi.stubGlobal('crypto', undefined);
    expect(newIdempotencyKey()).toMatch(UUID_V4);
  });

  it('no repite claves entre llamadas', () => {
    const claves = new Set(Array.from({ length: 50 }, () => newIdempotencyKey()));
    expect(claves.size).toBe(50);
  });
});
