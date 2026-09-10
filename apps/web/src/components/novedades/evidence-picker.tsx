'use client';

import * as React from 'react';
import Image from 'next/image';
import { Camera, FileVideo, ImageIcon, Paperclip, Trash2 } from 'lucide-react';

import { Button, buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export const MAX_ATTACHMENTS = 5;
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
export const MAX_VIDEO_BYTES = 100 * 1024 * 1024;
export const ALLOWED_TYPES = new Set([
  'image/jpeg',
  'image/png',
  'image/webp',
  'video/mp4',
  'video/webm',
]);

// `accept` estricto a propósito: con `image/*` iOS entrega HEIC, que el backend
// rechaza; al pedir JPEG/PNG/WebP el sistema convierte la foto.
const PHOTO_ACCEPT = 'image/jpeg,image/png,image/webp';
const ALL_ACCEPT = `${PHOTO_ACCEPT},video/mp4,video/webm`;

interface AttachmentPreviewProps {
  file: File;
  onRemove: () => void;
  disabled?: boolean;
}

function AttachmentPreview({ file, onRemove, disabled }: AttachmentPreviewProps) {
  const url = React.useMemo(() => URL.createObjectURL(file), [file]);

  React.useEffect(() => () => URL.revokeObjectURL(url), [url]);

  return (
    <div className="group relative overflow-hidden rounded-md border">
      {file.type.startsWith('video/') ? (
        <div className="bg-muted flex aspect-square w-full items-center justify-center">
          <FileVideo className="text-muted-foreground h-10 w-10" />
        </div>
      ) : (
        <Image
          src={url}
          alt={file.name}
          width={400}
          height={400}
          unoptimized
          className="aspect-square w-full object-cover"
        />
      )}
      <Button
        type="button"
        variant="destructive"
        size="icon"
        className="absolute right-2 top-2 h-10 w-10 opacity-95"
        onClick={onRemove}
        disabled={disabled}
        aria-label={`Quitar ${file.name}`}
      >
        <Trash2 className="h-4 w-4" />
      </Button>
      <p className="truncate px-2 py-1 text-xs">{file.name}</p>
    </div>
  );
}

interface EvidencePickerProps {
  files: File[];
  disabled?: boolean;
  onAdd: (files: FileList | null) => void;
  onRemove: (index: number) => void;
}

/**
 * Selector de evidencias con dos entradas separadas:
 *
 * - "Tomar foto": `<input capture="environment">`, sólo imágenes y sin
 *   `multiple` (la cámara entrega una). Se oculta en `lg`, donde no hay cámara
 *   que abrir. Es una entrada APARTE porque `capture` en la única entrada
 *   quitaría el camino de la galería en varios Android.
 * - "Elegir archivos": la entrada de siempre, múltiple, fotos y videos.
 *
 * Ambas limpian su `value` tras seleccionar para que repetir la misma foto
 * vuelva a disparar `change`.
 */
export function EvidencePicker({ files, disabled = false, onAdd, onRemove }: EvidencePickerProps) {
  const full = files.length >= MAX_ATTACHMENTS;
  const inputsDisabled = disabled || full;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <label
          className={cn(
            buttonVariants({ variant: 'outline' }),
            'h-11 cursor-pointer lg:hidden',
            inputsDisabled && 'pointer-events-none opacity-50',
          )}
        >
          <Camera className="h-4 w-4" aria-hidden />
          Tomar foto
          <input
            type="file"
            accept={PHOTO_ACCEPT}
            capture="environment"
            className="sr-only"
            disabled={inputsDisabled}
            data-testid="evidence-camera-input"
            onChange={(event) => {
              onAdd(event.target.files);
              event.currentTarget.value = '';
            }}
          />
        </label>
        <label
          className={cn(
            buttonVariants({ variant: 'outline' }),
            'h-11 cursor-pointer lg:col-span-2',
            inputsDisabled && 'pointer-events-none opacity-50',
          )}
        >
          <Paperclip className="h-4 w-4" aria-hidden />
          Elegir archivos
          <input
            type="file"
            accept={ALL_ACCEPT}
            multiple
            className="sr-only"
            disabled={inputsDisabled}
            data-testid="evidence-file-input"
            onChange={(event) => {
              onAdd(event.target.files);
              event.currentTarget.value = '';
            }}
          />
        </label>
      </div>

      {files.length > 0 ? (
        <div className="grid grid-cols-2 gap-3">
          {files.map((file, index) => (
            <AttachmentPreview
              key={`${file.name}-${file.size}-${index}`}
              file={file}
              disabled={disabled}
              onRemove={() => onRemove(index)}
            />
          ))}
        </div>
      ) : (
        <div className="text-muted-foreground flex items-center gap-2 rounded-md border px-3 py-3 text-sm">
          <ImageIcon className="h-4 w-4" />
          Sin archivos adjuntos
        </div>
      )}
    </div>
  );
}
