"""
dashboard/utils/charts.py
──────────────────────────
Plotly chart builder functions for all 5 dashboard pages.
Returns plotly.graph_objects.Figure objects that Streamlit renders.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ── Colour system ──────────────────────────────────────────────────────────────

AQI_COLORS = {
    "Good":                           "#00e400",
    "Moderate":                        "#ffff00",
    "Unhealthy for Sensitive Groups":  "#ff7e00",
    "Unhealthy":                       "#ff0000",
    "Very Unhealthy":                  "#8f3f97",
    "Hazardous":                       "#7e0023",
    "Unknown":                         "#aaaaaa",
}

CITY_PALETTE = px.colors.qualitative.Bold
POLLUTANT_COLORS = {
    "pm25": "#e63946",
    "pm10": "#f4a261",
    "no2":  "#2a9d8f",
    "o3":   "#457b9d",
    "co":   "#6a4c93",
    "so2":  "#f1c40f",
}

CHART_THEME = "plotly_dark"
FONT_FAMILY = "Inter, Roboto, sans-serif"

_BASE_LAYOUT = dict(
    template=CHART_THEME,
    font=dict(family=FONT_FAMILY, size=13),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=20, r=20, t=50, b=20),
)


def _apply_base(fig: go.Figure, title: str = "") -> go.Figure:
    fig.update_layout(title=dict(text=title, font=dict(size=16)), **_BASE_LAYOUT)
    return fig


# ── Page 1: AQI Trend Charts ──────────────────────────────────────────────────

def aqi_trend_line(df: pd.DataFrame, cities: list[str]) -> go.Figure:
    """Multi-city daily AQI line chart with category colour bands."""
    fig = go.Figure()

    # Background AQI bands
    bands = [
        (0,   50,  "rgba(0,228,0,0.07)",   "Good"),
        (51,  100, "rgba(255,255,0,0.07)",  "Moderate"),
        (101, 150, "rgba(255,126,0,0.07)",  "USG"),
        (151, 200, "rgba(255,0,0,0.07)",    "Unhealthy"),
        (201, 300, "rgba(143,63,151,0.07)", "Very Unhealthy"),
        (301, 500, "rgba(126,0,35,0.07)",   "Hazardous"),
    ]
    for lo, hi, color, label in bands:
        fig.add_hrect(y0=lo, y1=hi, fillcolor=color, line_width=0, annotation_text=label,
                      annotation_position="right", annotation_font_size=10)

    for i, city in enumerate(cities):
        city_df = df[df["city"] == city].sort_values("date_ist")
        if city_df.empty:
            continue
        color = CITY_PALETTE[i % len(CITY_PALETTE)]
        fig.add_trace(go.Scatter(
            x=city_df["date_ist"], y=city_df["aqi"],
            name=city, mode="lines+markers",
            line=dict(color=color, width=2),
            marker=dict(size=4),
            hovertemplate=(
                "<b>%{fullData.name}</b><br>"
                "Date: %{x}<br>"
                "AQI: %{y}<br>"
                "<extra></extra>"
            ),
        ))

    # Zoom to the data instead of always showing the full 0-500 AQI scale —
    # a city sitting in "Moderate" all month shouldn't render as a flat line
    # squeezed under empty Unhealthy/Hazardous bands.
    max_aqi = df["aqi"].max() if not df.empty else 100
    y_top = max(100, max_aqi * 1.15)

    _apply_base(fig, "Daily AQI Trend by City")
    fig.update_yaxes(title="AQI", range=[0, y_top])
    fig.update_xaxes(title="Date")
    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    return fig


def rolling_aqi_comparison(df: pd.DataFrame) -> go.Figure:
    """7-day rolling average AQI per city."""
    fig = px.line(
        df, x="date_ist", y="aqi_7day_avg", color="city",
        color_discrete_sequence=CITY_PALETTE,
        labels={"aqi_7day_avg": "7-Day Rolling AQI", "date_ist": "Date", "city": "City"},
    )
    _apply_base(fig, "7-Day Rolling Average AQI")
    return fig


# ── Page 2: Pollutant Contribution ───────────────────────────────────────────

def pollutant_pie(df: pd.DataFrame, selected_date: str) -> go.Figure:
    """Pie chart of pollutant AQI sub-index contributions for one date."""
    day_df = df[df["date_ist"].astype(str) == selected_date]
    if day_df.empty:
        return go.Figure()

    colors = [POLLUTANT_COLORS.get(p, "#888") for p in day_df["parameter"]]
    fig = go.Figure(go.Pie(
        labels=day_df["parameter"].str.upper(),
        values=day_df["aqi_sub_index"],
        hole=0.45,
        marker=dict(colors=colors),
        textinfo="label+percent",
        hovertemplate="<b>%{label}</b><br>Sub-Index: %{value:.1f}<br>Share: %{percent}<extra></extra>",
    ))
    _apply_base(fig, f"Pollutant Contribution — {selected_date}")
    return fig


def pollutant_stacked_bar(df: pd.DataFrame) -> go.Figure:
    """Stacked bar chart of pollutant sub-indices over time."""
    fig = go.Figure()
    for param, color in POLLUTANT_COLORS.items():
        param_df = df[df["parameter"] == param].sort_values("date_ist")
        if param_df.empty:
            continue
        fig.add_trace(go.Bar(
            x=param_df["date_ist"],
            y=param_df["aqi_sub_index"],
            name=param.upper(),
            marker_color=color,
            hovertemplate=f"<b>{param.upper()}</b><br>Date: %{{x}}<br>Sub-index: %{{y:.1f}}<extra></extra>",
        ))
    fig.update_layout(barmode="stack")
    _apply_base(fig, "Pollutant AQI Sub-Index Over Time")
    fig.update_yaxes(title="AQI Sub-Index")
    fig.update_xaxes(title="Date")
    return fig


def pollutant_avg_bar(df: pd.DataFrame, date_label: str) -> go.Figure:
    """Horizontal bar: average pollutant concentration for selected date."""
    day_df = df[df["date_ist"].astype(str) == date_label].copy()
    if day_df.empty:
        return go.Figure()
    day_df["color"] = day_df["parameter"].map(POLLUTANT_COLORS)
    fig = go.Figure(go.Bar(
        x=day_df["avg_value"],
        y=day_df["parameter"].str.upper(),
        orientation="h",
        marker_color=day_df["color"],
        hovertemplate="<b>%{y}</b>: %{x:.2f} µg/m³<extra></extra>",
    ))
    _apply_base(fig, f"Average Concentration (µg/m³) — {date_label}")
    fig.update_xaxes(title="µg/m³")
    return fig


# ── Page 3: City Comparison ───────────────────────────────────────────────────

def city_comparison_heatmap(df: pd.DataFrame) -> go.Figure:
    """Heatmap of daily AQI values: cities (y) × dates (x)."""
    pivot = df.pivot_table(index="city", columns="date_ist", values="aqi", aggfunc="mean")
    colorscale = [
        [0.0,  "#00e400"],
        [0.1,  "#ffff00"],
        [0.3,  "#ff7e00"],
        [0.5,  "#ff0000"],
        [0.75, "#8f3f97"],
        [1.0,  "#7e0023"],
    ]
    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=[str(c) for c in pivot.columns],
        y=pivot.index.tolist(),
        colorscale=colorscale,
        zmin=0, zmax=500,
        colorbar=dict(title="AQI"),
        hovertemplate="<b>%{y}</b><br>Date: %{x}<br>AQI: %{z:.0f}<extra></extra>",
    ))
    _apply_base(fig, "City AQI Heatmap")
    fig.update_xaxes(title="Date", tickangle=45)
    fig.update_yaxes(title="City")
    return fig


def city_avg_aqi_bar(df_stats: pd.DataFrame) -> go.Figure:
    """Grouped bar chart: avg/max/min AQI per city."""
    fig = go.Figure()
    fig.add_trace(go.Bar(name="Avg AQI", x=df_stats["city"], y=df_stats["avg_aqi"],
                         marker_color="#4cc9f0"))
    fig.add_trace(go.Bar(name="Max AQI", x=df_stats["city"], y=df_stats["max_aqi"],
                         marker_color="#f72585"))
    fig.add_trace(go.Bar(name="Min AQI", x=df_stats["city"], y=df_stats["min_aqi"],
                         marker_color="#7bf1a8"))
    fig.update_layout(barmode="group")
    _apply_base(fig, "City AQI Statistics Comparison")
    fig.update_yaxes(title="AQI")
    return fig


# ── Page 4: Hourly & Monthly Patterns ────────────────────────────────────────

def hourly_heatmap(df: pd.DataFrame) -> go.Figure:
    """Hour-of-day × day-of-week AQI heatmap."""
    day_labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    pivot = df.pivot_table(index="day_of_week", columns="hour_of_day", values="avg_aqi")
    pivot.index = [day_labels[int(i)] for i in pivot.index]

    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=[f"{int(h):02d}:00" for h in pivot.columns],
        y=pivot.index.tolist(),
        colorscale="RdYlGn_r",
        colorbar=dict(title="Avg AQI"),
        hovertemplate="<b>%{y} %{x}</b><br>Avg AQI: %{z:.1f}<extra></extra>",
    ))
    _apply_base(fig, "Average AQI by Hour of Day and Day of Week")
    fig.update_xaxes(title="Hour (IST)")
    fig.update_yaxes(title="Day", categoryorder="array",
                     categoryarray=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], autorange="reversed")
    return fig


def monthly_box_plot(df: pd.DataFrame) -> go.Figure:
    """Box plot of AQI distribution by month."""
    if df.empty:
        return go.Figure()
    month_order = df.sort_values("month_start")["month_label"].unique().tolist()
    fig = px.box(
        df, x="month_label", y="aqi",
        category_orders={"month_label": month_order},
        color_discrete_sequence=["#4cc9f0"],
        labels={"aqi": "Daily AQI", "month_label": "Month"},
    )
    _apply_base(fig, "Monthly AQI Distribution")
    return fig


# ── Page 5: Weather Impact ────────────────────────────────────────────────────

def weather_scatter(df: pd.DataFrame, x_col: str, x_label: str) -> go.Figure:
    """Scatter plot: weather variable vs AQI with category colour coding and trend line."""
    if df.empty or x_col not in df.columns:
        return go.Figure()

    df = df.dropna(subset=[x_col, "aqi"])
    colors = df["aqi_category"].map(AQI_COLORS).fillna("#888")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df[x_col], y=df["aqi"],
        mode="markers",
        marker=dict(color=colors, size=8, opacity=0.75),
        text=df["aqi_category"],
        hovertemplate=(
            f"<b>{x_label}</b>: %{{x:.1f}}<br>"
            "AQI: %{y}<br>"
            "Category: %{text}<extra></extra>"
        ),
        name="Daily observation",
    ))

    # OLS trend line — needs >=2 points with actual variance in x, else polyfit's
    # SVD has nothing to fit and raises LinAlgError.
    if len(df) >= 2 and df[x_col].nunique() > 1:
        z = np.polyfit(df[x_col], df["aqi"], 1)
        p = np.poly1d(z)
        x_range = np.linspace(df[x_col].min(), df[x_col].max(), 100)
        fig.add_trace(go.Scatter(
            x=x_range, y=p(x_range),
            mode="lines",
            line=dict(color="#f72585", dash="dash", width=2),
            name="Trend",
        ))

    _apply_base(fig, f"AQI vs {x_label}")
    fig.update_xaxes(title=x_label)
    fig.update_yaxes(title="AQI")
    return fig


def weather_correlation_matrix(df: pd.DataFrame) -> go.Figure:
    """Correlation heatmap between weather variables and AQI."""
    cols = ["aqi", "temp_avg", "humidity_avg", "wind_speed_avg", "precipitation_sum"]
    available = [c for c in cols if c in df.columns]
    corr = df[available].corr()

    labels = {
        "aqi": "AQI", "temp_avg": "Temperature", "humidity_avg": "Humidity",
        "wind_speed_avg": "Wind Speed", "precipitation_sum": "Precipitation",
    }
    display_labels = [labels.get(c, c) for c in corr.columns]

    fig = go.Figure(go.Heatmap(
        z=corr.values,
        x=display_labels, y=display_labels,
        zmin=-1, zmax=1,
        colorscale="RdBu",
        colorbar=dict(title="Correlation"),
        text=corr.values.round(2),
        texttemplate="%{text}",
        hovertemplate="<b>%{x} vs %{y}</b><br>Correlation: %{z:.2f}<extra></extra>",
    ))
    _apply_base(fig, "Weather–AQI Correlation Matrix")
    return fig


# ── Part 2: forecast + monitoring charts ──────────────────────────────────────

def forecast_vs_actual(df: pd.DataFrame) -> go.Figure:
    """Predicted vs actual next-day AQI; backtest and live predictions drawn separately."""
    fig = go.Figure()
    known = df.dropna(subset=["actual_aqi"])
    fig.add_trace(go.Scatter(
        x=known["forecast_date"], y=known["actual_aqi"], mode="lines", name="Actual",
        line=dict(color="#e6edf3", width=2),
        hovertemplate="Actual: %{y:.0f}<br>%{x}<extra></extra>",
    ))
    styles = {"backtest": ("#58a6ff", "Predicted (backtest)"), "live": ("#f78166", "Predicted (live)")}
    for source, (color, label) in styles.items():
        d = df[df["source"] == source]
        if d.empty:
            continue
        fig.add_trace(go.Scatter(
            x=d["forecast_date"], y=d["predicted_aqi"],
            mode="lines+markers" if source == "live" else "lines",
            name=label, line=dict(color=color, width=2, dash="dot" if source == "backtest" else "solid"),
            hovertemplate=label + ": %{y:.0f}<br>%{x}<extra></extra>",
        ))
    fig.update_yaxes(title="AQI")
    return _apply_base(fig, "Next-Day AQI — Predicted vs Actual")


def metric_line(df: pd.DataFrame, metric: str, title: str, y_title: str) -> go.Figure:
    """One monitoring metric over time (scope 'all'), with its alert threshold if present."""
    d = df[(df["metric"] == metric) & (df["scope"] == "all")]
    fig = go.Figure()
    if not d.empty:
        fig.add_trace(go.Scatter(x=d["run_date"], y=d["value"], mode="lines+markers", name=metric,
                                 line=dict(color="#58a6ff", width=2)))
        breach = d[d["breached"] == True]  # noqa: E712
        if not breach.empty:
            fig.add_trace(go.Scatter(x=breach["run_date"], y=breach["value"], mode="markers", name="Breached",
                                     marker=dict(color="#ff4b4b", size=11, symbol="x")))
        thr = d["threshold"].dropna()
        if not thr.empty:
            fig.add_hline(y=float(thr.iloc[-1]), line_dash="dash", line_color="#f78166",
                          annotation_text="threshold", annotation_position="top left")
    fig.update_yaxes(title=y_title)
    fig.update_xaxes(type="date", tickformat="%b %d", dtick=86400000 * max(1, len(d) // 8))
    return _apply_base(fig, title)


def psi_heatmap(df: pd.DataFrame) -> go.Figure:
    """PSI per feature over time; >0.25 is significant drift."""
    d = df[df["metric"] == "psi"]
    fig = go.Figure()
    if not d.empty:
        pivot = d.pivot_table(index="scope", columns="run_date", values="value")
        fig.add_trace(go.Heatmap(
            z=pivot.values, x=[str(c) for c in pivot.columns], y=pivot.index.tolist(),
            colorscale=[[0, "#2a9d8f"], [0.2, "#e9c46a"], [0.5, "#e76f51"], [1, "#9d0208"]],
            zmin=0, zmax=0.5, colorbar=dict(title="PSI"),
            hovertemplate="<b>%{y}</b><br>%{x}<br>PSI: %{z:.3f}<extra></extra>",
        ))
    return _apply_base(fig, "Feature Drift (PSI vs training window)")
