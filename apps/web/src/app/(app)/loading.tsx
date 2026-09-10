import { PageLoadingSkeleton } from '@/components/layout/page-loading-skeleton';

/**
 * Frontera de carga del grupo `(app)`.
 *
 * Sin ella, el App Router mantenía la pantalla ANTERIOR mientras cargaba el
 * segmento nuevo y no pintaba nada intermedio: hacer clic en «Reportes» dejaba
 * la vista congelada en la pantalla previa durante todo ese tiempo, que se lee
 * como que el clic se perdió. Con esta frontera, la barra lateral y la superior
 * se quedan donde están y solo el área de contenido pasa a esqueleto, de
 * inmediato.
 *
 * En este host la espera es de segundos porque el servidor de desarrollo
 * compila la ruta al pedirla (SEC-033); en producción es lo que tarde en bajar
 * el chunk de la ruta. La frontera hace falta en los dos casos.
 */
export default function AppGroupLoading() {
  return <PageLoadingSkeleton />;
}
