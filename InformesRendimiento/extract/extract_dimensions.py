import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import pandas as pd

from utils import authenticate, make_row_id, make_vehicle_id, safe_api_call
from config import (
    ACTIVE_VEHICLE_CATALOG,
    CREDENTIALS,
    DIMS_PATH,
    EVENT_RULES,
    EVENT_RULES_BY_MOTOR,
    RPM_RULES,
    RULES_BY_DB_MOTOR,
    RULES_BY_MOTOR,
    VEHICLE_CATALOG,
)
from fuel import classify_fuel

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def build_dim_vehiculos() -> pd.DataFrame:
    rows = []
    for vehicle in VEHICLE_CATALOG:
        database_name = vehicle['database_name'] or 'UNKNOWN'
        fuel_kind, fuel_unit, fuel_source, fuel_conflict = classify_fuel(
            vehicle.get('tipo_combustible'),
            vehicle.get('motor_type'),
        )
        if fuel_conflict:
            logging.warning(
                "Conflicto de combustible: motor N15 declarado como %r (%s)",
                vehicle.get('tipo_combustible'),
                vehicle.get('placa'),
            )
        rows.append({
            'vehicle_id': make_vehicle_id(database_name, vehicle['device_id']),
            'database_name': database_name,
            'device_id': vehicle['device_id'],
            'placa': vehicle['placa'],
            'motor_type': vehicle['motor_type'],
            'group_key': vehicle['group_key'],
            'rpm_class': vehicle['rpm_class'],
            'fuel_type_raw': vehicle.get('tipo_combustible'),
            'fuel_kind': fuel_kind,
            'fuel_unit': fuel_unit,
            'fuel_classification_source': fuel_source,
            'fuel_classification_conflict': fuel_conflict,
            'is_active': vehicle['database_name'] in CREDENTIALS,
        })
    return pd.DataFrame(rows).drop_duplicates(subset=['vehicle_id'])


def build_dim_reglas() -> pd.DataFrame:
    rows = []

    # Con data maestra las bandas se identifican por (base, motor): el mismo
    # motor en dos clientes son reglas distintas y ambas deben quedar en la
    # dimensión. Sin master se conserva el scope legado por motor.
    if RULES_BY_DB_MOTOR:
        for database_name, motors in RULES_BY_DB_MOTOR.items():
            for motor_type, rules in motors.items():
                for rule_name, rule_id in rules.items():
                    rows.append({
                        'rule_sk': make_row_id(
                            'combustible', 'database_motor', database_name, motor_type, rule_id
                        ),
                        'rule_id': rule_id,
                        'rule_name': rule_name,
                        'source_script': 'combustible',
                        'scope_type': 'database_motor',
                        'scope_value': f'{database_name}::{motor_type}',
                        'categoria': 'RPM Descenso' if 'Descenso' in rule_name else 'Rango RPM',
                    })
    else:
        for motor_type, rules in RULES_BY_MOTOR.items():
            for rule_name, rule_id in rules.items():
                rows.append({
                    'rule_sk': make_row_id('combustible', 'motor_type', motor_type, rule_id),
                    'rule_id': rule_id,
                    'rule_name': rule_name,
                    'source_script': 'combustible',
                    'scope_type': 'motor_type',
                    'scope_value': motor_type,
                    'categoria': 'RPM Descenso' if 'Descenso' in rule_name else 'Rango RPM',
                })

    for database_name, rules in EVENT_RULES.items():
        for rule_name, rule_id in rules.items():
            rows.append({
                'rule_sk': make_row_id('habitos', 'database_name', database_name, rule_id),
                'rule_id': rule_id,
                'rule_name': rule_name,
                'source_script': 'habitos',
                'scope_type': 'database_name',
                'scope_value': database_name,
                'categoria': 'Seguridad',
            })

    for database_name, motors in EVENT_RULES_BY_MOTOR.items():
        for motor_type, rules in motors.items():
            for rule_name, rule_id in rules.items():
                rows.append({
                    'rule_sk': make_row_id(
                        'habitos', 'database_motor', database_name, motor_type, rule_id
                    ),
                    'rule_id': rule_id,
                    'rule_name': rule_name,
                    'source_script': 'habitos',
                    'scope_type': 'database_motor',
                    'scope_value': f'{database_name}::{motor_type}',
                    'categoria': 'Seguridad',
                })

    for rpm_class, rule_ids in RPM_RULES.items():
        if len(rule_ids) > 0:
            rows.append({
                'rule_sk': make_row_id('habitos', 'rpm_class', rpm_class, rule_ids[0]),
                'rule_id': rule_ids[0],
                'rule_name': 'Exceso RPM',
                'source_script': 'habitos',
                'scope_type': 'rpm_class',
                'scope_value': rpm_class,
                'categoria': 'RPM',
            })
        if len(rule_ids) > 1:
            rows.append({
                'rule_sk': make_row_id('habitos', 'rpm_class', rpm_class, rule_ids[1]),
                'rule_id': rule_ids[1],
                'rule_name': 'Exceso RPM sin Carga',
                'source_script': 'habitos',
                'scope_type': 'rpm_class',
                'scope_value': rpm_class,
                'categoria': 'RPM',
            })

    return pd.DataFrame(rows).drop_duplicates(subset=['rule_sk'])


def extract_dim_diagnosticos() -> pd.DataFrame:
    rows = []
    for database_name, creds_list in CREDENTIALS.items():
        try:
            api = authenticate(database_name, creds_list[0])
            diags = safe_api_call(api, 'Diagnostic')
            logging.info(f"  [{database_name}] {len(diags)} diagnosticos obtenidos")
            for diagnostic in diags:
                if not isinstance(diagnostic, dict):
                    continue
                source = diagnostic.get('source')
                diagnostic_id = diagnostic.get('id')
                rows.append({
                    'diagnostic_sk': make_row_id(database_name, diagnostic_id),
                    'database_name': database_name,
                    'diagnostic_id': diagnostic_id,
                    'name': diagnostic.get('name'),
                    'code': diagnostic.get('code'),
                    'source_id': source.get('id') if isinstance(source, dict) else source,
                })
        except Exception as exc:
            logging.error(f"Error extrayendo Diagnostic de {database_name}: {exc}")

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(subset=['diagnostic_sk'])


def extract_dim_controllers() -> pd.DataFrame:
    rows = []
    for database_name, creds_list in CREDENTIALS.items():
        try:
            api = authenticate(database_name, creds_list[0])
            controllers = safe_api_call(api, 'Controller')
            logging.info(f"  [{database_name}] {len(controllers)} controllers obtenidos")
            for controller in controllers:
                if not isinstance(controller, dict):
                    continue
                controller_id = controller.get('id')
                rows.append({
                    'controller_sk': make_row_id(database_name, controller_id),
                    'database_name': database_name,
                    'controller_id': controller_id,
                    'name': controller.get('name'),
                    'code': controller.get('code'),
                })
        except Exception as exc:
            logging.error(f"Error extrayendo Controller de {database_name}: {exc}")

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(subset=['controller_sk'])


def extract_dim_failure_modes() -> pd.DataFrame:
    rows = []
    for database_name, creds_list in CREDENTIALS.items():
        try:
            api = authenticate(database_name, creds_list[0])
            failure_modes = safe_api_call(api, 'FailureMode')
            logging.info(f"  [{database_name}] {len(failure_modes)} failure modes obtenidos")
            for failure_mode in failure_modes:
                if not isinstance(failure_mode, dict):
                    continue
                failure_mode_id = failure_mode.get('id')
                rows.append({
                    'failure_mode_sk': make_row_id(database_name, failure_mode_id),
                    'database_name': database_name,
                    'failure_mode_id': failure_mode_id,
                    'name': failure_mode.get('name'),
                    'code': failure_mode.get('code'),
                })
        except Exception as exc:
            logging.error(f"Error extrayendo FailureMode de {database_name}: {exc}")

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(subset=['failure_mode_sk'])


def main():
    os.makedirs(DIMS_PATH, exist_ok=True)

    logging.info("=== Construyendo dim_vehiculos ===")
    df_veh = build_dim_vehiculos()
    df_veh.to_parquet(os.path.join(DIMS_PATH, 'dim_vehiculos.parquet'), index=False, compression='snappy')
    logging.info(f"dim_vehiculos: {len(df_veh)} registros")

    logging.info("=== Construyendo dim_reglas ===")
    df_reg = build_dim_reglas()
    df_reg.to_parquet(os.path.join(DIMS_PATH, 'dim_reglas.parquet'), index=False, compression='snappy')
    logging.info(f"dim_reglas: {len(df_reg)} registros")

    if ACTIVE_VEHICLE_CATALOG:
        logging.info("=== Extrayendo dim_diagnosticos ===")
        df_diag = extract_dim_diagnosticos()
        if not df_diag.empty:
            df_diag.to_parquet(os.path.join(DIMS_PATH, 'dim_diagnosticos.parquet'), index=False, compression='snappy')
            logging.info(f"dim_diagnosticos: {len(df_diag)} registros")

        logging.info("=== Extrayendo dim_controllers ===")
        df_ctrl = extract_dim_controllers()
        if not df_ctrl.empty:
            df_ctrl.to_parquet(os.path.join(DIMS_PATH, 'dim_controllers.parquet'), index=False, compression='snappy')
            logging.info(f"dim_controllers: {len(df_ctrl)} registros")

        logging.info("=== Extrayendo dim_failure_modes ===")
        df_fm = extract_dim_failure_modes()
        if not df_fm.empty:
            df_fm.to_parquet(os.path.join(DIMS_PATH, 'dim_failure_modes.parquet'), index=False, compression='snappy')
            logging.info(f"dim_failure_modes: {len(df_fm)} registros")

    logging.info("=== Dimensiones completadas ===")


if __name__ == '__main__':
    main()
