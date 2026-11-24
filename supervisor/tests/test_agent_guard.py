"""The PreToolUse guard of Claude Code sessions, .claude/hooks/guard.py (ADR-0211).

The guard runs as Claude Code runs it: a subprocess that gets the hook input as JSON on
standard input and answers with its exit code (0 allow, 2 block with one line on stderr, 1 an
internal error that never blocks). Every case runs in a temporary workspace with a fake
dev.env, never in the repository: the guard looks for dev.env files and device backups, asks
git where a push goes and writes its denials into the project directory it is given
(tmp/guard/denials.log). Its git commands and the fixtures' get an environment without the
GIT_* variables of an outer git and without the user's git configuration.
The table holds, for each rule, the commands it was made for and the everyday variants an
agent could type by accident; what the guard cannot see is in its docstring. The commands of the
Bash tool run in the background (rule background-deadline, ADR-0335) have a table of their own.
"""

from __future__ import annotations

import ast
import json
import os
import re
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / ".claude" / "hooks" / "guard.py"
FAKE_SECRET = "fake-secret-of-the-guard-test"

# (command, the rule that blocks it or None for allowed, where it runs). Places: "ws" is a
# workspace with dev.env on branch feature, "main" a repository on branch main, "empty" a
# directory without dev.env, "tracking" a branch feat-x that tracks origin/main (as git worktree
# add -b without --no-track makes it) with push.default upstream, "matching" a repository with
# push.default matching, "checkout" a main checkout with dev.env and a device backup in
# tmp/dev/backup, "linked" its linked worktree (git worktree add) beside it, whose scripts use
# the main checkout's dev.env and backups.
CASES = [
    # Ordinary work, the examples the guard must let through.
    ("git status", None, "ws"),
    ("git push origin phase1-broken-flows", None, "ws"),
    ("scripts/dev/ssh.sh 'docker ps'", None, "ws"),
    ("scripts/dev/deploy.sh build-supervisor", None, "ws"),
    ("make check", None, "ws"),
    ("grep -n PASS supervisor/src/apps/catalog.py", None, "ws"),
    ("npx playwright test", None, "ws"),
    ("BASE_URL=https://localhost:3443 npx playwright test", None, "ws"),
    ("git log --oneline -10 && git diff --stat", None, "ws"),
    ("cd supervisor && python -m pytest tests/test_auth.py -q", None, "ws"),
    ("/usr/bin/lockf -k -t 1800 /tmp/pytest.lock make test PYTHON=/opt/py/bin/python", None, "ws"),
    ("cd webui && npm run check && npm run build", None, "ws"),
    ("scripts/dev/deploy.sh status", None, "ws"),
    ("scripts/dev/sd.sh measure check -- scripts/dev/check.sh", None, "ws"),
    ("scripts/dev/linux-tests.sh tests/test_share_repair.py -q", None, "ws"),
    ("scripts/dev/api.py GET /storage/pools", None, "ws"),
    ("scripts/dev/api.py POST /shares/smb - < share.json", None, "ws"),
    ("curl -sk https://localhost:46443/api/v1/health", None, "ws"),
    ("python3 - <<'PY'\nprint('git push origin main')\nPY", None, "ws"),
    ("git commit -m \"$(cat <<'MSG'\nfix(dev): refuse a dev.env that others can read\n\n"
     "Reboot, git push origin main and --no-verify are only words here.\nMSG\n)\"", None, "ws"),
    ("echo reboot; man shutdown", None, "ws"),
    ("grep -rn reboot supervisor/src", None, "ws"),
    ("git status # git push origin main", None, "ws"),
    # push-main
    ("git push origin main", "push-main", "ws"),
    ("git push origin HEAD:main", "push-main", "ws"),
    ("git push -u origin master", "push-main", "ws"),
    ("git push origin feature:refs/heads/main", "push-main", "ws"),
    ("git push --all origin", "push-main", "ws"),
    ("git push", "push-main", "main"),
    ("git push origin HEAD", "push-main", "main"),
    ("gh pr merge 21 --squash", "push-main", "ws"),
    ("eval 'git push origin main'", "push-main", "ws"),
    ("if [ -f x ]; then git push origin main; fi", "push-main", "ws"),
    ("git push \\\n  origin main", "push-main", "ws"),
    ("git push", None, "ws"),
    ("git push origin HEAD", None, "ws"),
    ("git push origin main-fix", None, "ws"),
    ("git push --tags", None, "main"),
    ("git push --dry-run origin main", None, "ws"),
    ("git fetch origin main && git log origin/main -1", None, "ws"),
    ("gh pr view 21", None, "ws"),
    # device-push
    ("scripts/dev/ssh.sh 'cd /opt/unos && git push origin phase1-broken-flows'", "device-push", "ws"),
    ("ssh root@192.0.2.103 git push", "device-push", "ws"),
    # force-push
    ("git push --force origin feature", "force-push", "ws"),
    ("git push -f", "force-push", "ws"),
    ("git push origin +feature", "force-push", "ws"),
    ("git push --force-with-lease origin feature", "force-push", "ws"),
    ("git push -fu origin feature", "force-push", "ws"),
    ("bash -c 'git push --force'", "force-push", "ws"),
    ("git push -u origin feature", None, "ws"),
    # delete-branch
    ("git push origin --delete feature", "delete-branch", "ws"),
    ("git push origin :feature", "delete-branch", "ws"),
    ("git push -d origin feature", "delete-branch", "ws"),
    ("git push --prune origin", "delete-branch", "ws"),
    ("git branch -d merged-feature", None, "ws"),
    # skip-hooks
    ("git commit --no-verify -m 'fix: x'", "skip-hooks", "ws"),
    ("git commit -nm 'fix: x'", "skip-hooks", "ws"),
    ("git push --no-verify origin feature", "skip-hooks", "ws"),
    ("git merge --no-verify feature", "skip-hooks", "ws"),
    ("git -c core.hooksPath=/dev/null commit -m 'feat: x'", "skip-hooks", "ws"),
    ("git config core.hooksPath .git/hooks", "skip-hooks", "ws"),
    ("git config --unset core.hooksPath", "skip-hooks", "ws"),
    ("git commit -m 'fix: no --no-verify and no -n here'", None, "ws"),
    # A green run recorded by hand would skip make check-fast in the hooks (ADR-0383).
    ("python3 scripts/green-run.py record \"$(python3 scripts/green-run.py state)\"", "skip-hooks", "ws"),
    ("scripts/green-run.py record " + "a" * 64, "skip-hooks", "ws"),
    ("python3 -I /elsewhere/scripts/green-run.py record x", "skip-hooks", "ws"),
    ("python3 scripts/green-run.py state > /tmp/state && cat /tmp/state > tmp/stop-check/green", "skip-hooks", "ws"),
    ("echo abc >> ./tmp/stop-check/green", "skip-hooks", "ws"),
    ("cp /tmp/state tmp/stop-check/green", "skip-hooks", "ws"),
    ("python3 scripts/green-run.py state | tee $PWD/tmp/stop-check/green", "skip-hooks", "ws"),
    ("cd tmp/stop-check && dd if=/tmp/state of=green", "skip-hooks", "ws"),
    ("python3 scripts/green-run.py state", None, "ws"),
    ("python3 scripts/green-run.py check && cat tmp/stop-check/green", None, "ws"),
    ("python3 scripts/green-run.py forget", None, "ws"),
    ("cp tmp/stop-check/green /tmp/green-before", None, "ws"),
    # The second review's ways around it: the folder as a target, other writers, a copy of the script.
    ("cp /tmp/green tmp/stop-check/", "skip-hooks", "ws"),
    ("cp -R /tmp/fakedir/. tmp/stop-check/", "skip-hooks", "ws"),
    ("cp -t tmp/stop-check /tmp/green", "skip-hooks", "ws"),
    ("cp --target-directory=tmp/stop-check /tmp/green", "skip-hooks", "ws"),
    ("rsync /tmp/green tmp/stop-check/", "skip-hooks", "ws"),
    ("install /tmp/green tmp/stop-check", "skip-hooks", "ws"),
    ("ln -s /tmp/green tmp/stop-check/", "skip-hooks", "ws"),
    ("mv /tmp/fakedir tmp/stop-check", "skip-hooks", "ws"),
    ("ditto /tmp/green tmp/stop-check/green", "skip-hooks", "ws"),
    ("gcp /tmp/green tmp/stop-check/green", "skip-hooks", "ws"),
    ("sed -i '' 's/.*/abc/' tmp/stop-check/green", "skip-hooks", "ws"),
    ("perl -pi -e 's/.*/abc/' tmp/stop-check/green", "skip-hooks", "ws"),
    ("tar -xf /tmp/x.tar -C tmp/stop-check", "skip-hooks", "ws"),
    ("unzip -o /tmp/x.zip -d tmp/stop-check", "skip-hooks", "ws"),
    ("curl -o tmp/stop-check/green http://localhost/x", "skip-hooks", "ws"),
    ("find /tmp -name state -exec cp {} tmp/stop-check/green \\;", "skip-hooks", "ws"),
    ("python3 -c \"open('tmp/stop-check/green','w').write('x')\"", "skip-hooks", "ws"),
    ("cat /tmp/state | python3 -c \"import sys; open(sys.argv[1],'w').write(sys.stdin.read())\" tmp/stop-check/green",
     "skip-hooks", "ws"),
    ("bash -c 'echo x > tmp/stop-check/green'", "skip-hooks", "ws"),
    ("cd tmp && echo x > stop-check/green", "skip-hooks", "ws"),
    ("echo x > tmp/stop-check//green", "skip-hooks", "ws"),
    ("echo x > tmp/stop-check/./green", "skip-hooks", "ws"),
    ("git show HEAD:README.md > tmp/stop-check/green", "skip-hooks", "ws"),
    ("python3 - record abc < scripts/green-run.py", "skip-hooks", "ws"),
    ("cat scripts/green-run.py | python3 - record abc", "skip-hooks", "ws"),
    ("python3 -c \"import runpy,sys; sys.argv=['g','record','x']; runpy.run_path('scripts/green-run.py', "
     "run_name='__main__')\"", "skip-hooks", "ws"),
    ("cp scripts/green-run.py /tmp/g.py && python3 /tmp/g.py record x", "skip-hooks", "ws"),
    ("uv run scripts/green-run.py record abc", "skip-hooks", "ws"),
    ("env python3 scripts/green-run.py record abc", "skip-hooks", "ws"),
    ("xargs -I{} python3 scripts/green-run.py record {} < /tmp/state", "skip-hooks", "ws"),
    # Reading the log and the record, and the script itself, stays allowed.
    ("cat tmp/stop-check/check-fast.log", None, "ws"),
    ("tail -n 40 tmp/stop-check/check-fast.log && ls -la tmp/stop-check", None, "ws"),
    ("rm -f tmp/stop-check/green", None, "ws"),
    ("grep -n record scripts/green-run.py", None, "ws"),
    ("sed -n 1,20p scripts/green-run.py && git diff -- scripts/green-run.py", None, "ws"),
    ("python3 scripts/green-run.py state > /tmp/state", None, "ws"),
    ("git commit -am 'feat(shares): add per-user access'", None, "ws"),
    ("git config core.hooksPath scripts/git-hooks", None, "ws"),
    ("git config --get core.hooksPath", None, "ws"),
    # e2e-target
    ("BASE_URL=https://192.0.2.103 npx playwright test", "e2e-target", "ws"),
    ("export BASE_URL=https://192.0.2.103; npx playwright test auth.spec.ts", "e2e-target", "ws"),
    ("env BASE_URL=https://192.0.2.103 npx playwright test", "e2e-target", "ws"),
    ("cd webui && BASE_URL=https://192.168.1.10 npm test", "e2e-target", "ws"),
    ("HTTP_URL=http://192.0.2.103 BASE_URL=https://localhost:3443 npx playwright test", "e2e-target", "ws"),
    ("make e2e-local BASE_URL=https://192.0.2.103", "e2e-target", "ws"),
    ("BASE_URL=$DEVICE_URL npx playwright test", "e2e-target", "ws"),
    ("BASE_URL=https://nas.local scripts/standin/e2e.sh", "e2e-target", "ws"),
    ("scripts/dev/ssh.sh 'cd /opt/unos/webui && npx playwright test'", "e2e-target", "ws"),
    ("(cd webui && BASE_URL=https://192.0.2.103 npx playwright test)", "e2e-target", "ws"),
    ("BASE_URL=https://127.0.0.1:46443 npx playwright test auth.spec.ts", None, "ws"),
    ("BASE_URL=https://[::1]:46443 HTTP_URL=http://localhost:46080 npx playwright test", None, "ws"),
    ("BASE_URL=https://webui npx playwright test --reporter=list", None, "ws"),
    ("make e2e-local", None, "ws"),
    ("scripts/standin/e2e.sh tests/shares.spec.ts --grep Seeded", None, "ws"),
    ("npx playwright install chromium && npx playwright show-report", None, "ws"),
    ("BASE_URL=https://192.0.2.103 npm run build", None, "ws"),
    # disk-wipe
    ("mkfs.btrfs -f /dev/sdb", "disk-wipe", "ws"),
    ("sudo -u root -- mkfs.ext4 /dev/sdc", "disk-wipe", "ws"),
    ("scripts/dev/ssh.sh 'wipefs -a /dev/sda'", "disk-wipe", "ws"),
    ("sudo dd if=/dev/zero of=/dev/mmcblk1 bs=1M", "disk-wipe", "ws"),
    ("cat image.img > /dev/sdb", "disk-wipe", "ws"),
    ("tee /dev/sdb < image.img", "disk-wipe", "ws"),
    ("blkdiscard /dev/sda", "disk-wipe", "ws"),
    ("sgdisk --zap-all /dev/sda", "disk-wipe", "ws"),
    ("echo ',,L' | sfdisk /dev/sdb", "disk-wipe", "ws"),
    ("fdisk /dev/sdb", "disk-wipe", "ws"),
    ("parted -s /dev/sdb mklabel gpt", "disk-wipe", "ws"),
    ("shred -n 1 /dev/sdb", "disk-wipe", "ws"),
    ("cryptsetup luksFormat /dev/sdb", "disk-wipe", "ws"),
    ("btrfs device remove /dev/sdb /mnt/pools/data", "disk-wipe", "ws"),
    ("scripts/dev/ssh.sh 'btrfs dev del /dev/sdb /mnt/pools/data'", "disk-wipe", "ws"),
    ("diskutil eraseDisk APFS Scratch disk4", "disk-wipe", "ws"),
    ("scripts/dev/api.py POST /storage/pools - < pool.json", "disk-wipe", "ws"),
    ("scripts/dev/api.py DELETE /storage/pools/data", "disk-wipe", "ws"),
    ("dd if=/dev/zero of=/tmp/scratch bs=1M count=1", None, "ws"),
    ("scripts/dev/ssh.sh 'cat > /dev/shm/unos/x' < notes.txt", None, "ws"),
    ("scripts/dev/ssh.sh 'wipefs /dev/sda; lsblk -J; sgdisk -p /dev/sda; sfdisk -l /dev/sda'", None, "ws"),
    ("fdisk -l && parted -l && parted /dev/sda print", None, "ws"),
    ("shred -u notes.txt", None, "ws"),
    ("btrfs device stats /mnt/pools/data && btrfs filesystem show", None, "ws"),
    ("cryptsetup status cryptdata && diskutil list", None, "ws"),
    ("echo done > /dev/null 2>&1", None, "ws"),
    # device-docker
    ("scripts/dev/ssh.sh 'cd /opt/unos/deploy && docker compose build supervisor'", "device-docker", "ws"),
    ("scripts/dev/ssh.sh 'docker pull alpine'", "device-docker", "ws"),
    ("scripts/dev/ssh.sh 'docker run --rm alpine true'", "device-docker", "ws"),
    ("ssh root@192.0.2.103 docker system prune -af", "device-docker", "ws"),
    ("scripts/dev/ssh.sh 'docker image prune -a'", "device-docker", "ws"),
    ("dev.sh 'docker build -t x .'", "device-docker", "ws"),
    ("scripts/dev/ssh.sh 'docker compose up -d'", "device-docker", "ws"),
    ("scripts/dev/ssh.sh 'sudo bash -c \"docker volume prune -f\"'", "device-docker", "ws"),
    ("scripts/dev/ssh.sh bash -s <<'SH'\ncd /opt/unos/deploy\ndocker compose pull\nSH", "device-docker", "ws"),
    ("ssh -o StrictHostKeyChecking=no -p 22 root@192.0.2.103 \"bash -c 'docker builder prune'\"",
     "device-docker", "ws"),
    ("DOCKER_HOST=ssh://root@192.0.2.103 docker build .", "device-docker", "ws"),
    ("docker -H ssh://root@192.0.2.103 pull alpine", "device-docker", "ws"),
    ("scripts/dev/sd.sh measure build -- scripts/dev/ssh.sh 'docker compose build'", "device-docker", "ws"),
    ("scripts/dev/api.py POST /apps/install - < app.json", "device-docker", "ws"),
    # api.py's options that take a value: the value is not the method.
    ("scripts/dev/api.py --param x=y POST /apps/install - < app.json", "device-docker", "ws"),
    ("scripts/dev/api.py --timeout 3600 POST /apps/install - < app.json", "device-docker", "ws"),
    ("scripts/dev/api.py --repeat 5 POST /apps/install - < app.json", "device-docker", "ws"),
    ("scripts/dev/api.py --rep 5 DELETE /storage/pools/data", "disk-wipe", "ws"),
    ("scripts/dev/api.py --pa x=y --time 60 post apps/install -", "device-docker", "ws"),
    ("scripts/dev/api.py --raw --param=x=y --timeout=60 POST /apps/install -", "device-docker", "ws"),
    ("scripts/dev/api.py --param x=y -- POST /containers/images/pull -", "device-docker", "ws"),
    ("scripts/dev/sd.sh measure install -- sh -c 'scripts/dev/api.py --timeout 3600 POST /apps/install - < a.json'",
     "device-docker", "ws"),
    ("scripts/dev/api.py --timeout 600 GET /apps/installed --param limit=5", None, "ws"),
    ("scripts/dev/api.py --repeat 25 GET /containers --param all=true", None, "ws"),
    ("scripts/dev/api.py --param x=POST GET /apps/install", None, "ws"),
    ("scripts/dev/ssh.sh 'docker images; docker compose ps; docker logs unos-supervisor --tail 50'", None, "ws"),
    ("scripts/dev/ssh.sh 'docker compose -f /opt/unos/deploy/docker-compose.yml logs -f supervisor'",
     None, "ws"),
    ("docker compose -f deploy/docker-compose.test.yml build", None, "ws"),
    ("docker run --rm -v \"$PWD/supervisor:/s:ro\" python:3.11-slim-trixie sh -c 'apt-get -qq update'",
     None, "ws"),
    # device-packages
    ("scripts/dev/ssh.sh 'apt-get install -y jq'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'apt upgrade'", "device-packages", "ws"),
    ("ssh root@192.0.2.103 'DEBIAN_FRONTEND=noninteractive apt-get -y dist-upgrade'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'apt-get remove samba'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'apt-get update'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'pip install requests'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'python3 -m pip install --user x'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'dpkg -i /dev/shm/x.deb'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'docker exec unos-supervisor pip install x'", "device-packages", "ws"),
    ("timeout 30 scripts/dev/ssh.sh 'apt install x'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'apt list --installed; pip list; dpkg -l samba; apt-get -s install jq'", None, "ws"),
    ("pip install -r supervisor/requirements-dev.txt", None, "ws"),
    # power
    ("scripts/dev/ssh.sh reboot", "power", "ws"),
    ("scripts/dev/ssh.sh 'sudo shutdown -r now'", "power", "ws"),
    ("ssh root@192.0.2.103 'systemctl poweroff'", "power", "ws"),
    ("sudo halt", "power", "ws"),
    ("scripts/dev/ssh.sh bash <<< 'reboot'", "power", "ws"),
    ("scripts/dev/api.py POST /system/reboot", "power", "ws"),
    ("python3 scripts/dev/api.py POST /power/shutdown", "power", "ws"),
    ("scripts/dev/api.py --timeout 30 POST /system/reboot", "power", "ws"),
    ("scripts/dev/ssh.sh 'systemctl restart smbd && shutdown -c'", None, "ws"),
    # secret-argv
    # Put together, so that the secret scan of the history finds no password in this file (ADR-0445).
    ("ssh" + "pass -p secret ssh root@192.0.2.103 true", "secret-argv", "ws"),
    ("ssh" + "pass -psecret ssh root@192.0.2.103", "secret-argv", "ws"),
    ("SSH=\"sshpass -p 'x' ssh root@192.0.2.103\"; $SSH 'docker ps'", "secret-argv", "ws"),
    ("curl -sku admin:hunter2 https://localhost/api/v1/auth/me", "secret-argv", "ws"),
    ("curl --user=admin:hunter2 https://localhost/", "secret-argv", "ws"),
    ("curl -k 'https://admin:hunter2@127.0.0.1/api/v1/health'", "secret-argv", "ws"),
    ("mysql --password=hunter2", "secret-argv", "ws"),
    ("docker login -u x -p hunter2", "secret-argv", "ws"),
    ("UNOS_DEV_ADMIN_PASSWORD=hunter2 scripts/dev/api.py GET /system/info", "secret-argv", "ws"),
    ("export GITHUB_TOKEN=ghp_abc123", "secret-argv", "ws"),
    ("TRANSMISSION_PASS=x; echo done", "secret-argv", "ws"),
    ("docker run -e TRANSMISSION_PASS=hunter2 linuxserver/transmission", "secret-argv", "ws"),
    ("make setup UNOS_JWT_SECRET=abc", "secret-argv", "ws"),
    ("smbclient //127.0.0.1/DATA -U alice%hunter2", "secret-argv", "ws"),
    ("git remote add origin https://user:ghp_x@github.com/und3f1ned/unos.git", "secret-argv", "ws"),
    ("curl -u admin https://localhost/ && smbclient -L localhost -U alice", None, "ws"),
    ("SSH_ASKPASS=scripts/dev/askpass.sh SSH_ASKPASS_REQUIRE=force ssh root@192.0.2.103 true", None, "ws"),
    ("TRANSMISSION_PASS= scripts/dev/check.sh", None, "ws"),
    ("TOKEN_FILE=/tmp/token PASSWORD_MIN_LENGTH=8 CACHE_BYPASS=1 make test", None, "ws"),
    ("GH_TOKEN=$(cat ~/.config/gh-token) gh pr view", None, "ws"),
    ("docker login -u x --password-stdin < token.txt", None, "ws"),
    ("docker run -e TRANSMISSION_PASS linuxserver/transmission", None, "ws"),
    # dev-env
    ("cat dev.env", "dev-env", "ws"),
    ("head -n 3 ./dev.env", "dev-env", "ws"),
    ("grep PASS dev.env", "dev-env", "ws"),
    ("sed -n 1p dev.env", "dev-env", "ws"),
    ("awk -F= '{print $1}' dev.env", "dev-env", "ws"),
    ("base64 < dev.env", "dev-env", "ws"),
    ("less dev.env", "dev-env", "ws"),
    ("tail -f /opt/unos/dev.env", "dev-env", "ws"),
    ("xxd dev.env | head", "dev-env", "ws"),
    ("python3 -c \"print(open('dev.env').read())\"", "dev-env", "ws"),
    ("python3 - <<'PY'\nprint(open('dev.env').read())\nPY", "dev-env", "ws"),
    ("source dev.env && echo $UNOS_DEV_SSH_PASSWORD", "dev-env", "ws"),
    ("echo \"$(cat dev.env)\"", "dev-env", "ws"),
    ("diff <(cat dev.env) dev.env.example", "dev-env", "ws"),
    ("cat *.env", "dev-env", "ws"),
    ("cp dev.env /tmp/x", "dev-env", "ws"),
    ("scp dev.env root@192.0.2.103:/dev/shm/", "dev-env", "ws"),
    ("cp dev.env.example dev.env", "dev-env", "ws"),
    ("echo X=1 >> dev.env", "dev-env", "ws"),
    ("rm dev.env", "dev-env", "ws"),
    ("git add -f dev.env", "dev-env", "ws"),
    ("grep -rn PASSWORD .", "dev-env", "ws"),
    ("grep -r TOKEN", "dev-env", "ws"),
    ("rg -uu PASSWORD", "dev-env", "ws"),
    ("git grep --no-index -n PASS", "dev-env", "ws"),
    ("rsync -a ./ root@192.0.2.103:/opt/unos/", "dev-env", "ws"),
    ("ls -la dev.env && stat dev.env && chmod 600 dev.env && test -f dev.env", None, "ws"),
    ("cat dev.env.example", None, "ws"),
    ("git check-ignore -v dev.env && git grep -n dev.env", None, "ws"),
    ("grep -n dev.env .gitignore && grep -n '*.env' .gitignore", None, "ws"),
    ("sed -n '/dev.env/p' .gitignore", None, "ws"),
    ("grep -rn PASSWORD sub && rg -n PASSWORD", None, "ws"),
    ("grep -rn --exclude=dev.env PASS . && grep -rn --include='*.py' PASS .", None, "ws"),
    ("find . -name dev.env && ls -la dev.env*", None, "ws"),
    ("cat > notes.md <<'MD'\ndev.env holds the secrets\nMD", None, "ws"),
    ("cp dev.env.example dev.env && chmod 600 dev.env", None, "empty"),
    ("cp -r sub /tmp/copy", None, "ws"),
    # Everyday variants of each rule: other spellings and argument orders, wrappers, relative and
    # absolute paths.
    ("git push origin HEAD:refs/heads/main", "push-main", "ws"),
    ("git push --set-upstream origin main", "push-main", "ws"),
    ("git push origin heads/main", "push-main", "ws"),
    ("git push origin feature main", "push-main", "ws"),
    ("git push origin @:main", "push-main", "ws"),
    ("git push origin main --force", "push-main", "ws"),
    ("git -C . push upstream main:main", "push-main", "ws"),
    ("/usr/bin/git push origin main", "push-main", "ws"),
    ("sudo git push origin main", "push-main", "ws"),
    ("env GIT_TRACE=1 git push origin main", "push-main", "ws"),
    ("sh -c 'git push origin main'", "push-main", "ws"),
    ("bash -lc \"git push origin main\"", "push-main", "ws"),
    ("cd /tmp && git push origin main", "push-main", "ws"),
    ("git push --mirror origin", "push-main", "ws"),
    ("git push -u origin HEAD", "push-main", "main"),
    ("gh pr merge --squash --delete-branch", "push-main", "ws"),
    ("git push -n origin main && git pull origin main", None, "ws"),
    ("git push origin maintenance && git push origin heads/feature", None, "ws"),
    ("./scripts/dev/ssh.sh 'git push'", "device-push", "ws"),
    ("bash scripts/dev/ssh.sh 'cd /opt/unos && git push origin x'", "device-push", "ws"),
    ("ssh -t root@192.0.2.103 'git -C /opt/unos push'", "device-push", "ws"),
    ("/abs/path/scripts/dev/ssh.sh \"sudo -u root git push\"", "device-push", "ws"),
    ("git push origin feature -f", "force-push", "ws"),
    ("git push --force-if-includes origin feature", "force-push", "ws"),
    ("git push origin +HEAD:feature", "force-push", "ws"),
    ("sh -c \"git push -f origin feature\"", "force-push", "ws"),
    ("git push --delete origin feature", "delete-branch", "ws"),
    ("git push origin :refs/heads/feature", "delete-branch", "ws"),
    ("git commit -m 'fix: x' --no-verify", "skip-hooks", "ws"),
    ("git commit -am 'fix: x' -n", "skip-hooks", "ws"),
    ("git commit -anm 'fix: x'", "skip-hooks", "ws"),
    ("git commit --no-veri -m 'fix: x'", "skip-hooks", "ws"),
    ("git commit --amend --no-edit --no-verify", "skip-hooks", "ws"),
    ("bash -c 'git commit -n -m \"fix: x\"'", "skip-hooks", "ws"),
    ("git rebase --no-verify main", "skip-hooks", "ws"),
    ("git -c core.hookspath= commit -m 'fix: x'", "skip-hooks", "ws"),
    ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git commit", "skip-hooks", "ws"),
    ("git config core.hooksPath ''", "skip-hooks", "ws"),
    ("git config --global core.hooksPath /tmp/scripts/git-hooks", "skip-hooks", "ws"),
    ("git config unset core.hooksPath", "skip-hooks", "ws"),
    ("git config core.hooksPath \"$PWD/other-hooks\"", "skip-hooks", "ws"),
    ("git commit -m\"-n\" && git commit -m 'fix: x' -m 'about --no-verify'", None, "ws"),
    ("git config core.hooksPath \"$PWD/scripts/git-hooks\" && git -c core.hooksPath=scripts/git-hooks/ log", None,
     "ws"),
    ("BASE_URL=\"https://192.0.2.103\" npx playwright test", "e2e-target", "ws"),
    ("export BASE_URL=https://192.0.2.103 && npx playwright test", "e2e-target", "ws"),
    ("sudo BASE_URL=https://192.0.2.103 npx playwright test", "e2e-target", "ws"),
    ("BASE_URL=https://192.0.2.103 npm run test:auth", "e2e-target", "ws"),
    ("BASE_URL=https://192.0.2.103 npm --prefix webui test -- --grep x", "e2e-target", "ws"),
    ("BASE_URL=https://192.0.2.103 ./node_modules/.bin/playwright test", "e2e-target", "ws"),
    ("BASE_URL=https://192.0.2.103 npx --yes playwright test", "e2e-target", "ws"),
    ("BASE_URL=https://192.0.2.103 pnpm exec playwright test", "e2e-target", "ws"),
    ("BASE_URL=https://192.0.2.103 yarn playwright test", "e2e-target", "ws"),
    ("BASE_URL=https://192.0.2.103 bash -c 'npx playwright test'", "e2e-target", "ws"),
    ("cd webui; BASE_URL=https://192.0.2.103 npm run test:local", "e2e-target", "ws"),
    ("scripts/dev/ssh.sh 'cd /opt/unos/webui && npm test'", "e2e-target", "ws"),
    ("BASE_URL=https://localhost:46443 npm run test:auth && npm run test:local", None, "ws"),
    ("BASE_URL=https://192.0.2.103 curl -k \"$BASE_URL/api/v1/health\"", None, "ws"),
    ("mkfs -t ext4 /dev/sdb", "disk-wipe", "ws"),
    ("/sbin/mkfs.btrfs -L data /dev/sdb", "disk-wipe", "ws"),
    ("sudo wipefs --all /dev/sda", "disk-wipe", "ws"),
    ("wipefs -af /dev/sda", "disk-wipe", "ws"),
    ("dd of=/dev/sda if=image.img bs=4M", "disk-wipe", "ws"),
    ("sudo dd if=x.img of=/dev/rdisk4 bs=1m", "disk-wipe", "ws"),
    ("pv image.img > /dev/sdb", "disk-wipe", "ws"),
    ("echo x | sudo tee /dev/sda", "disk-wipe", "ws"),
    ("sudo sh -c 'cat x.img > /dev/sdb'", "disk-wipe", "ws"),
    ("parted /dev/sdb --script mklabel gpt", "disk-wipe", "ws"),
    ("cryptsetup -q luksFormat /dev/sdb", "disk-wipe", "ws"),
    ("btrfs device delete missing /mnt/pools/data", "disk-wipe", "ws"),
    ("ssh root@192.0.2.103 mkfs.btrfs -f /dev/sdb", "disk-wipe", "ws"),
    ("scripts/dev/api.py post storage/pools -", "disk-wipe", "ws"),
    ("scripts/dev/api.py POST /api/v1/storage/pools - < pool.json", "disk-wipe", "ws"),
    ("python3 scripts/dev/api.py DELETE '/storage/pools/data?force=true'", "disk-wipe", "ws"),
    ("cd scripts/dev && ./api.py DELETE /storage/pools/data", "disk-wipe", "ws"),
    ("scripts/dev/api.py --param force=true DELETE /storage/pools/data", "disk-wipe", "ws"),
    ("scripts/dev/api.py GET /storage/pools/data && lsblk -J -b -o NAME,SIZE && blkid", None, "ws"),
    ("scripts/dev/ssh.sh 'cd /opt/unos/deploy; docker-compose up -d'", "device-docker", "ws"),
    ("scripts/dev/ssh.sh docker run --rm alpine true", "device-docker", "ws"),
    ("scripts/dev/ssh.sh 'docker container run alpine'", "device-docker", "ws"),
    ("scripts/dev/ssh.sh 'docker image build -t x .'", "device-docker", "ws"),
    ("scripts/dev/ssh.sh 'docker compose --profile test up'", "device-docker", "ws"),
    ("scripts/dev/ssh.sh 'docker compose up --build -d supervisor'", "device-docker", "ws"),
    ("scripts/dev/ssh.sh 'docker network prune -f'", "device-docker", "ws"),
    ("scripts/dev/ssh.sh 'sudo docker pull alpine'", "device-docker", "ws"),
    ("scripts/dev/ssh.sh 'docker save x | docker load'", "device-docker", "ws"),
    ("scripts/dev/ssh.sh bash -lc \"'docker run x'\"", "device-docker", "ws"),
    ("docker --context device run alpine", "device-docker", "ws"),
    ("DOCKER_HOST=ssh://root@192.0.2.103 docker compose up -d", "device-docker", "ws"),
    ("scripts/dev/ssh.sh 'docker ps -a; docker inspect unos-supervisor; docker compose ps'", None, "ws"),
    ("scripts/dev/ssh.sh 'apt-get -y install jq'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'sudo apt update && sudo apt upgrade -y'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'apt-get purge -y samba'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'pip3 install --break-system-packages x'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'uv pip install x'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'dpkg --install x.deb'", "device-packages", "ws"),
    ("ssh root@192.0.2.103 apt-get install -y jq", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'cd /opt/unos/webui && npm ci'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'npm install -g pnpm'", "device-packages", "ws"),
    ("scripts/dev/ssh.sh 'apt-cache policy samba; dpkg -s samba; npm ls'", None, "ws"),
    ("cd webui && npm ci && npm install", None, "ws"),
    ("sudo reboot now", "power", "ws"),
    ("shutdown now", "power", "ws"),
    ("sudo systemctl reboot", "power", "ws"),
    ("systemctl isolate reboot.target", "power", "ws"),
    ("ssh root@192.0.2.103 'sudo poweroff'", "power", "ws"),
    ("scripts/dev/ssh.sh 'echo b > /proc/sysrq-trigger'", "power", "ws"),
    ("echo o | sudo tee /proc/sysrq-trigger", "power", "ws"),
    ("scripts/dev/api.py post power/reboot", "power", "ws"),
    ("scripts/dev/ssh.sh 'uptime; last reboot | head'", None, "ws"),
    ("sshpass -p \"$PW\" ssh root@192.0.2.103", "secret-argv", "ws"),
    ("SSHPASS=x sshpass -e ssh root@192.0.2.103", "secret-argv", "ws"),
    ("curl -u 'admin:pw' https://localhost/", "secret-argv", "ws"),
    ("wget --http-password=pw https://localhost/", "secret-argv", "ws"),
    ("smbclient //127.0.0.1/DATA --password pw", "secret-argv", "ws"),
    ("UNOS_DEV_SSH_PASSWORD='x' scripts/dev/ssh.sh true", "secret-argv", "ws"),
    ("sudo env TOKEN=abc ./run.sh", "secret-argv", "ws"),
    ("curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.e30.x' https://localhost:46443/api/v1/auth/me",
     "secret-argv", "ws"),
    ("curl --header \"Authorization: Basic YWRtaW46cHc=\" https://localhost/", "secret-argv", "ws"),
    ("zip -P pw -r /tmp/x.zip sub", "secret-argv", "ws"),
    ("curl -H \"Authorization: Bearer $TOKEN\" -H 'Content-Type: application/json' https://localhost/", None, "ws"),
    ("grep -rn 'Authorization: Bearer' supervisor/src", None, "ws"),
    ("cat \"$PWD/dev.env\"", "dev-env", "ws"),
    ("cat ../ws/dev.env", "dev-env", "ws"),
    ("/bin/cat dev.env", "dev-env", "ws"),
    ("sudo cat dev.env", "dev-env", "ws"),
    ("bash -c 'cat dev.env'", "dev-env", "ws"),
    ("nl dev.env", "dev-env", "ws"),
    ("cat dev.e*", "dev-env", "ws"),
    ("cat {dev,x}.env", "dev-env", "ws"),
    ("cp dev.env{,.bak}", "dev-env", "ws"),
    ("mv dev.env{,.old}", "dev-env", "ws"),
    ("diff dev.env.example dev.env", "dev-env", "ws"),
    ("git diff --no-index dev.env.example dev.env", "dev-env", "ws"),
    ("while read l; do echo \"$l\"; done < dev.env", "dev-env", "ws"),
    ("set -a; . ./dev.env; set +a", "dev-env", "ws"),
    ("grep -rn PASS ./", "dev-env", "ws"),
    ("grep -rn PASS \"$PWD\"", "dev-env", "ws"),
    ("grep -R PASS", "dev-env", "ws"),
    ("ack PASS", "dev-env", "ws"),
    ("cp -a . /tmp/backup", "dev-env", "ws"),
    ("scp -r . root@192.0.2.103:/dev/shm/x", "dev-env", "ws"),
    ("tar czf /tmp/x.tgz .", "dev-env", "ws"),
    ("tar -czf /tmp/x.tgz ./", "dev-env", "ws"),
    ("tar --create --gzip --file=/tmp/x.tgz .", "dev-env", "ws"),
    ("tar -cf - . | ssh root@192.0.2.103 'tar xf - -C /dev/shm'", "dev-env", "ws"),
    ("tar -C .. -czf /tmp/x.tgz ws", "dev-env", "ws"),
    ("cd .. && tar czf /tmp/x.tgz ws", "dev-env", "ws"),
    ("tar --exclude='*.py' -czf /tmp/x.tgz .", "dev-env", "ws"),
    ("tar cf /tmp/x.tar *", "dev-env", "ws"),
    ("zip -r /tmp/x.zip .", "dev-env", "ws"),
    ("zip -qr /tmp/x.zip ./ -x '*.py'", "dev-env", "ws"),
    ("cd .. && zip -r /tmp/x.zip ws", "dev-env", "ws"),
    ("zip /tmp/x.zip *", "dev-env", "ws"),
    ("zip -r /tmp/x.zip . -i '*.env'", "dev-env", "ws"),
    ("zip -r /tmp/x.zip . -i '*.py' && zip -r /tmp/y.zip . -x@exclude.lst", None, "ws"),
    ("tar czf /tmp/x.tgz --exclude=dev.env . && tar --exclude='*.env' -czf /tmp/y.tgz .", None, "ws"),
    ("tar -cf /tmp/x.tar --exclude-vcs-ignores .", None, "ws"),
    ("tar czf /tmp/x.tgz sub && tar -tzf /tmp/x.tgz && tar -xzf /tmp/x.tgz -C /tmp/out", None, "ws"),
    ("git ls-files | tar -czf /tmp/x.tgz -T - && git archive -o /tmp/y.tgz HEAD", None, "ws"),
    ("zip -r /tmp/x.zip . -x dev.env && zip -r /tmp/y.zip sub && unzip -l /tmp/x.zip", None, "ws"),
    ("cd sub && tar czf /tmp/x.tgz . && zip /tmp/x.zip .", None, "ws"),
    ("cp dev.env{.example,.example.bak} && ls dev.env{,.example} && cp -r sub{,.copy}", None, "ws"),
    ("grep -rn PASS sub && chmod 600 ./dev.env && ls -l \"$PWD/dev.env\"", None, "ws"),
    ("tar czf /tmp/x.tgz .", None, "empty"),
    # A bare push goes where git's configuration sends it: a worktree's branch made from
    # origin/main tracks main, and push.default upstream pushes there.
    ("git push", "push-main", "tracking"),
    ("git push origin", "push-main", "tracking"),
    ("git push -u origin", "push-main", "tracking"),
    ("git -c branch.feature.remote=origin -c branch.feature.merge=refs/heads/main -c push.default=upstream push",
     "push-main", "ws"),
    ("git push", "push-main", "matching"),
    ("git -c push.default=matching push", "push-main", "ws"),
    ("git -c remote.origin.push=HEAD:refs/heads/main push", "push-main", "ws"),
    ("git -c 'remote.origin.push=refs/heads/*:refs/heads/*' push origin", "push-main", "ws"),
    ("git push origin HEAD && git push origin feat-x && git push -u origin HEAD:feat-x", None, "tracking"),
    ("git -c push.default=simple push && git -c push.default=upstream push", None, "ws"),
    ("git -c push.default=simple push", None, "tracking"),
    ("git push origin feature && git push --tags", None, "matching"),
    ("git -c remote.origin.push=refs/heads/feature:refs/heads/feature push", None, "ws"),
    # Names of secrets beyond PASS, TOKEN and SECRET: API keys, private keys, access keys and
    # auth keys (Tailscale's TS_AUTHKEY), also in camelCase.
    ("OPENAI_API_KEY=hunter2 ./run.sh", "secret-argv", "ws"),
    ("TS_AUTHKEY=hunter2 docker compose up -d", "secret-argv", "ws"),
    ("export DEPLOY_PRIVATE_KEY=hunter2", "secret-argv", "ws"),
    ("MINIO_ACCESS_KEY=hunter2 TS_AUTH_KEY=hunter2 ./run.sh", "secret-argv", "ws"),
    ("env SONARR_APIKEY=hunter2 ./run.sh", "secret-argv", "ws"),
    ("apiKey=hunter2 node run.js", "secret-argv", "ws"),
    ("TS_AUTHKEY= API_KEY_FILE=/run/secrets/key AWS_ACCESS_KEY_ID=AKIAEXAMPLE ./run.sh", None, "ws"),
    ("PUBLIC_KEY=ssh-ed25519 SORT_KEY=name PRIVATE_KEY_PATH=/x ./run.sh", None, "ws"),
    # A linked worktree's scripts use the main checkout's dev.env and backups: a search, copy or
    # archive of a folder that holds the main checkout reaches them.
    ("grep -rn PASS ..", "dev-env", "linked"),
    ("rg --no-ignore PASS ..", "dev-env", "linked"),
    ("tar czf /tmp/x.tgz ..", "dev-env", "linked"),
    ("cp -r .. /tmp/copy", "dev-env", "linked"),
    ("zip -r /tmp/x.zip ..", "dev-env", "linked"),
    ("grep -rn PASS ../checkout", "dev-env", "linked"),
    ("cat ../checkout/dev.env", "dev-env", "linked"),
    ("tar czf /tmp/x.tgz --exclude=dev.env ..", "dev-env", "linked"),
    ("cat ../checkout/tmp/dev/backup/20261107T120000Z/meta.txt", "dev-env", "linked"),
    ("grep -rn PASS . && grep -rn PASS ../checkout/sub && rg -n PASS ..", None, "linked"),
    ("tar czf /tmp/x.tgz --exclude=dev.env --exclude=tmp .. && ls -la ../checkout/tmp/dev/backup", None,
     "linked"),
    # The device backups (tmp/dev/backup, ADR-0223) hold the device's JWT secret, password
    # hashes and database: only deploy.sh rollback reads them.
    ("tar -xf tmp/dev/backup/20261107T120000Z/device-state.tar -C /tmp/out", "dev-env", "checkout"),
    ("tar -tvf tmp/dev/backup/20261107T120000Z/device-state.tar", "dev-env", "checkout"),
    ("strings tmp/dev/backup/*/device-state.tar | grep -i secret", "dev-env", "checkout"),
    ("cat tmp/dev/backup/20261107T120000Z/meta.txt", "dev-env", "checkout"),
    ("base64 < tmp/dev/backup/20261107T120000Z/device-state.tar", "dev-env", "checkout"),
    ("cp -r tmp/dev/backup /tmp/b", "dev-env", "checkout"),
    ("scp tmp/dev/backup/20261107T120000Z/device-state.tar root@192.0.2.103:/dev/shm/", "dev-env", "checkout"),
    ("grep -rn SECRET tmp", "dev-env", "checkout"),
    ("cd tmp/dev && tar czf /tmp/x.tgz backup", "dev-env", "checkout"),
    ("git add -f tmp/dev/backup && git commit -m 'chore: keep the backup'", "dev-env", "checkout"),
    ("grep -rn --exclude=dev.env PASS .", "dev-env", "checkout"),
    ("python3 -c \"print(open('tmp/dev/backup/x/meta.txt').read())\"", "dev-env", "checkout"),
    ("scripts/dev/deploy.sh backup && scripts/dev/deploy.sh rollback", None, "checkout"),
    ("ls -la tmp/dev/backup && du -sh tmp/dev/backup && find tmp/dev/backup -name '*.tar'", None, "checkout"),
    ("tar -cf /tmp/x.tar --exclude-vcs-ignores . && rsync -a --exclude=dev.env --exclude=tmp/ ./ /tmp/copy/", None,
     "checkout"),
    ("grep -rn PASS --exclude=dev.env --exclude-dir=tmp . && git check-ignore -v tmp/dev/backup", None, "checkout"),
    # The lexer reads a line as bash and zsh do: the substitutions inside arithmetic run,
    # <<$'EOF' is a quoted heredoc that ends at EOF, and a carriage return is part of a word.
    ("echo $(( $(git push origin main) + 1 ))", "push-main", "ws"),
    ("cat <<$'EOF'\nbody\nEOF\ngit push origin main", "push-main", "ws"),
    ("git status \r#; git push origin main", "push-main", "ws"),
    ("echo $(( 6 * 7 )) && cat <<$'EOF'\ngit push origin main\nEOF", None, "ws"),
]

RULES = ("push-main", "device-push", "force-push", "delete-branch", "skip-hooks", "e2e-target", "disk-wipe",
         "device-docker", "device-packages", "power", "secret-argv", "dev-env")

# Claude Code wakes the session when a command of the Bash tool run with run_in_background ends,
# so such a command is one call of timeout with a deadline of at most 7200 s, with loops and
# pipelines inside `timeout <seconds> bash -c '...'` (ADR-0335). (command, the rule that blocks
# it or None, run_in_background), each run in the workspace "ws".
BACKGROUND_RULE = "background-deadline"
BACKGROUND_CASES = [
    # The stall of 2026-11-10: a wait loop written for bash ran in the Bash tool's zsh, whose [
    # does not know \>, so its test failed at every pass and the loop never ended.
    ("until [ \"$(date -u +%H%M%S)\" \\> \"222340\" ]; do sleep 15; done; echo ready", BACKGROUND_RULE, True),
    ("sleep 600", BACKGROUND_RULE, True),
    ("make check > /tmp/check.log 2>&1", BACKGROUND_RULE, True),
    ("nohup make check > /tmp/check.log 2>&1", BACKGROUND_RULE, True),
    ("env timeout 60 x", BACKGROUND_RULE, True),
    ("/usr/local/bin/timeout 60 x", BACKGROUND_RULE, True),
    # One call of timeout, after cd DIR && if need be, with assignments and redirections.
    ("timeout 600 make check > /tmp/check.log 2>&1", None, True),
    ("cd sub && timeout 1800 make check > /tmp/check.log 2>&1", None, True),
    ("cd sub && cd .. && timeout 60 x", None, True),
    ("# the gate\ncd sub &&\n  timeout 1800 make check > /tmp/check.log 2>&1", None, True),
    ("timeout 900 bash -c 'until [ \"$(date -u +%H%M%S)\" \\> \"222340\" ]; do sleep 15; done; echo ready'",
     None, True),
    ("timeout 60 bash -c 'a | b; c && d || e; echo `date` $(date)'", None, True),
    ("timeout 60 bash -c \"until false; do sleep 1; done; echo $HOME\"", None, True),
    ("E2E_HTTPS_PORT=46443 timeout 2400 scripts/standin/e2e.sh tests/shares.spec.ts > /tmp/e2e.log 2>&1", None, True),
    ("timeout -k 10 600 make check", None, True),
    ("timeout --kill-after=10 600 make check", None, True),
    ("timeout -k 10 7190 x", None, True),
    ("timeout -s KILL --preserve-status -v 60 x", None, True),
    ("timeout --signal INT --kill-after 5 -k5 -sTERM -p --verbose -- 60 x", None, True),
    ("timeout --signal=SIGHUP -s 9 -s sigquit 60 x", None, True),
    ("gtimeout 60 x", None, True),
    ("/opt/homebrew/bin/timeout 60 x", None, True),
    ("/opt/homebrew/bin/gtimeout 60 x", None, True),
    ("timeout 2h x", None, True),
    ("timeout 120m x &> /tmp/x.log < /dev/null", None, True),
    ("timeout 1.5m x 2>&1 > /tmp/x.log", None, True),
    ("timeout 0.5 x", None, True),
    ("timeout .5 x", None, True),
    ("timeout 60. x", None, True),
    ("timeout 600 make check # the gate\n", None, True),
    ("timeout 600 make check;", None, True),
    # A quoted heredoc is the input of the command that timeout runs.
    ("timeout 600 bash <<'SH'\nuntil false; do sleep 1; done\nSH", None, True),
    # A line with an unbalanced quote is a syntax error: the shell runs nothing and ends at once.
    ("timeout 60 bash -c 'unbalanced", None, True),
    # Nothing outside timeout: a second command, a pipe, the shell's own background.
    ("timeout 60 a; b", BACKGROUND_RULE, True),
    ("timeout 60 a && b", BACKGROUND_RULE, True),
    ("timeout 2h x && timeout 60 y", BACKGROUND_RULE, True),
    ("timeout 60 a || b", BACKGROUND_RULE, True),
    ("timeout 60 a | tail -5", BACKGROUND_RULE, True),
    ("timeout 60 a |& tail -5", BACKGROUND_RULE, True),
    ("timeout 60 a &", BACKGROUND_RULE, True),
    ("timeout 60 a\nuntil false; do :; done", BACKGROUND_RULE, True),
    ("timeout 600 bash <<'SH'\nsleep 1\nSH\nsleep 99999", BACKGROUND_RULE, True),
    ("cd sub; timeout 60 x", BACKGROUND_RULE, True),
    ("(timeout 60 x)", BACKGROUND_RULE, True),
    ("timeout 600 bash <<$'SH'\necho step\nSH\nuntil false; do sleep 15; done", BACKGROUND_RULE, True),
    ("timeout 60 x \r#; sleep 99999", BACKGROUND_RULE, True),
    # Substitutions outside single quotes run in the outer shell, before timeout starts.
    ("timeout 60 echo $(until false; do sleep 1; done)", BACKGROUND_RULE, True),
    ("timeout 60 echo `sleep 99999`", BACKGROUND_RULE, True),
    ("timeout 60 bash -c \"$(cat steps.sh)\"", BACKGROUND_RULE, True),
    ("timeout 60 diff <(sleep 99999) /dev/null", BACKGROUND_RULE, True),
    ("timeout 600 bash <<SH\n$(sleep 99999)\nSH", BACKGROUND_RULE, True),
    ("X=$(sleep 99999) timeout 60 x", BACKGROUND_RULE, True),
    ("cd \"$(git rev-parse --show-toplevel)\" && timeout 60 x", BACKGROUND_RULE, True),
    ("timeout 60 echo $(( $(sleep 99999) + 1 ))", BACKGROUND_RULE, True),
    ("timeout 60 echo \"$(( `sleep 99999` ))\"", BACKGROUND_RULE, True),
    ("timeout 600 cat <<EOF\n$(( $(sleep 99999) ))\nEOF", BACKGROUND_RULE, True),
    # The deadline: more than 0 and at most 7200 s with the SIGKILL of -k, a literal.
    ("timeout 99999 x", BACKGROUND_RULE, True),
    ("timeout 7201 x", BACKGROUND_RULE, True),
    ("timeout 3h x", BACKGROUND_RULE, True),
    ("timeout 1d x", BACKGROUND_RULE, True),
    ("timeout -k 1d 60 x", BACKGROUND_RULE, True),
    ("timeout -k 120 7100 x", BACKGROUND_RULE, True),
    ("timeout 0 x", BACKGROUND_RULE, True),
    ("timeout 0s x", BACKGROUND_RULE, True),
    ("timeout abc x", BACKGROUND_RULE, True),
    ("timeout \"$DEADLINE\" x", BACKGROUND_RULE, True),
    ("timeout ６０ x", BACKGROUND_RULE, True),
    ("timeout 600", BACKGROUND_RULE, True),
    ("timeout 600 > /tmp/x.log", BACKGROUND_RULE, True),
    ("timeout -k 10", BACKGROUND_RULE, True),
    ("timeout -k abc 60 x", BACKGROUND_RULE, True),
    ("timeout --badopt 60 x", BACKGROUND_RULE, True),
    # A signal that ends the command, sent to its whole process group.
    ("timeout -s CHLD 60 sleep 99999", BACKGROUND_RULE, True),
    ("timeout -s 0 60 x", BACKGROUND_RULE, True),
    ("timeout --signal=CONT 60 x", BACKGROUND_RULE, True),
    ("timeout -sSTOP 60 x", BACKGROUND_RULE, True),
    ("timeout --foreground 60 bash -c 'sleep 99999'", BACKGROUND_RULE, True),
    ("timeout -f 60 x", BACKGROUND_RULE, True),
    # A message shows no word of the command that may hold a secret.
    (f"timeout https://admin:{FAKE_SECRET}@example.org x", BACKGROUND_RULE, True),
    (f"admin:{FAKE_SECRET} 60 x", BACKGROUND_RULE, True),
    # The other rules hold in the background too.
    ("timeout 60 git push origin main", "push-main", True),
    # In the foreground the rule does not apply.
    ("until [ a \\> b ]; do sleep 15; done", None, False),
    ("sleep 600", None, False),
    ("timeout 60 a; b", None, False),
    ("timeout 99999 x | tail -5", None, False),
]


def without_git_variables(environ) -> dict:
    """A copy of an environment without GIT_DIR, GIT_INDEX_FILE and the other GIT_* variables
    that git exports to its hooks and to git rebase -x: inherited, they would point the git
    commands below at the developer's repository (conftest.py removes them too)."""
    return {key: value for key, value in environ.items() if not key.startswith("GIT_")}


# git without the user's and the system's configuration (push.default, hooks, signing).
GIT_ISOLATION = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _git(cwd, *args):
    env = dict(without_git_variables(os.environ), **GIT_ISOLATION)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.org", "-c", "commit.gpgsign=false",
                    *args], cwd=cwd, env=env, check=True, capture_output=True, timeout=30)


