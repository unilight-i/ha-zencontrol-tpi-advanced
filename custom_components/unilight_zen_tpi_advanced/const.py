"""Constants for the zencontrol TPI Advanced (Unilight) integration."""

DOMAIN = "unilight_zen_tpi_advanced"

CONF_SKIP_TEST = "skip_test"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_INCLUDE_UNLABELLED = "include_unlabelled"
CONF_EVENTS = "events_enabled"
CONF_EVENT_HOST = "event_host"            # IP of the computer running Home Assistant, as the controller sees it
CONF_EVENT_PORT = "event_port"
CONF_OCCUPANCY_HOLD = "occupancy_hold"
CONF_SENSORS = "sensors_enabled"          # options: motion sensors as binary_sensor entities (needs events)
CONF_BUTTONS = "buttons_enabled"          # options: keys of wall panels as event entities (needs events)
CONF_CONTROLLER = "controller"            # setup form: the controller picked from the list of found ones

DEFAULT_HOST = ""                # the setup form starts with an empty controller address
DEFAULT_PORT = 5108
DEFAULT_SCAN_INTERVAL = 30       # seconds
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 600

DEFAULT_EVENT_PORT = 6969
DEFAULT_OCCUPANCY_HOLD = 60      # seconds without a new motion report before the sensor shows "clear"
MIN_OCCUPANCY_HOLD = 5
MAX_OCCUPANCY_HOLD = 3600

EVENT_RESPONSE = "unilight_zen_tpi_advanced_response"
EVENT_TPI = "unilight_zen_tpi_advanced_event"       # fired for button, sensor, scene, variable and profile events
SIGNAL_OCCUPANCY = "unilight_zen_tpi_advanced_occupancy_{}"
SIGNAL_NEW_SENSOR = "unilight_zen_tpi_advanced_new_sensor_{}"
SIGNAL_EVENT = "unilight_zen_tpi_advanced_any_event_{}"
SIGNAL_BUTTON = "unilight_zen_tpi_advanced_button_{}"
SIGNAL_NEW_BUTTON = "unilight_zen_tpi_advanced_new_button_{}"

# Wire addresses used by lighting commands
GROUP_OFFSET = 64                # group n -> 64 + n
BROADCAST = 255

MAX_ARC = 254
DEFAULT_MIN_KELVIN = 2700
DEFAULT_MAX_KELVIN = 6500

# Delay before reading levels back after a command (lets the DALI fade finish)
REFRESH_AFTER_COMMAND = 1.5
REFRESH_AFTER_EVENT = 2.0
