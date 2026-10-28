"""
dashboard/pages/01_AQI_Trend.py
────────────────────────────────
Page 1 — AQI Trend

Shows daily AQI over time for selected cities with:
  - Multi-city line chart with AQI category colour bands
  - 7-day rolling average comparison
  - Recent AQI summary table
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
import pandas as pd
from datetime import date, timedelta

from dashboard.utils.db_queries import get_cities, get_date_range, get_aqi_trend, get_rolling_aqi
from dashboard.utils.charts import aqi_trend_line, rolling_aqi_comparison

st.set_page_config(page_title="AQI Trend | AQI Dashboard", page_icon="📈", layout="wide")

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

st.markdown("## 📈 AQI Trend Analysis")
st.markdown("Track daily Air Quality Index over time for selected Indian cities.")

# ── Sidebar controls ──────────────────────────────────────────────────────────

cities = get_cities()
min_date, max_date = get_date_range()
# Guard: get_date_range() can return None if the table is empty
today = date.today()
if min_date is None:
    min_date = today - timedelta(days=30)
if max_date is None:
    max_date = today
default_start = max(min_date, max_date - timedelta(days=30))

with st.sidebar:
    st.markdown("### 🎛️ Filters")
    selected_cities = st.multiselect(
        "Select Cities", cities, default=cities[:3],
        help="Choose one or more cities to compare"
    )
    date_range = st.date_input(
        "Date Range",
        value=(default_start, max_date),
        min_value=min_date,
        max_value=max_date,
    )
    show_rolling = st.toggle("Show 7-day rolling average", value=True)

if len(date_range) != 2:
    st.info("Please select a valid date range.")
    st.stop()

start_date, end_date = date_range

if not selected_cities:
    st.warning("Please select at least one city.")
    st.stop()

# ── Fetch data ────────────────────────────────────────────────────────────────

with st.spinner("Loading AQI data…"):
    df_trend = get_aqi_trend(selected_cities, start_date, end_date)

if df_trend.empty:
    st.info("📭 No data available for the selected cities and date range. Run the pipeline first.")
    st.stop()

# ── KPI row ───────────────────────────────────────────────────────────────────

st.markdown("### 📊 Summary Statistics")
kpi_cols = st.columns(len(selected_cities))
for col, city in zip(kpi_cols, selected_cities):
    city_df = df_trend[df_trend["city"] == city]
    if city_df.empty:
        continue
    avg_aqi = city_df["aqi"].mean()
    max_aqi = city_df["aqi"].max()
    last_aqi = city_df.sort_values("date_ist")["aqi"].iloc[-1]
    with col:
        st.metric(label=f"🏙️ {city} (latest)", value=f"{last_aqi:.0f}")
        st.caption(f"Avg {avg_aqi:.0f} · Max {max_aqi:.0f}")

st.markdown("---")

# ── Main trend chart ──────────────────────────────────────────────────────────

st.markdown("### 📈 Daily AQI Trend")
fig_trend = aqi_trend_line(df_trend, selected_cities)
st.plotly_chart(fig_trend, use_container_width=True)

# ── Rolling average chart ─────────────────────────────────────────────────────

if show_rolling:
    with st.spinner("Loading rolling average…"):
        df_rolling = get_rolling_aqi(selected_cities, start_date, end_date)
    if not df_rolling.empty:
        st.markdown("### 📉 7-Day Rolling Average AQI")
        fig_rolling = rolling_aqi_comparison(df_rolling)
        st.plotly_chart(fig_rolling, use_container_width=True)

# ── Data table ────────────────────────────────────────────────────────────────

with st.expander("📋 View Raw Data Table"):
    display_df = df_trend[["date_ist", "city", "aqi", "aqi_category", "dominant_pollutant",
                            "pm25_avg", "pm10_avg", "no2_avg", "o3_avg"]].copy()
    display_df.columns = ["Date", "City", "AQI", "Category", "Dominant Pollutant",
                          "PM2.5", "PM10", "NO2", "O3"]
    st.dataframe(
        display_df.sort_values(["Date", "City"], ascending=[False, True]),
        use_container_width=True,
        hide_index=True,
    )
    st.download_button(
        "⬇️ Download CSV",
        display_df.to_csv(index=False),
        file_name=f"aqi_trend_{start_date}_{end_date}.csv",
        mime="text/csv",
    )
