#!/usr/bin/env bash
#
# Render build step.
#
# Runs on every deploy, before the new container takes traffic.
#
set -o errexit   # stop on the first failure - a half-built image must not ship
set -o pipefail
set -o nounset

echo "--> Installing dependencies"
pip install --upgrade pip
pip install -r requirements/production.txt

echo "--> Collecting static files"
python manage.py collectstatic --no-input

echo "--> Applying database migrations"
# Safe to run on every deploy: migrations are idempotent, and Django skips any
# that are already applied. Running it here rather than at container start
# means two instances can never race each other on the same migration.
python manage.py migrate --no-input

echo "--> Build complete"
