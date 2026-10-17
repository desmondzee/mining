FROM python:3.12-slim AS build

ARG FFMPEG_VERSION=9.0.2
ARG NVCODEC_VERSION=n13.0.19.0
ARG JOBS=8
RUN apt-get update && apt-get install -y --no-install-recommends build-essential nasm pkg-config curl xz-utils ca-certificates libx264-dev \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://github.com/FFmpeg/nv-codec-headers/archive/refs/tags/${NVCODEC_VERSION}.tar.gz | tar xz -C /tmp \
    && make -C /tmp/nv-codec-headers-${NVCODEC_VERSION} PREFIX=/opt/ffmpeg install \
    && curl -fsSL https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz | tar xJ -C /tmp \
    && cd /tmp/ffmpeg-${FFMPEG_VERSION} \
    && PKG_CONFIG_PATH=/opt/ffmpeg/lib/pkgconfig ./configure --prefix=/opt/ffmpeg --enable-shared --disable-static --enable-gpl \
       --disable-autodetect --enable-libx264 --enable-ffnvcodec --enable-nvenc --disable-programs --disable-doc \
       --disable-indevs --disable-outdevs \
    && make -j"${JOBS}" install \
    && rm -rf /tmp/ffmpeg-* /tmp/nv-codec-headers-*

COPY --from=ghcr.io/astral-sh/uv:0.8.18 /uv /bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_NO_BINARY_PACKAGE=av PKG_CONFIG_PATH=/opt/ffmpeg/lib/pkgconfig LD_LIBRARY_PATH=/opt/ffmpeg/lib

WORKDIR /app/brain
COPY brain/pyproject.toml brain/uv.lock ./
RUN uv sync --frozen --no-dev --extra gpu --no-install-project

COPY brain/ ./
RUN uv sync --frozen --no-dev --extra gpu

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends libx264-164 && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/ffmpeg/lib /opt/ffmpeg/lib
RUN echo /opt/ffmpeg/lib > /etc/ld.so.conf.d/ffmpeg.conf && ldconfig
COPY --from=build /app/brain /app/brain
COPY web/ /app/web/
COPY data/worlds/default/world.json data/worlds/default/navgrid.json /app/data/worlds/default/
COPY data/worlds/quarry/world.json data/worlds/quarry/navgrid.json /app/data/worlds/quarry/

WORKDIR /app/brain
ENV WEB_DIR=/app/web WORLD_DIR=/app/data/worlds/quarry PATH=/app/brain/.venv/bin:$PATH NVIDIA_DRIVER_CAPABILITIES=compute,utility,video
EXPOSE 8080 9000
CMD ["brain"]
