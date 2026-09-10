from load.naming import normalize_column


def test_basic_spaces():
    assert normalize_column("Kms ECM") == "kms_ecm"
    assert normalize_column("Velocidad Promedio GPS") == "velocidad_promedio_gps"


def test_percent_prefix():
    assert normalize_column("% Rango Bajo") == "pct_rango_bajo"
    assert normalize_column("% Exceso RPM") == "pct_exceso_rpm"
    assert normalize_column("% Rango Potencia Ineficiente Descenso") == (
        "pct_rango_potencia_ineficiente_descenso"
    )


def test_accents():
    assert normalize_column("Revisión") == "revision"
    assert normalize_column("% Ralentí") == "pct_ralenti"
    assert normalize_column("Anio-Mes") == "anio_mes"


def test_slash():
    assert normalize_column("km/gal") == "km_gal"
    assert normalize_column("gal/hr gps") == "gal_hr_gps"


def test_already_normalized():
    assert normalize_column("vehicle_id") == "vehicle_id"
    assert normalize_column("date_key") == "date_key"


def test_idempotent():
    once = normalize_column("% Rango Bajo Descenso")
    assert normalize_column(once) == once
