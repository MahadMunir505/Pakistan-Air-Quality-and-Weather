"""Pakistan Air Quality and Weather — Streamlit dashboard (Phase 01: ETL & EDA).

Run locally:   streamlit run streamlit_app.py
Deploy:        point Streamlit Community Cloud at this file.
"""
import itertools
import json
import sqlite3

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

import config
from eda import analysis as an

st.set_page_config(page_title="Pakistan Air Quality and Weather", page_icon="🌫️", layout="wide")

CAT_COLORS = {
    "Good": "#2ecc8f", "Moderate": "#f5c542", "Unhealthy for Sensitive Groups": "#f28c28",
    "Unhealthy": "#ef5350", "Very Unhealthy": "#b36ae2", "Hazardous": "#c2415d",
}
PRIMARY = "#22b8f0"
_ids = itertools.count()  # unique keys for charts; resets on every script run


# ------------------------------------------------------------------ data loading
def run_etl_if_needed() -> None:
    """On a fresh deployment with no processed data, build it from the Open-Meteo API."""
    have_data = config.PROCESSED_PATH.exists() or config.DB_PATH.exists()
    if have_data and config.ETL_REPORT_PATH.exists():
        return
    with st.spinner("No processed data found — running the ETL pipeline from Open-Meteo (about a minute)…"):
        try:
            from etl import etl_pipeline
            etl_pipeline.run("open-meteo", past_days=config.OPEN_METEO_PAST_DAYS)
        except Exception as exc:  # show a readable message instead of a stack trace
            st.error(f"Could not build the dataset: {exc}")
            st.info("Run `python etl/etl_pipeline.py --input <csv>` locally and commit `data/processed/` "
                    "and `reports/etl_report.json`, or check the internet connection.")
            st.stop()


@st.cache_data(show_spinner="Loading data…")
def load_data() -> pd.DataFrame:
    if config.PROCESSED_PATH.exists():
        return pd.read_csv(config.PROCESSED_PATH, parse_dates=["timestamp", "date"])
    with sqlite3.connect(config.DB_PATH) as conn:
        return pd.read_sql_query(f"SELECT * FROM {config.DB_TABLE}", conn, parse_dates=["timestamp", "date"])


@st.cache_data
def load_etl_report() -> dict:
    return json.loads(config.ETL_REPORT_PATH.read_text(encoding="utf-8"))


run_etl_if_needed()
DF = load_data()
ETL = load_etl_report()


# ------------------------------------------------------------------ helpers
def show(fig, height: int = 340) -> None:
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        height=height, margin=dict(l=10, r=10, t=30, b=10), legend_title_text="",
    )
    st.plotly_chart(fig, width="stretch", key=f"chart{next(_ids)}")


def line(x, y, title: str = "", ylabel: str = "", color=PRIMARY):
    fig = px.line(x=x, y=y, title=title, labels={"x": "", "y": ylabel})
    fig.update_traces(line_color=color)
    return fig


def bar(x, y, title: str = "", ylabel: str = "", horizontal: bool = False, color=PRIMARY):
    if horizontal:
        fig = px.bar(x=y, y=x, orientation="h", title=title, labels={"x": ylabel, "y": ""})
    else:
        fig = px.bar(x=x, y=y, title=title, labels={"x": "", "y": ylabel})
    fig.update_traces(marker_color=color)
    return fig


def label(var: str) -> str:
    return an.LABELS.get(var, var)


def temporal(df: pd.DataFrame, var: str, by: str) -> pd.Series:
    """Mean of a variable grouped by hour / month / dow / season / weekend / year."""
    if by == "hour":
        return df.groupby("hour")[var].mean()
    if by == "month":
        return df.assign(ym=df["date"].dt.to_period("M").astype(str)).groupby("ym")[var].mean()
    if by == "dow":
        return df.groupby("day_of_week")[var].mean().reindex(an.DAY_ORDER).dropna()
    if by == "season":
        return df.groupby("season")[var].mean().reindex(an.SEASON_ORDER).dropna()
    if by == "weekend":
        s = df.groupby("is_weekend")[var].mean()
        s.index = ["Weekday" if i == 0 else "Weekend" for i in s.index]
        return s
    return df.groupby("year")[var].mean()


def daily(df: pd.DataFrame, var: str) -> pd.Series:
    return df.groupby("date")[var].mean()


