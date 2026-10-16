# Gateway: builds the React shell and serves it with nginx, routing /api/<service>/ to every microservice.
# Build context: the repository root (see start.sh).
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npx vite build

FROM nginx:1.27-alpine
COPY gateway/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=web /web/dist /usr/share/nginx/html
