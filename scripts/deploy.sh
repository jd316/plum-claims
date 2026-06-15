#!/usr/bin/env bash
# Runs ON the EC2 host (invoked by CD via SSM). Pulls the requested image tag, runs
# migrations, recreates the app containers, health-checks, and rolls back on failure.
#
#   TAG=<git-sha> bash scripts/deploy.sh
#
# Assumes the working tree is already at the target commit (the SSM caller does
# `git reset --hard origin/main` before invoking this, so the compose files are current).
set -euo pipefail

APP=/opt/plum-claims
DOMAIN=claims.zerocut.live
TAG="${TAG:-latest}"
COMPOSE="docker compose -p plumclaims -f docker-compose.yml -f docker-compose.deploy.yml -f docker-compose.tls.yml"

cd "$APP"
export TAG DOMAIN
PREV="$(cat .deployed_tag 2>/dev/null || echo latest)"
echo "==> deploying TAG=$TAG (previous=$PREV)"

roll() {                       # roll <tag>
  local t="$1"; export TAG="$t"
  $COMPOSE pull
  # migrations via a one-off container on the new image (db already running)
  $COMPOSE run --rm --no-deps --no-build backend alembic upgrade head || echo "(no migrations / skipped)"
  # --no-build: use the pulled image, never build on the host
  $COMPOSE up -d --no-build
}

healthy() {                    # local health check via Caddy (no NAT hairpin, real SNI/cert)
  for _ in $(seq 1 24); do
    code="$(curl -s -o /dev/null -m 8 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/health" || echo 000)"
    [ "$code" = "200" ] && return 0
    sleep 5
  done
  return 1
}

roll "$TAG"
if healthy; then
  echo "$TAG" > .deployed_tag
  docker image prune -f >/dev/null 2>&1 || true
  echo "==> DEPLOY OK ($TAG)"
else
  echo "==> HEALTH CHECK FAILED — rolling back to $PREV"
  roll "$PREV"
  healthy && echo "==> ROLLED BACK to $PREV (healthy)" || echo "==> ROLLBACK ALSO UNHEALTHY — manual attention needed"
  exit 1
fi
