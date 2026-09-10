# Imagen base para construir DENTRO de la red corporativa de Navitrans.
#
# Problema observado el 2026-09-09 construyendo apps/api/Dockerfile:
#   E: Failed to fetch http://deb.debian.org/... Hash Sum mismatch
#      Hashes of expected file: SHA256:e1feff...  Filesize:25036
#      Hashes of received file: SHA256:9401343... Filesize:33124
#      Last modification reported: Sun, 10 May 2026
# El fichero recibido no está corrupto: es OTRO fichero, de hace cuatro meses.
# Un proxy transparente intercepta el puerto 80 y sirve una copia rancia.
# Verificado que por HTTPS el mismo `apt-get install curl` funciona.
#
# Esta imagen es `python:3.12-slim` con los orígenes de apt en HTTPS. No cambia
# la versión de Python ni añade paquetes. `deploy/compose.ops.yml` la inyecta
# con la sustitución de contexto con nombre de BuildKit, de modo que el
# Dockerfile de la aplicación NO se modifica.
#
# Construir (una sola vez por máquina, o cuando cambie la base):
#   docker build -t portal-base:py312-https -f deploy/base-apt-https.Dockerfile .
#
# Fuera de esta red no hace falta: quitar las claves `build: *apt-https` del
# overlay y la aplicación construye contra la base oficial sin cambios.

FROM python:3.12-slim

RUN set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
      if [ -f "$f" ]; then \
        sed -i \
          -e 's|http://deb.debian.org|https://deb.debian.org|g' \
          -e 's|http://security.debian.org|https://security.debian.org|g' \
          "$f"; \
      fi; \
    done; \
    apt-get update -qq; \
    rm -rf /var/lib/apt/lists/*
