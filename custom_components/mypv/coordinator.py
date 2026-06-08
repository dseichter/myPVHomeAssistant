"""Provides the my-PV DataUpdateCoordinator."""

from datetime import timedelta
import logging
import aiohttp
import asyncio
import ssl
from aiohttp import ClientTimeout
from aiohttp import TCPConnector

from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .auth import authenticate_session
from .const import DOMAIN, WIFI_METER_NAME, CONF_UPDATE_KEY, DEFAULT_UPDATE_KEY

_LOGGER = logging.getLogger(__name__)

_SSL_NO_VERIFY = ssl.create_default_context() # NOSONAR
_SSL_NO_VERIFY.check_hostname = False
_SSL_NO_VERIFY.verify_mode = ssl.CERT_NONE # NOSONAR


class MYPVDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching my-PV data."""

    def __init__(self, hass: HomeAssistant, *, config: dict, options: dict):
        """Initialize my-PV coordinator."""
        self._host = config[CONF_HOST]
        self._update_key = options.get(
            CONF_UPDATE_KEY,
            config.get(
                CONF_UPDATE_KEY,
                options.get(CONF_PASSWORD, config.get(CONF_PASSWORD, DEFAULT_UPDATE_KEY)),
            ),
        )
        self._info = None
        self._setup = None
        self._data = "data.jsn"
        update_interval = timedelta(seconds=10)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )

    @property
    def update_key(self):
        """Return update key used for device authentication."""
        return self._update_key

    async def _fetch_json(self, session, url: str, timeout: ClientTimeout):
        """Fetch JSON from authenticated cookie session."""
        async with session.get(url, timeout=timeout, ssl=_SSL_NO_VERIFY) as response:
            if response.status == 200:
                return 200, await response.json(content_type=None)
            return response.status, None

    async def _async_update_data(self) -> dict:
        """Fetch data."""
        async with aiohttp.ClientSession(connector=TCPConnector(ssl=_SSL_NO_VERIFY)) as session:
            is_authenticated = await authenticate_session(
                session,
                self._host,
                self._update_key,
                _SSL_NO_VERIFY,
            )
            if not is_authenticated:
                _LOGGER.error(
                    "Authentication failed for my-PV device at %s. "
                    "Please check update key in integration options.",
                    self._host,
                )
                return {}

            if self._info is None:
                self._info = await self.async_update_info(session)
                if self._info is None:
                    return {}

            if self._data != "monitorjson":
                self._setup = await self.async_update_setup(session)
                if self._setup is None:
                    return {}

            data = await self.async_update_data(session)
            if data is None:
                return {}

            return {
                "data": data,
                "info": self._info,
                "setup": self._setup,
            }

    async def async_update_info(self, session):
        try:
            timeout = ClientTimeout(total=10)
            status, info = await self._fetch_json(
                session,
                f"https://{self._host}/mypv_dev.jsn",
                timeout,
            )
            if status == 200:
                if info.get("device") == WIFI_METER_NAME:
                    self._data = "monitorjson"
                return info
            elif status == 401:
                _LOGGER.error(
                    "Authentication failed for my-PV device at %s. "
                    "Please check update key in integration options.",
                    self._host,
                )
                return None
            else:
                _LOGGER.error(
                    f"Failed to connect to your my-PV device (status code: {status})"
                )
                return None
        except aiohttp.ClientConnectionError as e:
            _LOGGER.error(f"ClientConnectionError in async_update_info: {e}")
            return None
        except aiohttp.ClientPayloadError as e:
            _LOGGER.error(f"ClientPayloadError in async_update_info: {e}")
            return None
        except aiohttp.ClientResponseError as e:
            _LOGGER.error(f"ClientResponseError in async_update_info: {e}")
            return None
        except aiohttp.ClientTimeout as e:
            _LOGGER.error(f"ClientTimeout error in async_update_info: {e}")
            return None
        except asyncio.TimeoutError as e:
            _LOGGER.error(f"TimeoutError in async_update_info: {e}")
            return None

    async def async_update_setup(self, session):
        try:
            timeout = ClientTimeout(total=5)
            status, setup = await self._fetch_json(
                session,
                f"https://{self._host}/setup.jsn",
                timeout,
            )
            if status == 200:
                return setup
            elif status == 401:
                _LOGGER.error(
                    "Authentication failed for my-PV device at %s. "
                    "Please check update key in integration options.",
                    self._host,
                )
                return None
            else:
                _LOGGER.error(
                    f"Failed to connect to your my-PV device (status code: {status})"
                )
                return None
        except aiohttp.ClientError as e:
            _LOGGER.error(f"ClientError in async_update_setup: {e}")
            return None
        except asyncio.TimeoutError as e:
            _LOGGER.error(f"TimeoutError in async_update_setup: {e}")
            return None

    async def async_update_data(self, session):
        try:
            timeout = ClientTimeout(total=5)
            status, data = await self._fetch_json(
                session,
                f"https://{self._host}/{self._data}",
                timeout,
            )
            if status == 200:
                return data
            elif status == 401:
                _LOGGER.error(
                    "Authentication failed for my-PV device at %s. "
                    "Please check update key in integration options.",
                    self._host,
                )
                return None
            else:
                _LOGGER.error(
                    f"Failed to connect to your my-PV device (status code: {status})"
                )
                return None
        except aiohttp.ClientError as e:
            _LOGGER.error(f"ClientError in async_update_data: {e}")
            return None
        except asyncio.TimeoutError as e:
            _LOGGER.error(f"TimeoutError in async_update_data: {e}")
            return None
