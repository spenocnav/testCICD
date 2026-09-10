import { NovedadForm } from '@/components/novedades/novedad-form';
import { Can } from '@/components/auth/can';

interface NuevaNovedadPageProps {
  searchParams?: Promise<{
    vehicleId?: string;
  }>;
}

export default async function NuevaNovedadPage({ searchParams }: NuevaNovedadPageProps) {
  const params = await searchParams;
  return (
    <Can
      permission="novedades.edit"
      fallback={
        <section className="border-destructive/30 bg-destructive/5 rounded-lg border p-8 text-center">
          <h1 className="text-lg font-bold">Sin permiso para registrar novedades</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Solicita novedades.edit a un administrador.
          </p>
        </section>
      }
    >
      <NovedadForm initialVehicleId={params?.vehicleId ?? ''} />
    </Can>
  );
}
