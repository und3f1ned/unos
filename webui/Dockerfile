# Each base by tag and digest (ADR-0305): the digest decides what the build starts
# from, and Dependabot follows the tag when it proposes a newer digest.
#
# The builder runs Alpine's Node.js, because the node image is built for no linux/riscv64 and
# this image was built on riscv64 hosts (ADR-0370). Alpine's release fixes the
# major version: Alpine 3.22 has Node.js 22 on every platform, the same major version as the
# frontend job of .github/workflows/test.yml and "engines" in package.json
# (supervisor/tests/test_deploy_scripts.py checks it).
#
# The builder runs on the platform that builds, whatever the image's (BuildKit, which docker
# compose and buildx use): the static files are the same on every platform, and the build tools
# (Vite's Rolldown and Lightning CSS) publish no binaries for linux/riscv64 nor for linux/arm/v7
# with musl (ADR-0456): .github/workflows/release.yml builds the riscv64 image on its runner, and an
# ARMv7 host's is built on another machine (`docker buildx build --platform linux/arm/v7`).
FROM --platform=$BUILDPLATFORM alpine:3.22@sha256:49340b346798875b5592e5d420241f2f9cfba5f47e62ebb1446a1b34734e898f AS builder

RUN apk add --no-cache nodejs~22 npm

WORKDIR /app

COPY package*.json ./
# Install exactly what package-lock.json pins.
RUN npm ci

COPY . .
# On a SpacemiT K1, Node.js crashes now and then in the middle of this build (ADR-0373): a riscv64
# host pulls the release's image, whose static files .github/workflows/release.yml builds on its
# runner's platform (deploy.riscv64-release-images).
RUN npm run build

FROM caddy:2-alpine@sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0

COPY --from=builder /app/build /srv
COPY Caddyfile /etc/caddy/Caddyfile
# Both TLS sites of the Caddyfile, Caddy's local CA and the served certificate (ADR-0404), adapt:
# a Caddyfile that does not fails the build, not the web UI's start. Adapting loads no certificate.
RUN caddy adapt --config /etc/caddy/Caddyfile > /dev/null && UNOS_TLS=served caddy adapt --config /etc/caddy/Caddyfile > /dev/null

EXPOSE 80 443

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile"]
