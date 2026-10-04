# The queue worker. Build context: backend/.
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

# Dependencies first, so a change to the source does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# The weekly pricing job reads pages with a headless browser.
RUN uv run --no-sync playwright install --with-deps chromium

COPY src ./src
RUN uv sync --frozen --no-dev

CMD ["uv", "run", "--no-sync", "python", "-m", "productfoundry.jobs.worker"]
