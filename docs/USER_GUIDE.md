# UNOS User Guide

Welcome to UNOS! This guide will help you get the most out of your NAS.

## Table of Contents

1. [Getting Started](#getting-started)
2. [Dashboard](#dashboard)
3. [Storage Management](#storage-management)
4. [File Sharing](#file-sharing)
5. [File Browser](#file-browser)
6. [Docker Containers](#docker-containers)
7. [App Store](#app-store)
8. [User Management](#user-management)
9. [System Settings](#system-settings)
10. [Backups & Snapshots](#backups--snapshots)
11. [Notifications](#notifications)
12. [Tips & Best Practices](#tips--best-practices)

---

## Getting Started

### First Login

1. Open your web browser
2. Navigate to `https://YOUR_NAS_IP` (plain `http://` redirects there)
3. Your browser warns about the certificate: UNOS issues it from its own
   certificate authority. Continue to the site, or make your devices trust that
   authority once (see
   [HTTPS and the Certificate Warning](INSTALL.md#https-and-the-certificate-warning)).
4. On the very first visit UNOS asks you to create the administrator account:
   choose a user name and a password (at least 8 characters). There are no
   default credentials.
5. Afterwards you sign in on the login page.

### Interface Overview

The UNOS interface consists of:
- **Sidebar** - Navigation menu on the left, with your account and **Logout**
  at the bottom. Regular users see **Dashboard** and **Account** only (see
  [User Roles](#user-roles)).
- **Main Content** - Current page content

---

## Dashboard

The dashboard provides an at-a-glance view of your NAS:

### System Stats
- **CPU** - Current processor usage
- **Memory** - RAM usage and total
- **Network** - Current upload/download speeds
- **Temperature** - System temperature (if sensors available)

### Storage Overview
Shows all your storage pools with:
- Used/total space
- Health status
- Quick access to pool details

### Containers
Lists running Docker containers with status indicators

### Quick Actions
Common tasks like creating shares, managing pools, etc.

### Recent Activity
Latest system events and notifications

---

## Storage Management

### Understanding Storage Pools

UNOS uses BTRFS for storage management. A **storage pool** combines one or more disks into a single storage space.

### Viewing Disks

Go to **Storage** to see:
- All available disks
- Disk model, size, and serial number
- SMART health status
- Current mount status

### Creating a Storage Pool

1. Go to **Storage**
2. Click **Create Pool**
3. Enter a pool name (e.g., "data", "media")
4. Select disks to include. System disks and disks in use cannot be selected.
5. Choose a RAID profile:

| Profile | Min Disks | Redundancy | Space Efficiency |
|---------|-----------|------------|------------------|
| Single | 1 | None | 100% |
| RAID 0 | 2 | None | 100% |
| RAID 1 | 2 | 1 disk failure | 50% |
| RAID 1C3 | 3 | 2 disk failures | 33% |
| RAID 1C4 | 4 | 3 disk failures | 25% |
| RAID 10 | 4 | 1 per pair | 50% |

> **Note:** BTRFS RAID5 and RAID6 are not offered because they have a known
> "write hole" bug that can cause silent data corruption on power failure.
> UNOS prioritizes data safety. Use RAID 1C3 (3 copies) or RAID 1C4 (4 copies)
> for multi-disk redundancy beyond RAID 1.

6. Type the pool name again to confirm. If a selected disk already contains
   data, also tick **Erase the existing data on these disks**.
7. Click **Erase Disks & Create Pool**

UNOS mounts the new pool at `/mnt/pools/<name>` and adds it to the host's
`/etc/fstab`, so it is mounted again after a reboot.

### Pool Operations

For each pool, you can:
- **View Details** - Space usage, devices, status
- **Mount / Unmount** - Mount the pool (and mount it at every boot), or unmount
  it; the data on the disks is not touched
- **Add Devices** - Expand the pool
- **Scrub** - Check data integrity
- **Balance** - Redistribute data across devices
- **Delete** - Unmount the pool and remove it from `/etc/fstab`. The disks are
  not wiped: the data stays on them and the pool can be mounted again later.

### SMART Monitoring

Click on any disk to view:
- Health status
- Temperature
- Power-on hours
- Read/write errors
- Detailed SMART attributes

---

## File Sharing

### SMB Shares (Windows/Mac Compatible)

SMB shares allow you to access files from Windows, macOS, and Linux.

#### Creating a Share

1. Go to **Shares**
2. Click **Add Share**
3. Configure:
   - **Name** - Share name (appears in network)
   - **Pool** - Which storage pool to use
   - **Path** - Folder path within the pool
   - **Guest Access** - Allow anonymous access
   - **Read Only** - Prevent modifications
4. Click **Create**

#### Accessing Shares

**Windows:**
1. Open File Explorer
2. Type `\\YOUR_NAS_IP` in the address bar
3. Enter your UNOS username and password

**macOS:**
1. Open Finder
2. Press `Cmd+K`
3. Enter `smb://YOUR_NAS_IP`
4. Enter credentials

**Linux:**
```bash
# Mount temporarily
sudo mount -t cifs //YOUR_NAS_IP/sharename /mnt/nas -o username=admin

# Or browse with file manager
smb://YOUR_NAS_IP
```

---

## File Browser

The built-in file browser lets you manage files directly from the web interface.

### Features

- **Upload Files** - Drag and drop or click to upload
- **Create Folders** - Organize your files
- **Download** - Download files to your computer
- **Delete** - Remove files and folders
- **Navigate** - Browse through your storage pools

### Tips

- Use breadcrumbs at the top to navigate
- Click column headers to sort files
- Select multiple files by holding Ctrl/Cmd

---

## Docker Containers

UNOS can manage Docker containers for running additional applications.

### Viewing Containers

Go to **Containers** to see:
- Container name and image
- Status (running, stopped, etc.)
- Resource usage
- Ports exposed

### Container Actions

- **Start/Stop** - Control container state
- **Restart** - Restart the container
- **Logs** - View container output
- **Remove** - Delete the container

### Creating Containers

1. Click **Create Container**
2. Enter the image name (e.g., `nginx:latest`)
3. Configure:
   - Container name
   - Port mappings
   - Environment variables
   - Volume mounts
4. Click **Create**

---

## App Store

The App Store provides one-click installation of popular applications.

### Available Categories

- **Media** - Plex, Jellyfin, Emby
- **Downloads** - qBittorrent, Transmission
- **Productivity** - Nextcloud, Paperless
- **Home Automation** - Home Assistant
- **Development** - Gitea, Code Server
- **Monitoring** - Grafana, Uptime Kuma

### Installing an App

1. Go to **App Store**
2. Browse or search for an app
3. Click **Install**
4. Choose the **Storage pool** for the app's data (apps that keep data need a
   mounted pool; create or mount one on the **Storage** page first) and
   configure settings if prompted
5. Wait for installation to complete

Starting or restarting an app from UNOS fails while its data folders are not
on a mounted pool.

### Managing Installed Apps

Go to **App Store** → **Installed** to:
- View installed apps
- Start/stop apps
- Update apps
- Remove apps

---

## User Management

### Creating Users

1. Go to **Users** (administrators only)
2. Click **Add User**
3. Enter:
   - Username
   - Password (twice)
4. Click **Create User**

New accounts are regular users. To make one an administrator, edit it and
tick **Administrator privileges**.

### User Roles

| Role | Permissions |
|------|-------------|
| Admin | Full access to all features |
| User | **Dashboard** (system overview and live statistics) and **Account** (own password, two-factor authentication and login history) |

Every other page and API is for administrators only; regular users do not see
them. At least one administrator must remain: the last one cannot be demoted
or deleted.

### Changing Passwords

Users can change their own password:
1. Go to **Account** (administrators: **System** → **Security**)
2. Under **Change Password**, enter the current and the new password

Admins can reset any user's password via **Users** page.

### Two-Factor Authentication (2FA)

Protect your account with TOTP-based two-factor authentication:

1. Go to **Account** (administrators: **System** → **Security**)
2. Under **Two-Factor Authentication**, click **Enable**
3. Scan the QR code with an authenticator app (Google Authenticator, Authy,
   etc.). The QR code is drawn in your browser; the secret is never sent to
   another service.
4. Enter the verification code and click **Enable 2FA**

To disable 2FA:
1. Go to **Account** (administrators: **System** → **Security**)
2. Under **Two-Factor Authentication**, click **Disable**
3. Enter a current 2FA code and click **Disable 2FA**

### API Tokens

Tokens give scripts access to the UNOS API with the rights of the account that
created them: a regular user's token cannot use the administrator-only APIs.
There is no page for them yet; create one with the API, using the
`access_token` that `POST /api/v1/auth/login` returns (the full API token is
shown only once):

```bash
curl --cacert unos-root-ca.crt -X POST https://unos/api/v1/auth/tokens \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Backup Script"}'
```

Use tokens in API requests:
```bash
curl --cacert unos-root-ca.crt -H "Authorization: Bearer YOUR_TOKEN" https://unos/api/v1/system/info
```

`unos-root-ca.crt` is the UNOS root certificate (see
[Trust the UNOS Certificate Authority](INSTALL.md#trust-the-unos-certificate-authority)),
and `unos` the name on the certificate. List your tokens with
`GET /api/v1/auth/tokens` and revoke one with `DELETE /api/v1/auth/tokens/<id>`.

---

## System Settings

Access via **System** in the sidebar.

### General

- **Hostname** - Change NAS name
- **Timezone** - Set correct timezone

### Network

- View network interfaces
- Check IP addresses
- Monitor network statistics
- View active connections

### Power

- **Reboot** - Restart the NAS
- **Shutdown** - Power off the NAS
- **Scheduled Tasks** - Set up auto-shutdown

### Health

- System information
- Service status
- Resource usage history

### Logs

- View system logs
- Filter by type and date
- Export logs for troubleshooting

---

## Backups & Snapshots

### Snapshots

BTRFS snapshots provide instant, space-efficient backups.

#### Creating a Snapshot

1. Go to **Snapshots**
2. Select a storage pool
3. Click **Create Snapshot**
4. Enter a name (optional)
5. Click **Create**

#### Restoring from Snapshot

⚠️ **Warning:** Restoring will overwrite current data!

1. Go to **Snapshots**
2. Find the snapshot to restore
3. Click **Restore**
4. Confirm the action

### System Backup

Go to **System** → **Backup** to:
- Export UNOS configuration
- Import configuration from backup
- Schedule automatic backups

---

## Notifications

Get alerts about important events via email, Discord, or Telegram.

### Setting Up Email Notifications

1. Go to **System** → **Notifications**
2. Click **Add Channel** → **Email**
3. Configure:
   - SMTP server
   - Port (usually 587)
   - Username and password
   - Sender and recipient email
4. Click **Test** to verify
5. Click **Save**

### Setting Up Discord Notifications

1. Create a Discord webhook in your server
2. Go to **System** → **Notifications**
3. Click **Add Channel** → **Discord**
4. Paste the webhook URL
5. Test and save

### Notification Events

Choose which events trigger notifications:
- Disk health warnings
- Pool errors
- Container status changes
- System temperature alerts
- Backup completion

---

## Tips & Best Practices

### Security

1. ✅ Create the administrator account right after installing, with a strong password
2. ✅ Enable two-factor authentication (2FA)
3. ✅ Use strong, unique passwords
4. ✅ Keep UNOS updated
5. ✅ Don't expose to the internet without VPN
6. ✅ Use API tokens instead of passwords for scripts
7. ✅ Review audit logs periodically

### Storage

1. ✅ Use RAID1 or higher for important data
2. ✅ Monitor SMART status regularly
3. ✅ Create regular snapshots
4. ✅ Don't fill pools beyond 80%
5. ✅ Run scrub monthly

### Performance

1. ✅ Use SSDs for frequently accessed data
2. ✅ Avoid running too many containers
3. ✅ Monitor resource usage
4. ✅ Keep system storage separate from data

### Backup Strategy

Follow the 3-2-1 rule:
- **3** copies of data
- **2** different storage types
- **1** offsite backup

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `G` then `D` | Go to Dashboard |
| `G` then `S` | Go to Storage |
| `G` then `H` | Go to Shares |
| `G` then `C` | Go to Containers |
| `R` | Refresh current page |
| `?` | Show keyboard shortcuts |

---

## Getting Help

If you encounter issues:

1. Check **System** → **System Logs** for errors (administrators)
2. Review [Troubleshooting](INSTALL.md#troubleshooting)
3. Search [GitHub Issues](https://github.com/und3f1ned/unos/issues)
4. Ask in [Discussions](https://github.com/und3f1ned/unos/discussions)

---

## Glossary

| Term | Definition |
|------|------------|
| **BTRFS** | B-tree File System, a modern copy-on-write filesystem |
| **Pool** | A collection of disks managed as a single storage unit |
| **RAID** | Redundant Array of Independent Disks |
| **SMB** | Server Message Block, Windows file sharing protocol |
| **Snapshot** | A point-in-time copy of filesystem state |
| **Scrub** | Process to check and repair data integrity |
| **SMART** | Self-Monitoring, Analysis and Reporting Technology |
