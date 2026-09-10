# Sistema de Estilos - Navi Reportes Novedades

## 1) Identidad Visual
- Producto: `Navi Reportes Novedades`
- Estilo base: corporativo, limpio, alto contraste funcional, acentos cálidos y técnicos.
- Principio visual: superficie clara + acentos de marca + jerarquía tipográfica fuerte.

## 2) Paleta de Colores (Tokens Oficiales)

### 2.1 Marca
- `--brand-red`: `#EE2E2F` (CTA principal, alerta, énfasis)
- `--brand-black`: `#363534` (texto principal, iconos principales)
- `--brand-clear`: `#C3CAC8` (bordes suaves, separadores)
- `--brand-gray`: `#354550` (texto secundario, elementos UI)

### 2.2 Acentos
- `--accent-lime`: `#B2E100` (estado activo, energía, audio/voz)
- `--accent-cream`: `#EFF0C8` (fondos suaves de apoyo)
- `--accent-yellow`: `#FFB301` (resaltado positivo/aviso ligero)
- `--accent-blue`: `#185979` (enlaces, foco, elementos técnicos)

### 2.3 Neutros recomendados
- `--white`: `#FFFFFF`
- `--gray-50`: `#F8FAFB`
- `--gray-100`: `#F1F5F9`
- `--gray-200`: `#E2E8F0`
- `--gray-700`: `#334155`
- `--gray-900`: `#0F172A`

### 2.4 Variaciones para visualizaciones
Estas variaciones se reservan para gráficas sobre superficies claras. Derivan
de los colores oficiales y no sustituyen los tokens de marca en componentes UI.

- Rojo moderado: `#C95354` (eventos, tendencias adversas)
- Azul moderado: `#527F93` (series técnicas secundarias)
- Lima oscuro: `#718F00` (series positivas con contraste)
- Amarillo oscuro: `#C88700` (series de precaución con contraste)
- Gris moderado: `#738088` (rankings y series neutrales)
- Crema oscuro: `#9B945B` (sexta categoría en comparativos)

## 3) Tipografía

### 3.1 Familias
- Front usuario (`frontend`): `Manrope`, fallback `Segoe UI`, `Helvetica Neue`, `sans-serif`
- Front admin (`frontend-admin`):
  - Texto general: `Poppins`, fallback `Segoe UI`, `Helvetica Neue`, `sans-serif`
  - Encabezados: `Raleway`, fallback `Segoe UI`, `sans-serif`

### 3.2 Escala tipográfica sugerida
- `text-xs`: 12px / line-height 16px
- `text-sm`: 14px / line-height 20px
- `text-base`: 16px / line-height 24px
- `text-lg`: 18px / line-height 28px
- `text-xl`: 20px / line-height 30px
- `text-2xl`: 24px / line-height 32px
- `text-3xl`: 30px / line-height 38px

### 3.3 Pesos
- Regular: 400
- Medium: 500
- Semibold: 600
- Bold: 700
- ExtraBold (títulos destacados): 800

## 4) Espaciado y Ritmo
- Sistema base recomendado: múltiplos de 4px.
- Escala: 4, 8, 12, 16, 20, 24, 32, 40, 48.
- Gaps internos de componentes: 8-16px.
- Separación entre secciones: 24-40px.
- Contenedores principales: padding horizontal 16px (mobile), 24px (tablet), 32px (desktop).

## 5) Bordes, Radius y Líneas

### 5.1 Border radius
- `radius-sm`: 8px
- `radius-md`: 12px
- `radius-lg`: 16px
- `radius-xl`: 20px
- `radius-pill`: 9999px (chips, badges, avatares)

### 5.2 Bordes
- Bordes suaves por defecto: `1px solid rgba(195, 202, 200, 0.95)`
- Bordes de inputs en admin dark: `#334155`
- Divisores de listas/tablas: tonos `gray-100` / `#223146` (dark)

## 6) Sombras y Profundidad
- Sombra suave card:
  - `0 1px 2px rgba(16, 24, 40, 0.04), 0 4px 10px rgba(16, 24, 40, 0.06)`
- Sombra media modal/dropdown:
  - `0 10px 30px rgba(16, 24, 40, 0.14)`
- Uso: evitar sombras muy duras; priorizar contraste por color + borde.

## 7) Fondos y Superficies

### 7.1 Usuario (frontend)
- Background principal:
  - `linear-gradient(160deg, #F7F9F8 0%, #E9EDEB 55%, #E2E7E4 100%)`

