#!/usr/bin/env bash
# Run the Playwright E2E specs against the local stand-in, never against the
# device (ADR-0215: the specs create and delete users, shares and backups, and
# restore databases).
#
#   scripts/standin/e2e.sh [playwright test arguments...]       (make e2e-local)
#   scripts/standin/e2e.sh tests/shares.spec.ts --grep Seeded
#   scripts/standin/e2e.sh tests/first-run.spec.ts               the first-run setup alone (step 3)
#   scripts/standin/e2e.sh --help                                this text
#
# 1. Builds the web UI into webui/build (E2E_SKIP_BUILD=1 serves the last build).
# 2. Starts the stand-in under .standin/ at the repository root (ignored by git),
#    from a fresh data directory: scripts/standin/run_supervisor.py, the real
#    supervisor with every data path in .standin/, and scripts/standin/front.mjs
#    in place of Caddy (HTTPS with a self-signed certificate, made into
#    .standin/tls when missing or about to expire; the security headers of
#    webui/Caddyfile; plain HTTP redirects, except /api/v1/health).
# 3. Creates the administrator the specs sign in as (E2E_ADMIN of
#    webui/tests/fixtures.ts) as the owner creates theirs: on the new stand-in,
#    which has no account yet, a first Playwright run with E2E_FIRST_RUN=1 goes
#    through the /setup page (webui/tests/first-run.spec.ts, ADR-0261). The run
#    of the specs ignores that file: when the arguments name only it, its run
#    here is the whole run, and step 4 is left out.
# 4. Runs Playwright in webui/ with BASE_URL and HTTP_URL on localhost,
#    E2E_STAND_IN=1 (the specs skip, with the reason, what the stand-in cannot
#    provide: disks, Docker, the host's network, journal and /proc),
#    E2E_POOLS_BASE (the stand-in's pools folder), and E2E_STANDIN_STATE and
#    E2E_STANDIN_PYTHON (its state directory and Python, with which
#    scripts/standin/emergency.py runs the supervisor's emergency command line
#    against the stand-in's data).
# 5. Stops the stand-in and exits with Playwright's exit code.
#
# Logs: .standin/logs/ (build, supervisor, front, first-run, playwright); the
# HTML report is in webui/playwright-report. The data of the last run stay in
# .standin/data until the next run.
#
# Environment, with defaults:
#   PYTHON          supervisor/.venv/bin/python, else python3 (with the packages
#                   of supervisor/requirements.txt)
#   NODE=node  NPM=npm  OPENSSL=openssl
#   E2E_HTTPS_PORT=46443  E2E_HTTP_PORT=46080  E2E_BACKEND_PORT=46180
#   E2E_SKIP_BUILD=1    serve the existing webui/build
#   E2E_CHROMIUM_PATH   a Chromium binary for Playwright instead of the one that
#                       `npx playwright install chromium` downloads (make setup)
# A BASE_URL or HTTP_URL that is not on this machine is refused (exit 2).
set -uo pipefail

case ${1:-} in
  -h | --help)
    # The comment block above, without the shebang.
    sed -n '2,/^set -uo pipefail$/p' "$0" | sed '$d' | sed -E 's/^# ?//'
    exit 0
    ;;
esac

# Set for the first-run setup alone (step 3): a caller's value would make the
# specs' run that setup again.
unset E2E_FIRST_RUN

# Arguments that name only tests/first-run.spec.ts: the first-run setup of step
# 3 is the whole run, because the run of the specs (step 4) ignores that file.
first_run_only=
if [ $# -gt 0 ]; then
  first_run_only=1
  for arg in "$@"; do
    case $arg in
      first-run.spec.ts | */first-run.spec.ts) ;;
      *) first_run_only= ;;
    esac
  done
fi

# Physical paths: the supervisor resolves symbolic links in its pools base,
# and the specs build share paths from E2E_POOLS_BASE.
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
repo=$(cd "$here/../.." && pwd -P)
webui=$repo/webui
state=$repo/.standin
logs=$state/logs
tls=$state/tls
lock=$state/e2e.lock

https_port=${E2E_HTTPS_PORT:-46443}
http_port=${E2E_HTTP_PORT:-46080}
backend_port=${E2E_BACKEND_PORT:-46180}
base_url=https://localhost:$https_port
http_url=http://localhost:$http_port

