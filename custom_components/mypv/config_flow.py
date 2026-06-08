import logging
import voluptuous as vol
import ipaddress
import aiohttp
import aiofiles
import asyncio
import json
import socket
import ssl
from aiohttp import ClientTimeout
from aiofiles import os as aio_os

from homeassistant import config_entries
import homeassistant.helpers.config_validation as cv

from homeassistant.const import (
    CONF_HOST,
    CONF_MONITORED_CONDITIONS,
    CONF_DEVICE,
)
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, SENSOR_TYPES, DEFAULT_MENU_OPTIONS, WIFI_METER_NAME, WIFI_METER_SENSOR_TYPES, DEFAULT_MONITORED_CONDITIONS, AC_ELWA_E_NAME, CONF_UPDATE_KEY, DEFAULT_UPDATE_KEY

_LOGGER = logging.getLogger(__name__)

_SSL_NO_VERIFY = ssl.create_default_context()
_SSL_NO_VERIFY.check_hostname = False
_SSL_NO_VERIFY.verify_mode = ssl.CERT_NONE

@callback
def mypv_entries(hass: HomeAssistant):
    """Return the hosts for the domain."""
    return set(
        (entry.data[CONF_HOST]) for entry in hass.config_entries.async_entries(DOMAIN)
    )

class MypvConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Mypv config flow."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._errors = {}
        self._info = {}
        self._host = None
        self._update_key = DEFAULT_UPDATE_KEY
        self._filtered_sensor_types = {}
        self._devices = {}
        self._device_name = None

    async def _authenticate_session(self, session, host) -> bool:
        """Authenticate against /auth.jsn and keep cookie in session."""
        if not self._update_key:
            return True

        timeout = ClientTimeout(total=5)
        async with session.post(
            f"https://{host}/auth.jsn",
            timeout=timeout,
            ssl=_SSL_NO_VERIFY,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"pw": self._update_key},
        ) as response:
            if response.status != 200:
                return False

            payload = await response.json(content_type=None)
            auth_flag = payload.get("auth")
            return auth_flag in (1, True, "1")

    def _host_in_configuration_exists(self, host) -> bool:
        """Return True if host exists in configuration."""
        return host in mypv_entries(self.hass)

    async def _get_sensor(self, host):
        """Fetch sensor data and update _filtered_sensor_types."""
        async with aiohttp.ClientSession() as session:
            try:
                is_authenticated = await self._authenticate_session(session, host)
                if not is_authenticated:
                    _LOGGER.error("Authentication failed during sensor fetch for host %s", host)
                    self._filtered_sensor_types = {}
                    return

                timeout = ClientTimeout(total=5)
                async with session.get(
                    f"https://{host}/data.jsn",
                    timeout=timeout,
                    ssl=_SSL_NO_VERIFY,
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        json_keys = set(data.keys())
                        self._filtered_sensor_types = {}

                        for key, value in SENSOR_TYPES.items():
                            if key in json_keys:
                                self._filtered_sensor_types[key] = value[0]
                        
                        if not self._filtered_sensor_types:
                            _LOGGER.warning("No matching sensors found on the device.")
                    else:
                        self._filtered_sensor_types = {}
                        _LOGGER.error(f"Can't connect to {host}: Bad HTTP Request status")

            except aiohttp.ClientError as e:
                _LOGGER.error(f"Failed to connect to {host}: {e}")
                self._filtered_sensor_types = {}
            except asyncio.TimeoutError as e:
                _LOGGER.error(f"Timeout error occurred on {host}: {e}")
                self._filtered_sensor_types = {}
    
    async def _get_wifi_meter_sensors(self):
        for key, value in WIFI_METER_SENSOR_TYPES.items():
            self._filtered_sensor_types[key] = value[0]

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        try:
            translation = await self._get_translations()
        except Exception:
            translation = DEFAULT_MENU_OPTIONS
        
        return self.async_show_menu(
            step_id="user",
            menu_options={
                "ip_known": translation.get("ip_known"),
                "ip_unknown": translation.get("ip_unknown"),
                "automatic_scan" : translation.get("automatic_scan")
            },
        )

    async def _get_translations(self):
        language = self.hass.config.language
        filepath = f"custom_components/mypv/translations/{language}.json"
        if not await aio_os.path.exists(filepath):
            filepath = "custom_components/mypv/translations/en.json"
        try:
            async with aiofiles.open(filepath, mode='r') as file:
                data = await file.read()

            data = json.loads(data)
            menu_options = data['config']['step']['user']['menu_options']
            return menu_options
        except json.JSONDecodeError:
            raise ValueError(f"Error during parsing from {filepath}.")
        except KeyError as e:
            raise KeyError(f"Missing keys in the JSON data: {e}")
        except Exception as e:
            raise RuntimeError(f"Unexpected error: {e}")
    
    async def async_step_ip_known(self, user_input=None):
        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._update_key = user_input.get(CONF_UPDATE_KEY, DEFAULT_UPDATE_KEY)
            if self.is_valid_ip(self._host):
                device = await self.check_ip_device(self._host)
                if device:
                    if not self._host_in_configuration_exists(self._host):
                        self._devices[self._host] = f"{device} ({self._host})"
                        self._device_name = device
                        if self._device_name == WIFI_METER_NAME:
                            await self._get_wifi_meter_sensors()
                        else:
                            await self._get_sensor(self._host)
                        return await self.async_step_sensors()
                    else:
                        self._errors[CONF_HOST] = "host_already_configured"
                else:
                    self._errors[CONF_HOST] = "could_not_connect"
            else:
                self._errors[CONF_HOST] = "invalid_ip"

        user_input = user_input or {CONF_HOST: "192.168.0.0"}

        ip_known_schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default="192.168.0.0"): str,
                vol.Optional(CONF_UPDATE_KEY, default=self._update_key): str,
            }
        )
        return self.async_show_form(
            step_id="ip_known",
            data_schema=ip_known_schema,
            errors=self._errors
        )
    
    async def async_step_ip_unknown(self, user_input=None):
        self._errors = {}
        if user_input is not None:
            subnet = user_input["subnet"]
            if self.is_valid_subnet(subnet):
                self._devices = await self.scan_devices(subnet)
                if self._devices:
                    return await self.async_step_select_device()
                else:
                    self._errors["base"] = "no_devices_found"
            else:
                self._errors["base"] = "invalid_subnet"
            
        ip_unknown_schema = vol.Schema(
            {vol.Required("subnet", default="192.168.0"): str}
        )

        return self.async_show_form(
            step_id="ip_unknown",
            data_schema=ip_unknown_schema,
            errors=self._errors,
        )  
    
    async def async_step_automatic_scan(self, user_input=None):
        self._devices = await self.scan_devices(self.get_subnet(self.get_own_ip()))
        if not self._devices:
            return self.async_abort(reason="no_devices_found")
        return await self.async_step_select_device()
    
    async def async_step_select_device(self, user_input=None):
        self._errors = {}
        if user_input is not None:
            self._host = list(self._devices.keys())[list(self._devices.values()).index(user_input["device"])]
            self._device_name = await self.check_ip_device(self._host)
            if self._device_name == WIFI_METER_NAME:
                await self._get_wifi_meter_sensors()
            else:
                await self._get_sensor(self._host)
            return await self.async_step_sensors()        
        
        select_device_schema = vol.Schema({
            vol.Required("device"): vol.In(list(self._devices.values()))
        })
        
        return self.async_show_form(
            step_id="select_device",
            data_schema=select_device_schema,
            description_placeholders={"devices": ", ".join(self._devices.values())},
            errors=self._errors
        )  
        
    def is_valid_ip(self, ip):
        try:
            ipaddress.ip_address(ip)
            return True
        except ValueError:
            _LOGGER.error("Invalid IP entered")
            return False
    
    def is_valid_subnet(self, subnet):
        cntPeriod = subnet.count('.')
        if cntPeriod != 2:
            _LOGGER.error("Invalid subnet")
            return False
        ip = subnet + ".0"
        return self.is_valid_ip(ip)
    
    def get_own_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = None
            _LOGGER.error("Unable to get IP address")
        finally:
            s.close()
        return ip
    
    def get_subnet(self, ip):
        if self.is_valid_ip(ip):
            octets = ip.split('.')
            subnet = f"{octets[0]}.{octets[1]}.{octets[2]}"
            return subnet
        return None
        
    async def check_ip_device(self, ip):
        async with aiohttp.ClientSession() as session:
            return await self.check_device(session, ip)
    
    async def scan_devices(self, subnet):
        devices = {}
        async with aiohttp.ClientSession() as session:
            tasks = []
            for i in range(1, 255):
                ip = f"{subnet}.{i}"
                tasks.append(self.check_device(session, ip))

            results = await asyncio.gather(*tasks)

            for ip, device_name in zip([f"{subnet}.{i}" for i in range(1, 255)], results):
                if device_name is not None and not self._host_in_configuration_exists(ip):
                    devices[ip] = f"{device_name} ({ip})"

        return devices
    
    async def check_device(self, session, ip):
        try:
            is_authenticated = await self._authenticate_session(session, ip)
            if not is_authenticated:
                return None

            timeout = ClientTimeout(total=15)
            async with session.get(
                f"https://{ip}/mypv_dev.jsn",
                timeout=timeout,
                ssl=_SSL_NO_VERIFY,
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("device")
                else:
                    return None
        except aiohttp.ClientError:
            return None
        except asyncio.TimeoutError:
            return None

    async def async_step_sensors(self, user_input=None):
        """Handle the sensor selection step."""
        self._errors = {}

        if user_input is not None:
            selected_sensors = user_input[CONF_MONITORED_CONDITIONS]
            self._update_key = user_input.get(CONF_UPDATE_KEY, self._update_key)
            self._info['device'] = user_input.get('device', self._info.get('device'))
            self._info['number'] = user_input.get('number', self._info.get('number'))
            return self.async_create_entry(
                title=f"{self._devices[self._host]}",
                data={
                    CONF_HOST: self._host,
                    CONF_UPDATE_KEY: self._update_key,
                    CONF_MONITORED_CONDITIONS: selected_sensors,
                    '_filtered_sensor_types': self._filtered_sensor_types,
                    'selected_sensors': selected_sensors,
                    CONF_DEVICE: self._device_name,
                },
            )
        
        monitored_conditions_key = "default"
        if self._device_name == WIFI_METER_NAME or self._device_name == AC_ELWA_E_NAME:
            monitored_conditions_key = self._device_name
        default_monitored_conditions = DEFAULT_MONITORED_CONDITIONS[monitored_conditions_key]

        setup_schema = vol.Schema(
            {
                vol.Required(
                    CONF_MONITORED_CONDITIONS, default = default_monitored_conditions
                ): cv.multi_select(self._filtered_sensor_types),
                vol.Optional(CONF_UPDATE_KEY, default=self._update_key): str,
            }
        )

        return self.async_show_form(
            step_id="sensors", data_schema=setup_schema, errors=self._errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return MypvOptionsFlowHandler(config_entry)
    
class MypvOptionsFlowHandler(config_entries.OptionsFlow):
    """Handles options flow"""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self.filtered_sensor_types = config_entry.data.get('_filtered_sensor_types', {})
        self.selected_sensors = config_entry.data.get('selected_sensors', [])
        self.update_key = config_entry.options.get(
            CONF_UPDATE_KEY,
            config_entry.data.get(CONF_UPDATE_KEY, DEFAULT_UPDATE_KEY),
        )

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_MONITORED_CONDITIONS: user_input[CONF_MONITORED_CONDITIONS],
                    CONF_UPDATE_KEY: user_input.get(CONF_UPDATE_KEY, self.update_key),
                },
            )
    
        options_schema = vol.Schema(
            {
                vol.Required(
                    CONF_MONITORED_CONDITIONS,
                    default=self.config_entry.options.get(
                        CONF_MONITORED_CONDITIONS, self.selected_sensors
                    ),
                ): cv.multi_select(self.filtered_sensor_types),
                vol.Optional(CONF_UPDATE_KEY, default=self.update_key): str,
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema)