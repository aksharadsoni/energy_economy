import streamlit as st
import os
import pandas as pd
import numpy as np
from datetime import timedelta

st.set_page_config(page_title="Energy-Economy Disconnect", layout="wide")

conn = st.connection("snowflake", ttl=os.getenv("SNOWFLAKE_CONNECTION_TTL"))

COUNTRY_NAMES = {
    "USA": "United States", "CHN": "China", "IND": "India", "DEU": "Germany",
    "GBR": "United Kingdom", "FRA": "France", "JPN": "Japan", "BRA": "Brazil",
    "CAN": "Canada", "AUS": "Australia", "KOR": "South Korea", "RUS": "Russia",
    "MEX": "Mexico", "IDN": "Indonesia", "TUR": "Turkey", "SAU": "Saudi Arabia",
    "ZAF": "South Africa", "NGA": "Nigeria", "ARG": "Argentina", "ITA": "Italy",
    "ESP": "Spain", "THA": "Thailand", "POL": "Poland", "NLD": "Netherlands",
    "SWE": "Sweden", "NOR": "Norway", "CHE": "Switzerland", "AUT": "Austria",
    "BEL": "Belgium", "CHL": "Chile", "COL": "Colombia", "EGY": "Egypt",
    "IRN": "Iran", "IRQ": "Iraq", "MYS": "Malaysia", "PHL": "Philippines",
    "PAK": "Pakistan", "PER": "Peru", "BGD": "Bangladesh", "VNM": "Vietnam",
    "NZL": "New Zealand", "FIN": "Finland", "DNK": "Denmark", "PRT": "Portugal",
    "GRC": "Greece", "CZE": "Czech Republic", "ROU": "Romania", "HUN": "Hungary",
    "ISR": "Israel", "ARE": "United Arab Emirates", "QAT": "Qatar", "KWT": "Kuwait",
}

ECON_INDICATORS = {
    "GDP (current USD)": "WDI_NY.GDP.MKTP.CD",
    "GDP (constant 2015 USD)": "WDI_NY.GDP.MKTP.KD",
    "GDP Growth (annual %)": "WDI_NY.GDP.MKTP.KD.ZG",
    "GDP per Capita (current USD)": "WDI_NY.GDP.PCAP.CD",
}

ENERGY_INDICATORS = {
    "Energy Use (kg oil eq. per capita)": "ESG_EG.USE.PCAP.KG.OE",
    "Electric Power Consumption (kWh per capita)": "WDI_EG.USE.ELEC.KH.PC",
    "Fossil Fuel Consumption (% of total)": "ESG_EG.USE.COMM.FO.ZS",
    "Renewable Energy Consumption (% of total)": "ESG_EG.FEC.RNEW.ZS",
    "Energy Intensity (MJ/$2021 PPP GDP)": "ESG_EG.EGY.PRIM.PP.KD",
    "GHG Emissions (Mt CO2e)": "ESG_EN.GHG.ALL.MT.CE.AR5",
    "Carbon Intensity of GDP": "WDI_EN.GHG.CO2.RT.GDP.KD",
}

ALL_INDICATORS = {**ECON_INDICATORS, **ENERGY_INDICATORS}


@st.cache_data(ttl=timedelta(hours=1))
def load_wb_data(variable_ids, geo_ids):
    var_list = "'" + "','".join(variable_ids) + "'"
    geo_list = "'" + "','".join(geo_ids) + "'"
    sql = f"""
        SELECT GEO_ID, VARIABLE, VARIABLE_NAME,
               YEAR(DATE) AS YEAR, VALUE, UNIT
        FROM SNOWFLAKE_PUBLIC_DATA_FREE.PUBLIC_DATA_FREE.WORLD_BANK_TIMESERIES
        WHERE VARIABLE IN ({var_list})
          AND GEO_ID IN ({geo_list})
          AND VALUE IS NOT NULL
        ORDER BY GEO_ID, VARIABLE, YEAR
    """
    return conn.query(sql, ttl=timedelta(hours=1))


@st.cache_data(ttl=timedelta(hours=1))
def get_available_countries():
    sql = """
        SELECT DISTINCT SUBSTRING(GEO_ID, 9) AS CODE
        FROM SNOWFLAKE_PUBLIC_DATA_FREE.PUBLIC_DATA_FREE.WORLD_BANK_TIMESERIES
        WHERE GEO_ID LIKE 'country/%'
          AND VARIABLE = 'WDI_NY.GDP.MKTP.CD'
          AND VALUE IS NOT NULL
        ORDER BY CODE
    """
    df = conn.query(sql, ttl=timedelta(hours=1))
    codes = df["CODE"].tolist()
    return [c for c in codes if c in COUNTRY_NAMES]


