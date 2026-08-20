#!/opt/daly-ha/bin/python
"""DALY BMS UART to Home Assistant MQTT bridge.

Uses python-daly-bms for a single, serialized connection to the BMS. Publishes
MQTT Discovery entities and accepts charge/discharge MOSFET commands.
"""

import json
import logging
import os
import signal
import threading
import time

import paho.mqtt.client as mqtt
from dalybms.daly_bms import DalyBMS


DEVICE = os.environ.get(
    "DALY_DEVICE",
    "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
)
MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.0.107")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
POLL_SECONDS = max(5, int(os.environ.get("POLL_SECONDS", "10")))
CELL_COUNT = int(os.environ.get("CELL_COUNT", "14"))
BASE_TOPIC = os.environ.get("BASE_TOPIC", "daly_bms")
DISCOVERY_PREFIX = os.environ.get("DISCOVERY_PREFIX", "homeassistant")
OFFLINE_AFTER_FAILURES = max(2, int(os.environ.get("OFFLINE_AFTER_FAILURES", "3")))
REQUEST_GAP_SECONDS = float(os.environ.get("REQUEST_GAP_SECONDS", "0.15"))

AVAILABILITY_TOPIC = f"{BASE_TOPIC}/availability"
CHARGE_COMMAND_TOPIC = f"{BASE_TOPIC}/command/charge_mosfet"
DISCHARGE_COMMAND_TOPIC = f"{BASE_TOPIC}/command/discharge_mosfet"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("daly-ha-bridge")

stop_event = threading.Event()
serial_lock = threading.Lock()
state_lock = threading.Lock()
bms = None
client = None
state_cache = {}


DEVICE_INFO = {
    "identifiers": ["daly_r32u_uart"],
    "name": "Daly BMS",
    "manufacturer": "DALY",
    "model": "R32U",
}


def state_topic(item):
    return f"{BASE_TOPIC}/state/{item}"


def publish_discovery():
    switches = {
        "charge_mosfet": {
            "name": "Charge MOSFET",
            "unique_id": "daly_bms_charge_mosfet",
            "command_topic": CHARGE_COMMAND_TOPIC,
            "state_topic": state_topic("charge_mosfet"),
            "icon": "mdi:battery-arrow-up",
        },
        "discharge_mosfet": {
            "name": "Discharge MOSFET",
            "unique_id": "daly_bms_discharge_mosfet",
            "command_topic": DISCHARGE_COMMAND_TOPIC,
            "state_topic": state_topic("discharge_mosfet"),
            "icon": "mdi:battery-arrow-down",
        },
    }
    for object_id, config in switches.items():
        config.update(
            {
                "payload_on": "ON",
                "payload_off": "OFF",
                "availability_topic": AVAILABILITY_TOPIC,
                "payload_available": "online",
                "payload_not_available": "offline",
                "optimistic": False,
                "default_entity_id": f"switch.daly_bms_{object_id}",
                "device": DEVICE_INFO,
            }
        )
        topic = f"{DISCOVERY_PREFIX}/switch/daly_bms/{object_id}/config"
        client.publish(topic, json.dumps(config), qos=1, retain=True)

    sensors = {
        "pack_voltage": ("Pack Voltage", "V", "voltage", "mdi:battery"),
        "current": ("Current", "A", "current", "mdi:current-dc"),
        "soc": ("State of Charge", "%", "battery", "mdi:battery"),
        "remaining_capacity": ("Remaining Capacity", "Ah", None, "mdi:battery-clock"),
        "cell_delta": ("Cell Delta", "V", "voltage", "mdi:delta"),
        "alarms": ("Alarms", None, None, "mdi:alert"),
    }
    for cell_number in range(1, CELL_COUNT + 1):
        sensors[f"cell_{cell_number}_voltage"] = (
            f"Cell {cell_number} Voltage",
            "V",
            "voltage",
            "mdi:battery-medium",
        )

    for object_id, (name, unit, device_class, icon) in sensors.items():
        config = {
            "name": name,
            "unique_id": f"daly_bms_{object_id}",
            "default_entity_id": f"sensor.daly_bms_{object_id}",
            "state_topic": state_topic(object_id),
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "icon": icon,
            "device": DEVICE_INFO,
        }
        if unit:
            config["unit_of_measurement"] = unit
        if device_class:
            config["device_class"] = device_class
        if object_id == "cell_delta" or (
            object_id.startswith("cell_") and object_id.endswith("_voltage")
        ):
            config["suggested_display_precision"] = 3
        if object_id != "alarms":
            config["state_class"] = "measurement"
        topic = f"{DISCOVERY_PREFIX}/sensor/daly_bms/{object_id}/config"
        client.publish(topic, json.dumps(config), qos=1, retain=True)


