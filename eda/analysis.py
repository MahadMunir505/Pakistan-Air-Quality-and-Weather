"""Reusable EDA functions (used by the Flask API and by exploratory_analysis.py)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

ANALYSIS_VARS = list(config.POLLUTANTS) + list(config.WEATHER)
LABELS = {**config.POLLUTANTS, **config.WEATHER}
SEASON_ORDER = ["Winter", "Spring", "Summer", "Autumn"]
DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def load_clean_data(path: Path = config.PROCESSED_PATH) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run `python etl/etl_pipeline.py` first.")
    df = pd.read_csv(path, parse_dates=["timestamp", "date"])
    return df


def descriptive_statistics(df: pd.DataFrame) -> list[dict]:
    rows = []
    for col in ANALYSIS_VARS:
        s = df[col].astype(float)
        rows.append({
            "variable": col, "label": LABELS[col], "count": int(s.count()),
            "mean": s.mean(), "std": s.std(), "min": s.min(),
            "q25": s.quantile(.25), "median": s.median(), "q75": s.quantile(.75),
            "max": s.max(), "skew": s.skew(),
        })
    return rows


def missing_summary(df: pd.DataFrame) -> list[dict]:
    return [{"column": c, "missing": int(df[c].isna().sum()),
             "pct": round(100 * df[c].isna().mean(), 2), "dtype": str(df[c].dtype)} for c in df.columns]


def histogram(series: pd.Series, bins: int = 30) -> dict:
    counts, edges = np.histogram(series.dropna(), bins=bins)
    return {"labels": [f"{edges[i]:.1f}–{edges[i+1]:.1f}" for i in range(len(counts))],
            "counts": counts.tolist(), "mean": float(series.mean()), "median": float(series.median())}


def boxplot_stats(series: pd.Series) -> dict:
    q1, med, q3 = series.quantile([.25, .5, .75])
    iqr = q3 - q1
    return {"min": float(series.min()), "q1": float(q1), "median": float(med), "q3": float(q3),
            "max": float(series.max()), "whisker_low": float(series[series >= q1 - 1.5 * iqr].min()),
            "whisker_high": float(series[series <= q3 + 1.5 * iqr].max())}


def correlation_matrix(df: pd.DataFrame, cols: list[str] | None = None) -> pd.DataFrame:
    cols = [c for c in (cols or ANALYSIS_VARS) if c in df.columns]
    return df[cols].astype(float).corr()


def _strength(r: float) -> str:
    a = abs(r)
    if a >= 0.7:
        return "strong"
    if a >= 0.4:
        return "moderate"
    if a >= 0.2:
        return "weak"
    return "negligible"


def correlation_insights(corr: pd.DataFrame, target: str = "aqi") -> list[str]:
    """Plain-language statements generated only from the computed coefficients."""
    if target not in corr.columns or len(corr) < 3:
        return ["Not enough data in the current selection to compute correlations."]
    insights = []
    for var in ["pm2_5", "pm10", "nitrogen_dioxide", "ozone", "temperature", "humidity", "wind_speed"]:
        if var not in corr.columns:
            continue
        r = corr.loc[target, var]
        if np.isnan(r):
            continue
        s = _strength(r)
        if s == "negligible":
            insights.append(f"{LABELS[var]} shows a negligible linear relationship with AQI (r = {r:.2f}).")
        else:
            direction = "positive" if r > 0 else "negative"
            meaning = "higher values accompany worse air quality" if r > 0 else "higher values accompany better air quality"
            insights.append(f"{LABELS[var]} has a {s} {direction} correlation with AQI (r = {r:.2f}); {meaning}.")
    insights.append("Note: AQI is derived from PM2.5 in this project, so their correlation is high by construction. "
                    "Correlation does not imply causation.")
    return insights


def group_mean(df: pd.DataFrame, by: str, var: str, order: list | None = None) -> pd.DataFrame:
    out = df.groupby(by)[var].mean().reset_index()
    if order:
        out[by] = pd.Categorical(out[by], categories=order, ordered=True)
        out = out.sort_values(by)
    return out


def city_ranking(df: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in ANALYSIS_VARS if c in df.columns]
    return df.groupby("city")[cols].mean().round(2).sort_values("aqi", ascending=False).reset_index()