### 7.2 Admin
- Fondo base claro: `#F8FAFB`
- Superficies: blanco con borde gris suave.
- Tema oscuro (`.admin-theme-dark`):
  - Fondo superficie: `#111827`
  - Fondos semitransparentes: `rgba(15, 23, 42, 0.8)` y `rgba(15, 23, 42, 0.95)`
  - Texto principal: `#E2E8F0`

## 8) Estructura y Layout
- Altura de app:
  - Usuario: `#root` con `min-height: 100svh`
  - Admin: `#root` con `height: 100dvh`
- Rejilla recomendada:
  - 1 columna mobile
  - 2-4 columnas tablet
  - 12 columnas desktop
- Sidebar admin: superficie separada con borde derecho sutil.
- Contenido principal: cards apiladas con encabezado y acciones en zona superior.

## 9) Componentes (Guía)

### 9.1 Botones
- Primario:
  - Fondo `--brand-red`, texto blanco
  - Hover: oscurecer 6-8%
  - Focus: anillo `2px` en `--accent-blue` con opacidad 30%
- Secundario:
  - Fondo blanco / transparente
  - Borde `--brand-clear`
  - Texto `--brand-gray`

### 9.2 Inputs
- Altura recomendada: 40-44px
- Padding horizontal: 12-14px
- Radius: 12px
- Estado focus: borde `--accent-blue`
- Placeholder: tono secundario (ej. `#7F93AA` en dark)

### 9.3 Cards
- Fondo: blanco (o dark surface en modo oscuro)
- Borde: 1px suave
- Radius: 16px
- Padding interno: 16-24px

### 9.4 Badges / chips
- Radius pill
- Tamaño texto: 12-13px
- Combinaciones:
  - Info: `accent-blue` suave
  - Success/active: `accent-lime`
  - Warning: `accent-yellow`
  - Error: `brand-red`

## 10) Motion y Animaciones
- Entrada de mensaje (`message-enter`):
  - Duración: `0.28s`, easing `ease-out`, desplazamiento vertical corto.
- Onda de voz (`call-voice-wave`):
  - Duración: `1.05s`, infinito, alternancia de altura/opacidad.
- Pulso de avatar (`call-avatar-pulse`):
  - Duración: `1.25s`, infinito, expansión con fade-out.
- Regla: animaciones funcionales, no decorativas excesivas.

## 11) Scrollbars
- Estilo común (`chat-scroll`):
  - Grosor: 7px
  - Thumb: `rgba(53, 69, 80, 0.38)`
  - Radius: pill
  - Track: transparente

## 12) Accesibilidad
- Contraste mínimo objetivo: WCAG AA.
- Estados requeridos en controles: `default`, `hover`, `focus-visible`, `disabled`, `error`.
- No depender solo de color para estado; sumar icono/label.
- En mobile, inputs a `16px` para evitar zoom automático iOS.

## 13) Responsive
- Breakpoints sugeridos:
  - `sm`: 640px
  - `md`: 768px
  - `lg`: 1024px
  - `xl`: 1280px
- Priorización mobile-first.
- Tablas densas: usar versión card/list en mobile.

## 14) Estados de UI
- `Loading`: skeleton + indicador textual breve.
- `Empty`: ilustración simple + acción primaria.
- `Error`: mensaje claro + acción de reintento.
- `Success`: confirmación breve no intrusiva.

## 15) Tokens CSS Recomendados (listos para usar)
```css
:root {
  --brand-red: #ee2e2f;
  --brand-black: #363534;
  --brand-clear: #c3cac8;
  --brand-gray: #354550;
  --accent-lime: #b2e100;
  --accent-cream: #eff0c8;
  --accent-yellow: #ffb301;
  --accent-blue: #185979;

  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --radius-xl: 20px;
  --radius-pill: 9999px;

  --shadow-soft: 0 1px 2px rgba(16, 24, 40, 0.04), 0 4px 10px rgba(16, 24, 40, 0.06);
  --shadow-md: 0 10px 30px rgba(16, 24, 40, 0.14);
}
```

## 16) Convenciones de Implementación
- Priorizar tokens (`var(--token)`) sobre hex directos.
- Evitar hardcodes repetidos en componentes.
- Documentar cualquier color nuevo antes de usarlo globalmente.
- Mantener consistencia entre `frontend` y `frontend-admin` con el mismo set base de colores de marca.

## 17) Checklist de Diseño
- Paleta aplicada con tokens.
- Tipografía correcta por contexto (usuario/admin).
- Jerarquía visual clara en encabezados y acciones.
- Estados de interacción completos.
- Responsive validado en mobile y desktop.
- Contraste y accesibilidad revisados.
