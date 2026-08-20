# DALY R32U Home Assistant MQTT bridge

This service uses `python-daly-bms` with DALY address 4 to provide native Home
Assistant MQTT switches for the charge and discharge MOSFETs. It also publishes
pack voltage, current, state of charge, remaining capacity, cell-voltage delta,
alarms, and fourteen individual cell voltages.

## Prerequisites

The Pi needs Python 3 with the `venv` module, Git, and systemd. The installer
creates `/opt/daly-ha` and installs the pinned Python dependencies. If virtual
environment creation fails, install the OS package first:

```bash
sudo apt update
sudo apt install -y python3-venv git
```

SolarAssistant must not be configured to open the DALY USB cable. Only this
service may own the serial port. The inverter's separate serial connection may
remain configured in SolarAssistant.

## Install from GitHub

Clone this repository to the SolarAssistant Pi, enter it, and run:

```bash
git clone https://github.com/spikked27/daly-ha-bridge.git
cd daly-ha-bridge
sudo sh install.sh
```

Edit the protected configuration file:

```bash
sudo nano /etc/daly-ha.env
```

Enter the username and password for an MQTT account accepted by the Home
Assistant broker at `192.168.0.107:1883`. Do not add spaces around `=`. Keep the
password inside double quotes.

Start the service now and automatically at every boot:

```bash
sudo systemctl enable --now daly-ha-bridge
```

Check status and live logs:

```bash
sudo systemctl status daly-ha-bridge --no-pager
sudo journalctl -u daly-ha-bridge -f
```

Home Assistant MQTT Discovery should create a `Daly BMS` device. Commands
are not optimistic: each switch changes state only after the service reads the
actual MOSFET state back from the BMS. Retained MQTT command messages are
ignored so an old command cannot be replayed after a restart.

Optional multi-frame cell-voltage or alarm read failures preserve the last good
value and do not mark the device unavailable. Core communication must fail on
three consecutive polling cycles before availability changes to offline. The
service reconnects to both the serial port and MQTT broker automatically and
republishes discovery when Home Assistant sends its MQTT birth message.

All individual cell-voltage sensors and the cell-delta sensor request three
decimal places through Home Assistant MQTT Discovery. A precision manually set
by the user in Home Assistant takes priority over this suggested default.

## Update

From the existing clone, pull the latest version, reinstall it, and restart the
service with:

```bash
cd ~/daly-ha-bridge
sh update.sh
```

The updater uses a fast-forward-only Git pull, preserves `/etc/daly-ha.env`,
refreshes Python dependencies, and shows the service status when finished.

## Stop or remove

```bash
sudo systemctl disable --now daly-ha-bridge
```

Stopping the service does not change either MOSFET. It only stops monitoring
and command handling.
