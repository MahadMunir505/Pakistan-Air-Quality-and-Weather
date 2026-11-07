"""Central configuration: paths, variable metadata and Open-Meteo settings shared by ETL, EDA and the Streamlit app."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Load secrets from a local .env file if python-dotenv is installed (never commit .env).
# On Streamlit Community Cloud, put OPEN_METEO_API_KEY in the app's Secrets instead (optional).
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # python-dotenv is optional; real environment variables still work
    pass

RAW_PATH = BASE_DIR / "data" / "raw" / "original_dataset.csv"
OPEN_METEO_RAW_PATH = BASE_DIR / "data" / "raw" / "open_meteo_dataset.csv"
PROCESSED_PATH = BASE_DIR / "data" / "processed" / "cleaned_dataset.csv"
REJECTED_PATH = BASE_DIR / "data" / "processed" / "rejected_records.csv"
DB_PATH = BASE_DIR / "data" / "processed" / "air_quality.db"
DB_TABLE = "air_quality"
ETL_REPORT_PATH = BASE_DIR / "reports" / "etl_report.json"

# ---------------------------------------------------------------- Open-Meteo
# The FREE (non-commercial) tier needs NO API key: leave OPEN_METEO_API_KEY empty.
OPEN_METEO_API_KEY = os.getenv("OPEN_METEO_API_KEY", "").strip()
_HOST_PREFIX = "customer-" if OPEN_METEO_API_KEY else ""  # paid keys use the customer-* servers
OPEN_METEO_WEATHER_URL = f"https://{_HOST_PREFIX}api.open-meteo.com/v1/forecast"
OPEN_METEO_AIR_URL = f"https://{_HOST_PREFIX}air-quality-api.open-meteo.com/v1/air-quality"
OPEN_METEO_TIMEZONE = "Asia/Karachi"
OPEN_METEO_PAST_DAYS = 60          # both APIs allow at most 92 past days
OPEN_METEO_MAX_PAST_DAYS = 92

# Cities to download (name -> latitude, longitude). Edit this list to match your project.
CITIES = {
    "Lahore": (31.5204, 74.3587),
    "Karachi": (24.8607, 67.0011),
    "Islamabad": (33.6844, 73.0479),
    "Rawalpindi": (33.5651, 73.0169),
    "Faisalabad": (31.4504, 73.1350),
    "Multan": (30.1575, 71.5249),
    "Peshawar": (34.0151, 71.5249),
    "Quetta": (30.1798, 66.9750),
}

# Columns that really exist in the dataset (same names whether it comes from the CSV or Open-Meteo).
POLLUTANTS = {
    "aqi": "AQI (derived from PM2.5)",
    "pm2_5": "PM2.5 (µg/m³)",
    "pm10": "PM10 (µg/m³)",
    "carbon_monoxide": "CO (µg/m³)",
    "nitrogen_dioxide": "NO₂ (µg/m³)",
    "sulphur_dioxide": "SO₂ (µg/m³)",
    "ozone": "O₃ (µg/m³)",
    "dust": "Dust (µg/m³)",
}
WEATHER = {
    "temperature": "Temperature (°C)",
    "humidity": "Humidity (%)",
    "precipitation": "Precipitation (mm)",
    "wind_speed": "Wind speed (km/h)",
    "wind_direction": "Wind direction (°)",
    "pressure": "Pressure (hPa)",
}
AQI_CATEGORIES = [
    "Good",
    "Moderate",
    "Unhealthy for Sensitive Groups",
    "Unhealthy",
    "Very Unhealthy",
    "Hazardous",
]