def pct_change_col(series):
    return series.pct_change() * 100


def classify_signal(econ_chg, energy_chg):
    if pd.isna(econ_chg) or pd.isna(energy_chg):
        return "Insufficient Data"
    disconnect = econ_chg - energy_chg
    if abs(disconnect) < 1.0:
        return "Aligned"
    if econ_chg > 0 and energy_chg >= 0 and econ_chg > energy_chg:
        return "Improving Productivity"
    if energy_chg > 0 and econ_chg >= 0 and energy_chg > econ_chg:
        return "Intensity Increase"
    if econ_chg < 0 and energy_chg > 0:
        return "Economic-Energy Stress"
    if econ_chg > 0 and energy_chg < 0:
        return "Strong Decoupling"
    if econ_chg < 0 and energy_chg < 0:
        return "Joint Decline"
    return "Mixed Signal"


# ── SIDEBAR FILTERS ──
st.sidebar.markdown("## Filters")

avail_countries = get_available_countries()
default_countries = [c for c in ["USA", "CHN", "DEU", "IND", "BRA", "JPN"] if c in avail_countries]

selected_codes = st.sidebar.multiselect(
    "Countries",
    options=avail_countries,
    default=default_countries[:3],
    format_func=lambda c: COUNTRY_NAMES.get(c, c),
)

year_range = st.sidebar.slider("Year Range", 1970, 2023, (1990, 2022))

econ_label = st.sidebar.selectbox("Economic Indicator", list(ECON_INDICATORS.keys()), index=2)
energy_label = st.sidebar.selectbox("Energy Indicator", list(ENERGY_INDICATORS.keys()), index=0)

econ_var = ECON_INDICATORS[econ_label]
energy_var = ENERGY_INDICATORS[energy_label]

if not selected_codes:
    st.warning("Select at least one country from the sidebar.")
    st.stop()

geo_ids = [f"country/{c}" for c in selected_codes]
all_vars = list(set([econ_var, energy_var] + list(ECON_INDICATORS.values()) + list(ENERGY_INDICATORS.values())))

with st.spinner("Loading data..."):
    raw = load_wb_data(all_vars, geo_ids)

if raw.empty:
    st.error("No data found for the selected filters.")
    st.stop()

raw["COUNTRY"] = raw["GEO_ID"].str.slice(8).map(COUNTRY_NAMES).fillna(raw["GEO_ID"].str.slice(8))
raw["CODE"] = raw["GEO_ID"].str.slice(8)


def get_indicator_ts(df, variable, year_min, year_max):
    sub = df[(df["VARIABLE"] == variable) & (df["YEAR"] >= year_min) & (df["YEAR"] <= year_max)]
    return sub[["COUNTRY", "CODE", "YEAR", "VALUE", "UNIT"]].copy()


primary_country = selected_codes[0]
primary_name = COUNTRY_NAMES.get(primary_country, primary_country)

econ_ts = get_indicator_ts(raw, econ_var, year_range[0], year_range[1])
energy_ts = get_indicator_ts(raw, energy_var, year_range[0], year_range[1])

# ── TITLE ──
st.markdown(
    """
    <h1 style='text-align:center; font-size:2.4rem; letter-spacing:2px;'>
    ENERGY — ECONOMY DISCONNECT
    </h1>
    <p style='text-align:center; color:#888; font-size:1rem; margin-bottom:2rem;'>
    Uncovering hidden divergences between energy activity and economic performance
    </p>
    """,
    unsafe_allow_html=True,
)

# ════════════════════════════════════════════════════════
# 1. EXECUTIVE OVERVIEW
# ════════════════════════════════════════════════════════
st.markdown("---")
st.subheader(f"Executive Overview — {primary_name}")

pc_econ = econ_ts[econ_ts["CODE"] == primary_country].sort_values("YEAR")
pc_energy = energy_ts[energy_ts["CODE"] == primary_country].sort_values("YEAR")