@pytest.fixture(scope="module")
def places(tmp_path_factory):
    root = tmp_path_factory.mktemp("guard")
    workspace, on_main, empty = root / "ws", root / "main", root / "empty"
    tracking, matching = root / "tracking", root / "matching"
    checkout, linked = root / "worktrees" / "checkout", root / "worktrees" / "linked"
    for place, branch in ((workspace, "feature"), (on_main, "main"), (tracking, "main"), (matching, "feature"),
                          (checkout, "feature")):
        place.mkdir(parents=True)
        _git(place, "init", "-q", "-b", branch)
        _git(place, "commit", "-q", "--allow-empty", "-m", "chore: start")
    (workspace / "dev.env").write_text(f"UNOS_DEV_SSH_PASSWORD={FAKE_SECRET}\n")
    (workspace / "dev.env.example").write_text("UNOS_DEV_SSH_PASSWORD=\n")
    (workspace / "sub").mkdir()
    (workspace / "sub" / "settings.py").write_text("PASSWORD_MIN_LENGTH = 8\n")
    empty.mkdir()
    (empty / "dev.env.example").write_text("UNOS_DEV_SSH_PASSWORD=\n")
    # feat-x tracks origin/main, whose remote-tracking branch exists; the remote is never reached.
    _git(tracking, "config", "remote.origin.url", str(root / "origin.git"))
    _git(tracking, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    _git(tracking, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(tracking, "checkout", "-q", "-b", "feat-x", "--track", "origin/main")
    _git(tracking, "config", "push.default", "upstream")
    _git(matching, "config", "push.default", "matching")
    (checkout / "dev.env").write_text(f"UNOS_DEV_SSH_PASSWORD={FAKE_SECRET}\n")
    backup = checkout / "tmp" / "dev" / "backup" / "20261107T120000Z"
    backup.mkdir(parents=True)
    (backup / "device-state.tar").write_text(f"JWT secret {FAKE_SECRET}\n")
    (backup / "meta.txt").write_text("20261107T120000Z\n")
    (checkout / "sub").mkdir()
    (checkout / "sub" / "settings.py").write_text("PASSWORD_MIN_LENGTH = 8\n")
    _git(checkout, "worktree", "add", "-q", "-b", "linked", str(linked))
    return {"ws": workspace, "main": on_main, "empty": empty, "tracking": tracking, "matching": matching,
            "checkout": checkout, "linked": linked}


def run_guard(stdin: str, cwd: Path, project_dir: Path):
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=dict(without_git_variables(os.environ), CLAUDE_PROJECT_DIR=str(project_dir), **GIT_ISOLATION),
        timeout=60,
    )
    return result.returncode, result.stderr


def hook_input(command, cwd: Path, tool: str = "Bash", extra: dict | None = None) -> str:
    """The hook input of a tool call; `extra` adds fields to tool_input (the terminal tool's cwd)."""
    return json.dumps({"session_id": "test", "hook_event_name": "PreToolUse", "tool_name": tool,
                       "tool_input": {"command": command, "description": "test", **(extra or {})}, "cwd": str(cwd)})


@pytest.fixture(scope="module")
def results(places):
    """The guard's answer to every case, run in parallel: {index: (exit code, stderr)}."""

    def run(case):
        command, _, where = case
        return run_guard(hook_input(command, places[where]), places[where], places[where])

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(enumerate(pool.map(run, CASES)))


@pytest.mark.parametrize("index", range(len(CASES)), ids=[f"{rule or 'allow'}:{command[:60]}"
                                                         for command, rule, _ in CASES])
def test_case(results, index):
    command, rule, _ = CASES[index]
    code, stderr = results[index]

    if rule is None:
        assert (code, stderr) == (0, ""), command
    else:
        assert code == 2, command
        # One line that names the rule, the decision behind it and what to do instead.
        assert stderr.startswith(f"Blocked by .claude/hooks/guard.py ({rule}): "), stderr
        assert stderr.endswith(".\n") and stderr.count("\n") == 1, stderr
        assert re.search(r"\(ADR-\d{4}(, ADR-\d{4})*\)", stderr), stderr
        assert FAKE_SECRET not in stderr


def test_the_table_covers_every_rule_with_blocked_and_allowed_cases():
    blocked = {rule: [command for command, expected, _ in CASES if expected == rule] for rule in RULES}

    assert len(CASES) >= 60
    assert {rule: len(commands) >= 2 for rule, commands in blocked.items()} == {rule: True for rule in RULES}
    assert {expected for _, expected, _ in CASES} == set(RULES) | {None}
    assert sum(expected is None for _, expected, _ in CASES) >= 60


def test_the_docstring_lists_every_rule():
    docstring = ast.get_docstring(ast.parse(GUARD.read_text()))
    listed = re.findall(r"^  ([a-z0-9]+(?:-[a-z]+)+|power)\s", docstring, re.M)

    assert sorted(listed) == sorted((*RULES, BACKGROUND_RULE))


def test_the_guard_does_not_import_beyond_the_standard_library():
    tree = ast.parse(GUARD.read_text())
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}

    assert imported <= set(sys.stdlib_module_names) | {"__future__"}


