import { describe, expect, it } from 'vitest';

import { externalResolution, lifecycle, resolutionSummary } from './novedad-status';

const base = {
  cloudfleet_status: 'sent' as const,
  external_is_done: null,
  external_done_at: null,
  external_work_order_number: null,
};

describe('externalResolution', () => {
  it('no dice nada mientras el backend no haya verificado la issue', () => {
    // `null` no es "abierta": es "aún no se consultó". Pintar "Abierta" aquí
    // le diría al conductor algo que nadie comprobó.
    expect(externalResolution(base)).toBeNull();
  });

  it('resuelta, con orden de trabajo y fecha cuando vienen', () => {
    const resolution = externalResolution({
      ...base,
      external_is_done: true,
      external_done_at: '2026-08-21T20:00:00Z',
      external_work_order_number: 5619,
    });
    expect(resolution).toEqual({
      done: true,
      label: 'Resuelta en CloudFleet',
      workOrder: 5619,
      doneAt: '2026-08-21T20:00:00Z',
    });
    expect(resolutionSummary(resolution)).toMatch(/^Resuelta · OT 5619 · .*2026$/);
  });

  it('resuelta sin OT ni fecha se resume igual, sin partes vacías', () => {
    const resolution = externalResolution({ ...base, external_is_done: true });
    expect(resolutionSummary(resolution)).toBe('Resuelta');
  });

  it('abierta sólo si el envío llegó a CloudFleet', () => {
    expect(externalResolution({ ...base, external_is_done: false })).toEqual({
      done: false,
      label: 'Abierta en CloudFleet',
    });
    // Un envío fallido con `false` residual no puede describirse como abierto.
    expect(
      externalResolution({ ...base, cloudfleet_status: 'failed', external_is_done: false }),
    ).toBeNull();
  });

  it('el resumen de una novedad sin resolución es null', () => {
    expect(resolutionSummary(null)).toBeNull();
  });
});

describe('lifecycle', () => {
  const sent = { ...base, cloudfleet_issue_number: 698, external_deleted_at: null };

  it('el envío fallido o pendiente manda sobre cualquier otra cosa', () => {
    expect(lifecycle({ ...sent, cloudfleet_status: 'failed' }).code).toBe('failed');
    expect(lifecycle({ ...sent, cloudfleet_status: 'pending' }).code).toBe('pending');
  });

  it('enviada sin verificar o verificada abierta se lee "Abierta" con su número', () => {
    // Para quien reporta, sin confirmación de cierre sigue pendiente de atención.
    expect(lifecycle(sent)).toMatchObject({ code: 'open', label: 'Abierta', detail: '#698' });
    expect(lifecycle({ ...sent, external_is_done: false }).code).toBe('open');
    expect(lifecycle({ ...sent, cloudfleet_issue_number: null }).detail).toBeNull();
  });

  it('resuelta lleva OT y fecha como detalle, sin repetir la palabra', () => {
    const meta = lifecycle({
      ...sent,
      external_is_done: true,
      external_done_at: '2026-08-21T20:00:00Z',
      external_work_order_number: 5619,
    });
    expect(meta.code).toBe('resolved');
    expect(meta.label).toBe('Resuelta');
    expect(meta.detail).toMatch(/^OT 5619 · .*2026$/);
    expect(lifecycle({ ...sent, external_is_done: true }).detail).toBeNull();
  });

  it('borrada en CloudFleet gana sobre resuelta, y nunca se lee "Abierta"', () => {
    // Lo que se corrige: la baja se replicaba en el backend y la lista seguía
    // llamándola "Abierta", mandando a perseguir algo que ya no existe.
    expect(
      lifecycle({ ...sent, external_deleted_at: '2026-09-06T03:03:00Z' }),
    ).toMatchObject({ code: 'deleted', label: 'Borrada' });

    // Una issue que llegó a marcarse hecha antes de desaparecer NO es
    // "Resuelta": nadie la atendió. Fija el orden de las dos ramas.
    expect(
      lifecycle({
        ...sent,
        external_is_done: true,
        external_work_order_number: 5733,
        external_deleted_at: '2026-09-06T03:03:00Z',
      }).code,
    ).toBe('deleted');

    // Pero el envío sigue mandando: lo que nunca llegó no pudo borrarse allá.
    expect(
      lifecycle({
        ...sent,
        cloudfleet_status: 'failed',
        external_deleted_at: '2026-09-06T03:03:00Z',
      }).code,
    ).toBe('failed');
  });
});
