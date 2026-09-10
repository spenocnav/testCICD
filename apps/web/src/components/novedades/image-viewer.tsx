'use client';

import { useEffect, useState } from 'react';
import Image from 'next/image';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Download, ZoomIn, ZoomOut } from 'lucide-react';

interface ImageViewerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  src: string;
  filename: string;
  isVideo?: boolean;
}

const controlClassName =
  'flex h-11 w-11 items-center justify-center rounded-full text-white/80 transition-colors hover:bg-white/20 hover:text-white';

/**
 * Visor de evidencias. En el teléfono ocupa toda la pantalla (`100dvh`); desde
 * `sm` vuelve al 95 % con bordes. Los controles miden 44 px y la barra respeta
 * la zona segura inferior.
 */
export function ImageViewer({
  open,
  onOpenChange,
  src,
  filename,
  isVideo = false,
}: ImageViewerProps) {
  const [scale, setScale] = useState(1);

  useEffect(() => {
    if (!open) setScale(1);
  }, [open]);

  // Sin origen no hay nada que mostrar: un `<Image src="">` hace que el
  // navegador vuelva a pedir la página entera.
  if (!src) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="h-[100dvh] max-h-[100dvh] w-screen max-w-none gap-0 overflow-hidden rounded-none border-0 bg-black/95 p-0 shadow-2xl sm:h-[95vh] sm:max-h-[95vh] sm:w-[95vw] sm:max-w-[95vw] sm:rounded-lg [&_[aria-label=Cerrar]:hover]:text-white [&_[aria-label=Cerrar]]:text-white/80">
        <DialogTitle className="sr-only">{filename}</DialogTitle>
        <div className="flex h-full w-full items-center justify-center overflow-auto p-2 sm:p-8">
          {isVideo ? (
            <video
              src={src}
              controls
              playsInline
              preload="metadata"
              className="max-h-full max-w-full rounded-md"
              style={{ maxHeight: 'calc(100dvh - 7rem)' }}
            />
          ) : (
            <Image
              src={src}
              alt={filename}
              width={1600}
              height={1200}
              unoptimized
              className="max-h-full max-w-full object-contain transition-transform duration-200 ease-in-out"
              style={{ transform: `scale(${scale})` }}
            />
          )}
        </div>

        <div className="absolute bottom-[calc(1rem+env(safe-area-inset-bottom))] left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-full bg-black/70 px-2 py-1">
          {isVideo ? (
            <a
              href={src}
              download={filename}
              className="flex h-11 items-center gap-1.5 rounded-full px-4 text-xs font-medium text-white/80 transition-colors hover:bg-white/20 hover:text-white"
              aria-label="Descargar video"
            >
              <Download className="h-4 w-4" />
              Descargar
            </a>
          ) : (
            <>
              <button
                type="button"
                onClick={() => setScale((s) => Math.max(0.5, s - 0.5))}
                className={controlClassName}
                aria-label="Reducir zoom"
              >
                <ZoomOut className="h-4 w-4" />
              </button>
              <span className="min-w-[3rem] select-none text-center text-xs font-medium tabular-nums text-white">
                {Math.round(scale * 100)}%
              </span>
              <button
                type="button"
                onClick={() => setScale((s) => Math.min(5, s + 0.5))}
                className={controlClassName}
                aria-label="Aumentar zoom"
              >
                <ZoomIn className="h-4 w-4" />
              </button>

              <span className="mx-1 select-none text-white/20">|</span>

              <a
                href={src}
                download={filename}
                className={controlClassName}
                aria-label="Descargar imagen"
              >
                <Download className="h-4 w-4" />
              </a>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
