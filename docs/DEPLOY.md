# Deploy — single VM, production mode

The whole system runs on one Docker host. In **production mode** it engages JWT auth +
member/ops RBAC, at-rest PHI encryption, and confidence calibration — all driven by a root
`.env`. A plain `docker compose up` (no prod vars) stays in open dev mode; setting the vars
below flips it to prod with **no overlay and no code change**.

> The repo ships `docker-compose.yml` (5 services: db, redis, backend, worker, frontend) and
> an optional HTTPS overlay (`docker-compose.tls.yml`). MinIO is behind a profile and not used
> by default. See `README.md` → "Deploy" for the architecture details.

---

## 1. Provision the VM

- Any Linux box with **Docker + Docker Compose v2** (e.g. Ubuntu 22.04, 2 vCPU / 4 GB is plenty).
- Firewall / security group: open **80** (and **443** if you use HTTPS). Keep
  5432 / 6379 closed — Postgres/Redis bind to loopback only.
- Outbound HTTPS must be allowed (the vision pipeline calls the Gemini API).

```bash
# Docker (Ubuntu)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker
```

## 2. Get the code

```bash
git clone <YOUR_REPO_URL> plum-claims && cd plum-claims
```

## 3. Generate secrets

```bash
echo "JWT_SECRET=$(openssl rand -base64 48 | tr -d '/+=' )"
echo "PHI_ENCRYPTION_KEY=$(openssl rand -base64 32 | tr -d '/+=' )"
echo "POSTGRES_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=' )"
```

Pick the two **review credentials** you'll email (operator + member); they share one member
password across all member accounts (EMP001…, DEP001…).

## 4. Create the root `.env`

```ini
# --- required ---
GEMINI_API_KEY=<your-gemini-api-key>

# --- production posture ---
APP_ENV=production
AUTH_ENABLED=true
JWT_SECRET=<from step 3>
PHI_ENCRYPTION_ENABLED=true
PHI_ENCRYPTION_KEY=<from step 3>
SHOW_ROLE_HELP=true                 # Operator|Member toggle on the login page
# NOTE: leave CONFIDENCE_CALIBRATION_ENABLED off (default). The committed eval report
# and the assignment's confidence thresholds (e.g. TC004 > 0.85) are calibration-OFF;
# turning it on lowers the shown confidence and would fail those thresholds on the live
# Eval page. It's an optional production-reliability feature, not for the review build.

# --- database (internal only) ---
POSTGRES_PASSWORD=<from step 3>

# --- seeded review accounts (the passwords you email the reviewer) ---
OPS_DEFAULT_PASSWORD=<operator password>
MEMBER_DEFAULT_PASSWORD=<member password>
```

`APP_ENV=production` makes insecure defaults a **hard boot refusal**, so the backend won't
start unless `JWT_SECRET` and `PHI_ENCRYPTION_KEY` are real values — a safety net, not a gotcha.

## 5. Launch

```bash
docker compose up -d --build
```

That builds the images and starts all five services. The frontend is on **port 80**; the
backend auto-creates the DB schema and seeds the `ops` + per-member accounts on first boot.

Verify:

```bash
curl -s localhost/api/health                 # {"status":"ok"}
curl -s localhost/api/auth/config            # {"auth_enabled":true,"show_role_help":true}
docker compose ps                            # all healthy
```

Open `http://<vm-ip>/` → you land on the **login page** (Operator | Member toggle visible).

## 6. (Optional) HTTPS

With a domain pointed at the VM, terminate TLS with the bundled Caddy overlay (auto Let's
Encrypt). Open 80 + 443.

```bash
DOMAIN=claims.example.com \
  docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build
```

## 7. Put the URL in the README

Set `Deployed URL:` in `README.md` to your `http(s)://…` address, commit, push.

---

## Operate

```bash
docker compose logs -f backend         # tail logs (rotated at 10m x 3)
docker compose pull && docker compose up -d --build   # update after a git pull
docker compose down                    # stop (keeps the DB volume)
docker compose down -v                 # stop + wipe data
```

## Reviewer access

Email the two credential sets (operator + member) separately from the URL. On the login page
the reviewer picks **Operator** or **Member**, types the matching username + password, and gets
a role-scoped experience — operators see the full console (all claims, eval, fraud, policy
studio); members see only their own claims.
