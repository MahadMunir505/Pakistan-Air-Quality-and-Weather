"""
ETL pipeline — Pakistan Air Quality and Weather (Phase 01)

Extract   : read the raw CSV (kept unchanged; a copy is stored in data/raw/)
Transform : Pydantic row validation, type conversion, duplicate / missing handling,
            date-time features, derived AQI, outlier analysis
Load      : cleaned CSV + SQLite database + JSON ETL report

Usage:
    python etl/etl_pipeline.py --input path/to/pakistan_air_quality.csv      # static CSV (default)
    python etl/etl_pipeline.py --source open-meteo --past-days 60            # live Open-Meteo APIs
"""
import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

NUMERIC_COLS = [
    "latitude", "longitude", "pm10", "pm2_5", "carbon_monoxide", "nitrogen_dioxide",
    "sulphur_dioxide", "ozone", "dust", "temperature", "humidity", "precipitation",
    "wind_speed", "wind_direction", "pressure",
]
CATEGORICAL_COLS = ["city", "day_of_week", "month_name", "season", "aqi_category"]

# US EPA PM2.5 breakpoints (conc_low, conc_high, aqi_low, aqi_high). These match the
# category boundaries found in the dataset's own `aqi_category` column.
PM25_BREAKPOINTS = [
    (0.0, 12.0, 0, 50),
    (12.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 150.4, 151, 200),
    (150.5, 250.4, 201, 300),
    (250.5, 500.4, 301, 500),
]
CATEGORY_BY_BAND = config.AQI_CATEGORIES
SEASON_BY_MONTH = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
                   6: "Summer", 7: "Summer", 8: "Summer", 9: "Autumn", 10: "Autumn", 11: "Autumn"}


# --------------------------------------------------------------------------- schema
class AirQualityRecord(BaseModel):
    """Pydantic schema with physical / domain limits for one hourly record."""
    model_config = ConfigDict(extra="ignore")

    timestamp: datetime
    city: str = Field(min_length=1)
    latitude: float = Field(ge=23.0, le=37.5)      # Pakistan bounding box
    longitude: float = Field(ge=60.0, le=78.0)
    pm10: float = Field(ge=0, le=2000)
    pm2_5: float = Field(ge=0, le=2000)
    carbon_monoxide: float = Field(ge=0, le=50000)
    nitrogen_dioxide: float = Field(ge=0, le=1000)
    sulphur_dioxide: float = Field(ge=0, le=1000)
    ozone: float = Field(ge=0, le=1000)
    dust: float = Field(ge=0, le=5000)
    temperature: float = Field(ge=-60, le=60)
    humidity: float = Field(ge=0, le=100)
    precipitation: float = Field(ge=0, le=500)
    wind_speed: float = Field(ge=0, le=300)
    wind_direction: float = Field(ge=0, le=360)
    pressure: float = Field(ge=800, le=1100)
    hour: int = Field(ge=0, le=23)
    month: int = Field(ge=1, le=12)
    year: int = Field(ge=2000, le=2100)


# --------------------------------------------------------------------------- helpers
def pm25_to_aqi(pm25: pd.Series) -> pd.Series:
    """Convert PM2.5 concentration to the US EPA AQI sub-index (piece-wise linear)."""
    conc = pm25.clip(lower=0).round(1)
    aqi = pd.Series(np.nan, index=conc.index)
    for c_lo, c_hi, i_lo, i_hi in PM25_BREAKPOINTS:
        mask = (conc >= c_lo) & (conc <= c_hi)
        aqi[mask] = (i_hi - i_lo) / (c_hi - c_lo) * (conc[mask] - c_lo) + i_lo
    aqi[conc > PM25_BREAKPOINTS[-1][1]] = 500
    return aqi.round().astype("Int64")


def category_from_pm25(pm25: pd.Series) -> pd.Series:
    """EPA category derived from PM2.5 — used only to cross-check `aqi_category`."""
    conc = pm25.round(1)
    bounds = [b[1] for b in PM25_BREAKPOINTS[:-1]]
    idx = np.searchsorted(bounds, conc.to_numpy(), side="left")
    cats = pd.Series([CATEGORY_BY_BAND[i] for i in idx], index=pm25.index, dtype="object")
    cats[pm25.isna()] = None  # searchsorted would otherwise label NaN as "Hazardous"
    return cats


