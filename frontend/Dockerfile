FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build
# Non-root nginx: the official unprivileged image runs as uid 101 (nginx) and
# listens on 8080 (an unprivileged port). Compose publishes it as host :80.
# Digest-pinned (was floating ":alpine") for reproducible, supply-chain-safe builds.
FROM nginxinc/nginx-unprivileged:alpine@sha256:85bcbc6b2edd325462560c597d784ecee415024f1c6a004e53ac5f202b8ca561
COPY --from=build /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