NODE=${NODE:-node}
NPM=${NPM:-npm}
OPENSSL=${OPENSSL:-openssl}
if [ -z "${PYTHON:-}" ]; then
  if [ -x "$repo/supervisor/.venv/bin/python" ]; then PYTHON=$repo/supervisor/.venv/bin/python; else PYTHON=python3; fi
fi

die() {
  echo "e2e.sh: $*" >&2
  exit 1
}

need() {
  command -v "$1" > /dev/null 2>&1 || die "$1 not found: $2"
}

need "$NODE" "install Node.js 22 (webui/package.json), or set NODE"
need "$NPM" "install Node.js 22 with npm, or set NPM"
need "$OPENSSL" "install OpenSSL, or set OPENSSL"
need curl "install curl"
need "$PYTHON" "run make setup, or set PYTHON to a Python 3.11 with supervisor/requirements.txt"

# The host part of a URL, parsed as Playwright parses it (WHATWG URL).
url_host() {
  "$NODE" -e 'try { process.stdout.write(new URL(process.argv[1]).hostname) } catch { process.exit(1) }' "$1"
}

for name in BASE_URL HTTP_URL; do
  value=${!name:-}
  [ -n "$value" ] || continue
  host=$(url_host "$value") || host=
  case $host in
    localhost | 127.0.0.1 | '[::1]') ;;
    *)
      echo "e2e.sh: refusing $name=$value: the E2E specs run only against the local stand-in, never against the device" >&2
      exit 2
      ;;
  esac
done
if [ -n "${BASE_URL:-}" ] && [ "$BASE_URL" != "$base_url" ]; then
  echo "e2e.sh: BASE_URL=$BASE_URL is replaced by the stand-in's $base_url (E2E_HTTPS_PORT sets its port)"
fi
if [ -n "${HTTP_URL:-}" ] && [ "$HTTP_URL" != "$http_url" ]; then
  echo "e2e.sh: HTTP_URL=$HTTP_URL is replaced by the stand-in's $http_url (E2E_HTTP_PORT sets its port)"
fi

for port in "$https_port" "$http_port" "$backend_port"; do
  case $port in
    '' | *[!0-9]*) die "not a port number: '$port' (E2E_HTTPS_PORT, E2E_HTTP_PORT, E2E_BACKEND_PORT)" ;;
  esac
done

[ -x "$webui/node_modules/.bin/playwright" ] || die "webui/node_modules has no Playwright: run make setup (npm ci in webui)"
"$PYTHON" -c 'import fastapi, uvicorn, sqlalchemy, aiosqlite, slowapi' > /dev/null 2>&1 ||
  die "PYTHON=$PYTHON cannot import the supervisor's packages: run make setup, or set PYTHON"

mkdir -p "$logs"
# Ignored by git wherever the checkout is, whatever the repository's .gitignore says.
printf '*\n' > "$state/.gitignore"

# One run per checkout: a second one would delete the data under the first.
if ! mkdir "$lock" 2> /dev/null; then
  holder=$(cat "$lock/pid" 2> /dev/null || true)
  if [ -n "$holder" ] && ps -p "$holder" -o command= 2> /dev/null | grep -q 'e2e\.sh'; then
    die "another e2e.sh run (pid $holder) is using $state"
  fi
  rm -rf "$lock"
  mkdir "$lock" || die "cannot create $lock"
fi
echo $$ > "$lock/pid"

supervisor_pid=
front_pid=
stop() {
  for pid in $front_pid $supervisor_pid; do
    kill "$pid" 2> /dev/null
  done
  for pid in $front_pid $supervisor_pid; do
    wait "$pid" 2> /dev/null
  done
  rm -rf "$lock"
}
trap stop EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Something else on a port would answer in the stand-in's place.
for port in "$https_port" "$http_port" "$backend_port"; do
  if (exec 3<> "/dev/tcp/127.0.0.1/$port") 2> /dev/null; then
    die "port $port is in use (another stand-in?): stop it, or set E2E_HTTPS_PORT, E2E_HTTP_PORT and E2E_BACKEND_PORT"
  fi
done

if [ "${E2E_SKIP_BUILD:-}" = 1 ] && [ -f "$webui/build/index.html" ]; then
  echo "== serving the existing webui/build (E2E_SKIP_BUILD=1)"
