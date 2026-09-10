import { Construction, type LucideIcon } from 'lucide-react';

interface ComingSoonProps {
  /** Título del módulo (ej. "Reportes"). */
  title: string;
  /** Ícono del módulo; por defecto un ícono de construcción. */
  icon?: LucideIcon;
  /** Texto descriptivo opcional. */
  description?: string;
}

/**
 * Placeholder reutilizable para módulos aún sin implementar. Mantiene el
 * estilo de la tarjeta de "Página en construcción" del inicio.
 */
export function ComingSoon({ title, icon: Icon = Construction, description }: ComingSoonProps) {
  return (
    <section className="flex min-h-[60vh] items-center justify-center">
      <div className="border-border bg-white shadow-soft max-w-md rounded-lg border p-10 text-center">
        <div className="bg-accent-cream text-accent-yellow mx-auto mb-4 flex h-10 w-10 items-center justify-center rounded-md">
          <Icon className="h-5 w-5" aria-hidden />
        </div>
        <span className="rounded-pill bg-accent-cream text-accent-yellow mb-3 inline-block px-3 py-1 text-[11px] font-semibold uppercase tracking-wider">
          Próximamente
        </span>
        <h1 className="font-heading mb-2 text-2xl font-extrabold tracking-tight">{title}</h1>
        <p className="text-muted-foreground text-sm">
          {description ?? 'Este módulo está en desarrollo. Estará disponible muy pronto.'}
        </p>
      </div>
    </section>
  );
}
