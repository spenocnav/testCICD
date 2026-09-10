"""Render privado y autocontenido de documentos Cummins almacenados en MinIO."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.navifault import (
    NavifaultAsset,
    NavifaultFaultAnalysis,
    NavifaultFaultAnalysisAsset,
    NavifaultFaultPage,
    NavifaultFaultPageAsset,
    NavifaultTechnicalDocument,
    NavifaultTechnicalDocumentAsset,
)
from app.services import navifault_llm_service, object_storage

_SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_UNSAFE_BLOCK = re.compile(
    r"<(?:iframe|object|embed|form|base)\b[^>]*>.*?</(?:iframe|object|embed|form|base)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_UNSAFE_VOID = re.compile(r"<(?:iframe|object|embed|form|base)\b[^>]*>", re.IGNORECASE)
_STYLESHEET_LINK = re.compile(
    r"<link\b[^>]*\brel\s*=\s*(?:\"stylesheet\"|'stylesheet'|stylesheet)[^>]*>",
    re.IGNORECASE,
)
_EVENT_ATTRIBUTE = re.compile(
    r"\s+on[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE
)
_IMAGE_SOURCE = re.compile(
    r"(?P<prefix>\bsrc\s*=\s*)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE,
)
_ANY_HREF = re.compile(
    r"(?P<prefix>\bhref\s*=\s*)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_TARGET_ATTRIBUTE = re.compile(
    r"\s+target\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE
)
_HEAD_CLOSE = re.compile(r"</head\s*>", re.IGNORECASE)

_MAX_INLINE_IMAGE_BYTES = 12 * 1024 * 1024
# Placeholder inline para un grafico que el corpus no logro descargar: el alt
# original de la publicacion es "GRAPHIC NOT FOUND" y se lee como un error roto.
_MISSING_IMAGE_SVG = (
    "data:image/svg+xml;charset=utf-8,"
    "%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20width%3D%27420%27%20height%3D%27150%27%3E"
    "%3Crect%20width%3D%27420%27%20height%3D%27150%27%20fill%3D%27%23f8fafc%27%2F%3E"
    "%3Ctext%20x%3D%27210%27%20y%3D%2779%27%20text-anchor%3D%27middle%27%20"
    "font-family%3D%27sans-serif%27%20font-size%3D%2713%27%20fill%3D%27%2394a3b8%27%3E"
    "Gr%C3%A1fico%20no%20disponible%3C%2Ftext%3E%3C%2Fsvg%3E"
)
_RENDER_STYLE = """
<meta http-equiv="Content-Security-Policy"
  content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:">
