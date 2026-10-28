"""
dashboard/pages/02_Pollutant_Contribution.py
─────────────────────────────────────────────
Page 2 — Pollutant Contribution Analysis

Shows the breakdown of which pollutant (PM2.5, PM10, NO2, O3, CO, SO2)
is driving the AQI for a selected city and date range:
  - Pie chart of AQI sub-index contributions (single date)
  - Stacked bar chart over time
  - Horizontal bar: average concentrations in µg/m³
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
import pandas as pd
from datetime import date, timedelta

from dashboard.utils.db_queries import get_cities, get_date_range, get_pollutant_contribution
from dashboard.utils.charts import pollutant_pie, pollutant_stacked_bar, pollutant_avg_bar

st.set_page_config(page_title="Pollutant Contribution | AQI Dashboard", page_icon="🧪", layout="wide")

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

st.markdown("## 🧪 Pollutant Contribution Analysis")
st.markdown("Understand which pollutants are driving the AQI in each city.")

# ── Sidebar controls ──────────────────────────────────────────────────────────

cities = get_cities()
min_date, max_date = get_date_range()
today = date.today()
if min_date is None: min_date = today - timedelta(days=30)
if max_date is None: max_date = today
default_start = max(min_date, max_date - timedelta(days=30))

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

with st.spinner("Loading pollutant data…"):
    df_pollutant = get_pollutant_contribution(selected_city, start_date, end_date)

if df_pollutant.empty:
    st.info(f"📭 No pollutant data for {selected_city}. Run the ingestion + ETL pipeline first.")
    st.stop()

# ── Date selector for pie chart ───────────────────────────────────────────────

available_dates = sorted(df_pollutant["date_ist"].astype(str).unique())
selected_date = st.select_slider(
    "Select a Date for Detailed Breakdown",
    options=available_dates,
    value=available_dates[-1] if available_dates else None,
)

# ── Row 1: Pie + Concentration bar ───────────────────────────────────────────

st.markdown("### 🔬 Detailed Breakdown for Selected Date")
col1, col2 = st.columns(2)

with col1:
    fig_pie = pollutant_pie(df_pollutant, selected_date)
    st.plotly_chart(fig_pie, use_container_width=True)

with col2:
    fig_bar = pollutant_avg_bar(df_pollutant, selected_date)
    st.plotly_chart(fig_bar, use_container_width=True)

# ── Row 2: Stacked bar over time ──────────────────────────────────────────────

st.markdown("### 📊 Pollutant AQI Sub-Index Over Time")
fig_stacked = pollutant_stacked_bar(df_pollutant)
st.plotly_chart(fig_stacked, use_container_width=True)

# ── Pollutant info reference ──────────────────────────────────────────────────

with st.expander("ℹ️ Pollutant Reference Guide"):
    ref_data = {
        "Pollutant": ["PM2.5", "PM10", "NO2", "O3", "CO", "SO2"],
        "Full Name": [
            "Fine Particulate Matter",
            "Coarse Particulate Matter",
            "Nitrogen Dioxide",
            "Ozone",
            "Carbon Monoxide",
            "Sulfur Dioxide",
        ],
        "Primary Source": [
            "Vehicle exhaust, combustion",
            "Dust, construction",
            "Vehicle & industrial emissions",
            "Photochemical reactions",
            "Incomplete combustion",
            "Coal & oil burning",
        ],
        "Health Effect": [
            "Respiratory/cardiovascular disease",
            "Lung disease",
            "Respiratory irritation",
            "Chest pain, coughing",
            "Reduces oxygen delivery",
            "Throat/lung irritation",
        ],
        "AQI Averaging Time": ["24-hr", "24-hr", "1-hr", "8-hr", "8-hr", "1-hr"],
    }
    st.table(pd.DataFrame(ref_data))