if not pc_econ.empty and not pc_energy.empty:
    if econ_var == "WDI_NY.GDP.MKTP.KD.ZG":
        latest_econ_growth = pc_econ.iloc[-1]["VALUE"]
        avg_econ_growth = pc_econ["VALUE"].mean()
    else:
        pc_econ_vals = pc_econ.set_index("YEAR")["VALUE"]
        latest_econ_growth = pct_change_col(pc_econ_vals).iloc[-1] if len(pc_econ_vals) > 1 else 0
        avg_econ_growth = pct_change_col(pc_econ_vals).mean() if len(pc_econ_vals) > 1 else 0

    pc_energy_vals = pc_energy.set_index("YEAR")["VALUE"]
    latest_energy_chg = pct_change_col(pc_energy_vals).iloc[-1] if len(pc_energy_vals) > 1 else 0
    avg_energy_chg = pct_change_col(pc_energy_vals).mean() if len(pc_energy_vals) > 1 else 0

    disconnect_val = latest_econ_growth - latest_energy_chg
    avg_disconnect = avg_econ_growth - avg_energy_chg

    if not pc_econ.empty and not pc_energy.empty and len(pc_econ) > 1 and len(pc_energy) > 1:
        merged_kpi = pc_econ[["YEAR", "VALUE"]].merge(
            pc_energy[["YEAR", "VALUE"]], on="YEAR", suffixes=("_ECON", "_ENERGY")
        )
        if not merged_kpi.empty and merged_kpi["VALUE_ENERGY"].iloc[-1] != 0:
            energy_productivity = merged_kpi["VALUE_ECON"].iloc[-1] / merged_kpi["VALUE_ENERGY"].iloc[-1]
        else:
            energy_productivity = None

        if econ_var == "WDI_NY.GDP.MKTP.KD.ZG":
            econ_changes = merged_kpi["VALUE_ECON"]
        else:
            econ_changes = pct_change_col(merged_kpi["VALUE_ECON"])
        energy_changes = pct_change_col(merged_kpi["VALUE_ENERGY"])
        abs_disconnect = (econ_changes - energy_changes).abs()
        anomaly_threshold = abs_disconnect.mean() + 2 * abs_disconnect.std() if abs_disconnect.std() > 0 else 999
        n_anomalies = int((abs_disconnect > anomaly_threshold).sum())
    else:
        energy_productivity = None
        n_anomalies = 0

    kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
    kpi1.metric("Econ Growth", f"{latest_econ_growth:.1f}%", f"avg {avg_econ_growth:.1f}%", border=True)
    kpi2.metric("Energy Change", f"{latest_energy_chg:.1f}%", f"avg {avg_energy_chg:.1f}%", border=True)
    if energy_productivity is not None:
        kpi3.metric("Energy Productivity", f"{energy_productivity:.2f}", border=True)
    else:
        kpi3.metric("Energy Productivity", "N/A", border=True)
    kpi4.metric("Disconnect", f"{disconnect_val:+.1f}pp", f"avg {avg_disconnect:+.1f}pp", border=True)
    kpi5.metric("Anomalies Detected", str(n_anomalies), border=True)

    # Auto-generated insight
    if latest_econ_growth > 0 and latest_energy_chg >= 0 and latest_econ_growth > latest_energy_chg:
        insight = f"Economic output increased ({latest_econ_growth:.1f}%) while energy consumption grew more slowly ({latest_energy_chg:.1f}%), indicating an improvement in energy productivity."
    elif latest_econ_growth > 0 and latest_energy_chg < 0:
        insight = f"Economic output grew ({latest_econ_growth:.1f}%) while energy consumption declined ({latest_energy_chg:.1f}%), suggesting strong decoupling of growth from energy use."
    elif latest_econ_growth < 0 and latest_energy_chg > 0:
        insight = f"Economic output declined ({latest_econ_growth:.1f}%) while energy demand increased ({latest_energy_chg:.1f}%), a potential economic-energy stress signal."
    elif latest_energy_chg > latest_econ_growth:
        insight = f"Energy consumption grew ({latest_energy_chg:.1f}%) faster than economic output ({latest_econ_growth:.1f}%), indicating increasing energy intensity."
    else:
        insight = f"Economic growth: {latest_econ_growth:.1f}%, Energy change: {latest_energy_chg:.1f}% for the latest period."

    st.info(insight)
else:
    st.warning("Insufficient data for the primary country KPIs.")

# ════════════════════════════════════════════════════════
# 2. ENERGY vs ECONOMY — THE MAIN VIEW
# ════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("Energy vs Economy — Time Series")

