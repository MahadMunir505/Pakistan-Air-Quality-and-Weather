"""
Open-Meteo client — downloads hourly weather + air quality for the cities in config.CITIES.

* Weather      : https://api.open-meteo.com/v1/forecast              (free, no key)
* Air quality  : https://air-quality-api.open-meteo.com/v1/air-quality (free, no key)

The free tier is for NON-COMMERCIAL use and needs no API key. If OPEN_METEO_API_KEY is set
(commercial plan), requests automatically go to the `customer-*` servers with `apikey=...`.

Data notes (worth stating in your report):
  * Air-quality values are CAMS model data (~25-45 km grid), not ground-station readings.
  * Attribution is required: Open-Meteo.com (CC BY 4.0) and the Copernicus Atmosphere Monitoring Service.

Usage (stand-alone test):
    python etl/open_meteo_client.py --past-days 30
"""
import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

TIMEOUT = 30
MAX_RETRIES = 3

# Open-Meteo variable -> column name used throughout this project
WEATHER_VARS = {
    "temperature_2m": "temperature",
    "relative_humidity_2m": "humidity",
    "precipitation": "precipitation",
    "wind_speed_10m": "wind_speed",
    "wind_direction_10m": "wind_direction",
    "pressure_msl": "pressure",
}
AIR_VARS = ["pm10", "pm2_5", "carbon_monoxide", "nitrogen_dioxide", "sulphur_dioxide", "ozone", "dust"]

RAW_COLUMNS = ["timestamp", "city", "latitude", "longitude", *AIR_VARS, *WEATHER_VARS.values()]


def _redact(text: str) -> str:
    """Never let the API key leak into logs / error messages."""
    key = config.OPEN_METEO_API_KEY
    return text.replace(key, "***") if key else text


def _request(url: str, params: dict) -> dict:
    """GET with retries; adds the optional API key; raises a clear error on failure."""
    if config.OPEN_METEO_API_KEY:
        params = {**params, "apikey": config.OPEN_METEO_API_KEY}
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            if resp.status_code == 400:  # bad parameters: retrying will not help
                try:
                    reason = resp.json().get("reason", resp.text)
                except ValueError:
                    reason = resp.text
                raise RuntimeError(f"Open-Meteo rejected the request: {reason}")
            resp.raise_for_status()  # includes 429 (rate limit) -> retried below
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = _redact(str(exc))
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)
    raise RuntimeError(f"Open-Meteo request failed after {MAX_RETRIES} attempts: {last_error}")


def _hourly_frame(payload: dict, rename: dict | None = None) -> pd.DataFrame:
    hourly = payload.get("hourly")
    if not hourly or "time" not in hourly:
        raise RuntimeError("Open-Meteo response contains no hourly data.")
    return pd.DataFrame(hourly).rename(columns={"time": "timestamp", **(rename or {})})


def fetch_city(city: str, lat: float, lon: float, past_days: int) -> pd.DataFrame:
    """Hourly weather + air quality for one city, joined on timestamp."""
    base = {
        "latitude": lat, "longitude": lon, "timezone": config.OPEN_METEO_TIMEZONE,
        "past_days": past_days, "forecast_days": 1,
    }
    weather = _hourly_frame(
        _request(config.OPEN_METEO_WEATHER_URL, {**base, "hourly": ",".join(WEATHER_VARS)}), WEATHER_VARS)
    air = _hourly_frame(
        _request(config.OPEN_METEO_AIR_URL, {**base, "hourly": ",".join(AIR_VARS)}))
    df = weather.merge(air, on="timestamp", how="inner")
    df["city"], df["latitude"], df["longitude"] = city, lat, lon
    return df[RAW_COLUMNS]


def fetch_dataset(past_days: int = config.OPEN_METEO_PAST_DAYS, cities: dict | None = None) -> pd.DataFrame:
    """Download every city and return one raw DataFrame (no future hours)."""
    past_days = min(max(int(past_days), 1), config.OPEN_METEO_MAX_PAST_DAYS)
    cities = cities or config.CITIES
    frames = []
    for name, (lat, lon) in cities.items():
        try:
            frames.append(fetch_city(name, lat, lon, past_days))
            print(f"[OPEN-METEO] {name}: {len(frames[-1]):,} hourly rows")
        except RuntimeError as exc:
            print(f"[OPEN-METEO] WARNING - {name} skipped: {exc}", file=sys.stderr)
        time.sleep(0.3)  # be polite to the free public server
    if not frames:
        raise RuntimeError("No city could be downloaded. Check your internet connection and config.CITIES.")
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    now_local = pd.Timestamp.now(tz=config.OPEN_METEO_TIMEZONE).tz_localize(None)
    df = df[df["timestamp"] <= now_local]  # drop hours that have not happened yet
    return df.sort_values(["city", "timestamp"]).reset_index(drop=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Open-Meteo weather + air quality to a CSV")
    parser.add_argument("--past-days", type=int, default=config.OPEN_METEO_PAST_DAYS, help="1-92")
    parser.add_argument("--out", type=Path, default=config.OPEN_METEO_RAW_PATH)
    args = parser.parse_args()
    data = fetch_dataset(args.past_days)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(args.out, index=False)
    print(f"Saved {len(data):,} rows to {args.out}")