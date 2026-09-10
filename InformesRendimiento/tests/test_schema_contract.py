"""Evita drift entre load/schema.py y validate/validate_semantic_model.py.

Verifica que cada TableSpec declara la misma PK que valida `validate`, y que la
cobertura de tablas coincide en ambas direcciones.
"""

from load import schema
from validate import validate_semantic_model as v


def _validate_pks() -> dict[str, str]:
    pks = {file: pk for file, pk in v.DIM_CHECKS}
    for row in v.FACT_CHECKS:
        file, pk = row[0], row[1]
        pks[file] = pk
    return pks


def test_pk_matches_validate():
    validate_pks = _validate_pks()
    for spec in schema.TABLES:
        assert spec.parquet_name in validate_pks, f"{spec.parquet_name} no está en validate"
        assert len(spec.pk) == 1, f"{spec.parquet_name}: PK compuesta no soportada en contrato"
        assert spec.pk[0] == validate_pks[spec.parquet_name], (
            f"{spec.parquet_name}: PK {spec.pk[0]} != validate {validate_pks[spec.parquet_name]}"
        )


def test_full_coverage_both_ways():
    validate_files = set(_validate_pks())
    schema_files = {s.parquet_name for s in schema.TABLES}
    assert schema_files == validate_files, (
        f"faltan en schema: {validate_files - schema_files}; "
        f"sobran en schema: {schema_files - validate_files}"
    )


def test_no_duplicate_pg_tables():
    names = [s.pg_table for s in schema.TABLES]
    assert len(names) == len(set(names))


def test_dims_before_facts():
    kinds = [s.kind for s in schema.TABLES]
    assert kinds == sorted(kinds, key=lambda k: 0 if k == "dim" else 1)
    # y que no haya un dim después de un fact
    first_fact = next(i for i, k in enumerate(kinds) if k == "fact")
    assert all(k == "fact" for k in kinds[first_fact:])


def test_declared_indexes_reference_unique_existing_columns():
    for spec in schema.TABLES:
        names: set[str] = set()
        for name, columns in spec.indexes:
            assert name not in names, f"{spec.pg_table}: índice duplicado {name}"
            assert columns, f"{spec.pg_table}: índice {name} sin columnas"
            assert len(columns) == len(set(columns)), (
                f"{spec.pg_table}: índice {name} repite columnas"
            )
            names.add(name)


def test_portal_critical_analytics_indexes_are_versioned():
    declared = {
        name
        for spec in schema.TABLES
        for name, _columns in spec.indexes
    }
    assert {
        "dim_vehicle_device_id",
        "fact_combustible_monthly_veh_month",
        "fact_habito_event_scope_time",
        "fact_fault_event_scope_time",
    } <= declared