for code in selected_codes:
    name = COUNTRY_NAMES.get(code, code)
    c_econ = econ_ts[econ_ts["CODE"] == code].sort_values("YEAR")
    c_energy = energy_ts[energy_ts["CODE"] == code].sort_values("YEAR")

    if c_econ.empty or c_energy.empty:
        continue

    merged = c_econ[["YEAR", "VALUE"]].merge(
        c_energy[["YEAR", "VALUE"]], on="YEAR", suffixes=("_ECON", "_ENERGY")
    ).sort_values("YEAR")

    if merged.empty:
        continue

    econ_min, econ_max = merged["VALUE_ECON"].min(), merged["VALUE_ECON"].max()
    energy_min, energy_max = merged["VALUE_ENERGY"].min(), merged["VALUE_ENERGY"].max()
    econ_range = econ_max - econ_min if econ_max != econ_min else 1
    energy_range = energy_max - energy_min if energy_max != energy_min else 1

    chart_df = pd.DataFrame({
        "Year": merged["YEAR"],
        econ_label + " (norm)": (merged["VALUE_ECON"] - econ_min) / econ_range,
        energy_label + " (norm)": (merged["VALUE_ENERGY"] - energy_min) / energy_range,
    }).set_index("Year")

    with st.container(border=True):
        st.markdown(f"**{name}** — Normalized comparison")
        st.line_chart(chart_df, use_container_width=True)

# ════════════════════════════════════════════════════════
# 3. DISCONNECT ENGINE
# ════════════════════════════════════════════════════════
st.markdown("---")
st.markdown(
    """
    <h2 style='text-align:center; color:#00D4AA; letter-spacing:3px;'>
    DISCONNECT ENGINE
    </h2>
    <p style='text-align:center; color:#888;'>
    The signature analytical feature — quantifying energy-economy divergence
    </p>
    """,
    unsafe_allow_html=True,
)

disconnect_frames = []
for code in selected_codes:
    name = COUNTRY_NAMES.get(code, code)
    c_econ = econ_ts[econ_ts["CODE"] == code].sort_values("YEAR")
    c_energy = energy_ts[energy_ts["CODE"] == code].sort_values("YEAR")
    if c_econ.empty or c_energy.empty:
        continue

    merged = c_econ[["YEAR", "VALUE"]].merge(
        c_energy[["YEAR", "VALUE"]], on="YEAR", suffixes=("_ECON", "_ENERGY")
    ).sort_values("YEAR").reset_index(drop=True)

    if len(merged) < 2:
        continue

    if econ_var == "WDI_NY.GDP.MKTP.KD.ZG":
        merged["ECON_CHG"] = merged["VALUE_ECON"]
    else:
        merged["ECON_CHG"] = pct_change_col(merged["VALUE_ECON"])

    merged["ENERGY_CHG"] = pct_change_col(merged["VALUE_ENERGY"])
    merged["DISCONNECT"] = merged["ECON_CHG"] - merged["ENERGY_CHG"]
    merged["ENERGY_PRODUCTIVITY"] = np.where(
        merged["VALUE_ENERGY"] != 0,
        merged["VALUE_ECON"] / merged["VALUE_ENERGY"],
        np.nan,
    )
    merged["SIGNAL"] = merged.apply(lambda r: classify_signal(r["ECON_CHG"], r["ENERGY_CHG"]), axis=1)
    merged["COUNTRY"] = name
    merged["CODE"] = code
    disconnect_frames.append(merged)

