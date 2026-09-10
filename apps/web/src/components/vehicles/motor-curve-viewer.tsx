'use client';

import * as React from 'react';
import { Download, ExternalLink, FileWarning, Loader2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { motorCurveFileUrl } from '@/lib/vehicles';
import type { MotorCurve } from '@/lib/types';

/**
 * El documento NO se pone directo como `src` del iframe.
 *
 * Si el proveedor no está disponible el API responde 503 con un JSON, y un
 * iframe pintaría ese JSON crudo dentro del visor. Descargándolo como blob el
 * fallo se puede leer y mostrar como mensaje, y el `src` sólo recibe un
 * documento que ya se sabe válido.
 *
 * Se usa `fetch` directo y no el cliente HTTP porque éste parsea JSON siempre.
 * El alcance no viaja en el header a propósito: el backend valida el documento
 * contra TODAS las flotas autorizadas del usuario, que es la frontera real.
 */
async function fetchCurveBlob(curveId: string, signal: AbortSignal): Promise<string> {
  const response = await fetch(motorCurveFileUrl(curveId), {
    credentials: 'include',
    signal,
  });
  if (!response.ok) {
    let detail = 'No se pudo obtener el documento.';
    try {
      const payload: unknown = await response.json();
      if (payload && typeof payload === 'object' && 'detail' in payload) {
        const raw = (payload as { detail: unknown }).detail;
        if (typeof raw === 'string' && raw.trim()) detail = raw;
      }
    } catch {
      // Cuerpo no-JSON: se queda el mensaje genérico.
    }
    throw new Error(detail);
  }
  return URL.createObjectURL(await response.blob());
}

export function MotorCurveViewer({
  curve,
  onOpenChange,
}: {
  curve: MotorCurve | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [objectUrl, setObjectUrl] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [isLoading, setIsLoading] = React.useState(false);
  const [attempt, setAttempt] = React.useState(0);
  const curveId = curve?.id ?? null;

  React.useEffect(() => {
    if (!curveId) {
      setObjectUrl(null);
      setError(null);
      setIsLoading(false);
      return;
    }
    const controller = new AbortController();
    let created: string | null = null;
    setIsLoading(true);
    setError(null);
    setObjectUrl(null);

    fetchCurveBlob(curveId, controller.signal)
      .then((url) => {
        created = url;
        setObjectUrl(url);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : 'No se pudo obtener el documento.');
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });

    return () => {
      controller.abort();
      // El object URL retiene el blob en memoria hasta revocarlo.
      if (created) URL.revokeObjectURL(created);
    };
  }, [curveId, attempt]);

  return (
    <Dialog open={Boolean(curve)} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[90vh] max-w-5xl flex-col gap-4">
        <DialogHeader>
          <DialogTitle>Curva de motor {curve?.motor_type}</DialogTitle>
          <DialogDescription>
            {curve?.original_filename ?? 'Documento de par y potencia'}
            {curve?.cpl ? ` · CPL ${curve.cpl}` : ''}
          </DialogDescription>
        </DialogHeader>

        <div className="bg-muted/40 relative min-h-0 flex-1 overflow-hidden rounded-md border">
          {isLoading && (
            <div className="text-muted-foreground absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm">
              <Loader2 className="h-6 w-6 animate-spin" aria-hidden />
              Cargando documento…
            </div>
          )}
          {!isLoading && error && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-6 text-center">
              <FileWarning className="text-muted-foreground h-8 w-8" aria-hidden />
              <p className="text-sm font-medium">{error}</p>
              <p className="text-muted-foreground max-w-md text-xs">
                El documento vive en Navi Vehículos y se copia al portal la primera vez que alguien
                lo abre. Si el origen no está disponible, se puede reintentar.
              </p>
              <Button type="button" variant="outline" onClick={() => setAttempt((n) => n + 1)}>
                Reintentar
              </Button>
            </div>
          )}
          {objectUrl && (
            // Sin `sandbox` a propósito: Chrome se niega a renderizar un PDF en
            // un iframe sandboxeado salvo que se le den `allow-scripts` y
            // `allow-same-origin` a la vez, que juntos equivalen a no
            // sandboxear. El documento es un blob que este código acaba de
            // descargar del propio API, no una URL de terceros.
            <iframe
              src={objectUrl}
              title={`Curva de motor ${curve?.motor_type ?? ''}`}
              className="h-full w-full"
            />
          )}
        </div>

        {objectUrl && (
          <div className="flex flex-wrap justify-end gap-2">
            <Button asChild variant="outline">
              <a href={objectUrl} target="_blank" rel="noreferrer">
                <ExternalLink className="mr-2 h-4 w-4" aria-hidden />
                Abrir en pestaña nueva
              </a>
            </Button>
            <Button asChild>
              <a
                href={objectUrl}
                download={curve?.original_filename ?? `curva-${curve?.motor_type ?? 'motor'}.pdf`}
              >
                <Download className="mr-2 h-4 w-4" aria-hidden />
                Descargar
              </a>
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