def test_the_guard_runs_on_python_3_8_syntax():
    # .claude/settings.json runs it with the python3 on PATH, on macOS possibly /usr/bin/python3.
    ast.parse(GUARD.read_text(), feature_version=(3, 8))


def test_an_internal_error_never_blocks(places):
    code, stderr = run_guard("this is not JSON", places["ws"], places["ws"])

    assert code == 1
    assert stderr.startswith("guard.py: internal error, the command was not checked")


@pytest.mark.parametrize("payload", [
    # Reading dev.env is denied by the permission rules of .claude/settings.json, and the Desktop
    # Commander tools as a whole: they never reach the guard.
    {"tool_name": "Read", "tool_input": {"file_path": "dev.env"}},
    {"tool_name": "mcp__plugin_desktop-commander_desktop-commander__start_process",
     "tool_input": {"command": "git push origin main"}},
    {"tool_name": "Bash", "tool_input": {}},
    {"tool_name": "Bash", "tool_input": {"command": ""}},
    {"tool_name": "Bash", "tool_input": {"command": 42}},
    {"tool_name": "mcp__terminal__run_in_terminal", "tool_input": {"command": " "}},
])
def test_other_tools_and_empty_commands_pass(places, payload):
    assert run_guard(json.dumps(payload), places["ws"], places["ws"]) == (0, "")


