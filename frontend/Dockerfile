# Multi-stage build — self-contained.
# Stage 1 builds Angular from source so we can never publish a stale dist.
# Stage 2 serves the result via nginx.
#
# If you hit OOM during the build stage on a tight host, bump NODE_OPTIONS heap
# or build outside Docker (npm run build) and use the BUILD_MODE=prebuilt arg
# to skip stage 1 (see comment below).

ARG BUILD_MODE=full

# ---- Stage 1: build Angular dist ------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app

# Install deps separately so the layer caches when only sources change
COPY package.json package-lock.json* ./
# --legacy-peer-deps : Angular 16 + ng2-charts peer dep conflict avec npm 10+
RUN npm ci --no-audit --no-fund --legacy-peer-deps

# Copy sources and build
COPY angular.json tsconfig*.json ./
COPY src ./src
ENV NODE_OPTIONS=--max-old-space-size=3072
RUN node node_modules/@angular/cli/bin/ng.js build --configuration=production

# ---- Stage 2: nginx runtime -----------------------------------------------------
FROM nginx:1.27-alpine

# Run nginx as non-root on port 8080
RUN addgroup -S appgroup && adduser -S appuser -G appgroup \
    && mkdir -p /var/cache/nginx/client_temp /var/run \
    && chown -R appuser:appgroup /var/cache/nginx /var/run /usr/share/nginx/html \
    && sed -i 's|user  nginx;||g' /etc/nginx/nginx.conf \
    && sed -i 's|/var/run/nginx.pid|/tmp/nginx.pid|g' /etc/nginx/nginx.conf

COPY --from=build /app/dist/frontend /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost:8080/ > /dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
