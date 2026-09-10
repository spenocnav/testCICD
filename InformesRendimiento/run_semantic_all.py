import importlib
import logging
import os

import config
import master_state

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

STREAMING_DATASETS = {
    value.strip()
    for value in os.environ.get('ETL_STREAMING_DATASETS', '').split(',')
    if value.strip()
}

MODULES = [
    'transform.transform_dim_vehicle',
    'transform.transform_dim_rule',
    'transform.transform_reference_dims',
    'transform.transform_dim_date',
    'transform.transform_combustible',
    'transform.transform_habitos',
    'transform.transform_fallas',
    'transform.transform_altimetria',
    'transform.transform_factor_carga',
    'transform.transform_def',
    'transform.transform_pedal',
    'transform.transform_ubicaciones',
    'validate.validate_silver_keys',
    'validate.validate_semantic_model',
    'load.load_semantic',
]

if 'factor_carga' in STREAMING_DATASETS:
    MODULES.remove('transform.transform_factor_carga')
if os.environ.get('ETL_FAULTS_REALTIME_ENABLED', 'false').lower() in {'1', 'true', 'yes'}:
    if 'transform.transform_fallas' in MODULES:
        MODULES.remove('transform.transform_fallas')

# Dominio apagado = no se transforma. Sin esto el transform releía la historia
# completa del silver y reescribía el semántico aunque nadie hubiera extraído
# nada nuevo para ese dominio.
if config.DISABLED_TRANSFORMS:
    MODULES = [m for m in MODULES if m not in config.DISABLED_TRANSFORMS]

# Dimensiones, validación y carga sostienen el modelo completo: si fallan, no
# hay nada que publicar. Un transform de dominio solo afecta a su propio hecho,
# así que su fallo deja el resto de los reportes actualizados.
CRITICAL_MODULES = frozenset({
    'transform.transform_dim_vehicle',
    'transform.transform_dim_rule',
    'transform.transform_reference_dims',
    'transform.transform_dim_date',
    'validate.validate_silver_keys',
    'validate.validate_semantic_model',
    'load.load_semantic',
})

# Código de salida que el worker interpreta como "cargó, pero incompleto".
PARTIAL_EXIT_CODE = 3


def main() -> int:
    if config.DISABLED_DOMAINS:
        logging.info(
            "dominios desactivados (sin transform ni load): %s",
            ", ".join(sorted(config.DISABLED_DOMAINS)),
        )
    failed: dict[str, str] = {}
    for module_name in MODULES:
        logging.info(f"Ejecutando {module_name}")
        try:
            module = importlib.import_module(module_name)
            module.main()
        except Exception as exc:
            if module_name in CRITICAL_MODULES:
                raise
            error_code = master_state.safe_extraction_error_code(exc)
            failed[module_name] = error_code
            logging.error(
                "%s falló; sigo con el resto del modelo (%s)",
                module_name,
                error_code,
            )
    if failed:
        logging.warning(
            "modelo semántico cargado sin: %s", ", ".join(sorted(failed))
        )
        return PARTIAL_EXIT_CODE
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