@pytest.mark.parametrize("command, rule", [
    ("git push origin main", "push-main"),
    ("scripts/dev/ssh.sh reboot", "power"),
    ("cat dev.env", "dev-env"),
    ("tar czf /tmp/repo.tgz .", "dev-env"),
    ("make check && npm run dev", None),
])
def test_the_terminal_tool_is_checked_like_bash(places, command, rule):
    """The desktop app's run_in_terminal runs a line in the owner's login shell."""
    code, stderr = run_guard(hook_input(command, places["ws"], tool="mcp__terminal__run_in_terminal"),
                             places["ws"], places["ws"])

    assert code == (2 if rule else 0) and stderr.startswith(f"Blocked by .claude/hooks/guard.py ({rule})" if rule else "")


def test_the_terminal_tool_runs_in_its_own_cwd(places):
    def run(command, cwd):
        payload = hook_input(command, places["ws"], tool="mcp__terminal__run_in_terminal", extra={"cwd": cwd})
        return run_guard(payload, places["ws"], places["ws"])[0]

    # A bare push pushes the branch of the tab's directory; a search of sub/ does not reach dev.env.
    assert (run("git push", str(places["main"])), run("git push", ".")) == (2, 0)
    assert (run("grep -rn PASS .", "sub"), run("grep -rn PASS .", ".")) == (0, 2)


