FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.18 /uv /bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1

WORKDIR /app/brain
COPY brain/pyproject.toml brain/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY brain/ ./
RUN uv sync --frozen --no-dev
COPY web/ /app/web/
COPY data/worlds/default/world.json data/worlds/default/navgrid.json /app/data/worlds/default/
COPY data/worlds/quarry/world.json data/worlds/quarry/navgrid.json /app/data/worlds/quarry/

ENV WEB_DIR=/app/web WORLD_DIR=/app/data/worlds/quarry PATH=/app/brain/.venv/bin:$PATH
EXPOSE 8080 9000
CMD ["brain"]
