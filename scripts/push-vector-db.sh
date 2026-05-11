#!/usr/bin/env bash
set -euo pipefail

REMOTE_USER=${REMOTE_USER:-normchel}
REMOTE_HOST=${REMOTE_HOST:-111.88.255.142}
REMOTE_DIR=${REMOTE_DIR:-/home/normchel/apps/matmod-rag}
SSH_KEY=${SSH_KEY:-$HOME/.ssh/ssh-key-1778518535526}
POSTGRES_USER=${POSTGRES_USER:-matmod}
POSTGRES_DB=${POSTGRES_DB:-matmod_rag}

stamp=$(date +%Y%m%d-%H%M%S)
dump_name="vector-db-${stamp}.dump"
dump_path="infra/postgres/backups/${dump_name}"

mkdir -p infra/postgres/backups

docker compose exec -T postgres pg_dump \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -Fc \
  --no-owner \
  --no-acl \
  > "$dump_path"

rsync -az \
  -e "ssh -i ${SSH_KEY} -o IdentitiesOnly=yes" \
  "$dump_path" \
  "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/infra/postgres/backups/"

cat <<MSG
Uploaded: ${REMOTE_DIR}/infra/postgres/backups/${dump_name}
Restore after project deploy:
  cd ${REMOTE_DIR}
  docker compose up -d postgres
  docker compose exec -T postgres pg_restore --clean --if-exists --no-owner --no-acl -U ${POSTGRES_USER} -d ${POSTGRES_DB} /backups/${dump_name}
MSG