# --------------------------------------------------------------------------- EXTRACT
def extract(input_path: Path) -> tuple[pd.DataFrame, dict]:
    """Read the dataset safely and keep an untouched copy in data/raw/."""
    if not input_path.exists():
        raise FileNotFoundError(f"Dataset not found: {input_path}")

    df, used_encoding = None, None
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(input_path, encoding=encoding)
            used_encoding = encoding
            break
        except UnicodeDecodeError:
            continue
    if df is None:
        raise ValueError("Could not decode the dataset with utf-8 / utf-8-sig / latin-1.")

    config.RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    if input_path.resolve() != config.RAW_PATH.resolve():
        shutil.copyfile(input_path, config.RAW_PATH)  # preserve the original file

    info = {
        "dataset_name": input_path.name,
        "file_type": input_path.suffix.lstrip(".").upper() or "UNKNOWN",
        "file_size_kb": round(input_path.stat().st_size / 1024, 1),
        "encoding": used_encoding,
        "original_rows": int(len(df)),
        "original_columns": int(df.shape[1]),
        "column_names": list(df.columns),
        "original_dtypes": {c: str(t) for c, t in df.dtypes.items()},
        "source": "Static CSV file (hourly air-quality + weather observations)",
    }
    print(f"[EXTRACT] {info['original_rows']:,} rows x {info['original_columns']} columns ({used_encoding})")
    return df, info


def extract_open_meteo(past_days: int) -> tuple[pd.DataFrame, dict]:
    """Download weather + air quality from the Open-Meteo APIs and keep a copy in data/raw/."""
    from etl.open_meteo_client import fetch_dataset  # imported here so CSV-only runs need no network libs

    df = fetch_dataset(past_days)
    config.OPEN_METEO_RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(config.OPEN_METEO_RAW_PATH, index=False)
    info = {
        "dataset_name": "Open-Meteo Weather + Air Quality APIs",
        "file_type": "API (saved as CSV)",
        "file_size_kb": round(config.OPEN_METEO_RAW_PATH.stat().st_size / 1024, 1),
        "encoding": "utf-8",
        "original_rows": int(len(df)),
        "original_columns": int(df.shape[1]),
        "column_names": list(df.columns),
        "original_dtypes": {c: str(t) for c, t in df.dtypes.items()},
        "source": "Open-Meteo.com (CC BY 4.0); air quality from CAMS (Copernicus) model data",
    }
    print(f"[EXTRACT] Open-Meteo API: {info['original_rows']:,} rows x {info['original_columns']} columns")
    return df, info


# --------------------------------------------------------------------------- TRANSFORM
def ensure_derived_columns(df: pd.DataFrame, log: list) -> pd.DataFrame:
    """Create calendar / season / category columns when the source (e.g. the API) does not supply them."""
    df = df.copy()
    ts = pd.to_datetime(df["timestamp"], errors="coerce")
    derived = {
        "hour": ts.dt.hour, "month": ts.dt.month, "year": ts.dt.year,
        "day_of_week": ts.dt.day_name(), "month_name": ts.dt.month_name(),
        "is_weekend": (ts.dt.dayofweek >= 5).astype(int),
        "season": ts.dt.month.map(SEASON_BY_MONTH),
    }
    added = [c for c in derived if c not in df.columns]
    for col in added:
        df[col] = derived[col]
    if "aqi_category" not in df.columns:
        df["aqi_category"] = category_from_pm25(pd.to_numeric(df["pm2_5"], errors="coerce"))
        added.append("aqi_category")
    if added:
        log.append("Columns not supplied by the source were derived: " + ", ".join(added))
    return df