def on_connect(mqtt_client, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        LOG.error("MQTT connection rejected with code %s", reason_code)
        return
    LOG.info("Connected to MQTT broker %s:%s", MQTT_HOST, MQTT_PORT)
    mqtt_client.subscribe(
        [
            (CHARGE_COMMAND_TOPIC, 1),
            (DISCHARGE_COMMAND_TOPIC, 1),
            (f"{DISCOVERY_PREFIX}/status", 1),
        ]
    )
    publish_discovery()
    publish_cached_state()
    with state_lock:
        have_confirmed_state = "charge_mosfet" in state_cache
    if have_confirmed_state:
        mqtt_client.publish(AVAILABILITY_TOPIC, "online", qos=1, retain=True)


def on_disconnect(mqtt_client, userdata, reason_code, properties=None):
    LOG.warning("Disconnected from MQTT broker; automatic reconnect is enabled")


def close_bms():
    global bms
    if bms is not None:
        try:
            bms.disconnect()
        except Exception:
            pass
    bms = None


def ensure_bms():
    global bms
    if bms is None:
        LOG.info("Opening DALY BMS on %s using address 4", DEVICE)
        candidate = DalyBMS(address=4)
        candidate.connect(DEVICE)
        bms = candidate
    return bms


def read_mosfet_state():
    status = ensure_bms().get_mosfet_status()
    if not isinstance(status, dict):
        raise RuntimeError("DALY did not return MOSFET status")
    return status


def publish_value(item, value):
    with state_lock:
        state_cache[item] = str(value)
        payload = state_cache[item]
    client.publish(state_topic(item), payload, qos=1, retain=True)


def publish_cached_state():
    with state_lock:
        cached_items = list(state_cache.items())
    for item, value in cached_items:
        client.publish(state_topic(item), value, qos=1, retain=True)


def publish_mosfet_state(status):
    publish_value(
        "charge_mosfet", "ON" if status["charging_mosfet"] else "OFF"
    )
    publish_value(
        "discharge_mosfet", "ON" if status["discharging_mosfet"] else "OFF"
    )


def read_required(method, description, attempts=2):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            value = method()
            if value is not False and value is not None:
                return value
            last_error = RuntimeError(f"no {description} response")
        except Exception as error:
            last_error = error
        if attempt < attempts:
            time.sleep(0.25)
    raise RuntimeError(f"DALY {description} read failed") from last_error


def read_optional(method, description):
    try:
        return read_required(method, description, attempts=2)
    except Exception as error:
        LOG.warning("Optional %s read failed; preserving last value: %s", description, error)
        return None


def publish_measurements():
    daly = ensure_bms()
    # Voltage/current/SOC and MOSFET state are core health checks. A missed
    # optional multi-frame cell or alarm response must not flap availability.
    soc = read_required(daly.get_soc, "SOC")
    time.sleep(REQUEST_GAP_SECONDS)
    mosfet = read_required(daly.get_mosfet_status, "MOSFET status")

    if not isinstance(soc, dict) or not isinstance(mosfet, dict):
        raise RuntimeError("Incomplete core response from DALY BMS")

    publish_mosfet_state(mosfet)
    values = {
        "pack_voltage": soc["total_voltage"],
        "current": soc["current"],
        "soc": soc["soc_percent"],
        "remaining_capacity": mosfet["capacity_ah"],
    }
    for key, value in values.items():
        publish_value(key, value)

    time.sleep(REQUEST_GAP_SECONDS)
    cells = read_optional(daly.get_cell_voltages, "cell voltages")
    if isinstance(cells, dict) and cells:
        publish_value("cell_delta", round(max(cells.values()) - min(cells.values()), 3))
        for cell_number, voltage in cells.items():
            publish_value(f"cell_{cell_number}_voltage", voltage)

    time.sleep(REQUEST_GAP_SECONDS)
    errors = read_optional(daly.get_errors, "alarms")
    if isinstance(errors, list):
        publish_value("alarms", "; ".join(errors) if errors else "None")


def on_message(mqtt_client, userdata, message):
    payload = message.payload.decode("utf-8", errors="replace").strip()
    if message.topic == f"{DISCOVERY_PREFIX}/status":
        if payload.lower() == "online":
            LOG.info("Home Assistant birth received; republishing discovery and state")
            publish_discovery()
            publish_cached_state()
            with state_lock:
                have_confirmed_state = "charge_mosfet" in state_cache
            if have_confirmed_state:
                mqtt_client.publish(AVAILABILITY_TOPIC, "online", qos=1, retain=True)
        return

    if message.retain:
        LOG.warning("Ignored retained command on %s", message.topic)
        return

    payload = payload.upper()
    if payload not in ("ON", "OFF"):
        LOG.warning("Ignored invalid payload %r on %s", payload, message.topic)
        return

    desired = payload == "ON"
    try:
        with serial_lock:
            daly = ensure_bms()
            if message.topic == CHARGE_COMMAND_TOPIC:
                LOG.warning("Setting charge MOSFET to %s", payload)
                daly.set_charge_mosfet(desired)
                state_key = "charging_mosfet"
            elif message.topic == DISCHARGE_COMMAND_TOPIC:
                LOG.warning("Setting discharge MOSFET to %s", payload)
                daly.set_discharge_mosfet(desired)
                state_key = "discharging_mosfet"
            else:
                return

            time.sleep(0.6)
            status = read_mosfet_state()
            publish_mosfet_state(status)
            client.publish(AVAILABILITY_TOPIC, "online", qos=1, retain=True)

            if bool(status[state_key]) != desired:
                LOG.error("BMS state did not match requested %s command", payload)
    except Exception:
        LOG.exception("MOSFET command failed")
        close_bms()


def make_mqtt_client():
    try:
        result = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION1,
            client_id="daly-ha-bridge",
            clean_session=True,
        )
    except (AttributeError, TypeError):
        result = mqtt.Client(client_id="daly-ha-bridge", clean_session=True)

    if MQTT_USERNAME:
        result.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    result.will_set(AVAILABILITY_TOPIC, "offline", qos=1, retain=True)
    result.reconnect_delay_set(min_delay=1, max_delay=30)
    result.on_connect = on_connect
    result.on_disconnect = on_disconnect
    result.on_message = on_message
    return result


def request_stop(signum, frame):
    stop_event.set()


def main():
    global client
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    client = make_mqtt_client()
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    consecutive_failures = 0
    try:
        while not stop_event.is_set():
            try:
                with serial_lock:
                    publish_measurements()
                consecutive_failures = 0
                client.publish(AVAILABILITY_TOPIC, "online", qos=1, retain=True)
            except Exception:
                consecutive_failures += 1
                LOG.exception(
                    "DALY core polling failed (%s/%s)",
                    consecutive_failures,
                    OFFLINE_AFTER_FAILURES,
                )
                close_bms()
                if consecutive_failures >= OFFLINE_AFTER_FAILURES:
                    client.publish(AVAILABILITY_TOPIC, "offline", qos=1, retain=True)
            stop_event.wait(POLL_SECONDS)
    finally:
        client.publish(AVAILABILITY_TOPIC, "offline", qos=1, retain=True)
        time.sleep(0.2)
        close_bms()
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
