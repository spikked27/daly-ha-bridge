#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer with sudo." >&2
    exit 1
fi

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required." >&2
    exit 1
fi

if [ ! -x /opt/daly-ha/bin/python ]; then
    echo "Creating /opt/daly-ha virtual environment..."
    if ! python3 -m venv /opt/daly-ha; then
        echo "Could not create the virtual environment." >&2
        echo "Install python3-venv, then run this installer again." >&2
        exit 1
    fi
fi

/opt/daly-ha/bin/pip install --disable-pip-version-check -r "$project_dir/requirements.txt"

install -m 0755 "$project_dir/daly_ha_bridge.py" /opt/daly-ha/daly_ha_bridge.py
install -m 0644 "$project_dir/daly-ha-bridge.service" /etc/systemd/system/daly-ha-bridge.service

if [ ! -e /etc/daly-ha.env ]; then
    install -m 0600 "$project_dir/daly-ha.env.example" /etc/daly-ha.env
    echo "Created /etc/daly-ha.env. Edit its MQTT credentials before starting."
else
    echo "Preserved existing /etc/daly-ha.env."
fi

systemctl daemon-reload
echo "Installation complete."
echo "Next: sudo nano /etc/daly-ha.env"
echo "Then: sudo systemctl enable --now daly-ha-bridge"
