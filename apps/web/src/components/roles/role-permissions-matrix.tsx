'use client';

import * as React from 'react';
import { Loader2, Save } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { extractErrorMessage } from '@/lib/api-client';
import { useSetRolePermissions } from '@/lib/roles';
import type { ModuleRead, RoleDetail } from '@/lib/types';

interface Access {
  view: boolean;
  edit: boolean;
}

type AccessMap = Record<string, Access>;

interface PermissionSection {
  title: string;
  description: string;
  items: PermissionSectionItem[];
}

interface PermissionSectionItem {
  code: string;
  label?: string;
  description?: string;
  /**
   * El módulo no tiene permiso de edición en el catálogo. Sin esto la matriz
   * pinta la casilla igual, y guardar manda un código que no existe: el backend
   * responde 422 y **se pierde el guardado completo**, incluidos los cambios
   * legítimos de las otras filas.
   */
  viewOnly?: boolean;
}

interface GroupedModules {
  section: PermissionSection;
  modules: DisplayModule[];
}

interface DisplayModule extends ModuleRead {
  displayName: string;
  displayDescription: string | null;
  viewOnly: boolean;
}

const PERMISSION_SECTIONS: PermissionSection[] = [
  {
    title: 'Operación',
    description: 'Rutas operativas disponibles en el sidebar.',
    items: [
      {
        code: 'reportes',
        label: 'Reportes y vehículos',
        description: 'Acceso a /reportes y /vehiculos.',
      },
      { code: 'navifault' },
      {
        code: 'navifault_management',
        label: 'Confirmar gestión de fallas',
        description:
          'Marque Editar para permitir confirmar una falla contra la orden que la resolvió en CloudFleet, y cerrar con una nota las fallas del equipo telemático. También requiere Editar Navifault.',
      },
      {
        code: 'navifault_corpus',
        label: 'Documentación técnica Cummins',
        description:
          'Documento original, resumen oficial y candidatas de resolución. Es contenido de Navitrans: un perfil de cliente ve su falla y la comunicación redactada para él, no el manual.',
        viewOnly: true,
      },
    ],
  },
  {
    title: 'Mantenimiento',
    description: 'Rutas de novedades y mantenimiento disponibles en el sidebar.',
    items: [
      { code: 'novedades' },
      {
        code: 'mantenimiento',
        description: 'Informe Mtto: disponibilidad, confiabilidad y órdenes.',
      },
    ],
  },
  {
    title: 'Gestión',
    description: 'Administración del portal.',
    items: [{ code: 'calidad_datos' }, { code: 'users' }, { code: 'roles' }, { code: 'flotas' }],
  },
];

function buildAccessMap(role: RoleDetail, modules: ModuleRead[]): AccessMap {
  const codes = new Set(role.permissions.map((p) => p.code));
  const map: AccessMap = {};
  for (const m of modules) {
    const edit = codes.has(`${m.code}.edit`);
    map[m.code] = { view: edit || codes.has(`${m.code}.view`), edit };
  }
  return map;
}

function toPermissionCodes(map: AccessMap): string[] {
  const codes: string[] = [];
  for (const [moduleCode, access] of Object.entries(map)) {
    if (access.view) codes.push(`${moduleCode}.view`);
    if (access.edit) codes.push(`${moduleCode}.edit`);
  }
  return codes;
}

function groupModules(modules: ModuleRead[]): GroupedModules[] {
  const byCode = new Map(modules.map((item) => [item.code, item]));
  const groups: GroupedModules[] = [];

  for (const section of PERMISSION_SECTIONS) {
    const sectionModules = section.items.flatMap((item) => {
      const moduleItem = byCode.get(item.code);
      if (!moduleItem) return [];
      return [
        {
          ...moduleItem,
          displayName: item.label ?? moduleItem.name,
          displayDescription: item.description ?? moduleItem.description,
          viewOnly: item.viewOnly ?? false,
        },
      ];
    });
    if (sectionModules.length > 0) {
      groups.push({ section, modules: sectionModules });
    }
  }

  return groups;
}

interface Props {
  role: RoleDetail;
  modules: ModuleRead[];
  /** Si false, la matriz es de solo lectura. */
  editable: boolean;
}