def _denials(project: Path) -> list:
    log = project / "tmp" / "guard" / "denials.log"
    return log.read_text().splitlines() if log.exists() else []


def test_each_denial_is_logged_with_its_rule_and_tool_but_never_the_command(tmp_path, places):
    project = tmp_path / "project"
    project.mkdir()
    secret = "ssh" + "pass -p hunter2 ssh root@192.0.2.103 true"

    blocked = run_guard(hook_input(secret, places["ws"]), places["ws"], project)
    allowed = run_guard(hook_input("git status", places["ws"]), places["ws"], project)
    terminal = run_guard(hook_input("git push origin main", places["ws"], tool="mcp__terminal__run_in_terminal"),
                         places["ws"], project)
    log = project / "tmp" / "guard" / "denials.log"

    assert [blocked[0], allowed[0], terminal[0]] == [2, 0, 2]
    lines = _denials(project)
    assert len(lines) == 2
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ secret-argv Bash", lines[0])
    assert lines[1].split()[1:] == ["push-main", "mcp__terminal__run_in_terminal"]
    assert "hunter2" not in log.read_text() and "sshpass" not in log.read_text()
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_a_log_that_cannot_be_written_still_blocks(tmp_path, places):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tmp").write_text("a file where the log directory would be\n")

    code, stderr = run_guard(hook_input("git push origin main", places["ws"]), places["ws"], project)

    assert code == 2
    assert stderr.startswith("Blocked by .claude/hooks/guard.py (push-main): ") and stderr.count("\n") == 1


