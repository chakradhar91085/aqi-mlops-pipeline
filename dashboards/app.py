import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from db import get_engine  # noqa: E402

st.set_page_config(page_title="AQI Dashboard", layout="wide")
st.title("Air Quality Index — Trends & Pollutant Analysis")

engine = get_engine()

city_daily = pd.read_sql("SELECT * FROM mart.city_daily_aqi ORDER BY obs_date", engine)
station_daily = pd.read_sql("SELECT * FROM mart.station_daily_pollutant ORDER BY obs_date", engine)

if city_daily.empty:
    st.warning("No data in mart.city_daily_aqi yet — run the Airflow DAG first.")
    st.stop()

cities = sorted(city_daily["city"].dropna().unique())
selected_city = st.selectbox("City", cities)

city_df = city_daily[city_daily["city"] == selected_city]
station_df = station_daily[station_daily["city"] == selected_city]

col1, col2 = st.columns(2)
with col1:
    st.subheader("AQI trend by date")
    st.line_chart(city_df.set_index("obs_date")["avg_aqi"])
with col2:
    st.subheader("AQI category counts")
    st.bar_chart(city_df["aqi_category"].value_counts())

st.subheader("Pollutant contribution (station-level averages)")
st.bar_chart(station_df.groupby("pollutant")["avg_value"].mean())

st.subheader("City comparison — latest day avg AQI")
latest_date = city_daily["obs_date"].max()
st.bar_chart(city_daily[city_daily["obs_date"] == latest_date].set_index("city")["avg_aqi"])

st.subheader("Station-level daily detail")
st.dataframe(station_df)