def weekly_by_city(df: pd.DataFrame, var: str) -> pd.DataFrame:
    wk = df.assign(week_start=df["date"].dt.to_period("W-SUN").dt.start_time)
    return wk.groupby(["week_start", "city"])[var].mean().reset_index()


# ------------------------------------------------------------------ sidebar: navigation + filters
PAGES = ["Dashboard", "ETL", "EDA", "Air Quality", "Weather", "Cities", "Data", "About"]
st.sidebar.title("🌫️ Pakistan Air Quality & Weather")
st.sidebar.caption("Phase 01 — ETL & Exploratory Data Analysis")
page = st.sidebar.radio("Page", PAGES, label_visibility="collapsed")

NO_FILTER_PAGES = {"ETL", "About"}
d_min, d_max = DF["date"].min().date(), DF["date"].max().date()

if page in NO_FILTER_PAGES:
    df = DF
    var = "aqi"
else:
    st.sidebar.divider()
    st.sidebar.subheader("Filters")
    all_cities = sorted(DF["city"].unique())
    sel_cities = st.sidebar.multiselect("City", all_cities, default=all_cities)
    date_range = st.sidebar.date_input("Date range", value=(d_min, d_max), min_value=d_min, max_value=d_max)
    years = ["All"] + sorted(DF["year"].unique().tolist())
    sel_year = st.sidebar.selectbox("Year", years)
    month_names = DF.drop_duplicates("month").sort_values("month").set_index("month")["month_name"].to_dict()
    sel_month = st.sidebar.selectbox("Month", ["All"] + list(month_names), format_func=lambda m: m if m == "All" else month_names[m])
    var = st.sidebar.selectbox("Pollutant", list(config.POLLUTANTS), format_func=label)

    df = DF[DF["city"].isin(sel_cities)]
    if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
        df = df[(df["date"] >= pd.Timestamp(date_range[0])) & (df["date"] <= pd.Timestamp(date_range[1]))]
    if sel_year != "All":
        df = df[df["year"] == sel_year]
    if sel_month != "All":
        df = df[df["month"] == sel_month]

    # city ranking / comparison pages ignore the city filter (as in the original dashboard)
    df_all_cities = DF[(DF["date"] >= df["date"].min()) & (DF["date"] <= df["date"].max())] if len(df) else DF.iloc[0:0]
    if len(df) and sel_year != "All":
        df_all_cities = df_all_cities[df_all_cities["year"] == sel_year]
    if len(df) and sel_month != "All":
        df_all_cities = df_all_cities[df_all_cities["month"] == sel_month]

    st.sidebar.caption(f"{len(df):,} of {len(DF):,} records selected")
    if df.empty:
        st.warning("No records match the current filters. Adjust them in the sidebar.")
        st.stop()


# ------------------------------------------------------------------ pages
def page_dashboard():
    st.title("Dashboard")
    by_city_aqi = df.groupby("city")["aqi"].mean()
    c = st.columns(4)
    c[0].metric("Records", f"{len(df):,}")
    c[1].metric("Cities", df["city"].nunique())
    c[2].metric("Average AQI", f"{df['aqi'].mean():.0f}")
    c[3].metric("Maximum AQI", f"{df['aqi'].max():.0f}")
    c = st.columns(4)
    c[0].metric("Avg PM2.5 (µg/m³)", f"{df['pm2_5'].mean():.1f}")
    c[1].metric("Avg PM10 (µg/m³)", f"{df['pm10'].mean():.1f}")
    c[2].metric("Avg temperature (°C)", f"{df['temperature'].mean():.1f}")
    c[3].metric("Avg humidity (%)", f"{df['humidity'].mean():.0f}")
    c = st.columns(4)
    c[0].metric("Most polluted city", by_city_aqi.idxmax())
    c[1].metric("Cleanest city", by_city_aqi.idxmin())
    c[2].metric("Hours with AQI ≥ 151", f"{100 * (df['aqi'] >= 151).mean():.1f}%")
    c[3].metric("Data quality", f"{ETL['quality']['overall_quality_pct']:.1f}%")

    st.subheader("AQI trend over time")
    s = df.groupby(["date", "city"])["aqi"].mean().reset_index()
    show(px.line(s, x="date", y="aqi", color="city", labels={"date": "", "aqi": "AQI"}), 380)

    a, b = st.columns(2)
    with a:
        st.subheader(f"{label(var)} trend")
        s = daily(df, var)
        show(line(s.index, s.values, ylabel=label(var)))
    with b:
        st.subheader("AQI category share")
        cats = df["aqi_category"].value_counts().reindex(config.AQI_CATEGORIES).fillna(0).reset_index()
        cats.columns = ["category", "hours"]
        show(px.pie(cats, names="category", values="hours", hole=.45, color="category", color_discrete_map=CAT_COLORS))
    a, b = st.columns(2)
    with a:
        st.subheader("PM2.5 trend")
        s = daily(df, "pm2_5")
        show(line(s.index, s.values, ylabel="µg/m³"))
    with b:
        st.subheader("PM10 trend")
        s = daily(df, "pm10")
        show(line(s.index, s.values, ylabel="µg/m³", color="#2dd4bf"))
    a, b = st.columns(2)
    with a:
        st.subheader("Mean AQI by city")
        s = by_city_aqi.sort_values()
        show(bar(s.index, s.values, ylabel="AQI", horizontal=True))
    with b:
        st.subheader("Monthly mean AQI")
        s = temporal(df, "aqi", "month")
        show(bar(s.index, s.values, ylabel="AQI"))
    st.subheader("Seasonal mean AQI")
    s = temporal(df, "aqi", "season")
    show(bar(s.index, s.values, ylabel="AQI"), 300)