export function RolePermissionsMatrix({ role, modules, editable }: Props) {
  const groupedModules = React.useMemo(() => groupModules(modules), [modules]);
  const visibleModules = React.useMemo(
    () => groupedModules.flatMap((group) => group.modules),
    [groupedModules],
  );
  const initial = React.useMemo(() => buildAccessMap(role, visibleModules), [role, visibleModules]);
  const [draft, setDraft] = React.useState<AccessMap>(initial);
  const setPermissions = useSetRolePermissions();

  // Resetear el borrador al cambiar de rol o recargar permisos.
  React.useEffect(() => {
    setDraft(initial);
  }, [initial]);

  const dirty = React.useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(initial),
    [draft, initial],
  );

  const toggle = (moduleCode: string, action: 'view' | 'edit', checked: boolean) => {
    setDraft((prev) => {
      const current = prev[moduleCode] ?? { view: false, edit: false };
      let next: Access;
      if (action === 'view') {
        // Quitar "ver" también quita "editar".
        next = { view: checked, edit: checked ? current.edit : false };
      } else {
        // Marcar "editar" implica "ver".
        next = { edit: checked, view: checked ? true : current.view };
      }
      return { ...prev, [moduleCode]: next };
    });
  };

  const onSave = async () => {
    try {
      await setPermissions.mutateAsync({
        id: role.id,
        permission_codes: toPermissionCodes(draft),
      });
      toast.success(`Permisos de "${role.name}" actualizados`);
    } catch (err) {
      toast.error(extractErrorMessage(err, 'No se pudieron guardar los permisos'));
    }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-md border bg-white">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-1/2">Módulo</TableHead>
              <TableHead className="text-center">Ver</TableHead>
              <TableHead className="text-center">Editar</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {groupedModules.map(({ section, modules: sectionModules }) => (
              <React.Fragment key={section.title}>
                <TableRow className="border-brand-clear/30 bg-[var(--sidebar-item-active-bg)] hover:bg-[var(--sidebar-item-active-bg)]">
                  <TableCell colSpan={3} className="py-4">
                    <div className="flex items-center gap-3">
                      <span className="bg-brand-red h-8 w-1 rounded-full" aria-hidden />
                      <div>
                        <p className="text-brand-red text-xs font-extrabold uppercase tracking-wide">
                          {section.title}
                        </p>
                        <p className="text-muted-foreground mt-0.5 text-xs">
                          {section.description}
                        </p>
                      </div>
                    </div>
                  </TableCell>
                </TableRow>
                {sectionModules.map((m) => {
                  const access = draft[m.code] ?? { view: false, edit: false };
                  return (
                    <TableRow key={m.code}>
                      <TableCell>
                        <p className="text-foreground text-sm font-semibold">{m.displayName}</p>
                        {m.displayDescription && (
                          <p className="text-muted-foreground text-xs">{m.displayDescription}</p>
                        )}
                      </TableCell>
                      <TableCell className="text-center">
                        <Checkbox
                          checked={access.view}
                          disabled={!editable}
                          aria-label={`Ver ${m.name}`}
                          onCheckedChange={(v) => toggle(m.code, 'view', v === true)}
                        />
                      </TableCell>
                      <TableCell className="text-center">
                        {m.viewOnly ? (
                          <span
                            className="text-muted-foreground text-xs"
                            title="Este módulo es sólo de consulta: no existe permiso de edición."
                            aria-label={`${m.name} no tiene permiso de edición`}
                          >
                            —
                          </span>
                        ) : (
                          <Checkbox
                            checked={access.edit}
                            disabled={!editable}
                            aria-label={`Editar ${m.name}`}
                            onCheckedChange={(v) => toggle(m.code, 'edit', v === true)}
                          />
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </React.Fragment>
            ))}
          </TableBody>
        </Table>
      </div>

      {editable && (
        <div className="flex items-center justify-end gap-3">
          {dirty && <span className="text-muted-foreground text-xs">Cambios sin guardar</span>}
          <Button onClick={onSave} disabled={!dirty || setPermissions.isPending}>
            {setPermissions.isPending ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : (
              <Save aria-hidden />
            )}
            Guardar permisos
          </Button>
        </div>
      )}
    </div>
  );
}
