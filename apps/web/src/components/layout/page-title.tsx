import type { LucideIcon } from 'lucide-react';

import { cn } from '@/lib/utils';

interface PageTitleProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  iconClassName?: string;
  titleClassName?: string;
  descriptionClassName?: string;
  className?: string;
}

/** Título canónico de las páginas del menú: icono 20px, caja 40px y título 24px. */
export function PageTitle({
  icon: Icon,
  title,
  description,
  iconClassName,
  titleClassName,
  descriptionClassName,
  className,
}: PageTitleProps) {
  return (
    <div className={cn('flex items-center gap-3', className)}>
      {Icon && (
        <span
          className={cn(
            'bg-muted text-brand-gray flex h-10 w-10 shrink-0 items-center justify-center rounded-md',
            iconClassName,
          )}
        >
          <Icon className="h-5 w-5" aria-hidden />
        </span>
      )}
      <div>
        <h1
          className={cn(
            'font-heading text-2xl font-extrabold tracking-tight',
            titleClassName,
          )}
        >
          {title}
        </h1>
        {description && (
          <p className={cn('text-muted-foreground text-sm', descriptionClassName)}>
            {description}
          </p>
        )}
      </div>
    </div>
  );
}