def test_without_a_project_directory_nothing_is_logged(tmp_path, places):
    env = {key: value for key, value in without_git_variables(os.environ).items() if key != "CLAUDE_PROJECT_DIR"}
    result = subprocess.run([sys.executable, str(GUARD)], input=hook_input("git push origin main", tmp_path),
                            capture_output=True, text=True, cwd=tmp_path, env=env, timeout=60)

    assert result.returncode == 2
    assert list(tmp_path.iterdir()) == []


def test_a_large_heredoc_is_parsed_quickly(places):
    body = "\n".join(f"line {i} with ' and \" and $(not run) and `not run`" for i in range(20000))
    command = f"cat > /tmp/big.txt <<'EOF'\n{body}\nEOF\ngit push origin main"

    started = time.monotonic()
    code, stderr = run_guard(hook_input(command, places["ws"]), places["ws"], places["ws"])
    elapsed = time.monotonic() - started

    assert code == 2 and "(push-main)" in stderr
    # A tenth of a second here; Claude Code waits 60 seconds for a hook before it gives up.
    assert elapsed < 5, f"{elapsed:.1f} s"


# --- background commands (rule background-deadline, ADR-0335) -------------------------------


def background_input(command, cwd: Path, background=True, tool: str = "Bash") -> str:
    return hook_input(command, cwd, tool=tool, extra={"run_in_background": background})


