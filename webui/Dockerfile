# Each base by tag and digest (ADR-0305): the digest decides what the build starts
# from, and Dependabot follows the tag when it proposes a newer digest.
#
# A supported Node.js release, the same major version as the frontend job of
# .github/workflows/test.yml and "engines" in package.json
# (supervisor/tests/test_deploy_scripts.py checks it).
FROM node:22-alpine@sha256:0e1754758c2df4cfa3e68d59e2e1b7374bb7fd9ea6328eaaacf15cc934af8202 AS builder

WORKDIR /app

COPY package*.json ./
# Install exactly what package-lock.json pins.
RUN npm ci

COPY . .
RUN npm run build

FROM caddy:2-alpine@sha256:f2b257f20955d6be2229bed86bad24193eeb8c4dc962a4031a6eb42344ffa457

COPY --from=builder /app/build /srv
COPY Caddyfile /etc/caddy/Caddyfile

EXPOSE 80 443

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile"]
