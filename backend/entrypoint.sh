#!/bin/sh

set -e
python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec gunicorn core.wsgi:application --workers 2 --threads 16 --timeout 120 --bind 0.0.0.0:8000
