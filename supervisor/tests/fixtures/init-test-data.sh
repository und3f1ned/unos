#!/bin/bash
# Initialize test data for deviceless testing

set -e

echo "Initializing test environment..."

# Create necessary directories
mkdir -p /data /mnt/pools /etc/samba

# Create initial SMB config
cat > /data/smb.conf << 'EOF'
[global]
   workgroup = WORKGROUP
   server string = UNOS NAS Test
   security = user
   map to guest = bad user
   log file = /var/log/samba/log.%m
   max log size = 50
EOF

# Create empty NFS exports file
touch /data/exports

# Create test app catalog
cat > /data/app-catalog.json << 'EOF'
{
  "apps": [
    {
      "id": "plex",
      "name": "Plex Media Server",
      "description": "Stream your media to any device",
      "category": "media",
      "icon": "plex.png",
      "version": "1.32.0",
      "image": "plexinc/pms-docker:latest",
      "ports": [{"container": 32400, "host": 32400, "protocol": "tcp"}],
      "volumes": [
        {"container": "/config", "description": "Configuration"},
        {"container": "/data", "description": "Media library"}
      ],
      "env": [
        {"name": "PLEX_CLAIM", "description": "Plex claim token", "required": false}
      ]
    },
    {
      "id": "nextcloud",
      "name": "Nextcloud",
      "description": "Self-hosted productivity platform",
      "category": "productivity",
      "icon": "nextcloud.png",
      "version": "28.0",
      "image": "nextcloud:latest",
      "ports": [{"container": 80, "host": 8081, "protocol": "tcp"}],
      "volumes": [
        {"container": "/var/www/html", "description": "Application data"}
      ],
      "env": []
    },
    {
      "id": "jellyfin",
      "name": "Jellyfin",
      "description": "Free software media system",
      "category": "media",
      "icon": "jellyfin.png",
      "version": "10.8.0",
      "image": "jellyfin/jellyfin:latest",
      "ports": [{"container": 8096, "host": 8096, "protocol": "tcp"}],
      "volumes": [
        {"container": "/config", "description": "Configuration"},
        {"container": "/media", "description": "Media library"}
      ],
      "env": []
    }
  ],
  "categories": ["media", "productivity", "backup", "download", "monitoring"]
}
EOF

# Create initial admin user file
cat > /data/init-user.json << 'EOF'
{
  "username": "admin",
  "password": "admin123"
}
EOF

echo "Test environment initialized successfully!"
