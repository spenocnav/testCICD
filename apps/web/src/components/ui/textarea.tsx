import * as React from 'react';

import { cn } from '@/lib/utils';

const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        'border-input bg-background text-foreground flex min-h-28 w-full rounded-md border px-3.5 py-2 text-base shadow-sm transition-colors',
        'placeholder:text-muted-foreground',
        'focus-visible:ring-ring focus-visible:ring-offset-background focus-visible:border-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1',
        'disabled:cursor-not-allowed disabled:opacity-50',
        'md:text-sm',
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = 'Textarea';

export { Textarea };
