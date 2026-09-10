# 0005 — Hash bcrypt directo sin passlib

- **Estado:** Aceptado
- **Fecha:** 2026-05-27

## Contexto

El plan original usaba `passlib[bcrypt]` para hash de contraseñas. En la práctica, `passlib 1.7.4` (última release) lee `bcrypt.__about__.__version__` para detectar la versión instalada. Las versiones de `bcrypt >= 4.1` ya no exponen ese atributo, por lo que `passlib` falla con `AttributeError`. Fijar `bcrypt < 4.1` es una solución frágil: introduce riesgo de vulnerabilidades sin parchear.

## Decisión

Eliminar `passlib`. Usar la librería `bcrypt` directamente. Truncar el input a 72 bytes (límite del algoritmo) en `core/security.py`:

```python
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode()[:72], bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode()[:72], hashed.encode())
```

## Alternativas consideradas

- **Pinear `bcrypt<4.1`:** evita el error pero perdemos parches de seguridad.
- **Migrar a `argon2-cffi`:** más fuerte que bcrypt pero requiere dependencia extra (libsodium en algunos sistemas) y romper hashes existentes.
- **Esperar a `passlib` ≥ 1.8:** ese release no ha salido, no hay timeline conocido.

## Consecuencias

- ✅ Cero capa de abstracción: menos bugs por incompatibilidad de versiones.
- ✅ Hashes válidos contra cualquier verificador bcrypt estándar.
- ⚠️ Si en el futuro queremos rotar a otro algoritmo (argon2), tendremos que escribir nosotros la lógica de versión (`$argon2id$...` vs `$2b$...`). Por ahora, fuera de alcance.
