'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { LogOut, Menu, User as UserIcon } from 'lucide-react';
import { toast } from 'sonner';

import { FleetFilter } from '@/components/fleet/fleet-filter';
import { MOBILE_NAV_ID } from '@/components/layout/mobile-nav';
import { ProfileDialog } from '@/components/layout/profile-dialog';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import { clearFleetScope } from '@/lib/api-client';
import { useMe, logoutRequest } from '@/lib/auth';

function getInitials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p.charAt(0).toUpperCase())
    .join('');
}

interface AppTopbarProps {
  /** Abre el menú móvil. Sin él no se pinta el botón hamburguesa. */
  onOpenMenu?: () => void;
  menuOpen?: boolean;
}

export function AppTopbar({ onOpenMenu, menuOpen = false }: AppTopbarProps) {
  const router = useRouter();
  const qc = useQueryClient();
  const { data, isLoading } = useMe();
  const [profileOpen, setProfileOpen] = React.useState(false);

  const onLogout = async () => {
    // El logout debe dejar al cliente limpio INDEPENDIENTEMENTE del backend:
    // si la API falla (red, 401, 5xx) ya no queremos seguir mostrando datos
    // del usuario previo en ninguna pantalla.
    try {
      await logoutRequest();
    } catch {
      // ignorar: la limpieza local es la fuente de verdad para el aislamiento.
    }
    qc.clear();
    clearFleetScope();
    toast.success('Sesión cerrada');
    router.replace('/login');
  };

  return (
    <header className="border-border bg-background/85 sticky top-0 z-40 flex h-16 items-center justify-between gap-2 border-b px-3 pt-[env(safe-area-inset-top)] backdrop-blur lg:px-6">
      <div className="flex min-w-0 flex-1 items-center gap-2">
        {onOpenMenu && (
          <Button
            variant="ghost"
            size="icon"
            className="h-11 w-11 shrink-0 lg:hidden"
            aria-label="Abrir menú"
            aria-controls={MOBILE_NAV_ID}
            aria-expanded={menuOpen}
            onClick={onOpenMenu}
          >
            <Menu className="!size-5" />
          </Button>
        )}
        {/* El filtro de flota se queda en la barra superior también en móvil:
            es el alcance de todo lo que se ve, y esconderlo en el menú haría
            que la lista quedara filtrada sin que se note. */}
        <div className="min-w-0 flex-1">
          <FleetFilter />
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        {isLoading ? (
          <Skeleton className="rounded-pill h-9 w-9" />
        ) : data?.user ? (
          <>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  className="rounded-pill hover:bg-muted focus-visible:ring-ring focus-visible:ring-offset-background flex items-center gap-3 px-2 py-1 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                >
                  <Avatar>
                    <AvatarFallback>{getInitials(data.user.full_name)}</AvatarFallback>
                  </Avatar>
                  <div className="hidden text-left md:block">
                    <p className="text-sm font-semibold leading-none">{data.user.full_name}</p>
                    <p className="text-muted-foreground text-xs">{data.user.email}</p>
                  </div>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuLabel>Mi cuenta</DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => setProfileOpen(true)}>
                  <UserIcon />
                  <span>Perfil</span>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={onLogout} className="text-destructive">
                  <LogOut />
                  <span>Cerrar sesión</span>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <ProfileDialog open={profileOpen} onOpenChange={setProfileOpen} data={data} />
          </>
        ) : (
          <Button asChild variant="outline" size="sm">
            <a href="/login">Iniciar sesión</a>
          </Button>
        )}
      </div>
    </header>
  );
}
