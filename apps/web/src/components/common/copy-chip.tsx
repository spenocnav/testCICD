'use client';

import { Check, Copy } from 'lucide-react';
import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * Copia un texto al portapapeles sin depender de contexto seguro.
 *
 * `navigator.clipboard` **sólo existe en contexto seguro**, igual que
 * `crypto.randomUUID`. El portal se sirve por HTTP sobre una IP, así que ahí no
 * está y un botón que lo use directamente no hace nada. El respaldo con
 * `execCommand` está obsoleto pero es el único que funciona sin HTTPS, y es
 * exactamente el entorno donde hoy se usa esto.
 */
async function copiar(texto: string, anfitrion: HTMLElement | null): Promise<boolean> {
  const portapapeles = globalThis.navigator?.clipboard;
  if (portapapeles?.writeText) {
    try {
      await portapapeles.writeText(texto);
      return true;
    } catch {
      // Permiso denegado o contexto inseguro: se intenta el respaldo.
    }
  }
  const area = document.createElement('textarea');
  try {
    area.value = texto;
    // `readonly` evita el teclado en móvil; `display:none` no se puede copiar.
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.top = '0';
    area.style.left = '0';
    area.style.width = '1px';
    area.style.height = '1px';
    area.style.padding = '0';
    area.style.border = 'none';
    area.style.opacity = '0';
    // Un ancestro puede heredar `user-select: none` y entonces no hay selección
    // que copiar.
    area.style.userSelect = 'text';
    area.style.webkitUserSelect = 'text';

    // Va JUNTO al botón y no en `document.body`. Dentro de un modal de Radix el
    // resto del documento queda inerte y con el foco atrapado, así que un
    // textarea colgado del body no se puede seleccionar y `execCommand` no
    // copia nada. Ése era el motivo de que el respaldo fallara en silencio.
    const contenedor = anfitrion?.parentElement ?? anfitrion ?? document.body;
    contenedor.appendChild(area);

    area.focus({ preventScroll: true });
    area.select();
    // iOS ignora `select()` en algunos casos y sí respeta el rango explícito.
    area.setSelectionRange(0, texto.length);

    const copiado = document.execCommand('copy');
    // El foco vuelve al botón: dejarlo en un nodo que se va a borrar lo
    // devuelve al body y saca al usuario del modal.
    anfitrion?.focus({ preventScroll: true });
    return copiado;
  } catch {
    return false;
  } finally {
    area.remove();
  }
}

interface CopyChipProps {
  value: string;
  /** Qué se lee en el botón; por defecto, el propio valor. */
  label?: string;
  title?: string;
  className?: string;
  /**
   * Icono de copiar a la izquierda. Se apaga donde el chip acompaña a un texto
   * —la referencia junto al código de la falla—: ahí el icono compite con el
   * dato y la confirmación se da con el color, sin mover el texto de sitio.
   */
  showIcon?: boolean;
}

/** Valor monoespaciado que se copia al hacer clic, con confirmación visible. */
export function CopyChip({
  value,
  label,
  title,
  className,
  showIcon = true,
}: CopyChipProps) {
  const [estado, setEstado] = React.useState<'idle' | 'ok' | 'error'>('idle');
  const boton = React.useRef<HTMLButtonElement>(null);

  React.useEffect(() => {
    if (estado === 'idle') return;
    const t = setTimeout(() => setEstado('idle'), 1200);
    return () => clearTimeout(t);
  }, [estado]);

  return (
    <button
      ref={boton}
      type="button"
      onClick={async (event) => {
        event.stopPropagation();
        setEstado((await copiar(value, boton.current)) ? 'ok' : 'error');
      }}
      title={title ?? `Copiar ${value}`}
      aria-label={title ?? `Copiar ${value}`}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-xs',
        'transition-colors',
        estado === 'ok'
          ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
          : estado === 'error'
            ? 'border-destructive/40 bg-destructive/5 text-destructive'
            : 'hover:bg-muted/60',
        className,
      )}
    >
      {showIcon &&
        (estado === 'ok' ? (
          <Check className="h-3 w-3 shrink-0" />
        ) : (
          <Copy className="h-3 w-3 shrink-0" />
        ))}
      {/* Los dos textos ocupan la MISMA celda de la rejilla, así que la caja se
          dimensiona por el más ancho y el chip no encoge al decir "Copiado".
          Un salto de ancho en el encabezado movería la línea entera. */}
      <span className="grid">
        <span
          aria-hidden={estado !== 'idle'}
          className={cn(
            'col-start-1 row-start-1 transition-opacity',
            estado === 'idle' ? 'opacity-100' : 'opacity-0',
          )}
        >
          {label ?? value}
        </span>
        <span
          aria-hidden={estado === 'idle'}
          className={cn(
            'col-start-1 row-start-1 text-center transition-opacity',
            estado === 'idle' ? 'opacity-0' : 'opacity-100',
          )}
        >
          {estado === 'error' ? 'No se pudo copiar' : 'Copiado'}
        </span>
      </span>
    </button>
  );
}
