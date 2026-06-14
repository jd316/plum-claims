import os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# --- Test database isolation -------------------------------------------------
# Several suites (test_whole_policy, test_ops, test_phi, test_documents, …) write
# claim/document rows through persistence.save_claim(). The persistence layer binds
# a module-level engine to settings.database_url at import time, so without isolation
# those fixture rows land in the SAME database the running app reads — polluting the
# Ops worklist with un-viewable "seeded" claims (their documents point at /tmp/x.png).
#
# Redirect the whole test run to a dedicated `<db>_test` database, created on demand,
# BEFORE any app module binds its engine. We mutate the already-loaded settings
# singleton (persistence reads settings.database_url at its first import, which happens
# later when a test imports app.*), and also export DATABASE_URL so any subprocess
# inherits the same isolation. If Postgres is unreachable we still point at the _test
# URL: DB-writing tests then skip via their own _db_reachable()/require_db guards
# rather than ever touching the application database.
from app.config import settings  # loads .env → the real DATABASE_URL

_base, _name = settings.database_url.rsplit("/", 1)
if not _name.endswith("_test"):
    _test_name = f"{_name}_test"
    _test_url = f"{_base}/{_test_name}"
    try:
        import psycopg

        _admin = _base.replace("postgresql+psycopg://", "postgresql://") + "/postgres"
        with psycopg.connect(_admin, autocommit=True) as _c:
            _exists = _c.execute(
                "select 1 from pg_database where datname=%s", (_test_name,)
            ).fetchone()
            if not _exists:
                _c.execute(f'create database "{_test_name}"')
    except Exception:
        pass  # unreachable/insufficient privs → DB tests skip; app DB stays untouched
    settings.database_url = _test_url
    os.environ["DATABASE_URL"] = _test_url
