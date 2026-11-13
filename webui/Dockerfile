# Each base by tag and digest (ADR-0305): the digest decides what the build starts
# from, and Dependabot follows the tag when it proposes a newer digest.
#
# The builder runs Alpine's Node.js, because the node image is built for no linux/riscv64 and
# install.sh and update.sh build this image on the host (ADR-0370). Alpine's release fixes the
# major version: Alpine 3.22 has Node.js 22 on every platform, the same major version as the
# frontend job of .github/workflows/test.yml and "engines" in package.json
# (supervisor/tests/test_deploy_scripts.py checks it).
FROM alpine:3.22@sha256:49340b346798875b5592e5d420241f2f9cfba5f47e62ebb1446a1b34734e898f AS builder

RUN apk add --no-cache nodejs~22 npm

WORKDIR /app

COPY package*.json ./
# Install exactly what package-lock.json pins.
RUN npm ci

COPY . .
# Node.js 22's optimizing compiler Maglev stops this build on a SpacemiT K1 with "Fatal error ...
# unreachable code" (SIGTRAP), though not under QEMU: on riscv64 vite runs without it, which takes
# about 9 minutes on the K1. Elsewhere npm run build, which is vite build (ADR-0371).
RUN if [ "$(uname -m)" = riscv64 ]; then node --no-maglev node_modules/vite/bin/vite.js build; else npm run build; fi

FROM caddy:2-alpine@sha256:f2b257f20955d6be2229bed86bad24193eeb8c4dc962a4031a6eb42344ffa457

COPY --from=builder /app/build /srv
COPY Caddyfile /etc/caddy/Caddyfile

EXPOSE 80 443

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile"]
