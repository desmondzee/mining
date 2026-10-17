# syntax=docker/dockerfile:1
# Build stage runs on the build host's native arch (output is static files), so
# cross-building for arm64/amd64 never emulates node.
FROM --platform=$BUILDPLATFORM node:20-alpine AS build
WORKDIR /src
COPY web/package*.json ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY web/ ./
RUN npm run build

FROM nginx:alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 80
