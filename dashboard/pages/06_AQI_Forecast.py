"""
dashboard/pages/06_AQI_Forecast.py
───────────────────────────────────
Page 6 — Next-day AQI forecast (Part 2)

  - Live forecast from the FastAPI model service
  - Predicted vs actual history (backtest + live) from ml.predictions
"""

import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import requests
import streamlit as st

from dashboard.utils.charts import AQI_COLORS, forecast_vs_actual
from dashboard.utils.db_queries import get_cities, get_predictions

API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(page_title="AQI Forecast | AQI Dashboard", page_icon="🔮", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.stApp { background: linear-gradient(135deg, #0d1117 0%, #161b22 100%); }
[data-testid="stSidebar"] { background: #161b22; border-right: 1px solid #30363d; }
[data-testid="stSidebarNavItems"] { max-height: none !important; }
[data-testid="stSidebarNavViewButton"], [data-testid="stSidebarNavSeparator"] { display: none !important; }
</style>
""", unsafe_allow_html=True)

st.markdown("## 🔮 Next-Day AQI Forecast")
st.markdown("Tomorrow's AQI predicted by the champion model served through the FastAPI service.")

with st.sidebar:
    st.markdown("### 🎛️ Filters")
    city = st.selectbox("Select City", get_cities(), index=0)


@st.cache_data(ttl=300)
def fetch_forecast(city: str) -> dict:
    r = requests.get(f"{API_URL}/predict", params={"city": city}, timeout=15)
    if r.status_code != 200:
        return {"error": f"API returned {r.status_code}: {r.json().get('detail', r.text)}"}
    return r.json()


try:
    fc = fetch_forecast(city)
except requests.RequestException as exc:
    fc = {"error": f"Prediction API unreachable at {API_URL} ({type(exc).__name__})"}

if "error" in fc:
    st.warning(fc["error"])
else:
    if fc.get("data_age_days", 0) > 2:
        st.warning(f"This forecast is based on data from {fc['as_of']}, which is {fc['data_age_days']} days old, "
                   "so it is not really 'tomorrow'. Check the ingestion DAG in Airflow.")
    color = AQI_COLORS.get(fc["predicted_category"], "#aaaaaa")
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Forecast for {fc['forecast_date']}", f"{fc['predicted_aqi']:.0f} AQI")
    c2.markdown(
        f"<div style='padding-top:18px'>Category<br><span style='background:{color};color:#111;"
        f"padding:4px 12px;border-radius:12px;font-weight:700'>{fc['predicted_category']}</span></div>",
        unsafe_allow_html=True,
    )
    c3.metric("Model", f"{fc['model_name']} v{fc['model_version']}", help=f"Based on data up to {fc['as_of']}")

df = get_predictions(city)
if df.empty:
    st.info("No predictions stored yet. Train a model (`python -m ml.train`) to seed the backtest history.")
    st.stop()

st.markdown("### 📊 Forecast accuracy")
m1, m2 = st.columns(2)
for col, source, label in [(m1, "backtest", "Backtest MAE (held-out test period)"), (m2, "live", "Live MAE (real forecasts)")]:
    d = df[(df["source"] == source) & df["abs_error"].notna()]
    col.metric(label, f"{d['abs_error'].mean():.1f} AQI" if len(d) else "—", help=f"{len(d)} predictions scored")

st.plotly_chart(forecast_vs_actual(df), use_container_width=True)
