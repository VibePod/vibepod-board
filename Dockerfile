FROM node:22-alpine AS client

WORKDIR /app
COPY package*.json ./
RUN npm ci

COPY index.html vite.config.ts tsconfig.client.json ./
COPY src ./src
RUN npm run build

FROM python:3.12-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    APP_ENV=production \
    PUBLIC_DIR=/app/dist/client \
    PORT=3000 \
    HOST=0.0.0.0

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/vibepod_board ./vibepod_board
RUN uv sync --frozen --no-dev

COPY --from=client /app/dist/client /app/dist/client

RUN useradd --system --uid 1000 --no-create-home board \
    && chown -R board /app
USER board
EXPOSE 3000

CMD ["python", "-m", "vibepod_board"]