<style>
  :root {
    color-scheme: light;
    --brand-red: #ee2e2f;
    --accent: #185979;
    --ink: #0f172a;
    --body: #334155;
    --muted: #64748b;
    --line: #e2e8f0;
    --line-soft: #eef2f7;
    --surface: #ffffff;
    --canvas: #f4f6f9;
  }
  * { box-sizing: border-box; }
  html {
    background: var(--canvas);
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
  body {
    margin: 0;
    padding: 0;
    background: var(--canvas);
    color: var(--body);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 14px;
    line-height: 1.65;
  }

  /* Ruido de la publicacion original: barra de contacto, impresion, cookies y pie corporativo. */
  a.print, .efctree-feedback, .slider-bottom, #qsol-common-footer,
  .foot, noscript, .graphic_size_link, .glyphicon,
  .feedbackViewer { display: none !important; }

  /* Envoltorios de Bootstrap que ya no tienen hoja de estilo. */
  .row, .col-md-12, .container-fluid { margin: 0; padding: 0; min-width: 0; }
  .container-fluid > br, .section-box > br, .divForSearch > br { display: none; }

  .container {
    width: min(100%, 940px);
    margin: 0 auto;
    padding: 24px 20px 40px;
  }
  .divForSearch {
    overflow: hidden;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--surface);
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 12px 32px -12px rgba(15, 23, 42, 0.12);
  }

  /* Cabecera: codigo de falla y titulo publicado. */
  .container-fluid > h3.faultcode {
    display: inline-block;
    margin: 28px 28px 0;
    padding: 4px 11px;
    border: 1px solid rgba(238, 46, 47, 0.2);
    border-radius: 999px;
    background: rgba(238, 46, 47, 0.07);
    color: var(--brand-red);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }
  .failurecodename {
    margin: 12px 0 0;
    padding: 0 28px 22px;
    border-bottom: 1px solid var(--line-soft);
    color: var(--ink);
    font-size: 21px;
    font-weight: 700;
    line-height: 1.3;
    letter-spacing: -0.015em;
  }

  /* El codigo repetido dentro de tablas y parrafos es texto, no una etiqueta. */
  .faultcode {
    display: inline;
    margin: 0;
    padding: 0;
    border: 0;
    background: none;
    color: var(--brand-red);
    font-size: inherit;
    font-weight: 700;
    letter-spacing: normal;
    text-transform: none;
  }

  h1, h2, h3, h4, h5, h6 { color: var(--ink); font-weight: 700; }
  h3.overviewtext, .section-box h3 {
    display: flex;
    align-items: center;
    gap: 9px;
    margin: 26px 0 12px;
    color: var(--accent);
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.07em;
  }
  h3.overviewtext::before, .section-box h3::before {
    content: "";
    flex: none;
    width: 3px;
    height: 13px;
    border-radius: 2px;
    background: var(--accent);
  }
  h3.overviewtext { margin: 26px 28px 12px; }
  .section-box { margin: 0 28px; }
  .section-box:last-of-type { padding-bottom: 8px; }
  .white-box {
    overflow: hidden;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--surface);
  }
  .white-box > .row > .col-md-12 { padding: 16px 18px; }
  .white-box > .row > .col-md-12:empty { display: none; }

  /* Jerarquía de los procedimientos: la publicación la marca con estas clases
     y su hoja de estilo es una de las cinco que el saneado retira. Sin ellas
     un procedimiento anidado se lee plano y deja de decir qué depende de qué. */
  .indent1 { margin-left: 18px; }
  .indent2 { margin-left: 36px; }

  /* El índice del documento viaja en un `ul.navbar-nav`, que sin la hoja de
     Cummins se pinta como una lista suelta de viñetas. Los 34 vacíos de cada
     40 no estorban porque una lista sin elementos no ocupa nada. */
  ul.nav, ul.navbar-nav {
    margin: 0 0 14px;
    padding: 12px 16px;
    border: 1px solid var(--line-soft);
    border-radius: 10px;
    background: var(--surface);
    list-style: none;
  }
  ul.nav:not(:has(li)), ul.navbar-nav:not(:has(li)) { display: none; }
  ul.nav li, ul.navbar-nav li { margin: 2px 0; }

  /* Los análisis y los documentos técnicos maquetan figura y texto en columnas
     de Bootstrap. Sin su hoja se apilan, que es lo correcto en el ancho del
     visor; sólo hace falta que respiren y que la imagen no se desborde. */
  .col-md-4, .col-md-8 { margin: 0; padding: 0; min-width: 0; }
  .col-md-4 + .col-md-8, .col-md-8 + .col-md-4 { margin-top: 12px; }
  .thumbnail { margin: 0; padding: 0; border: 0; background: none; }
  .genimg, .img-responsive { display: block; max-width: 100%; height: auto; }
  .table-responsive {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  .white-box > .row > .col-md-12 > .table-responsive { margin: -16px -18px; }

  table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
  }
  th, td {
    padding: 13px 16px;
    border-right: 1px solid var(--line-soft);
    border-bottom: 1px solid var(--line-soft);
    text-align: left;
    vertical-align: top;
  }
  th:last-child, td:last-child { border-right: 0; }
  tr:last-child td { border-bottom: 0; }
  th {
    background: #f8fafc;
    border-bottom: 1px solid var(--line);
    color: var(--muted);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    white-space: nowrap;
  }
  td { color: var(--body); font-size: 13.5px; }
  td:first-child { width: 1%; white-space: nowrap; }

  b, strong { color: var(--ink); font-weight: 600; }
  p, .para { margin: 0 0 10px; }
  p:last-child, .para:last-child { margin-bottom: 0; }

  /* La publicacion parte cada vinieta en su propia lista; se leen como una sola. */
  ul, ol { margin: 0 0 10px; padding-left: 20px; }
  ul:last-child, ol:last-child { margin-bottom: 0; }
  ul + ul, ol + ol { margin-top: -10px; }
  li { margin: 0 0 6px; padding-left: 2px; }
  li:last-child { margin-bottom: 0; }
  li::marker { color: #94a3b8; }

  /* Figura tecnica: marco y pie de imagen. */
  [align="center"] {
    margin: 22px 28px;
    padding: 18px;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: #fbfcfd;
    color: var(--muted);
    font-size: 12px;
    font-style: italic;
    text-align: center;
  }
  .genimg { margin: 0 0 12px; padding: 0; border: 0; background: none; }
  img {
    display: block;
    max-width: 100%;
    height: auto;
    margin: 0 auto;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--surface);
  }

  /* Los enlaces de la publicacion apuntan al portal Cummins y aqui no navegan. */
  a { color: inherit; text-decoration: none; cursor: default; }
  .container-fluid > a {
    display: block;
    margin: 22px 28px 4px;
    padding: 12px 16px;
    border: 1px dashed var(--line);
    border-radius: 10px;
    background: #f8fafc;
    color: var(--muted);
    font-size: 12.5px;
  }
  .last-update-box {
    margin: 22px 28px 24px;
    color: #94a3b8;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 999px; }
  ::-webkit-scrollbar-thumb:hover { background: #94a3b8; }

  @media (max-width: 640px) {
    .container { padding: 12px 12px 24px; }
    .container-fluid > h3.faultcode { margin: 18px 18px 0; }
    .failurecodename { padding: 0 18px 18px; font-size: 18px; }
    h3.overviewtext { margin: 20px 18px 10px; }
    .section-box { margin: 0 18px; }
    [align="center"], .container-fluid > a, .last-update-box { margin-left: 18px; margin-right: 18px; }
    .white-box > .row > .col-md-12 { padding: 14px; }
    .white-box > .row > .col-md-12 > .table-responsive { margin: -14px; }
    th, td { padding: 11px 13px; }
  }
</style>
"""


class NavifaultDocumentError(RuntimeError):
    """No fue posible recuperar el documento original privado."""


@dataclass(frozen=True)
class RenderedFaultPage:
    fault_page_id: str
    pub_id: str
    language: str
    fault_code: int
    variant: str
    title: str | None
    html: str
    embedded_images: int
    missing_images: int


def _asset_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(
        r"\s+([_.])",
        r"\1",
        PurePosixPath(urlparse(value).path).name.casefold(),
    )


def _matches_cached_asset(source_name: str, remote_name: str) -> bool:
    """Evita asociar una imagen descargada a otro gráfico de la misma FC."""
    if not source_name or not remote_name:
        return False
    source_stem = PurePosixPath(source_name).stem
    remote_stem = PurePosixPath(remote_name).stem
    return source_stem == remote_stem or source_stem.endswith(f"_{remote_stem}")


async def _image_data_uris(
    session: AsyncSession, placement: Any, owner_column: Any, owner_id: str
) -> dict[str, str]:
    """Incrusta en base64 las imágenes de un documento del corpus.

    Las tres tablas de colocación —página, análisis y documento técnico— tienen
    la misma forma y sólo cambian en la columna que nombra al dueño, así que la
    función se parametriza en vez de triplicarse. El tope de bytes es por
    documento y sigue vigente: los documentos técnicos tienen 441.768 imágenes
    entre todos y uno solo puede traer muchas.
    """

    rows = (
        await session.execute(
            select(NavifaultAsset, placement)
            .join(placement, placement.asset_id == NavifaultAsset.asset_id)
            .where(
                owner_column == owner_id,
                NavifaultAsset.local_status == "available",
                NavifaultAsset.object_key.is_not(None),
            )
            .order_by(placement.ordinal)
        )
    ).all()
    by_name: dict[str, str] = {}
    total_size = 0
    for asset, placement in rows:
        source_name = _asset_name(asset.source_url)
        display_name = _asset_name(placement.display_url)
        high_res_name = _asset_name(placement.high_res_url)
        names = {source_name} if source_name else set()
        if _matches_cached_asset(source_name, display_name):
            names.add(display_name)
        if _matches_cached_asset(source_name, high_res_name):
            names.add(high_res_name)
        if not names or not asset.object_key:
            continue
        try:
            content = await object_storage.get_object(settings.navifault_minio_bucket, asset.object_key)
        except object_storage.ObjectStorageError:
            continue
        if not content or total_size + len(content) > _MAX_INLINE_IMAGE_BYTES:
            continue
        media_type = asset.media_type or "image/png"
        encoded = base64.b64encode(content).decode("ascii")
        data_uri = f"data:{media_type};base64,{encoded}"
        for name in names:
            by_name.setdefault(name, data_uri)
        total_size += len(content)
    return by_name


#: Palabras funcionales que la publicación en español marca en negrita por un
#: artefacto de su traducción: el énfasis del original inglés —`not`, `must`—
#: se quedó en la posición del token y no en la palabra, así que aterrizó sobre
#: artículos y preposiciones. Medido sobre 10 páginas de cada idioma: 14
#: negritas en inglés frente a 238 en español, de las cuales 165 son `la`.
#:
#: La lista es cerrada y sólo de palabras funcionales. `debe` y `sin` NO entran:
#: son la traducción correcta de `must` y de un énfasis real, y quitarlas
#: borraría una advertencia del manual. Tampoco entra ninguna palabra con
#: contenido técnico.
_FUNCTION_WORDS = (
    "la", "el", "los", "las", "un", "una", "unos", "unas",
    "de", "del", "al", "y", "o", "en", "a", "con", "por", "para",
)
_STRAY_EMPHASIS = re.compile(
    r"<(?P<tag>b|strong)\b[^>]*>(?P<word>\s*(?:" + "|".join(_FUNCTION_WORDS) + r")\s*)</(?P=tag)>",
    re.IGNORECASE,
)


#: Prefijo con el que el visor nombra un destino del corpus. El HTML saneado no
#: lleva URLs navegables: lleva ESTE identificador en un atributo de datos, y el
#: padre del iframe lo lee al capturar el clic. Así la navegación no depende de
#: relajar el sandbox ni de que el cliente sepa interpretar rutas de Cummins.
LINK_TARGET_ATTRIBUTE = "data-navifault-target"


async def _resolve_corpus_links(
    session: AsyncSession, raw_html: str, base_url: str | None
) -> dict[str, str]:
    """Traduce los `href` de la publicación a destinos del corpus.

    La publicación enlaza con rutas del portal Cummins (`/qs3/pubsys2/...`), que
    es exactamente lo que el corpus guardó como `source_url`. Se resuelve por
    **URL exacta** —resolviendo la relativa contra la del propio documento— y no
    por sufijo: `source_url` es único e indexado, y un `LIKE '%...'` no podría
    usar ese índice.

    Un `href` que no está en el corpus no se traduce y sigue neutralizado. Medido
    sobre 40 páginas al azar: 42 enlaces internos, **cero sin resolver**.
    """

    if not settings.navifault_corpus_navigation_enabled:
        # Apagada: ningún `href` se traduce, así que todos quedan neutralizados
        # y el visor se comporta exactamente como antes de existir la
        # navegación. El camino completo sigue aquí, sin ejercitarse.
        return {}

    candidatos: dict[str, str] = {}
    for match in _ANY_HREF.finditer(raw_html):
        crudo = (match.group("value") or "").strip()
        if not crudo or crudo.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absoluta = urljoin(base_url or "", crudo).split("#", 1)[0]
        if absoluta:
            candidatos[crudo] = absoluta
    if not candidatos:
        return {}

    urls = set(candidatos.values())
    encontrados: dict[str, str] = {}
    # El orden importa poco —los tres espacios de URL son disjuntos— pero se fija
    # para que un dato inesperado no haga que el mismo enlace lleve a un sitio
    # distinto según el plan de la consulta.
    for tipo, modelo, columna_id in (
        ("analysis", NavifaultFaultAnalysis, NavifaultFaultAnalysis.analysis_id),
        ("document", NavifaultTechnicalDocument, NavifaultTechnicalDocument.document_id),
        ("page", NavifaultFaultPage, NavifaultFaultPage.fault_page_id),
    ):
        pendientes = urls - set(encontrados)
        if not pendientes:
            break
        filas = (
            await session.execute(
                select(modelo.source_url, columna_id).where(modelo.source_url.in_(pendientes))
            )
        ).all()
        for source_url, doc_id in filas:
            encontrados.setdefault(source_url, f"{tipo}:{doc_id}")

    return {
        crudo: encontrados[absoluta]
        for crudo, absoluta in candidatos.items()
        if absoluta in encontrados
    }


_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _clean_title(value: str | None) -> str | None:
    """Deja el título en texto plano.

    La publicación mete marcado y comentarios dentro del propio título —hay
    documentos titulados `<!--WARNING: No Translation for this term-->Solution
    Set`— y eso acabaría pintado tal cual en el encabezado del visor.
    """

    if not value:
        return None
    limpio = re.sub(r"<[^>]+>", "", _HTML_COMMENT.sub("", value))
    limpio = re.sub(r"\s+", " ", limpio).strip()
    return limpio or None


def _drop_stray_emphasis(html: str) -> str:
    """Quita la negrita de las palabras funcionales, conservando el texto.

    No se toca ninguna palabra: sólo se retira el marcado que la publicación
    puso mal. Es la misma clase de limpieza de presentación que ya hace el visor
    con el banner de cookies o el botón de imprimir, y sin ella el texto se lee
    con artículos resaltados en mitad de la frase.
    """

    return _STRAY_EMPHASIS.sub(r"\g<word>", html)


def _sanitize_and_embed(
    raw_html: str,
    image_data_uris: dict[str, str],
    link_targets: dict[str, str] | None = None,
) -> tuple[str, int, int]:
    sanitized = _SCRIPT_BLOCK.sub("", raw_html)
    sanitized = _UNSAFE_BLOCK.sub("", sanitized)
    sanitized = _UNSAFE_VOID.sub("", sanitized)
    sanitized = _STYLESHEET_LINK.sub("", sanitized)
    sanitized = _EVENT_ATTRIBUTE.sub("", sanitized)
    # Ningún `href` sobrevive apuntando afuera: los que el corpus reconoce se
    # convierten en un destino interno que el visor sabe abrir, y el resto queda
    # neutralizado como antes. Nunca se deja una URL navegable, ni siquiera la
    # del propio Cummins.
    objetivos = link_targets or {}

    def replace_href(match: re.Match[str]) -> str:
        prefijo, comilla = match.group("prefix"), match.group("quote")
        destino = objetivos.get((match.group("value") or "").strip())
        neutro = f"{prefijo}{comilla}#{comilla}"
        if destino is None:
            return neutro
        return f'{neutro} {LINK_TARGET_ATTRIBUTE}={comilla}{destino}{comilla}'

    sanitized = _ANY_HREF.sub(replace_href, sanitized)
    sanitized = _TARGET_ATTRIBUTE.sub("", sanitized)
    sanitized = _drop_stray_emphasis(sanitized)

    embedded = 0
    missing = 0

    def replace_image(match: re.Match[str]) -> str:
        nonlocal embedded, missing
        name = _asset_name(match.group("value"))
        data_uri = image_data_uris.get(name)
        if data_uri is None:
            missing += 1
            return (
                f'{match.group("prefix")}{match.group("quote")}'
                f'{_MISSING_IMAGE_SVG}{match.group("quote")}'
            )
        embedded += 1
        return f'{match.group("prefix")}{match.group("quote")}{data_uri}{match.group("quote")}'

    sanitized = _IMAGE_SOURCE.sub(replace_image, sanitized)
    if _HEAD_CLOSE.search(sanitized):
        sanitized = _HEAD_CLOSE.sub(_RENDER_STYLE + "</head>", sanitized, count=1)
    else:
        sanitized = f"<!doctype html><html><head>{_RENDER_STYLE}</head><body>{sanitized}</body></html>"
    return sanitized, embedded, missing


async def render_fault_page_model(
    session: AsyncSession, page: NavifaultFaultPage
) -> RenderedFaultPage:
    if page.raw_html_status != "available" or not page.raw_html_object_key:
        raise NavifaultDocumentError("La FC no tiene HTML original disponible en el corpus privado")
    try:
        raw_bytes = await object_storage.get_object(
            settings.navifault_minio_bucket, page.raw_html_object_key
        )
    except object_storage.ObjectStorageError as exc:
        raise NavifaultDocumentError("No fue posible recuperar el HTML original de la FC") from exc
    raw_html = raw_bytes.decode("utf-8", errors="replace")
    images = await _image_data_uris(
        session, NavifaultFaultPageAsset, NavifaultFaultPageAsset.fault_page_id, page.fault_page_id
    )
    enlaces = await _resolve_corpus_links(session, raw_html, page.source_url)
    rendered_html, embedded, missing = _sanitize_and_embed(raw_html, images, enlaces)
    return RenderedFaultPage(
        fault_page_id=page.fault_page_id,
        pub_id=page.pub_id,
        language=page.language,
        fault_code=page.fault_code,
        variant=page.variant,
        title=page.title,
        html=rendered_html,
        embedded_images=embedded,
        missing_images=missing,
    )


#: Las tres clases de documento del corpus, con lo que hace falta para
#: renderizar cualquiera: su modelo, su clave, su tabla de imágenes y la columna
#: que ata una imagen a su dueño. Tenerlo en UN sitio es lo que permite que el
#: visor navegue entre clases sin tres caminos paralelos que se desincronicen.
_CORPUS_KINDS: dict[str, tuple[Any, Any, Any, Any]] = {
    "page": (
        NavifaultFaultPage,
        NavifaultFaultPage.fault_page_id,
        NavifaultFaultPageAsset,
        NavifaultFaultPageAsset.fault_page_id,
    ),
    "analysis": (
        NavifaultFaultAnalysis,
        NavifaultFaultAnalysis.analysis_id,
        NavifaultFaultAnalysisAsset,
        NavifaultFaultAnalysisAsset.analysis_id,
    ),
    "document": (
        NavifaultTechnicalDocument,
        NavifaultTechnicalDocument.document_id,
        NavifaultTechnicalDocumentAsset,
        NavifaultTechnicalDocumentAsset.document_id,
    ),
}


@dataclass(slots=True)
class RenderedCorpusDocument:
    """Un documento del corpus listo para el visor, sea de la clase que sea."""

    kind: str
    document_id: str
    title: str | None
    html: str
    embedded_images: int
    missing_images: int


async def render_corpus_document(
    session: AsyncSession, *, kind: str, document_id: str
) -> RenderedCorpusDocument:
    """Renderiza un análisis, un documento técnico o una página del corpus.

    Es a donde lleva un enlace del visor. Comparte el saneado, el incrustado de
    imágenes y la resolución de enlaces con la página de falla: si divergieran,
    un documento alcanzado por navegación podría servir contenido con reglas más
    laxas que el que se abre desde la ficha.
    """

    entrada = _CORPUS_KINDS.get(kind)
    if entrada is None:
        raise NavifaultDocumentError("Tipo de documento desconocido en el corpus privado")
    modelo, _clave, placement, columna_dueno = entrada

    documento = await session.get(modelo, document_id)
    if documento is None:
        raise NavifaultDocumentError("Documento no encontrado en el corpus privado")
    if documento.raw_html_status != "available" or not documento.raw_html_object_key:
        raise NavifaultDocumentError("El documento no tiene HTML original en el corpus privado")
    try:
        raw_bytes = await object_storage.get_object(
            settings.navifault_minio_bucket, documento.raw_html_object_key
        )
    except object_storage.ObjectStorageError as exc:
        raise NavifaultDocumentError("No fue posible recuperar el documento original") from exc

    raw_html = raw_bytes.decode("utf-8", errors="replace")
    images = await _image_data_uris(session, placement, columna_dueno, document_id)
    enlaces = await _resolve_corpus_links(session, raw_html, documento.source_url)
    html, embedded, missing = _sanitize_and_embed(raw_html, images, enlaces)
    return RenderedCorpusDocument(
        kind=kind,
        document_id=document_id,
        title=_clean_title(documento.title),
        html=html,
        embedded_images=embedded,
        missing_images=missing,
    )


async def render_fault_page_by_id(
    session: AsyncSession, fault_page_id: str
) -> RenderedFaultPage:
    page = await session.get(NavifaultFaultPage, fault_page_id)
    if not page:
        raise NavifaultDocumentError("Página de falla no encontrada en el corpus privado")
    return await render_fault_page_model(session, page)


async def render_fault_page(
    session: AsyncSession, resolution: navifault_llm_service.Resolution
) -> RenderedFaultPage:
    return await render_fault_page_model(session, resolution.page)
