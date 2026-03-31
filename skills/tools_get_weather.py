import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests


class WeatherFetchError(Exception):
    """Raised when weather data cannot be retrieved."""
    pass


VALID_UNITS = {"metric", "imperial", "standard"}
DEFAULT_UNITS = "metric"
DEFAULT_LANG = "en"
BASE_URL = "https://api.openweathermap.org/data/2.5/weather"


def _parse_lat_lon(location: str) -> Optional[tuple]:
    """
    If location looks like 'lat,lon', return (lat, lon) floats.
    Otherwise return None.
    """
    pattern = r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$"
    match = re.match(pattern, location)
    if match:
        lat = float(match.group(1))
        lon = float(match.group(2))
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            return lat, lon
    return None


def _unix_to_iso(unix_ts: int, tz_offset_seconds: int) -> str:
    """
    Convert a Unix timestamp to an ISO-8601 string using the given timezone offset
    (seconds east of UTC), as provided by OpenWeatherMap.
    """
    tz = timezone(timedelta(seconds=tz_offset_seconds))
    dt = datetime.fromtimestamp(unix_ts, tz=tz)
    return dt.isoformat()


def get_weather(
    location: str,
    api_key: Optional[str] = None,
    units: str = DEFAULT_UNITS,
    lang: str = DEFAULT_LANG,
    timeout: int = 10,
    return_raw: bool = False,
) -> dict:
    """
    Fetch current weather for a given location using the OpenWeatherMap API.

    Parameters
    ----------
    location : str
        City name, ZIP/postal code, or "lat,lon" pair.
    api_key : str, optional
        OpenWeatherMap API key. Falls back to OPENWEATHER_API_KEY env var.
    units : str
        "metric", "imperial", or "standard". Defaults to "metric".
    lang : str
        Language code for weather description. Defaults to "en".
    timeout : int
        HTTP request timeout in seconds. Defaults to 10.
    return_raw : bool
        If True, return the full JSON payload as-is.

    Returns
    -------
    dict
        Trimmed weather data dict, or full JSON if return_raw=True.

    Raises
    ------
    ValueError
        If location is empty or API key is missing.
    WeatherFetchError
        On network errors, HTTP errors, or unexpected API responses.
    """
    # --- 1. Validate inputs ---
    if not isinstance(location, str) or not location.strip():
        raise ValueError("'location' must be a non-empty string.")

    location = location.strip()

    if not api_key:
        api_key = os.environ.get("OPENWEATHER_API_KEY")
    if not api_key:
        raise ValueError(
            "No API key provided. Pass 'api_key' or set the "
            "OPENWEATHER_API_KEY environment variable."
        )

    if units not in VALID_UNITS:
        units = DEFAULT_UNITS

    if not isinstance(lang, str) or not lang.strip():
        lang = DEFAULT_LANG
    else:
        lang = lang.strip().lower()

    # --- 2. Build request parameters ---
    params = {
        "units": units,
        "lang": lang,
        "appid": api_key,
    }

    lat_lon = _parse_lat_lon(location)
    if lat_lon:
        params["lat"] = lat_lon[0]
        params["lon"] = lat_lon[1]
    else:
        params["q"] = location

    # --- 3. Perform HTTP GET request ---
    try:
        with requests.get(BASE_URL, params=params, timeout=timeout) as resp:
            # --- 4. Parse response ---
            try:
                data = resp.json()
            except ValueError as exc:
                raise WeatherFetchError(
                    f"Failed to parse API response as JSON. "
                    f"HTTP status: {resp.status_code}. Raw text: {resp.text[:200]}"
                ) from exc

            # OpenWeatherMap embeds error codes in the JSON body even for some 4xx responses
            cod = data.get("cod")
            # cod can be int 200 or string "200"
            try:
                cod_int = int(cod)
            except (TypeError, ValueError):
                cod_int = resp.status_code

            if cod_int != 200:
                api_message = data.get("message", "No error message provided.")
                raise WeatherFetchError(
                    f"API returned error (cod={cod}): {api_message} "
                    f"[HTTP status: {resp.status_code}]"
                )

            if not resp.ok:
                raise WeatherFetchError(
                    f"HTTP error {resp.status_code}: {resp.reason}"
                )

    except requests.exceptions.Timeout as exc:
        raise WeatherFetchError(
            f"Request timed out after {timeout} seconds for location '{location}'."
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise WeatherFetchError(
            f"Connection error while fetching weather for '{location}': {exc}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise WeatherFetchError(
            f"Unexpected request error for location '{location}': {exc}"
        ) from exc

    # --- 7. Return raw payload if requested ---
    if return_raw:
        return data

    # --- 5 & 6. Extract fields and assemble output dict ---
    try:
        tz_offset = data["timezone"]  # seconds east of UTC

        country = data.get("sys", {}).get("country", "")
        location_name = data["name"]
        if country:
            location_name = f"{location_name},{country}"

        sunrise_iso = _unix_to_iso(data["sys"]["sunrise"], tz_offset)
        sunset_iso = _unix_to_iso(data["sys"]["sunset"], tz_offset)

        result = {
            "location_name": location_name,
            "temperature": data["main"]["temp"],
            "feels_like": data["main"]["feels_like"],
            "humidity": data["main"]["humidity"],
            "pressure": data["main"]["pressure"],
            "wind_speed": data["wind"]["speed"],
            "wind_deg": data["wind"].get("deg"),
            "weather_main": data["weather"][0]["main"],
            "weather_desc": data["weather"][0]["description"],
            "cloudiness": data["clouds"]["all"],
            "sunrise": sunrise_iso,
            "sunset": sunset_iso,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
    except KeyError as exc:
        raise WeatherFetchError(
            f"Unexpected API response structure; missing expected key: {exc}. "
            f"Consider using return_raw=True to inspect the full response."
        ) from exc

    return result