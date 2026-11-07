"""REST API blueprint — serves cleaned / aggregated data computed from the SQLite database."""
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Blueprint, jsonify, request

sys.path.append(str(Path(__file__).resolve().parents[2]))
import config  # noqa: E402
from eda import analysis as an  # noqa: E402

api = Blueprint("api", __name__, url_prefix="/api")

# ---- load the processed data once at start-up (21k rows: small enough for memory) ----
with sqlite3.connect(config.DB_PATH) as _conn:
    DF = pd.read_sql_query(f"SELECT * FROM {config.DB_TABLE}", _conn, parse_dates=["timestamp", "date"])
ETL_REPORT = json.loads(config.ETL_REPORT_PATH.read_text(encoding="utf-8"))
TABLE_COLUMNS = list(DF.columns)


def clean(value):
    """Make numpy / NaN values JSON-safe."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if np.isnan(value) else round(float(value), 3)
    if isinstance(value, (pd.Timestamp,)):
        return value.strftime("%Y-%m-%d %H:%M")
    return value


def ok(payload):
    return jsonify(clean(payload))


def filtered(apply_city: bool = True) -> pd.DataFrame:
    """Apply the global filters (city, start, end, year, month) taken from the query string."""
    df = DF
    city = request.args.get("city", "all")
    if apply_city and city != "all":
        df = df[df["city"].isin(city.split(","))]
    if request.args.get("start"):
        df = df[df["date"] >= pd.to_datetime(request.args["start"])]
    if request.args.get("end"):
        df = df[df["date"] <= pd.to_datetime(request.args["end"])]
    if request.args.get("year", "all") != "all":
        df = df[df["year"] == int(request.args["year"])]
    if request.args.get("month", "all") != "all":
        df = df[df["month"] == int(request.args["month"])]
    return df


def variable() -> str:
    var = request.args.get("var", "aqi")
    return var if var in an.ANALYSIS_VARS else "aqi"


# ------------------------------------------------------------------ metadata / reports
@api.get("/meta")
def meta():
    months = DF.drop_duplicates("month").sort_values(["year", "month"])[["month", "month_name"]]
    return ok({
        "cities": sorted(DF["city"].unique()),
        "date_min": DF["date"].min().strftime("%Y-%m-%d"),
        "date_max": DF["date"].max().strftime("%Y-%m-%d"),
        "years": sorted(DF["year"].unique().tolist()),
        "months": months.to_dict("records"),
        "pollutants": config.POLLUTANTS, "weather": config.WEATHER,
        "columns": TABLE_COLUMNS, "has_lahore": "Lahore" in DF["city"].values,
        "aqi_categories": config.AQI_CATEGORIES,
    })


@api.get("/etl")
def etl():
    return ok(ETL_REPORT)


@api.get("/summary")
def summary():
    df = filtered()
    if df.empty:
        return ok({"records": 0})
    missing = int(df.isna().sum().sum())
    return ok({
        "records": len(df), "cities": df["city"].nunique(), "features": df.shape[1],
        "avg_aqi": df["aqi"].mean(), "max_aqi": df["aqi"].max(),
        "avg_pm25": df["pm2_5"].mean(), "avg_pm10": df["pm10"].mean(),
        "avg_temp": df["temperature"].mean(), "avg_humidity": df["humidity"].mean(),
        "missing_values": missing,
        "data_quality": ETL_REPORT["quality"]["overall_quality_pct"],
        "worst_city": df.groupby("city")["aqi"].mean().idxmax(),
        "best_city": df.groupby("city")["aqi"].mean().idxmin(),
        "unhealthy_share": 100 * df["aqi"].ge(151).mean(),
    })


# ------------------------------------------------------------------ EDA endpoints
@api.get("/stats")
def stats():
    df = filtered()
    return ok({"stats": an.descriptive_statistics(df) if len(df) else [], "missing": an.missing_summary(df)})


@api.get("/timeseries")
def timeseries():
    """Mean of a variable per day/week; optionally one line per city."""
    df, var = filtered(), variable()
    freq = "date"
    if request.args.get("freq") == "week":
        df = df.assign(week_start=df["date"].dt.to_period("W-SUN").dt.start_time)  # Monday of each week
        freq = "week_start"
    if request.args.get("by_city") == "1":
        pivot = df.groupby([freq, "city"])[var].mean().unstack("city")
        return ok({"labels": [str(i)[:10] for i in pivot.index],
                   "series": {c: pivot[c].tolist() for c in pivot.columns}})
    s = df.groupby(freq)[var].mean()
    return ok({"labels": [str(i)[:10] for i in s.index], "values": s.tolist()})


@api.get("/by_city")
def by_city():
    df = filtered(apply_city=False)
    rank = an.city_ranking(df)
    return ok({"cities": rank.to_dict("records")})


@api.get("/distribution")
def distribution():
    df, var = filtered(), variable()
    if df.empty:
        return ok({"labels": [], "counts": []})
    return ok({**an.histogram(df[var]), "box": an.boxplot_stats(df[var])})


@api.get("/categories")
def categories():
    df = filtered()
    counts = df["aqi_category"].value_counts().reindex(config.AQI_CATEGORIES).fillna(0)
    return ok({"labels": list(counts.index), "counts": counts.tolist()})


@api.get("/temporal")
def temporal():
    """Mean of a variable grouped by hour / month / dow / season / year."""
    df, var = filtered(), variable()
    by = request.args.get("by", "hour")
    if by == "hour":
        s = df.groupby("hour")[var].mean()
    elif by == "month":
        s = df.groupby(["year", "month", "month_name"])[var].mean()
        s.index = [f"{n[:3]} {y}" for y, _, n in s.index]
    elif by == "dow":
        s = df.groupby("day_of_week")[var].mean().reindex(an.DAY_ORDER)
    elif by == "season":
        s = df.groupby("season")[var].mean().reindex(an.SEASON_ORDER)
    elif by == "weekend":
        s = df.groupby("is_weekend")[var].mean()
        s.index = ["Weekday" if i == 0 else "Weekend" for i in s.index]
    else:
        s = df.groupby("year")[var].mean()
    s = s.dropna()
    return ok({"labels": [str(i) for i in s.index], "values": s.tolist()})


@api.get("/correlation")
def correlation():
    df = filtered()
    corr = an.correlation_matrix(df)
    return ok({"columns": list(corr.columns), "matrix": corr.values.tolist(),
               "insights": an.correlation_insights(corr)})


@api.get("/scatter")
def scatter():
    """Sampled scatter points (max 800) for AQI vs a chosen weather variable."""
    df, var = filtered(), variable()
    sample = df.sample(min(800, len(df)), random_state=1) if len(df) else df
    return ok({"x": sample[var].tolist(), "y": sample["aqi"].tolist()})


# ------------------------------------------------------------------ data table
@api.get("/records")
def records():
    df = filtered()
    search = request.args.get("search", "").strip().lower()
    if search:
        text_cols = ["city", "season", "aqi_category", "month_name", "day_of_week"]
        mask = pd.Series(False, index=df.index)
        for col in text_cols:
            mask |= df[col].astype(str).str.lower().str.contains(search, regex=False)
        mask |= df["timestamp"].dt.strftime("%Y-%m-%d %H:%M").str.contains(search, regex=False)
        df = df[mask]
    sort = request.args.get("sort", "timestamp")
    if sort in TABLE_COLUMNS:
        df = df.sort_values(sort, ascending=request.args.get("dir", "asc") == "asc")
    cols = [c for c in request.args.get("cols", "").split(",") if c in TABLE_COLUMNS] or TABLE_COLUMNS
    size = min(max(int(request.args.get("size", 25)), 5), 100)
    page = max(int(request.args.get("page", 1)), 1)
    chunk = df.iloc[(page - 1) * size: page * size][cols].copy()
    for c in chunk.select_dtypes("datetime").columns:
        chunk[c] = chunk[c].dt.strftime("%Y-%m-%d %H:%M" if c == "timestamp" else "%Y-%m-%d")
    chunk = chunk.astype(object).where(chunk.notna(), None)
    return ok({"total": len(df), "page": page, "size": size, "columns": cols, "rows": chunk.values.tolist()})