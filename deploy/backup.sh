#!/bin/sh
# Backup diário do Postgres (serviço `backup` do docker-compose.prod.yaml).
# Formato custom (-Fc): restaura com pg_restore, inclusive só uma tabela.
# Grava em .tmp e renomeia: um dump pela metade nunca fica com cara de backup bom.
#
# Restaurar (em outro banco, pra conferir antes de sobrescrever qualquer coisa):
#   docker compose -f docker-compose.prod.yaml exec -T db createdb -U "$POSTGRES_USER" restore_test
#   docker compose -f docker-compose.prod.yaml exec -T db pg_restore -U "$POSTGRES_USER" -d restore_test --no-owner < backups/chatly-AAAA-MM-DD.dump
set -eu

while true; do
    file="/backups/chatly-$(date +%F).dump"
    if pg_dump -Fc -f "$file.tmp"; then
        mv "$file.tmp" "$file"
        echo "$(date -Iseconds) backup ok: $file ($(du -h "$file" | cut -f1))"
    else
        rm -f "$file.tmp"
        echo "$(date -Iseconds) backup FALHOU" >&2
    fi
    find /backups -name 'chatly-*.dump' -mtime +"${KEEP_DAYS:-7}" -delete
    sleep 86400
done
