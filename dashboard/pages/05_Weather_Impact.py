"""
dashboard/pages/05_Weather_Impact.py
──────────────────────────────────────
Page 5 — Weather Impact on AQI

Explores how meteorological conditions affect air quality:
  - Scatter plots: AQI vs Temperature, Humidity, Wind Speed, Precipitation
  - Correlation heatmap across all weather variables
  - Weather–AQI trend overlay
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
import pandas as pd
from datetime import date, timedelta

from dashboard.utils.db_queries import get_cities, get_date_range, get_weather_aqi
from dashboard.utils.charts import weather_scatter, weather_correlation_matrix

st.set_page_config(page_title="Weather Impact | AQI Dashboard", page_icon="🌡️", layout="wide")

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

st.markdown("## 🌡️ Weather Impact on AQI")
st.markdown(
    "Explore how temperature, humidity, wind speed, and precipitation "
    "correlate with air quality index values."
)

# ── Sidebar controls ──────────────────────────────────────────────────────────

cities = get_cities()
min_date, max_date = get_date_range()
today = date.today()
if min_date is None: min_date = today - timedelta(days=90)
if max_date is None: max_date = today
default_start = max(min_date, max_date - timedelta(days=90))

with st.sidebar:
    st.markdown("### 🎛️ Filters")
    selected_city = st.selectbox("Select City", cities, index=0)
    date_range = st.date_input(
        "Date Range",
        value=(default_start, max_date),
        min_value=min_date,
        max_value=max_date,
    )

if len(date_range) != 2:
    st.info("Select both a start date and an end date to continue.")
    st.stop()
start_date, end_date = date_range

# ── Fetch data ────────────────────────────────────────────────────────────────

with st.spinner("Loading weather–AQI data…"):
    df = get_weather_aqi(selected_city, start_date, end_date)

if df.empty:
    st.info(
        f"📭 No weather–AQI joined data for {selected_city}. "
        "Run the full ETL pipeline (including gold layer build) first."
    )
    st.stop()

# ── Correlation matrix ────────────────────────────────────────────────────────

st.markdown("### 🔗 Weather–AQI Correlation Matrix")
fig_corr = weather_correlation_matrix(df)
st.plotly_chart(fig_corr, use_container_width=True)

with st.expander("📖 How to Read Correlations"):
    st.markdown("""
    - **+1.0**: Perfect positive correlation (as variable increases, AQI increases)
    - **−1.0**: Perfect negative correlation (as variable increases, AQI decreases)
    - **0.0**: No linear relationship
    - Typical findings:
      - 🌬️ **Wind speed** often shows **negative correlation** (wind disperses pollutants)
      - 💧 **Precipitation** shows **negative correlation** (rain washes out particles)
      - 🌡️ **Temperature** relationship varies by season
      - 💦 **Humidity** often **positively** correlated in winter (foggy conditions trap pollutants)
    """)

st.markdown("---")

# ── Scatter plots grid ────────────────────────────────────────────────────────

st.markdown("### 🔬 Scatter Plots: Weather Variable vs AQI")

scatter_vars = [
    ("temp_avg", "Temperature (°C)"),
    ("humidity_avg", "Relative Humidity (%)"),
    ("wind_speed_avg", "Wind Speed (km/h)"),
    ("precipitation_sum", "Precipitation (mm)"),
]

available_vars = [(col, label) for col, label in scatter_vars if col in df.columns]

if available_vars:
    col1, col2 = st.columns(2)
    for i, (x_col, x_label) in enumerate(available_vars):
        target_col = col1 if i % 2 == 0 else col2
        with target_col:
            fig = weather_scatter(df, x_col, x_label)
            st.plotly_chart(fig, use_container_width=True)

st.markdown("---")

# ── AQI + weather trend overlay ───────────────────────────────────────────────

st.markdown("### 📈 AQI vs Wind Speed Over Time")

if "wind_speed_avg" in df.columns and not df.empty:
    import plotly.graph_objects as go

    df_sorted = df.sort_values("date_ist")
    fig_overlay = go.Figure()

    fig_overlay.add_trace(go.Scatter(
        x=df_sorted["date_ist"], y=df_sorted["aqi"],
        name="AQI", mode="lines",
        line=dict(color="#f72585", width=2),
        yaxis="y",
    ))
    fig_overlay.add_trace(go.Scatter(
        x=df_sorted["date_ist"], y=df_sorted["wind_speed_avg"],
        name="Wind Speed (km/h)", mode="lines",
        line=dict(color="#4cc9f0", width=2, dash="dot"),
        yaxis="y2",
        opacity=0.8,
    ))

    fig_overlay.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif"),
        yaxis=dict(title="AQI", color="#f72585"),
        yaxis2=dict(
            title="Wind Speed (km/h)", overlaying="y", side="right", color="#4cc9f0"
        ),
        xaxis=dict(title="Date"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=20, r=20, t=50, b=20),
    )
    st.plotly_chart(fig_overlay, use_container_width=True)

# ── Data download ─────────────────────────────────────────────────────────────

with st.expander("📋 View Data Table"):
    st.dataframe(df.sort_values("date_ist", ascending=False), use_container_width=True, hide_index=True)
    st.download_button(
        "⬇️ Download CSV",
        df.to_csv(index=False),
        file_name=f"weather_aqi_{selected_city}_{start_date}_{end_date}.csv",
        mime="text/csv",
    )