if disconnect_frames:
    disc_all = pd.concat(disconnect_frames, ignore_index=True)
    disc_all = disc_all.dropna(subset=["DISCONNECT"])

    # Disconnect over time chart
    col_disc1, col_disc2 = st.columns(2)

    with col_disc1:
        with st.container(border=True):
            st.markdown("**Energy-Economy Disconnect Over Time**")
            st.caption("Economic Growth % minus Energy Growth %")
            for code in selected_codes:
                name = COUNTRY_NAMES.get(code, code)
                cdata = disc_all[disc_all["CODE"] == code][["YEAR", "DISCONNECT"]].set_index("YEAR")
                if not cdata.empty:
                    cdata.columns = [name]
                    st.line_chart(cdata, use_container_width=True)

    with col_disc2:
        with st.container(border=True):
            st.markdown("**Energy Productivity Over Time**")
            st.caption(f"{econ_label} / {energy_label}")
            prod_pivot = disc_all.pivot_table(index="YEAR", columns="COUNTRY", values="ENERGY_PRODUCTIVITY")
            if not prod_pivot.empty:
                st.line_chart(prod_pivot, use_container_width=True)

    # Top Disconnect Periods table
    st.markdown("### Top Disconnect Periods")
    top_disc = disc_all.nlargest(15, "DISCONNECT", keep="first")[
        ["COUNTRY", "YEAR", "ECON_CHG", "ENERGY_CHG", "DISCONNECT", "ENERGY_PRODUCTIVITY", "SIGNAL"]
    ].copy()
    top_disc.columns = ["Country", "Year", "Econ Change %", "Energy Change %", "Disconnect (pp)", "Energy Productivity", "Signal"]
    for c in ["Econ Change %", "Energy Change %", "Disconnect (pp)"]:
        top_disc[c] = top_disc[c].round(2)
    top_disc["Energy Productivity"] = top_disc["Energy Productivity"].round(4)
    st.dataframe(top_disc, use_container_width=True, hide_index=True)
else:
    st.warning("Insufficient data to compute disconnect metrics.")

# ════════════════════════════════════════════════════════
# 4. HIDDEN SIGNALS
# ════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("Hidden Signals")

