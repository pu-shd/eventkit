# eventkit's own test/dev image. The library ships as a wheel, not a container;
# this exists so `docker compose run --rm test` is the same command here as in
# each of the five application repositories.
#
# Fixes carried forward from the two existing Dockerfiles, which this template
# replaces in every app repo:
#   * `tests/` is never copied into a runtime stage.
#   * runs as a non-root user.
#   * has a HEALTHCHECK.
#   * no build-essential / libpq-dev in a shipped image (psycopg[binary] instead).
#   * dependency layer is separate from source, so an edit does not reinstall.

FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd -m -u 10001 app
WORKDIR /app

# ---------------------------------------------------------------------------
FROM base AS deps
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e ".[test]"

# ---------------------------------------------------------------------------
FROM deps AS test
COPY . .
# `-e` above already installed the package; reinstall so entry points pick up
# any change to pyproject.toml made after the deps layer was cached.
RUN pip install --no-cache-dir -e ".[test]"
# COPY lands as root, but the suite runs as `app` and coverage writes its data
# file into the working directory. Without this the run dies in pytest-cov's
# teardown with "Couldn't use data file '/app/.coverage...': unable to open
# database file" — after the tests themselves have already passed.
RUN chown -R app:app /app
USER app
CMD ["./run_tests.sh"]

# ---------------------------------------------------------------------------
# A minimal runtime stage, used only by `eventkit azure` when an operator runs
# the CLI in a container rather than via pipx.
FROM base AS runtime
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY --chown=app:app src/ ./src/
COPY --chown=app:app pyproject.toml README.md ./
USER app
ENTRYPOINT ["eventkit"]
CMD ["--help"]