else
  echo "== building the web UI (log: .standin/logs/build.log)"
  if ! (cd "$webui" && "$NPM" run build) > "$logs/build.log" 2>&1; then
    tail -30 "$logs/build.log" >&2
    die "the web UI build failed"
  fi
fi

if [ ! -s "$tls/cert.pem" ] || [ ! -s "$tls/key.pem" ] ||
  ! "$OPENSSL" x509 -checkend 86400 -noout -in "$tls/cert.pem" > /dev/null 2>&1; then
  echo "== making a self-signed certificate for localhost in .standin/tls"
  mkdir -p "$tls"
  if ! (umask 077 && "$OPENSSL" req -x509 -newkey rsa:2048 -nodes -sha256 -days 30 -subj /CN=localhost \
    -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1' \
    -keyout "$tls/key.pem" -out "$tls/cert.pem") > "$logs/openssl.log" 2>&1; then
    cat "$logs/openssl.log" >&2
    die "openssl could not make the certificate"
  fi
fi

echo "== starting the stand-in: $base_url (plain HTTP $http_url, supervisor on 127.0.0.1:$backend_port)"
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" "$here/run_supervisor.py" --state-dir "$state" --port "$backend_port" --fresh \
  --front-https-port "$https_port" > "$logs/supervisor.log" 2>&1 &
supervisor_pid=$!
# The served certificate that the supervisor writes (UNOS_SERVED_CERTIFICATE_DIR of run_supervisor.py, ADR-0404).
"$NODE" "$here/front.mjs" "$webui/build" "$https_port" "$http_port" "$backend_port" "$tls/cert.pem" "$tls/key.pem" \
  "$state/data/served-certificate/served.pem" > "$logs/front.log" 2>&1 &
front_pid=$!

ready=
for _ in $(seq 1 60); do
  if curl -fsk -m 2 -o /dev/null "$base_url/api/v1/health"; then
    ready=1
    break
  fi
  if ! kill -0 "$supervisor_pid" 2> /dev/null; then
    tail -40 "$logs/supervisor.log" >&2
    die "the supervisor stopped (log: .standin/logs/supervisor.log)"
  fi
  if ! kill -0 "$front_pid" 2> /dev/null; then
    tail -20 "$logs/front.log" >&2
    die "front.mjs stopped (log: .standin/logs/front.log)"
  fi
  sleep 1
done
[ -n "$ready" ] || die "the stand-in did not answer $base_url/api/v1/health within 60 seconds"

# The fresh data directory has no account yet. The test administrator is
# created as the owner creates theirs after installing: through the /setup page
# (E2E_FIRST_RUN=1 runs webui/tests/first-run.spec.ts alone, ADR-0261).
if ! curl -fsk -m 10 "$base_url/api/v1/auth/status" | grep -q '"setup_required": *true'; then
  die "the stand-in has an account already: its data directory is not fresh"
fi
cd "$webui" || die "no $webui"
echo "== creating the test administrator through the /setup page (log: .standin/logs/first-run.log)"
if ! E2E_FIRST_RUN=1 E2E_STAND_IN=1 BASE_URL="$base_url" HTTP_URL="$http_url" \
  "$webui/node_modules/.bin/playwright" test --reporter=list > "$logs/first-run.log" 2>&1; then
  tail -40 "$logs/first-run.log" >&2
  die "creating the test administrator through the /setup page failed (log: .standin/logs/first-run.log)"
fi
if [ -n "$first_run_only" ]; then
  cat "$logs/first-run.log"
  echo "== Playwright exit code 0 (tests/first-run.spec.ts alone; logs: .standin/logs)"
  exit 0
fi
echo "== created the test administrator; running Playwright (log: .standin/logs/playwright.log)"

E2E_STAND_IN=1 E2E_POOLS_BASE="$state/pools" E2E_STANDIN_STATE="$state" E2E_STANDIN_PYTHON="$PYTHON" \
  BASE_URL="$base_url" HTTP_URL="$http_url" PLAYWRIGHT_HTML_OPEN=never \
  "$webui/node_modules/.bin/playwright" test "$@" 2>&1 | tee "$logs/playwright.log"
rc=${PIPESTATUS[0]}
echo "== Playwright exit code $rc (logs: .standin/logs, report: webui/playwright-report)"
exit "$rc"