if disconnect_frames:
    # ── A. Energy Shock Detector ──
    st.markdown("### A. Energy Shock Detector")
    st.caption("Periods where energy indicators changed far beyond the historical average")

    shocks_found = []
    for code in selected_codes:
        name = COUNTRY_NAMES.get(code, code)
        cdata = disc_all[disc_all["CODE"] == code].sort_values("YEAR")
        if len(cdata) < 5:
            continue
        mean_chg = cdata["ENERGY_CHG"].mean()
        std_chg = cdata["ENERGY_CHG"].std()
        if std_chg == 0:
            continue
        threshold = mean_chg + 2 * std_chg
        neg_threshold = mean_chg - 2 * std_chg
        shock_rows = cdata[(cdata["ENERGY_CHG"] > threshold) | (cdata["ENERGY_CHG"] < neg_threshold)]
        for _, row in shock_rows.iterrows():
            year = int(row["YEAR"])
            before = cdata[cdata["YEAR"] == year - 1]
            after = cdata[cdata["YEAR"] == year + 1]
            econ_before = before["ECON_CHG"].iloc[0] if not before.empty else None
            econ_after = after["ECON_CHG"].iloc[0] if not after.empty else None
            shocks_found.append({
                "Country": name,
                "Year": year,
                "Energy Change %": round(row["ENERGY_CHG"], 2),
                "Econ Before (%)": round(econ_before, 2) if econ_before is not None else None,
                "Econ During (%)": round(row["ECON_CHG"], 2),
                "Econ After (%)": round(econ_after, 2) if econ_after is not None else None,
            })

    if shocks_found:
        shock_df = pd.DataFrame(shocks_found)
        for _, shock in shock_df.iterrows():
            with st.container(border=True):
                st.markdown(f"**Energy Shock Detected — {shock['Country']}, {shock['Year']}**")
                sc1, sc2, sc3, sc4 = st.columns(4)
                sc1.metric("Energy Change", f"{shock['Energy Change %']}%")
                sc2.metric("Econ Before", f"{shock['Econ Before (%)']}%" if shock["Econ Before (%)"] is not None else "N/A")
                sc3.metric("Econ During", f"{shock['Econ During (%)']}%")
                sc4.metric("Econ After", f"{shock['Econ After (%)']}%" if shock["Econ After (%)"] is not None else "N/A")
            if len([s for s in shocks_found if s["Country"] == shock["Country"]]) > 3:
                break
    else:
        st.info("No energy shocks detected at 2-sigma threshold for the selected countries and period.")

    # ── B. Lag Effect Analysis ──
    st.markdown("### B. Lag Effect Analysis")
    st.caption("Correlation between energy changes and subsequent economic changes at different lags")
    st.markdown("*Correlation indicates association, not causation.*")

    lag_results = []
    for code in selected_codes:
        name = COUNTRY_NAMES.get(code, code)
        cdata = disc_all[disc_all["CODE"] == code].sort_values("YEAR").reset_index(drop=True)
        if len(cdata) < 15:
            continue
        for lag in [0, 1, 2, 3, 5]:
            if lag >= len(cdata):
                continue
            energy_series = cdata["ENERGY_CHG"].iloc[:len(cdata) - lag if lag > 0 else len(cdata)]
            econ_series = cdata["ECON_CHG"].iloc[lag:]
            min_len = min(len(energy_series), len(econ_series))
            if min_len < 5:
                continue
            e1 = energy_series.iloc[:min_len].reset_index(drop=True)
            e2 = econ_series.iloc[:min_len].reset_index(drop=True)
            valid = e1.notna() & e2.notna()
            if valid.sum() < 5:
                continue
            corr = e1[valid].corr(e2[valid])
            lag_results.append({"Country": name, "Lag (years)": lag, "Correlation": round(corr, 3)})

    if lag_results:
        lag_df = pd.DataFrame(lag_results)
        lag_pivot = lag_df.pivot_table(index="Lag (years)", columns="Country", values="Correlation")
        with st.container(border=True):
            st.markdown("**Lag (years) vs Correlation Strength**")
            st.bar_chart(lag_pivot, use_container_width=True)
        st.dataframe(lag_df, use_container_width=True, hide_index=True)
    else:
        st.info("Insufficient data for lag analysis (need 15+ years of overlap).")

    # ── C. Contradiction Cards ──
    st.markdown("### C. Contradiction Cards")
    st.caption("Unusual combinations of energy and economic behavior")

    cards = []
    for code in selected_codes:
        name = COUNTRY_NAMES.get(code, code)
        cdata = disc_all[disc_all["CODE"] == code].sort_values("YEAR")
        if len(cdata) < 3:
            continue

        for _, row in cdata.iterrows():
            e_chg = row["ECON_CHG"]
            n_chg = row["ENERGY_CHG"]
            if pd.isna(e_chg) or pd.isna(n_chg):
                continue
            year = int(row["YEAR"])
            disc = row["DISCONNECT"]

            if e_chg > 2 and abs(n_chg) < 1:
                cards.append({
                    "type": "ECONOMY UP / ENERGY FLAT",
                    "country": name, "year": year,
                    "econ": e_chg, "energy": n_chg, "disc": disc,
                    "desc": f"Economic output increased {e_chg:.1f}% while energy demand changed only {n_chg:+.1f}%.",
                })
            elif n_chg > 2 and e_chg < -1:
                cards.append({
                    "type": "ENERGY UP / ECONOMY DOWN",
                    "country": name, "year": year,
                    "econ": e_chg, "energy": n_chg, "disc": disc,
                    "desc": f"Energy demand increased {n_chg:.1f}% despite economic output declining {e_chg:.1f}%.",
                })
            elif e_chg > 3 and e_chg > n_chg + 2:
                cards.append({
                    "type": "EFFICIENCY GAIN",
                    "country": name, "year": year,
                    "econ": e_chg, "energy": n_chg, "disc": disc,
                    "desc": f"Economic output grew {e_chg:.1f}% while energy grew only {n_chg:.1f}%, a {disc:.1f}pp productivity gain.",
                })
            elif e_chg < -2 and n_chg < -2:
                cards.append({
                    "type": "JOINT DECLINE",
                    "country": name, "year": year,
                    "econ": e_chg, "energy": n_chg, "disc": disc,
                    "desc": f"Both economic output ({e_chg:.1f}%) and energy consumption ({n_chg:.1f}%) declined.",
                })

    if cards:
        cards_df = pd.DataFrame(cards)
        cards_df["abs_disc"] = cards_df["disc"].abs()
        top_cards = cards_df.nlargest(5, "abs_disc")

        card_cols = st.columns(min(len(top_cards), 3))
        for i, (_, card) in enumerate(top_cards.iterrows()):
            col_idx = i % len(card_cols)
            with card_cols[col_idx]:
                with st.container(border=True):
                    color = {"ECONOMY UP / ENERGY FLAT": "#00D4AA",
                             "ENERGY UP / ECONOMY DOWN": "#FF6B6B",
                             "EFFICIENCY GAIN": "#4ECDC4",
                             "JOINT DECLINE": "#FFE66D"}.get(card["type"], "#888")
                    st.markdown(f"<span style='color:{color}; font-weight:bold; font-size:0.9rem;'>{card['type']}</span>", unsafe_allow_html=True)
                    st.markdown(f"**{card['country']}** — {card['year']}")
                    st.markdown(f"Econ: {card['econ']:+.1f}% &nbsp; Energy: {card['energy']:+.1f}%")
                    st.markdown(f"*{card['desc']}*")
        if len(top_cards) > 3:
            card_cols2 = st.columns(min(len(top_cards) - 3, 3))
            for i, (_, card) in enumerate(list(top_cards.iterrows())[3:]):
                with card_cols2[i % len(card_cols2)]:
                    with st.container(border=True):
                        st.markdown(f"**{card['type']}**")
                        st.markdown(f"**{card['country']}** — {card['year']}")
                        st.markdown(f"Econ: {card['econ']:+.1f}% &nbsp; Energy: {card['energy']:+.1f}%")
                        st.markdown(f"*{card['desc']}*")
    else:
        st.info("No strong contradictions detected for the selected data.")

