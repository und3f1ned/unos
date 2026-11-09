# UNOS Recovery

What to do when the NAS does not boot, or when no administrator can sign in.

1. [Reach a Pool from Another Linux Machine](#reach-a-pool-from-another-linux-machine)
2. [Reset an Administrator's Password and Two-Factor Authentication](#reset-an-administrators-password-and-two-factor-authentication)

---

## Reach a Pool from Another Linux Machine

When the NAS does not boot, after its system disk or its board failed, the files of its pools are
still on their disks. A pool is a plain btrfs filesystem, not encrypted, whose label is the pool's
name: any Linux machine with btrfs reads it. The steps below only read the pool's disks: they
mount the pool read-only and copy its files to another disk. They never repair the filesystem,
because a repair writes to disks that may be failing too.

You need:

- A Linux machine with Linux 5.9 or later and btrfs-progs (Debian and Ubuntu:
  `sudo apt install btrfs-progs`). The live system of a current Debian or Ubuntu, started from a
  USB stick, will do: install btrfs-progs there when it is missing.
- A port for every disk of the pool (SATA, or USB adapters), and another disk with room for the
  copies.

### 1. Find the Pool

A desktop may mount a disk read-write as soon as it is attached, and that mount already writes to
it: btrfs replays its log, ext4 its journal. Stop that before you attach a disk of the NAS. GNOME,
the desktop of Ubuntu's live system and of Debian's GNOME one, does it; turn it off as the
desktop's user (without `sudo`):

```bash
gsettings set org.gnome.desktop.media-handling automount false
```

Another desktop has such a setting among those for removable drives and media; a system without
a desktop mounts nothing by itself. If the desktop is to mount the disk for the copies, attach
that disk first.

Attach the pool's disks, then check that nothing mounted them:

```bash
lsblk -f
```

The pool's disks show `btrfs` as FSTYPE and the pool's name as LABEL, and must have no mount point
(MOUNTPOINTS, or MOUNTPOINT on older systems). When one of them has one, unmount it at once with
`sudo umount` and that mount point: the desktop mounted it read-write, and a read-only mount
beside it would not stop the writes. Then:

```bash
sudo btrfs device scan
sudo btrfs filesystem show
```

Each btrfs filesystem is listed with its label, its UUID and its disks:

```
Label: 'data'  uuid: 0b6c2f1e-3a5d-4e7f-9c21-6d8a4b3e2f10
	Total devices 2 FS bytes used 1.21TiB
	devid    1 size 3.64TiB used 1.22TiB path /dev/sdb
	devid    2 size 3.64TiB used 1.22TiB path /dev/sdc
```

`*** Some devices missing` below a filesystem means that one of its disks is not there.

### 2. Mount It Read-Only

```bash
sudo mkdir -p /mnt/recovery
sudo mount -o ro,rescue=nologreplay /dev/sdb /mnt/recovery
```

Name any one disk of the pool, as `btrfs filesystem show` listed it (here `/dev/sdb`): btrfs
finds the others. `ro` mounts the pool read-only, and `rescue=nologreplay` keeps btrfs from
replaying its log, which it otherwise writes to the disks even on a read-only mount. On Linux
before 5.9, write `nologreplay` instead. Without the replay, the copies may lack what was written
in the last 30 seconds before the NAS stopped.

When a disk failed or is not attached, the mount fails (`sudo dmesg | tail` says that a device
is missing). Add `degraded`:

```bash
sudo mount -o ro,degraded,rescue=nologreplay /dev/sdb /mnt/recovery
```

Whether a pool mounts with disks missing depends on its profile, which the Storage page showed
(once it is mounted, `sudo btrfs filesystem df /mnt/recovery` names it):

| Profile | Disks that may be missing |
| --- | --- |
| Single (`single`) | none |
| RAID 0 (`raid0`) | none |
| RAID 1 (`raid1`) | 1 |
| RAID 1C3 (`raid1c3`) | 2 |
| RAID 1C4 (`raid1c4`) | 3 |
| RAID 10 (`raid10`) | 1 |

If the pool does not mount read-only, see [When the Pool Does Not Mount](#when-the-pool-does-not-mount).

### 3. Find the Files

`/mnt/recovery` is now the pool's top folder, `/mnt/pools/<pool>` on the NAS: a folder
`/mnt/pools/<pool>/media/films` of the NAS is `/mnt/recovery/media/films` here.

| In the pool | What it is |
| --- | --- |
| `<volume>/` | A volume: a btrfs subvolume at the top of the pool, with its own files |
| `.snapshots/<name>/` | A snapshot of the pool: its files as they were when the snapshot was made, read-only, without the files of its volumes |
| `<volume>/.snapshots/<name>/` | A snapshot of a volume |
| `restore_<date>_<time>/` | A writable copy of a snapshot that **Restore** on the Snapshots page made, at the top of the pool or of a volume |
| `.unos-trash/` | The trash of the pool; each volume has its own `.unos-trash`. Every deleted item is in a folder of its own, as `item`, with the path it was deleted from in `origin` |
| `apps/<app>/` | The data of an app |

`sudo btrfs subvolume list /mnt/recovery` lists the volumes, the snapshots and the restored
copies. Snapshot names are `snap_<date>_<time>` (made on the Snapshots page), `auto_<date>_<time>`
(made by a scheduled task) or the name given when it was made; the dates and times are in UTC.

**Which folders are shares.** A share is a folder of a pool or of a volume. When the NAS's system
disk still reads, it names them: attach it too, check with `lsblk -f` that nothing mounted it, as
in [step 1](#1-find-the-pool), and mount its root partition read-only, here `/dev/sdd2` (`lsblk -f`
lists the partitions; ext4 replays its journal unless `noload` is given):

```bash
sudo mkdir -p /mnt/nas-system
sudo mount -o ro,noload /dev/sdd2 /mnt/nas-system
grep -E '^\[|^ *path = ' /mnt/nas-system/etc/samba/smb.conf
```

`/etc/samba/smb.conf` holds each share's `[name]` and its folder,
`path = /mnt/pools/<pool>/<folder>`. The UNOS database holds them too, in the table
`smb_shares` (the columns `name` and `path`) of `unos.db` in the Docker volume `deploy_unos-data`,
which is `/var/lib/docker/volumes/deploy_unos-data/_data` with Docker's default folders. Its
latest changes can still be in `unos.db-wal` beside it, which SQLite reads with it only where it
may write: copy the files whose names start with `unos.db` to this machine, as root, whose shell
lists them in Docker's folders (yours cannot), and read the copy (`sudo apt install sqlite3` for
the last command):

```bash
mkdir -p ~/unos-db
sudo sh -c 'cp /mnt/nas-system/var/lib/docker/volumes/deploy_unos-data/_data/unos.db* "$1"' sh ~/unos-db/
sudo sqlite3 ~/unos-db/unos.db 'SELECT name, path FROM smb_shares'
```

**Owners.** Share folders belong to root and the group 70000 (`unos-smb` on the NAS), with mode
2770, and the files written over SMB belong to the user ids of the SMB accounts, from 70000 on.
This machine has no names for those ids, and only root reads the share folders: copy as root.

### 4. Copy the Files

```bash
sudo rsync -a --info=progress2 /mnt/recovery/media/ /media/backup/media/
```

`/media/backup` stands for the folder where the disk for the copies is mounted. `-a` keeps the
owners, modes and times. On a disk that cannot keep Linux owners (exFAT, NTFS), use
`sudo rsync -rt --info=progress2` instead. Files of a snapshot are copied the same way, from
`/mnt/recovery/.snapshots/<name>/`.

### 5. Unmount

```bash
sudo umount /mnt/recovery
```

### When the Pool Does Not Mount

When the pool does not mount read-only, even with `degraded`, stop there. Do not mount it
read-write on this machine, and do not run `btrfs check --repair` or `btrfs rescue`: they write to
the disks, and a wrong step loses what is still readable. Copy every disk of the pool to a new
disk at least as large first, for example with GNU ddrescue, and work on the copies only, with
the btrfs documentation: the `rescue=` mount options (`man 5 btrfs`) and `btrfs restore`, which
copies files out of a filesystem that does not mount.

### Back to UNOS

To use the pool with UNOS again, attach its disks to the UNOS host. **Storage** lists the pool by
its label, and **Mount** mounts it at `/mnt/pools/<pool>` and adds it to the host's `/etc/fstab`.
The users, the shares and their access are not on the pool but in the UNOS database: they come
back with a backup ([System Backup](USER_GUIDE.md#system-backup)), or are created again.

---

## Reset an Administrator's Password and Two-Factor Authentication

When no administrator can sign in, because the password is forgotten, or the authenticator app
and its [recovery codes](USER_GUIDE.md#recovery-codes) are lost, root on the NAS resets an
administrator, at the NAS or over SSH:

```bash
sudo /opt/unos/deploy/reset-admin.sh          # the only administrator
sudo /opt/unos/deploy/reset-admin.sh alice    # the administrator alice
```

It asks for the new password twice, without showing it: at least 8 characters, at most 72 bytes
(UTF-8), and no control characters such as tabs. Then it:

1. sets the new password;
2. turns the administrator's two-factor authentication off and deletes its recovery codes;
3. records both in the audit log: `password_change` and, when two-factor authentication was on,
   `totp_disabled` (**Password change** and **Totp disabled** on the Audit Log page), by the user
   `root`, for the resource `user` and the administrator's name, with the severity warning.

Without a name it takes the only administrator; when there are several, it lists them and changes
nothing. It takes only administrators: an administrator sets a regular user's password on the
Users page. Without a terminal it reads the new password as the first line of its standard input
and does not ask twice. Never give the password as an argument: every process of the host can
read a command line. The exit status is 0 when the reset is done and recorded, 1 when nothing was
changed, or when the reset was done but the audit log could not record it (the message says
which), 2 for a wrong command line and 130 when interrupted at the password prompt.

UNOS must be running (`sudo systemctl start unos`): the script runs the command in the
`unos-supervisor` container. With UNOS installed in another folder, or without the script:

```bash
sudo docker exec -it unos-supervisor python -m src.emergency reset-admin [NAME]
```

Afterwards:

1. Sign in with the new password: no two-factor code is asked.
2. Turn two-factor authentication on again (**System** → **Security**) and generate new recovery
   codes.

The reset does not end the sessions that were signed in before: they stay valid until they
expire, 24 hours after their sign-in by default (UNOS cannot end a session on the server yet,
feature-list entry `auth.session-revocation`), and the account's API tokens keep working.

### When Someone Else May Know the Old Password

Whoever knew it may have signed in, with every right of an administrator, and what they did
stays after the reset. Then, in this order:

1. Sign every session out, by having the supervisor generate a new signing secret:

   ```bash
   sudo docker exec unos-supervisor rm /data/jwt_secret
   sudo docker restart unos-supervisor
   ```

   When `JWT_SECRET` is set in `/etc/unos/secrets.env`, the supervisor uses that value instead:
   set a new one there ([Step 4](INSTALL.md#step-4-prepare-the-secrets-file)) and run
   `sudo systemctl restart unos`.
2. Sign in with the new password and delete the API tokens you do not know
   ([API Tokens](USER_GUIDE.md#api-tokens)): the new secret does not end them.
3. On the **Users** page, delete every account you do not know, administrators included:
   deleting an account deletes its API tokens too.
4. On the **Audit Log** page, set **User** to the administrator's name and look at what was done
   in its name: **User created**, **User updated** (`password changed`: a password set;
   `is_admin=True`: made an administrator), **User deleted**, and the API tokens created
   (`Created API token: <name>`). Set a new password on the **Users** page for every account whose
   password it set, and for every other administrator when you cannot tell. Only an account's own
   user sees its API tokens: each of those users deletes the tokens they do not know.
5. The audit log does not record everything yet ([Audit Log](USER_GUIDE.md#audit-log)): look
   through the scheduled tasks, the apps and containers, the shares and their access, and the
   notification channels too.