def validate_with_pydantic(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Validate every row with the Pydantic schema; split into valid / rejected."""
    bad_idx, field_errors = [], {}
    for idx, record in zip(df.index, df.to_dict("records")):
        try:
            AirQualityRecord.model_validate(record)
        except ValidationError as exc:
            bad_idx.append(idx)
            for err in exc.errors():
                field = str(err["loc"][0]) if err["loc"] else "unknown"
                field_errors[field] = field_errors.get(field, 0) + 1
    rejected = df.loc[bad_idx]
    valid = df.drop(index=bad_idx)
    summary = {
        "rows_checked": int(len(df)),
        "rows_valid": int(len(valid)),
        "rows_rejected": int(len(rejected)),
        "errors_by_field": field_errors,
        "rules": {name: f"{f.metadata}" for name, f in AirQualityRecord.model_fields.items() if f.metadata},
    }
    print(f"[TRANSFORM] Pydantic validation: {summary['rows_valid']:,} valid, {summary['rows_rejected']:,} rejected")
    return valid, rejected, summary


def convert_types(df: pd.DataFrame, log: list) -> pd.DataFrame:
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for col in NUMERIC_COLS + ["hour", "month", "year", "is_weekend"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in CATEGORICAL_COLS:
        df[col] = df[col].astype("string").str.strip()
    df["city"] = df["city"].str.title()
    log.append("timestamp -> datetime; measurement columns -> numeric; text columns trimmed and cast to string")
    return df


def handle_missing(df: pd.DataFrame, log: list) -> tuple[pd.DataFrame, dict]:
    """Column-aware missing-value strategy (never blind zero-filling)."""
    df = df.sort_values(["city", "timestamp"]).reset_index(drop=True)
    decisions = {}
    for col in NUMERIC_COLS:
        n_missing = int(df[col].isna().sum())
        if n_missing == 0:
            continue
        share = n_missing / len(df)
        if share > 0.4:
            decisions[col] = f"{n_missing} missing ({share:.0%}) — excessive; column kept but flagged"
            continue
        if col in ("latitude", "longitude"):
            df[col] = df.groupby("city")[col].transform(lambda s: s.fillna(s.mode().iloc[0] if not s.mode().empty else s))
            decisions[col] = f"{n_missing} filled with the city's coordinate"
        else:
            df[col] = df.groupby("city")[col].transform(lambda s: s.interpolate(limit=6, limit_direction="both"))
            df[col] = df[col].fillna(df.groupby("city")[col].transform("median"))
            decisions[col] = f"{n_missing} filled by per-city time interpolation (max 6 h), then city median"
    for col in CATEGORICAL_COLS:
        n_missing = int(df[col].isna().sum())
        if n_missing:
            df[col] = df[col].fillna(df[col].mode().iloc[0])
            decisions[col] = f"{n_missing} filled with mode"
    log.append("Missing values: " + ("; ".join(f"{k}: {v}" for k, v in decisions.items()) if decisions else "none found — no imputation required"))
    return df, decisions


def add_datetime_features(df: pd.DataFrame, log: list) -> tuple[pd.DataFrame, dict]:
    """Rebuild calendar features from `timestamp` and cross-check the supplied ones."""
    df = df.copy()
    ts = df["timestamp"]
    rebuilt = {
        "hour": ts.dt.hour, "month": ts.dt.month, "year": ts.dt.year,
        "day_of_week": ts.dt.day_name(), "month_name": ts.dt.month_name(),
    }
    mismatches = {}
    for col, series in rebuilt.items():
        mismatches[col] = int((df[col].astype(str) != series.astype(str)).sum())
        df[col] = series if col in ("hour", "month", "year") else series.astype("string")
    df["date"] = ts.dt.normalize()
    df["day"] = ts.dt.day
    df["week"] = ts.dt.isocalendar().week.astype(int)
    df["is_weekend"] = (ts.dt.dayofweek >= 5).astype(int)
    log.append("Date-time features rebuilt from timestamp (hour, day, week, month, year, day_of_week, is_weekend); added: day, week")
    return df, mismatches


def add_aqi(df: pd.DataFrame, log: list) -> tuple[pd.DataFrame, dict]:
    """The dataset has no numeric AQI, so derive it from PM2.5 (US EPA method)."""
    df = df.copy()
    df["aqi"] = pm25_to_aqi(df["pm2_5"])
    derived_cat = category_from_pm25(df["pm2_5"])
    mismatch = int((derived_cat != df["aqi_category"]).sum())
    log.append("Derived numeric `aqi` from PM2.5 via US EPA breakpoints (dataset only provides aqi_category)")
    return df, {"aqi_category_mismatches_vs_pm25": mismatch}


def analyse_outliers(df: pd.DataFrame, log: list) -> list[dict]:
    """IQR + Z-score screening. Extremes are NOT removed: pollution spikes are real events."""
    rows = []
    for col in ["pm10", "pm2_5", "carbon_monoxide", "nitrogen_dioxide", "sulphur_dioxide", "ozone", "dust",
                "temperature", "humidity", "precipitation", "wind_speed", "pressure"]:
        s = df[col].astype(float)
        q1, q3 = s.quantile([0.25, 0.75])
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        z = (s - s.mean()) / s.std(ddof=0)
        rows.append({
            "variable": col,
            "q1": round(q1, 2), "q3": round(q3, 2),
            "iqr_lower": round(lo, 2), "iqr_upper": round(hi, 2),
            "iqr_outliers": int(((s < lo) | (s > hi)).sum()),
            "zscore_outliers": int((z.abs() > 3).sum()),
            "max": round(s.max(), 2),
            "decision": "Retained — values lie inside the physical limits, treated as genuine extremes"
                        if col in ("pm10", "pm2_5", "carbon_monoxide", "nitrogen_dioxide", "sulphur_dioxide", "ozone", "dust", "precipitation", "wind_speed")
                        else "Retained — within plausible range",
        })
    pm_gap = int((df["pm2_5"] > df["pm10"] + 0.5).sum())
    log.append(f"Outlier analysis (IQR, |z|>3) performed; none deleted. PM2.5 > PM10 inconsistencies found: {pm_gap}")
    return rows


def transform(df_raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    log: list[str] = []
    missing_before = df_raw.isna().sum()

    # 1. duplicates
    dup_full = int(df_raw.duplicated().sum())
    dup_key = int(df_raw.duplicated(subset=["city", "timestamp"]).sum())
    df = df_raw.drop_duplicates().drop_duplicates(subset=["city", "timestamp"], keep="first")
    duplicates = {
        "full_row_duplicates_before": dup_full,
        "city_timestamp_duplicates_before": dup_key,
        "removed": int(len(df_raw) - len(df)),
        "remaining": int(len(df)),
    }
    log.append(f"Duplicates: {duplicates['removed']} removed ({duplicates['remaining']:,} remain)")
    print(f"[TRANSFORM] duplicates removed: {duplicates['removed']}")

    # 2. derive any calendar / season / category columns the source did not supply
    df = ensure_derived_columns(df, log)

    # 3. types
    df = convert_types(df, log)

    # 4. missing values — handled BEFORE validation, otherwise NaN rows would fail the
    #    range checks and be quarantined instead of being imputed.
    df, missing_decisions = handle_missing(df, log)

    # 4b. Pydantic validation (invalid values)
    df, rejected, validation = validate_with_pydantic(df)
    log.append(f"Pydantic validation: {validation['rows_rejected']} invalid rows quarantined to rejected_records.csv")

    # 5. features
    df, calendar_mismatches = add_datetime_features(df, log)
    df, aqi_checks = add_aqi(df, log)

    # 6. outliers
    outliers = analyse_outliers(df, log)

    # 7. time-series completeness per city
    counts = df.groupby("city").size()
    full_range = pd.date_range(df["timestamp"].min(), df["timestamp"].max(), freq="h")
    gaps = {c: int(len(full_range) - n) for c, n in counts.items()}

    ordered = ["timestamp", "date", "city", "latitude", "longitude",
               "aqi", "aqi_category", "pm2_5", "pm10", "carbon_monoxide", "nitrogen_dioxide",
               "sulphur_dioxide", "ozone", "dust",
               "temperature", "humidity", "precipitation", "wind_speed", "wind_direction", "pressure",
               "year", "month", "month_name", "day", "week", "day_of_week", "hour", "is_weekend", "season"]
    df = df[ordered].sort_values(["city", "timestamp"]).reset_index(drop=True)

    details = {
        "missing_before": {c: int(v) for c, v in missing_before.items()},
        "missing_before_total": int(missing_before.sum()),
        "missing_after": {c: int(v) for c, v in df.isna().sum().items()},
        "missing_after_total": int(df.isna().sum().sum()),
        "missing_decisions": missing_decisions,
        "duplicates": duplicates,
        "validation": validation,
        "calendar_feature_mismatches_fixed": calendar_mismatches,
        "aqi_checks": aqi_checks,
        "outliers": outliers,
        "missing_hours_per_city": gaps,
        "type_conversions": {c: str(t) for c, t in df.dtypes.items()},
        "transformations": log,
    }
    return df, rejected, details


# --------------------------------------------------------------------------- LOAD
def load(df: pd.DataFrame, rejected: pd.DataFrame) -> dict:
    config.PROCESSED_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(config.PROCESSED_PATH, index=False)
    if len(rejected):
        rejected.to_csv(config.REJECTED_PATH, index=False)
    elif config.REJECTED_PATH.exists():
        config.REJECTED_PATH.unlink()

    db_df = df.copy()
    db_df["timestamp"] = db_df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
    db_df["date"] = db_df["date"].dt.strftime("%Y-%m-%d")
    with sqlite3.connect(config.DB_PATH) as conn:
        db_df.to_sql(config.DB_TABLE, conn, if_exists="replace", index=False)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_city_ts ON {config.DB_TABLE}(city, timestamp)")
        db_rows = conn.execute(f"SELECT COUNT(*) FROM {config.DB_TABLE}").fetchone()[0]
    print(f"[LOAD] {len(df):,} rows -> {config.PROCESSED_PATH.name} and SQLite table '{config.DB_TABLE}' ({db_rows:,} rows)")
    return {
        "final_rows": int(len(df)),
        "final_columns": int(df.shape[1]),
        "processed_csv": str(config.PROCESSED_PATH.relative_to(config.BASE_DIR)),
        "database": str(config.DB_PATH.relative_to(config.BASE_DIR)),
        "database_table": config.DB_TABLE,
        "database_rows_verified": int(db_rows),
        "clean_dataset_status": "Ready" if db_rows == len(df) else "Check required",
    }


def quality_scores(info: dict, details: dict, loaded: dict) -> dict:
    cells_before = info["original_rows"] * info["original_columns"]
    completeness = 100 * (1 - details["missing_before_total"] / cells_before)
    validity = 100 * details["validation"]["rows_valid"] / max(details["validation"]["rows_checked"], 1)
    uniqueness = 100 * (1 - details["duplicates"]["removed"] / info["original_rows"])
    overall = float(np.mean([completeness, validity, uniqueness]))
    return {
        "completeness_pct": round(completeness, 2),
        "validity_pct": round(validity, 2),
        "uniqueness_pct": round(uniqueness, 2),
        "overall_quality_pct": round(overall, 2),
        "rows_retained_pct": round(100 * loaded["final_rows"] / info["original_rows"], 2),
    }


def run(source: str = "csv", input_path: Path = config.RAW_PATH, past_days: int = config.OPEN_METEO_PAST_DAYS) -> dict:
    if source == "open-meteo":
        raw_df, extract_info = extract_open_meteo(past_days)
    else:
        raw_df, extract_info = extract(input_path)
    clean_df, rejected, details = transform(raw_df)
    loaded = load(clean_df, rejected)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "extract": extract_info,
        "transform": details,
        "load": loaded,
        "quality": quality_scores(extract_info, details, loaded),
        "date_range": {"start": str(clean_df["timestamp"].min()), "end": str(clean_df["timestamp"].max())},
        "cities": sorted(clean_df["city"].unique().tolist()),
    }
    config.ETL_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.ETL_REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[REPORT] {config.ETL_REPORT_PATH}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Phase-01 ETL pipeline")
    parser.add_argument("--source", choices=["csv", "open-meteo"], default="csv",
                        help="Where to read data from (default: csv)")
    parser.add_argument("--input", type=Path, default=config.RAW_PATH, help="Path to the raw CSV dataset (csv source)")
    parser.add_argument("--past-days", type=int, default=config.OPEN_METEO_PAST_DAYS,
                        help="Days of history to download from Open-Meteo, 1-92 (open-meteo source)")
    args = parser.parse_args()
    run(args.source, args.input, args.past_days)