def page_etl():
    st.title("ETL pipeline summary")
    ex, tr, ld, q = ETL["extract"], ETL["transform"], ETL["load"], ETL["quality"]
    c = st.columns(3)
    c[0].metric("① Extracted rows", f"{ex['original_rows']:,}")
    c[1].metric("② Valid rows after cleaning", f"{tr['validation']['rows_valid']:,}")
    c[2].metric("③ Loaded rows", f"{ld['final_rows']:,}")
    st.caption(f"Report generated {ETL['generated_at']} · data from {ETL['date_range']['start']} to {ETL['date_range']['end']}")

    def kv(d: dict) -> pd.DataFrame:
        return pd.DataFrame({"Item": list(d), "Value": [str(v) for v in d.values()]})

    a, b, c = st.columns(3)
    with a:
        st.subheader("1 · Extraction")
        st.dataframe(kv({"Dataset": ex["dataset_name"], "File type": ex["file_type"], "Size (KB)": ex["file_size_kb"],
                         "Encoding": ex["encoding"], "Rows": f"{ex['original_rows']:,}", "Columns": ex["original_columns"],
                         "Source": ex["source"]}), hide_index=True, width="stretch")
    with b:
        st.subheader("2 · Transformation")
        dup = tr["duplicates"]
        st.dataframe(kv({"Duplicates removed": dup["removed"], "Rows invalid (Pydantic)": tr["validation"]["rows_rejected"],
                         "Missing before": tr["missing_before_total"], "Missing after": tr["missing_after_total"],
                         "AQI category mismatches": tr["aqi_checks"]["aqi_category_mismatches_vs_pm25"]}),
                     hide_index=True, width="stretch")
    with c:
        st.subheader("3 · Loading")
        st.dataframe(kv({"Final rows": f"{ld['final_rows']:,}", "Final columns": ld["final_columns"], "CSV": ld["processed_csv"],
                         "Database": ld["database"], "Table": ld["database_table"], "Status": ld["clean_dataset_status"]}),
                     hide_index=True, width="stretch")

    st.subheader("Data-quality scores")
    for name, key in [("Completeness", "completeness_pct"), ("Validity", "validity_pct"),
                      ("Uniqueness", "uniqueness_pct"), ("Overall quality", "overall_quality_pct"),
                      ("Rows retained", "rows_retained_pct")]:
        st.progress(min(max(q[key] / 100, 0.0), 1.0), text=f"{name}: {q[key]:.2f}%")

    st.subheader("Transformations performed")
    for i, t in enumerate(tr["transformations"], 1):
        st.markdown(f"{i}. {t}")

    st.subheader("Pydantic validation rules (invalid-value checks)")
    errs = tr["validation"]["errors_by_field"]
    rules = pd.DataFrame({"field": list(tr["validation"]["rules"]), "rule": list(tr["validation"]["rules"].values())})
    rules["rejected rows"] = rules["field"].map(errs).fillna(0).astype(int)
    st.dataframe(rules, hide_index=True, width="stretch")

    st.subheader("Outlier analysis (IQR & Z-score) — extremes retained as genuine events")
    st.dataframe(pd.DataFrame(tr["outliers"]), hide_index=True, width="stretch")

    st.subheader("Missing values before vs after cleaning")
    miss = pd.DataFrame({"column": list(tr["missing_before"]), "before": list(tr["missing_before"].values())})
    miss["after"] = miss["column"].map(tr["missing_after"])
    st.dataframe(miss, hide_index=True, width="stretch")