@pytest.fixture(scope="module")
def background_results(places):
    """The guard's answer to every case of BACKGROUND_CASES, run in parallel: {index: (exit code, stderr)}."""

    def run(case):
        command, _, background = case
        return run_guard(background_input(command, places["ws"], background), places["ws"], places["ws"])

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(enumerate(pool.map(run, BACKGROUND_CASES)))


@pytest.mark.parametrize("index", range(len(BACKGROUND_CASES)),
                         ids=[f"{rule or 'allow'}:{'background' if background else 'foreground'}:{command[:60]}"
                              for command, rule, background in BACKGROUND_CASES])
def test_background_case(background_results, index):
    command, rule, _ = BACKGROUND_CASES[index]
    code, stderr = background_results[index]

    if rule is None:
        assert (code, stderr) == (0, ""), command
    else:
        assert code == 2, command
        assert stderr.startswith(f"Blocked by .claude/hooks/guard.py ({rule}): "), stderr
        assert stderr.endswith(".\n") and stderr.count("\n") == 1, stderr
        assert re.search(r"\(ADR-\d{4}(, ADR-\d{4})*\)", stderr), stderr
        assert FAKE_SECRET not in stderr
    if rule == BACKGROUND_RULE:
        # What to write instead.
        assert "(ADR-0335)" in stderr and "`timeout <seconds> bash -c '<command>'`" in stderr, stderr
        assert "a deadline of at most 7200 s" in stderr, stderr


