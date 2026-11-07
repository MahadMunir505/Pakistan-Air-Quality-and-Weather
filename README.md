# Pakistan Air Quality and Weather — Phase 01

**Phase 01 — ETL & Exploratory Data Analysis**

## 1. Description
A university project that builds an **ETL pipeline** (extract → transform/validate with pandas and Pydantic → load into a database) and a structured **exploratory data analysis** of hourly air-quality and weather observations for Pakistani cities, presented through an interactive dashboard (HTML/CSS/JavaScript + Flask API).

## 2. Phase-01 scope
Included: ETL, EDA, interactive frontend.
**Not included (future phases):** machine learning, prediction, forecasting, model training/evaluation, MLflow/PyTorch.
The proposal table lists "Extract data from an API". The supplied dataset is a CSV file, so extraction reads that file; the `extract()` function in `etl/etl_pipeline.py` is the single place to swap in an API call.

## 3. Dataset (verified programmatically)
| Item | Value |
|---|---|
| Rows × columns | 21,840 × 26 |
| Cities (10) | Faisalabad, Islamabad, Karachi, Lahore, Multan, Peshawar, Quetta, Rahim Yar Khan, Rawalpindi, Sialkot |
| Period | 2025-11-06 00:00 → 2026-02-04 23:00 (hourly, 2,184 hours per city) |
| Pollutants | pm2_5, pm10, carbon_monoxide, nitrogen_dioxide, sulphur_dioxide, ozone, dust |
| Weather | temperature, humidity, precipitation, wind_speed, wind_direction, pressure |
| Existing calendar fields | date, hour, day_of_week, month, month_name, year, is_weekend, season |
| AQI | only `aqi_category` is provided (no numeric AQI) |
| Missing / duplicates | 0 / 0 |

**Derived AQI:** a numeric `aqi` is computed from PM2.5 with US EPA breakpoints and cross-checked against `aqi_category` (0 mismatches). It reflects PM2.5 only.
**Limitation:** the data covers ~3 months (Autumn and Winter), so yearly/seasonal comparisons are limited. Visibility and cloud cover do not exist in the dataset and are not shown.

## 4. Technologies
Python, pandas, NumPy, Pydantic v2, SQLite, Flask, matplotlib (static EDA report), HTML, CSS, JavaScript, Chart.js (CDN – internet needed in the browser).

## 5. Structure
```
Pakistan-Air-Quality-and-Weather/
├── config.py                  paths + variable metadata
├── data/raw/original_dataset.csv        (copy of the untouched source)
├── data/processed/cleaned_dataset.csv, air_quality.db
├── etl/etl_pipeline.py        Extract / Transform / Load
├── eda/analysis.py            reusable EDA functions
├── eda/exploratory_analysis.py   -> reports/eda_report.html
├── reports/etl_report.json, eda_report.html
├── backend/app.py, backend/api/routes.py   Flask + REST API
├── frontend/index.html, css/style.css, js/script.js
└── requirements.txt
```

## 6. Installation
```bash
python -m venv venv
venv\Scripts\activate          # Windows   (Linux/macOS: source venv/bin/activate)
pip install -r requirements.txt
```

## 7. Run
```bash
# 1. ETL (pass the path to your CSV; later runs can omit --input)
python etl/etl_pipeline.py --input path/to/pakistan_air_quality_final_clean_v2.csv

# 2. EDA report (static HTML)
python eda/exploratory_analysis.py

# 3. Dashboard
python backend/app.py
```
Open **http://127.0.0.1:5000** — pages: Dashboard, ETL, EDA, Air Quality, Weather, Cities, Data, About. The full EDA report is at `/report`.

## 8. What the ETL does
1. **Extract** – safe read (utf-8 / utf-8-sig / latin-1 fallback), original copied to `data/raw/`.
2. **Transform** – duplicate removal; type conversion; **Pydantic** schema validation with physical limits (coordinates, humidity 0–100, wind direction 0–360, non-negative pollutants …) with rejected rows quarantined; column-aware missing-value strategy (per-city interpolation → median; mode for categories; no blind zero fill); calendar features rebuilt and cross-checked; derived AQI; IQR/Z-score outlier analysis (extreme pollution values are **retained**, not deleted).
3. **Load** – cleaned CSV + SQLite table + `reports/etl_report.json` (row/column counts, missing values before/after, duplicates, quality scores).

## 9. Screenshots
Add dashboard screenshots to a `screenshots/` folder.

## 10. Future work
Phase 02 and later (outside this phase): scikit-learn pipeline and PyTorch/MLflow modelling.
