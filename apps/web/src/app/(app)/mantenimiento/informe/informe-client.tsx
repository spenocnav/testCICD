'use client';

import { ClipboardList } from 'lucide-react';
import * as React from 'react';

import { PageTitle } from '@/components/layout/page-title';
import { useCanEdit } from '@/lib/auth';
import { cn } from '@/lib/utils';
import {
  MttoToolbar,
  monthsAgo,
  rangeForMonth,
  today,
  useMttoFilters,
  type MttoToolbarState,
} from '@/app/(app)/mantenimiento/shared';
import { DisponibilidadTab } from './disponibilidad-tab';
import {
  ConfiabilidadTab,
  HistoricoTab,
  OrdenesTab,
  PreventivoTab,
  ProgramacionKpis,
  ProgramacionRankings,
  ProximasTab,
  TiemposTallerTab,
} from './programacion-tabs';

type Tab =
  | 'disponibilidad'
  | 'preventivo'
  | 'proximas'
  | 'historico'
  | 'confiabilidad'
  | 'ordenes'
  | 'tiempos-taller';
const TABS: { id: Tab; label: string }[] = [
  { id: 'disponibilidad', label: 'Disponibilidad' },
  { id: 'preventivo', label: 'Preventivo' },
  { id: 'proximas', label: 'Próximas programaciones' },
  { id: 'historico', label: 'Histórico' },
  { id: 'confiabilidad', label: 'Confiabilidad' },
  { id: 'ordenes', label: 'Órdenes' },
  { id: 'tiempos-taller', label: 'Tiempos en taller' },
];

function isTab(value: string | null, tabs: readonly { id: Tab }[]): value is Tab {
  return tabs.some((tab) => tab.id === value);
}

/**
 * Informe Mtto: una sola pantalla con disponibilidad (tab por defecto) y el
 * detalle de programación/confiabilidad/órdenes y tiempos en taller. Cada tab
 * monta sus propios hooks, así una visita solo dispara las consultas del tab visible.
 */
export function InformeMttoClient() {
  const canEditMaintenance = useCanEdit()('mantenimiento');
  const visibleTabs = React.useMemo(
    () => (canEditMaintenance ? TABS : TABS.filter((tab) => tab.id !== 'tiempos-taller')),
    [canEditMaintenance],
  );
  const [state, setState] = React.useState<MttoToolbarState>({
    placas: [],
    groupIds: [],
    dateFrom: monthsAgo(4),
    dateTo: today(),
  });
  const [tab, setTab] = React.useState<Tab>('disponibilidad');
  const [selectedMonth, setSelectedMonth] = React.useState<string | null>(null);

  React.useEffect(() => {
    const syncTabFromUrl = () => {
      const value = new URLSearchParams(window.location.search).get('tab');
      if (isTab(value, visibleTabs)) setTab(value);
    };
    syncTabFromUrl();
    window.addEventListener('popstate', syncTabFromUrl);
    return () => window.removeEventListener('popstate', syncTabFromUrl);
  }, [visibleTabs]);

  React.useEffect(() => {
    if (!visibleTabs.some((visibleTab) => visibleTab.id === tab)) {
      setTab('disponibilidad');
    }
  }, [tab, visibleTabs]);

  const selectTab = (nextTab: Tab) => {
    setTab(nextTab);
    const url = new URL(window.location.href);
    url.searchParams.set('tab', nextTab);
    window.history.pushState({}, '', url);
  };
  const filters = useMttoFilters(state);
  const activeFilters = React.useMemo(
    () => (selectedMonth ? rangeForMonth(selectedMonth, filters) : filters),
    [filters, selectedMonth],
  );
  const selectedMonthLabel = selectedMonth
    ? new Intl.DateTimeFormat('es-CO', { month: 'long', year: 'numeric' }).format(
        new Date(`${selectedMonth}-01T00:00:00`),
      )
    : null;

  const changeToolbar = (next: MttoToolbarState) => {
    setState(next);
    setSelectedMonth(null);
  };

  return (
    <div className="space-y-4">
      <header className="flex items-center gap-3">
        <PageTitle
          icon={ClipboardList}
          title="Informe Mtto"
          description="Disponibilidad, cumplimiento preventivo, confiabilidad, tiempos en taller y órdenes de trabajo."
          iconClassName="bg-accent-blue/10 text-accent-blue"
        />
      </header>

      <MttoToolbar state={state} onChange={changeToolbar} />

      <div className="border-b">
        <nav className="flex gap-4">
          {visibleTabs.map((t) => (
            <button
              key={t.id}
              onClick={() => selectTab(t.id)}
              className={cn(
                'border-b-2 px-1 pb-2 text-sm font-medium',
                tab === t.id
                  ? 'border-brand-red text-foreground'
                  : 'text-muted-foreground border-transparent',
              )}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </div>

      {selectedMonthLabel && (
        <div
          role="status"
          className="border-accent-blue/30 bg-accent-blue/5 text-accent-blue flex items-center gap-2 rounded-md border px-3 py-2 text-xs"
        >
          <span className="font-semibold">Filtro cruzado:</span>
          <span>Mes: {selectedMonthLabel}</span>
          <button
            type="button"
            onClick={() => setSelectedMonth(null)}
            className="ml-auto font-semibold underline-offset-2 hover:underline"
          >
            Limpiar
          </button>
        </div>
      )}

      {tab === 'disponibilidad' ? (
        <DisponibilidadTab
          filters={filters}
          activeFilters={activeFilters}
          selectedMonth={selectedMonth}
          onMonthChange={setSelectedMonth}
          onSelectGroup={(groupId) => changeToolbar({ ...state, groupIds: [groupId] })}
        />
      ) : tab === 'tiempos-taller' ? (
        <TiemposTallerTab
          filters={filters}
          activeFilters={activeFilters}
          selectedMonth={selectedMonth}
          onMonthChange={setSelectedMonth}
        />
      ) : (
        <>
          <ProgramacionKpis filters={activeFilters} />
          {tab === 'preventivo' && (
            <PreventivoTab
              filters={filters}
              activeFilters={activeFilters}
              selectedMonth={selectedMonth}
              onMonthChange={setSelectedMonth}
            />
          )}
          {tab === 'proximas' && <ProximasTab filters={activeFilters} />}
          {tab === 'historico' && <HistoricoTab filters={activeFilters} />}
          {tab === 'confiabilidad' && (
            <ConfiabilidadTab
              filters={filters}
              selectedMonth={selectedMonth}
              onMonthChange={setSelectedMonth}
            />
          )}
          {tab === 'ordenes' && (
            <OrdenesTab
              filters={filters}
              activeFilters={activeFilters}
              selectedMonth={selectedMonth}
              onMonthChange={setSelectedMonth}
            />
          )}
          <ProgramacionRankings filters={activeFilters} />
        </>
      )}
    </div>
  );
}