# ════════════════════════════════════════════════════════
# 5. COUNTRY / REGION COMPARISON
# ════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("Country Comparison")

if disconnect_frames and len(selected_codes) > 1:
    country_summary = []
    for code in selected_codes:
        name = COUNTRY_NAMES.get(code, code)
        cdata = disc_all[disc_all["CODE"] == code]
        if cdata.empty:
            continue
        country_summary.append({
            "Country": name,
            "Avg Econ Growth %": round(cdata["ECON_CHG"].mean(), 2),
            "Avg Energy Change %": round(cdata["ENERGY_CHG"].mean(), 2),
            "Avg Disconnect (pp)": round(cdata["DISCONNECT"].mean(), 2),
            "Avg Energy Productivity": round(cdata["ENERGY_PRODUCTIVITY"].mean(), 4),
        })

    if country_summary:
        summary_df = pd.DataFrame(country_summary)

        col_scatter, col_table = st.columns([3, 2])
        with col_scatter:
            with st.container(border=True):
                st.markdown("**Economic Growth vs Energy Growth**")
                st.caption("Each point is a country-year observation")
                scatter_data = disc_all[["COUNTRY", "ECON_CHG", "ENERGY_CHG"]].dropna()
                if not scatter_data.empty:
                    st.scatter_chart(
                        scatter_data,
                        x="ECON_CHG",
                        y="ENERGY_CHG",
                        color="COUNTRY",
                        use_container_width=True,
                    )

        with col_table:
            with st.container(border=True):
                st.markdown("**Country Averages**")
                st.dataframe(summary_df, use_container_width=True, hide_index=True)

elif len(selected_codes) == 1:
    st.info("Select multiple countries in the sidebar to enable comparison.")

# ════════════════════════════════════════════════════════
# INSIGHTS SUMMARY
# ════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("Auto-Generated Insights")

if disconnect_frames:
    insights = []
    for code in selected_codes:
        name = COUNTRY_NAMES.get(code, code)
        cdata = disc_all[disc_all["CODE"] == code].dropna(subset=["DISCONNECT"])
        if len(cdata) < 3:
            continue

        avg_econ = cdata["ECON_CHG"].mean()
        avg_energy = cdata["ENERGY_CHG"].mean()
        avg_disc = cdata["DISCONNECT"].mean()
        max_disc_row = cdata.loc[cdata["DISCONNECT"].abs().idxmax()]

        insights.append(f"**{name}**: Average economic growth {avg_econ:.1f}%, average energy change {avg_energy:.1f}% over {year_range[0]}-{year_range[1]}.")

        insights.append(f"**{name}**: Largest energy-economy divergence occurred in {int(max_disc_row['YEAR'])} ({max_disc_row['DISCONNECT']:+.1f}pp).")

        if avg_disc > 1:
            insights.append(f"**{name}**: Energy productivity improved on average during this period (disconnect: {avg_disc:+.1f}pp).")
        elif avg_disc < -1:
            insights.append(f"**{name}**: Energy intensity increased on average during this period (disconnect: {avg_disc:+.1f}pp).")

        shock_years = cdata[cdata["ENERGY_CHG"].abs() > cdata["ENERGY_CHG"].abs().mean() + 2 * cdata["ENERGY_CHG"].abs().std()]
        if not shock_years.empty:
            shock_year_list = ", ".join([str(int(y)) for y in shock_years["YEAR"].tolist()[:3]])
            insights.append(f"**{name}**: Unusually large energy changes occurred in: {shock_year_list}.")

    for ins in insights:
        st.markdown(f"- {ins}")

st.markdown("---")
st.caption("Data: World Bank Open Data via Snowflake Marketplace. Correlation indicates association, not causation. Energy productivity = economic output / energy consumption.")
