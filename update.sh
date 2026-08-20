#!/bin/sh
set -eu

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this updater as the normal SolarAssistant user, not with sudo." >&2
    exit 1
fi

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ ! -d "$project_dir/.git" ]; then
    echo "This updater must be run from a git clone of the project." >&2
    exit 1
fi

git -C "$project_dir" pull --ff-only
sudo "$project_dir/install.sh"
sudo systemctl enable --now daly-ha-bridge
sudo systemctl restart daly-ha-bridge
sudo systemctl --no-pager --full status daly-ha-bridge
