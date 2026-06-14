FROM python:3.12-slim
WORKDIR /srv
# Reproducible builds: install the fully-pinned dependency closure first (this layer is
# cached until requirements.lock changes), then install the app itself with --no-deps so
# pip never re-resolves to newer versions. Regenerate the lock with:
#   docker build -f - . <<'EOF' ... pip freeze  (see README "Reproducible builds")
COPY backend/pyproject.toml backend/requirements.lock ./backend/
RUN pip install --no-cache-dir -r backend/requirements.lock
COPY backend ./backend
COPY policy_terms.json test_cases.json ./
RUN pip install --no-cache-dir -e ./backend --no-deps
# Run as a non-root user. Create the storage dir and hand /srv to the user BEFORE
# the named volume mounts onto /srv/backend/storage, so the volume inherits this
# ownership on first creation and the app (uid 10001) can write uploads/fixtures.
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /srv/backend/storage \
 && chown -R appuser:appuser /srv
WORKDIR /srv/backend
ENV POLICY_PATH=/srv/policy_terms.json TEST_CASES_PATH=/srv/test_cases.json STORAGE_DIR=/srv/backend/storage
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
