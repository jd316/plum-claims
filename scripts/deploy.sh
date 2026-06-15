#!/usr/bin/env bash
# Runs ON the EC2 host (invoked by CD via SSM). Pulls the requested image tag, brings the
# app up waiting on the containers' own healthchecks, refreshes Caddy, runs best-effort
# migrations, and rolls back to the previous tag if the new containers never become healthy.
#
#   TAG=<git-sha> bash scripts/deploy.sh
#
# The SSM caller does `git reset --hard origin/main` first so the compose files are current.
set -uo pipefail

# Serialize deploys on this host: if an overlapping CD run is mid-deploy, wait for it
# (up to 5 min) rather than racing the same containers. Belt-and-suspenders on top of
# the workflow-level concurrency cancel.
exec 9>/tmp/plum-deploy.lock
flock -w 300 9 || { echo "another deploy holds the lock — aborting"; exit 1; }

APP=/opt/plum-claims
DOMAIN=claims.zerocut.live
TAG="${TAG:-latest}"
COMPOSE="docker compose -p plumclaims -f docker-compose.yml -f docker-compose.deploy.yml -f docker-compose.tls.yml"

cd "$APP"
export TAG DOMAIN
PREV="$(cat .deployed_tag 2>/dev/null || echo latest)"
echo "==> deploying TAG=$TAG (previous=$PREV)"

# Poll one container's own Docker HEALTHCHECK until healthy (up to ~180s).
wait_healthy() {
  local c="$1" h
  for _ in $(seq 1 36); do
    h="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$c" 2>/dev/null || echo missing)"
    [ "$h" = "healthy" ] && return 0
    sleep 5
  done
  echo "   $c health=$h (gave up)"
  return 1
}

# Bring the stack up at <tag> and wait until the app containers report healthy via their
# own Docker HEALTHCHECKs (backend's probe hits /api/health internally). This — not an
# external curl racing Caddy/container startup — is the authoritative deployment gate.
# (We poll the containers directly rather than `up --wait`, which is unreliable here
# because Caddy has no healthcheck.)
bring_up() {
  local t="$1"; export TAG="$t"
  $COMPOSE pull
  $COMPOSE up -d --no-build
  wait_healthy plumclaims-backend-1 && wait_healthy plumclaims-frontend-1
}

post() {   # refresh Caddy's upstream (new frontend IP) + best-effort migration + prune
  $COMPOSE restart caddy >/dev/null 2>&1 || true
  timeout 60 $COMPOSE exec -T backend alembic upgrade head >/dev/null 2>&1 || true
  docker image prune -f >/dev/null 2>&1 || true
}

if bring_up "$TAG"; then
  post
  echo "$TAG" > .deployed_tag
  pub="$(curl -s -o /dev/null -m 8 --resolve "$DOMAIN:443:127.0.0.1" -w '%{http_code}' "https://$DOMAIN/api/health" || echo 000)"
  echo "==> DEPLOY OK ($TAG) — public health: $pub"
else
  echo "==> app containers did NOT become healthy — rolling back to $PREV"
  if bring_up "$PREV"; then
    post
    echo "$PREV" > .deployed_tag
    echo "==> ROLLED BACK to $PREV (healthy)"
  else
    echo "==> ROLLBACK ALSO UNHEALTHY — manual attention needed"
  fi
  exit 1
fi