def test_the_background_table_covers_both_modes():
    answers = {(rule == BACKGROUND_RULE, background) for _, rule, background in BACKGROUND_CASES}

    assert {(True, True), (False, True), (False, False)} <= answers
    assert not any(rule == BACKGROUND_RULE for _, rule, background in BACKGROUND_CASES if not background)
    assert sum(rule == BACKGROUND_RULE for _, rule, _ in BACKGROUND_CASES) >= 20
    assert sum(rule is None and background for _, rule, background in BACKGROUND_CASES) >= 15


@pytest.mark.parametrize("command, problem", [
    ("sleep 600", "`sleep` is not timeout"),
    ("X=1 ./wait.sh", "`./wait.sh` is not timeout"),
    ("timeout 60 a; b", "`;` after the call of timeout"),
    ("timeout 60 a | tail -5", "`|` after the call of timeout"),
    ("timeout 60 a &", "`&` after the call of timeout"),
    ("timeout 60 a\nb", "a newline after the call of timeout"),
    ("timeout 60 echo $(date)", "outside single quotes run in the outer shell"),
    ("timeout 7201 x", "a deadline of 7201 s is longer than 7200 s"),
    ("timeout -k 120 7100 x", "a deadline of 7220 s, with the SIGKILL of -k, is longer than 7200 s"),
    ("timeout 0 x", "a deadline of 0"),
    ("timeout abc x", "timeout's duration `abc` is not a number"),
    (f"timeout https://admin:{FAKE_SECRET}@example.org x", "timeout's duration is not a number"),
    (f"admin:{FAKE_SECRET} 60 x", "its first word is not timeout"),
    ("timeout 600 > /tmp/x.log", "no command"),
    ("timeout --badopt 60 x", "`--badopt` is not an option of timeout"),
    ("timeout -s CHLD 60 x", "the signal `CHLD` may not end the command"),
    ("timeout --foreground 60 x", "`--foreground` leaves the children of the command running"),
])
def test_a_background_denial_says_what_is_wrong(places, command, problem):
    code, stderr = run_guard(background_input(command, places["ws"]), places["ws"], places["ws"])

    assert code == 2 and problem in stderr, stderr


@pytest.mark.parametrize("command", ["sleep 600", "until [ a \\> b ]; do sleep 15; done"])
def test_the_terminal_tool_has_no_background_commands(places, command):
    """The terminal tool runs a line in a tab of the desktop app, and no session waits for it."""
    payload = background_input(command, places["ws"], tool="mcp__terminal__run_in_terminal")

    assert run_guard(payload, places["ws"], places["ws"]) == (0, "")


def test_a_background_denial_is_logged_like_the_others(tmp_path, places):
    project = tmp_path / "project"
    project.mkdir()

    code, _ = run_guard(background_input("sleep 600", places["ws"]), places["ws"], project)

    assert code == 2
    assert [line.split()[1:] for line in _denials(project)] == [[BACKGROUND_RULE, "Bash"]]