def page_eda():
    st.title("Exploratory data analysis")
    c = st.columns(4)
    c[0].metric("Records", f"{len(df):,}")
    c[1].metric("Features", df.shape[1])
    c[2].metric("Cities", df["city"].nunique())
    c[3].metric("Period", f"{df['timestamp'].min():%d %b} – {df['timestamp'].max():%d %b %Y}")

    st.subheader("Descriptive statistics")
    stats = pd.DataFrame(an.descriptive_statistics(df)).drop(columns=["variable"]).set_index("label")
    st.dataframe(stats.round(2), width="stretch")

    a, b = st.columns(2)
    with a:
        st.subheader(f"Distribution — {label(var)}")
        h = an.histogram(df[var])
        fig = bar(h["labels"], h["counts"], ylabel="hours")
        show(fig)
        st.caption(f"Mean {h['mean']:.1f} · median {h['median']:.1f}"
                   + (" — mean > median indicates a right-skewed distribution (a tail of severe episodes)." if h["mean"] > h["median"] else "."))
    with b:
        st.subheader(f"Mean {label(var)} by hour of day")
        s = temporal(df, var, "hour")
        show(line(s.index, s.values, ylabel=label(var)))
        st.caption(f"Peak at {int(s.idxmax()):02d}:00 ({s.max():.1f}); lowest at {int(s.idxmin()):02d}:00 ({s.min():.1f}).")
    a, b = st.columns(2)
    with a:
        st.subheader("Mean by weekday")
        s = temporal(df, var, "dow")
        show(bar(s.index, s.values, ylabel=label(var)))
    with b:
        st.subheader("Weekday vs weekend")
        s = temporal(df, var, "weekend")
        show(bar(s.index, s.values, ylabel=label(var)))

    st.subheader("Correlation matrix")
    corr = an.correlation_matrix(df)
    show(px.imshow(corr, text_auto=".2f", color_continuous_scale="RdBu_r", zmin=-1, zmax=1, aspect="auto"), 620)
    st.subheader("Automatic insights")
    for t in an.correlation_insights(corr):
        st.markdown(f"- {t}")

    st.subheader("Missing-value analysis (current selection)")
    st.dataframe(pd.DataFrame(an.missing_summary(df)), hide_index=True, width="stretch")


def page_air():
    st.title("Air quality analysis")
    st.subheader("Pollutant comparison by city (each pollutant as % of its highest city mean)")
    cols = [c for c in config.POLLUTANTS if c != "aqi"]
    means = df.groupby("city")[cols].mean()
    pct = (means / means.max() * 100).round(1)
    pct.columns = [label(c) for c in pct.columns]
    long = pct.reset_index().melt(id_vars="city", var_name="pollutant", value_name="% of highest city mean")
    show(px.bar(long, x="city", y="% of highest city mean", color="pollutant", barmode="group"), 400)

    a, b = st.columns(2)
    with a:
        st.subheader(f"Distribution — {label(var)}")
        h = an.histogram(df[var])
        show(bar(h["labels"], h["counts"], ylabel="hours"))
    with b:
        st.subheader("Box-plot statistics")
        box = an.boxplot_stats(df[var])
        st.dataframe(pd.DataFrame({"Statistic": [k.replace("_", " ").title() for k in box],
                                   "Value": [round(v, 2) for v in box.values()]}), hide_index=True, width="stretch")
    st.subheader(f"{label(var)} by hour of day")
    s = temporal(df, var, "hour")
    show(line(s.index, s.values, ylabel=label(var)))
    st.subheader(f"{label(var)} — monthly means")
    s = temporal(df, var, "month")
    show(bar(s.index, s.values, ylabel=label(var)))


