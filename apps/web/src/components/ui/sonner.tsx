'use client';

import { Toaster as SonnerToaster, type ToasterProps } from 'sonner';

export function Toaster(props: ToasterProps) {
  return (
    <SonnerToaster
      position="top-right"
      closeButton
      richColors
      toastOptions={{
        classNames: {
          toast: 'rounded-md border border-border bg-popover text-popover-foreground shadow-soft items-center',
          title: 'font-semibold',
          description: 'text-sm text-muted-foreground',
          closeButton:
            '!right-3 !top-1/2 !-translate-y-1/2 !left-auto',
        },
      }}
      {...props}
    />
  );
}