def page_weather():
    st.title("Weather analysis")
    wvar = st.selectbox("Weather variable", list(config.WEATHER), format_func=label)
    st.subheader(f"{label(wvar)} trend")
    s = daily(df, wvar)
    show(line(s.index, s.values, ylabel=label(wvar)), 360)
    a, b = st.columns(2)
    with a:
        st.subheader(f"{label(wvar)} by city")
        s = df.groupby("city")[wvar].mean().sort_values()
        show(bar(s.index, s.values, ylabel=label(wvar), horizontal=True))
    with b:
        st.subheader(f"{label(wvar)} by hour")
        s = temporal(df, wvar, "hour")
        show(line(s.index, s.values, ylabel=label(wvar)))
    st.subheader(f"AQI vs {label(wvar)} (sample of up to 800 records)")
    sample = df.sample(min(800, len(df)), random_state=1)
    show(px.scatter(sample, x=wvar, y="aqi", color="city", opacity=.7, labels={wvar: label(wvar), "aqi": "AQI"}), 420)


def page_cities():
    st.title("City-based analysis")
    st.subheader("City ranking (means for the selected period, sorted by AQI)")
    rank = an.city_ranking(df_all_cities)
    st.dataframe(rank, hide_index=True, width="stretch")
    a, b = st.columns(2)
    with a:
        st.subheader(f"{label(var)} by city")
        s = df_all_cities.groupby("city")[var].mean().sort_values()
        show(bar(s.index, s.values, ylabel=label(var), horizontal=True))
    with b:
        st.subheader("Mean temperature by city")
        s = df_all_cities.groupby("city")["temperature"].mean().sort_values()
        show(bar(s.index, s.values, ylabel="°C", horizontal=True, color="#f28c28"))
    st.subheader(f"Weekly {label(var)} trend by city")
    w = weekly_by_city(df, var)
    show(px.line(w, x="week_start", y=var, color="city", labels={"week_start": "", var: label(var)}), 400)


def page_data():
    st.title("Cleaned dataset")
    a, b = st.columns([3, 2])
    search = a.text_input("Search", placeholder="City, season, category, month, weekday or date…").strip().lower()
    cols = b.multiselect("Columns", list(DF.columns), default=list(DF.columns))
    view = df
    if search:
        mask = pd.Series(False, index=view.index)
        for col in ["city", "season", "aqi_category", "month_name", "day_of_week"]:
            mask |= view[col].astype(str).str.lower().str.contains(search, regex=False)
        mask |= view["timestamp"].dt.strftime("%Y-%m-%d %H:%M").str.contains(search, regex=False)
        view = view[mask]
    view = view[cols] if cols else view
    st.caption(f"{len(view):,} rows")
    st.dataframe(view, hide_index=True, width="stretch", height=560)  # sortable by clicking headers
    st.download_button("Download filtered data (CSV)", view.to_csv(index=False).encode("utf-8"),
                       file_name="air_quality_filtered.csv", mime="text/csv")


def page_about():
    st.title("About")
    st.markdown("""
### Project
This dashboard is **Phase 01** of the “Pakistan Air Quality and Weather” project: an **ETL pipeline**
(pandas + Pydantic validation, loaded into a SQLite database) followed by a structured **exploratory data analysis**.

### Data
Hourly observations of pollutants (PM2.5, PM10, CO, NO₂, SO₂, O₃, dust) and weather (temperature, humidity,
precipitation, wind speed/direction, pressure) for the cities in the filter. Every number is computed from the processed dataset.

### Data source
When the pipeline runs with `--source open-meteo`, data comes from the free
[Open-Meteo](https://open-meteo.com/) Weather and Air Quality APIs (CC BY 4.0). Air-quality values are CAMS
(Copernicus) model estimates on a coarse grid, not ground-station measurements.

### Derived AQI
The source file contains an AQI *category* but no numeric AQI. A numeric AQI is derived from PM2.5 with the
US EPA breakpoints (and verified against the supplied category). It reflects PM2.5 only.

### Scope
Machine learning, prediction and forecasting are **not** part of Phase 01; they belong to later phases.

### Stack
Python · pandas · Pydantic · SQLite · Streamlit · Plotly
""")
    st.caption("Weather and air-quality data: Open-Meteo.com (CC BY 4.0) · Air quality: Copernicus Atmosphere Monitoring Service (CAMS)")


{
    "Dashboard": page_dashboard, "ETL": page_etl, "EDA": page_eda, "Air Quality": page_air,
    "Weather": page_weather, "Cities": page_cities, "Data": page_data, "About": page_about,
}[page]()