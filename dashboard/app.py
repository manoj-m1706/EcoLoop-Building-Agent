import sys
from pathlib import Path

# Add project root to sys.path to allow importing config and main modules from subdirectories
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import streamlit as st
import pandas as pd
# pyrefly: ignore [missing-import]
import plotly.graph_objects as go
import plotly.express as px
import json
import time

from config import Config
from main import run_optimization_pipeline
from utils.file_manager import read_file

# Page configuration
st.set_page_config(
    page_title="EcoLoop Building BMS Dashboard",
    page_icon="🍃",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Premium Aesthetics CSS Styling
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Plus+Jakarta+Sans:wght@300;400;500;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Plus Jakarta Sans', sans-serif;
    }
    
    .main-title {
        font-family: 'Outfit', sans-serif;
        font-size: 2.8rem;
        font-weight: 800;
        background: linear-gradient(135deg, #00FF87 0%, #60EFFF 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }
    
    .subtitle {
        color: #A0AEC0;
        font-size: 1.1rem;
        margin-bottom: 2rem;
    }
    
    .card {
        background-color: #1A202C;
        border-radius: 12px;
        padding: 1.5rem;
        border: 1px solid #2D3748;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        margin-bottom: 1rem;
    }
    
    .metric-value {
        font-size: 2.2rem;
        font-weight: 700;
        color: #00FF87;
        margin: 0.5rem 0;
    }
    
    .metric-label {
        font-size: 0.9rem;
        color: #A0AEC0;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
</style>
""", unsafe_allow_html=True)

# Helper functions
def load_results():
    results_file = Config.RESULTS_FILE
    if results_file.exists():
        try:
            return json.loads(results_file.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None

def get_simulation_csvs():
    base_csv = Config.SIMULATIONS_DIR / "baseline" / "eplusout.csv"
    opt_csv = Config.SIMULATIONS_DIR / "optimized" / "eplusout.csv"
    return base_csv if base_csv.exists() else None, opt_csv if opt_csv.exists() else None

# Title area
st.markdown('<div class="main-title">🍃 EcoLoop Building BMS</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Autonomous AI-Driven Building Energy Management & Comfort Closed-Loop Control</div>', unsafe_allow_html=True)

# Load configuration and results
results = load_results()
base_csv_path, opt_csv_path = get_simulation_csvs()

# Sidebar Setup
st.sidebar.markdown("### ⚙️ System Settings")
st.sidebar.markdown(f"**Execution Mode:** `{'Mock Mode' if Config.IS_MOCK_MODE else 'Real EnergyPlus'}`")

# Dynamically read what LLM model was actually used from results
if results and "control_decisions" in results and "model_used" in results["control_decisions"]:
    model_display = results["control_decisions"]["model_used"]
    if "GEMINI" in model_display.upper():
        endpoint_label = "Gemini Endpoint"
        endpoint_val = "generativelanguage.googleapis.com"
    elif "OPENAI" in model_display.upper():
        endpoint_label = "OpenAI Endpoint"
        endpoint_val = "api.openai.com"
    elif "HEURISTIC" in model_display.upper():
        endpoint_label = "Optimization Engine"
        endpoint_val = "Rule-Based Heuristics"
    else:
        endpoint_label = "Ollama Host"
        endpoint_val = Config.OLLAMA_HOST
else:
    model_display = f"{Config.MODEL_NAME} (Configured)"
    endpoint_label = "Ollama Host"
    endpoint_val = Config.OLLAMA_HOST

st.sidebar.markdown(f"**LLM Model:** `{model_display}`")
st.sidebar.markdown(f"**{endpoint_label}:** `{endpoint_val}`")

st.sidebar.markdown("---")
st.sidebar.markdown("### 🚀 Trigger Simulation Run")
demo_idf = Config.BASE_DIR / "demo" / "sample_building.idf"
demo_epw = Config.BASE_DIR / "demo" / "weather.epw"

if st.sidebar.button("Run Optimization Pipeline", use_container_width=True):
    import threading
    from streamlit.runtime.scriptrunner import add_script_run_ctx
    
    st.sidebar.markdown("### 🔄 Live Optimization Stream")
    log_placeholder = st.sidebar.empty()
    status_text = st.sidebar.empty()
    
    status_text.info("Executing Pipeline...")
    
    # We use a mutable dict to store the result from the thread
    thread_result = {}
    
    def run_pipeline_thread():
        try:
            # Generate dummy demo files if missing
            if not demo_idf.exists():
                demo_idf.parent.mkdir(parents=True, exist_ok=True)
                demo_idf.write_text("! - AI cooling setpoint: 24.0\n! - AI heating setpoint: 20.0\n! - AI lighting status: on\nSchedule:Constant,CoolingSetpointSchedule,Temperature,24.0;\nSchedule:Constant,HeatingSetpointSchedule,Temperature,20.0;\nSchedule:Constant,LightingSchedule,Any Number,1.0;", encoding="utf-8")
            if not demo_epw.exists():
                demo_epw.write_text("Dummy EPW File", encoding="utf-8")

            res = run_optimization_pipeline(demo_idf, demo_epw)
            thread_result['data'] = res
        except Exception as e:
            thread_result['error'] = e

    pipeline_thread = threading.Thread(target=run_pipeline_thread)
    add_script_run_ctx(pipeline_thread)
    pipeline_thread.start()
    
    log_file = Config.LOGS_DIR / "simulation.log"
    
    # Loop and update UI while thread is running
    while pipeline_thread.is_alive():
        if log_file.exists():
            try:
                lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                # Show the last 20 lines of the live log
                log_preview = "\n".join(lines[-20:])
                log_placeholder.code(log_preview, language="log")
            except Exception:
                pass
        time.sleep(0.5)
        
    pipeline_thread.join()
    
    if 'error' in thread_result:
        status_text.error(f"Pipeline failed: {thread_result['error']}")
    else:
        status_text.success("Pipeline executed successfully!")
        time.sleep(1)
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("### 📝 Active Controls")
if results:
    decision = results.get("control_decisions", {})
    st.sidebar.metric("AI Cooling Setpoint", f"{decision.get('cooling_setpoint', '-')} °C")
    st.sidebar.metric("AI Heating Setpoint", f"{decision.get('heating_setpoint', '-')} °C")
    st.sidebar.metric("Lighting Status", str(decision.get('lighting', '-')).upper())
    st.sidebar.metric("Ventilation Rate", str(decision.get('ventilation', '-')).upper())
else:
    st.sidebar.info("No run results. Click the button above to run simulation.")

# Main dashboard contents
if not results:
    st.warning("⚠️ No simulation run results found. Please click 'Run Optimization Pipeline' in the sidebar to execute the pipeline.")
else:
    # 1. Metrics Cards (Row 1)
    savings = results.get("savings", {})
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    
    with m_col1:
        st.markdown(f"""
        <div class="card">
            <div class="metric-label">⚡ Total Savings</div>
            <div class="metric-value">{savings.get('savings_pct', 0.0):.1f}%</div>
            <div style="color:#A0AEC0; font-size:0.85rem;">Compared to baseline schedule</div>
        </div>
        """, unsafe_allow_html=True)
        
    with m_col2:
        st.markdown(f"""
        <div class="card">
            <div class="metric-label">📉 Energy Saved</div>
            <div class="metric-value">{savings.get('electricity_saved_kwh', 0.0):.2f} kWh</div>
            <div style="color:#A0AEC0; font-size:0.85rem;">Total consumption reduction</div>
        </div>
        """, unsafe_allow_html=True)
        
    with m_col3:
        st.markdown(f"""
        <div class="card">
            <div class="metric-label">💰 Cost Savings</div>
            <div class="metric-value">${savings.get('cost_saved_usd', 0.0):.2f}</div>
            <div style="color:#A0AEC0; font-size:0.85rem;">At rate of $0.12/kWh</div>
        </div>
        """, unsafe_allow_html=True)
        
    with m_col4:
        pmv_chg = savings.get('comfort_pmv_change', 0.0)
        pmv_color = "#00FF87" if pmv_chg <= 0 else "#FFA500"
        pmv_desc = "Comfort Improved / Maintained" if pmv_chg <= 0 else "Slight comfort deviation"
        st.markdown(f"""
        <div class="card">
            <div class="metric-label">😊 PMV Dev Change</div>
            <div class="metric-value" style="color: {pmv_color};">{pmv_chg:+.2f}</div>
            <div style="color:#A0AEC0; font-size:0.85rem;">{pmv_desc}</div>
        </div>
        """, unsafe_allow_html=True)

    # 2. AI Reasoning Section
    st.markdown("### 🧠 AI Optimization Brain & Rationale")
    st.info(f"**AI Reasoning:** {results.get('control_decisions', {}).get('reason', 'N/A')}")

    # 3. Dynamic Charts (Row 2)
    if base_csv_path and opt_csv_path:
        df_base = pd.read_csv(base_csv_path)
        df_opt = pd.read_csv(opt_csv_path)
        
        # Clean columns
        df_base.columns = [c.strip() for c in df_base.columns]
        df_opt.columns = [c.strip() for c in df_opt.columns]
        
        # Helper to find column name
        def find_col(df, keyword):
            for c in df.columns:
                if keyword.lower() in c.lower():
                    return c
            return None

        time_col = find_col(df_base, "date") or df_base.columns[0]
        temp_in_col = find_col(df_base, "zone air temperature")
        temp_out_col = find_col(df_base, "outdoor air drybulb")
        pmv_col = find_col(df_base, "pmv") or find_col(df_base, "comfort")
        if not pmv_col:
            # Construct a simulated PMV comfort column based on zone temperature and humidity
            t_zones = [c for c in df_base.columns if "zone air temperature" in c.lower() or ("space" in c.lower() and "temperature" in c.lower())]
            rh_zones = [c for c in df_base.columns if "relative humidity" in c.lower() or ("space" in c.lower() and "humidity" in c.lower())]
            if t_zones and rh_zones:
                df_base["Simulated_PMV"] = (df_base[t_zones].mean(axis=1) - 23.5) * 0.33 + (df_base[rh_zones].mean(axis=1) - 50.0) * 0.005
                df_opt["Simulated_PMV"] = (df_opt[t_zones].mean(axis=1) - 23.5) * 0.33 + (df_opt[rh_zones].mean(axis=1) - 50.0) * 0.005
                pmv_col = "Simulated_PMV"

        elec_col = find_col(df_base, "electricity demand") or find_col(df_base, "total electricity") or df_base.columns[5]
        
        # Identify all cooling and heating columns across zones (Joule or Watt outputs)
        cool_cols = []
        heat_cols = []
        for col in df_base.columns:
            col_low = col.lower()
            if "cooling" in col_low or "clg" in col_low:
                if "[j]" in col_low or "joule" in col_low:
                    cool_cols.append(col)
                elif "[w]" in col_low or "watt" in col_low:
                    if not any(("[j]" in c.lower() or "joule" in c.lower()) for c in df_base.columns if "cooling" in c.lower() or "clg" in c.lower()):
                        cool_cols.append(col)
            elif "heating" in col_low or "htg" in col_low:
                if "[j]" in col_low or "joule" in col_low:
                    heat_cols.append(col)
                elif "[w]" in col_low or "watt" in col_low:
                    if not any(("[j]" in c.lower() or "joule" in c.lower()) for c in df_base.columns if "heating" in c.lower() or "htg" in c.lower()):
                        heat_cols.append(col)
        
        # Calculate total building electricity (lighting/equipment + hvac cooling/heating demand in Watts)
        def get_hvac_watts_series(df, cols):
            series = pd.Series(0.0, index=df.index)
            for col in cols:
                if col not in df.columns:
                    continue
                if "[j]" in col.lower() or "joule" in col.lower():
                    # convert Joules to Watts (hourly values represent Joules consumed in 1 hour)
                    series += df[col] / 3600.0
                else:
                    series += df[col]
            return series

        base_hvac_watts = get_hvac_watts_series(df_base, cool_cols) + get_hvac_watts_series(df_base, heat_cols)
        opt_hvac_watts = get_hvac_watts_series(df_opt, cool_cols) + get_hvac_watts_series(df_opt, heat_cols)
        
        base_total_watts = df_base[elec_col] + base_hvac_watts if elec_col else base_hvac_watts
        opt_total_watts = df_opt[elec_col] + opt_hvac_watts if elec_col else opt_hvac_watts

        # Parse time series
        hours = list(range(len(df_base)))
        
        # Plotly Grid Layout
        c_col1, c_col2 = st.columns(2)
        
        with c_col1:
            # CHART 1: Electricity Demand Comparison
            st.markdown("#### ⚡ Electricity Consumption Profile (Watts)")
            fig1 = go.Figure()
            fig1.add_trace(go.Scatter(x=hours, y=base_total_watts, name="Baseline", line=dict(color="#FF4B4B", width=2, dash="dash")))
            fig1.add_trace(go.Scatter(x=hours, y=opt_total_watts, name="Optimized (AI)", line=dict(color="#00FF87", width=3)))
            fig1.update_layout(
                template="plotly_dark",
                margin=dict(l=40, r=40, t=20, b=40),
                xaxis=dict(title="Hour of Day", tickmode="linear", tick0=0, dtick=2),
                yaxis=dict(title="Electricity Demand (Watts)"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig1, use_container_width=True)

        with c_col2:
            # CHART 2: Temperature Profile comparison
            st.markdown("#### 🌡️ Indoor vs Outdoor Temperature Profile (°C)")
            fig2 = go.Figure()
            if temp_out_col:
                fig2.add_trace(go.Scatter(x=hours, y=df_base[temp_out_col], name="Outdoor Temp", line=dict(color="#718096", width=1.5, dash="dot")))
            if temp_in_col:
                fig2.add_trace(go.Scatter(x=hours, y=df_base[temp_in_col], name="Baseline Indoor", line=dict(color="#FF4B4B", width=2, dash="dash")))
                fig2.add_trace(go.Scatter(x=hours, y=df_opt[temp_in_col], name="Optimized Indoor (AI)", line=dict(color="#60EFFF", width=3)))
                
            fig2.update_layout(
                template="plotly_dark",
                margin=dict(l=40, r=40, t=20, b=40),
                xaxis=dict(title="Hour of Day", tickmode="linear", tick0=0, dtick=2),
                yaxis=dict(title="Temperature (°C)"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig2, use_container_width=True)
            
        # Row 3
        c_col3, c_col4 = st.columns(2)
        with c_col3:
            # CHART 3: Fanger Comfort PMV Index
            st.markdown("#### 😊 Thermal Comfort PMV Comparison")
            fig3 = go.Figure()
            if pmv_col:
                fig3.add_trace(go.Scatter(x=hours, y=df_base[pmv_col], name="Baseline PMV", line=dict(color="#FF4B4B", width=2, dash="dash")))
                fig3.add_trace(go.Scatter(x=hours, y=df_opt[pmv_col], name="Optimized PMV (AI)", line=dict(color="#00FF87", width=3)))
                
                # Highlight Comfort Band (-0.7 to 0.7)
                fig3.add_hrect(y0=-0.7, y1=0.7, fillcolor="#00FF87", opacity=0.1, line_width=0, annotation_text="Ideal Comfort Range", annotation_position="top left")
                
            fig3.update_layout(
                template="plotly_dark",
                margin=dict(l=40, r=40, t=20, b=40),
                xaxis=dict(title="Hour of Day", tickmode="linear", tick0=0, dtick=2),
                yaxis=dict(title="Predicted Mean Vote (PMV)", range=[-3.0, 3.0]),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig3, use_container_width=True)
            
        with c_col4:
            # CHART 4: HVAC Energy Consumption Components
            st.markdown("#### ❄️ HVAC Cooling vs Heating Demand (Joule Sum)")
            fig4 = go.Figure()
            
            # Sum up HVAC baseline vs optimized using helper
            def sum_energy_kwh(df, cols):
                total_kwh = 0.0
                for col in cols:
                    if col not in df.columns:
                        continue
                    if "[j]" in col.lower() or "joule" in col.lower():
                        total_kwh += df[col].sum() * 2.77778e-7
                    else:
                        mean_w = df[col].mean()
                        hours = len(df)
                        total_kwh += (mean_w * hours) / 1000.0
                return total_kwh

            base_cool_sum = sum_energy_kwh(df_base, cool_cols)
            base_heat_sum = sum_energy_kwh(df_base, heat_cols)
            opt_cool_sum = sum_energy_kwh(df_opt, cool_cols)
            opt_heat_sum = sum_energy_kwh(df_opt, heat_cols)
            
            fig4.add_trace(go.Bar(
                name="Baseline",
                x=["Cooling Load", "Heating Load"],
                y=[base_cool_sum, base_heat_sum],
                marker_color="#FF4B4B"
            ))
            fig4.add_trace(go.Bar(
                name="Optimized",
                x=["Cooling Load", "Heating Load"],
                y=[opt_cool_sum, opt_heat_sum],
                marker_color="#00FF87"
            ))
            fig4.update_layout(
                template="plotly_dark",
                margin=dict(l=40, r=40, t=20, b=40),
                yaxis=dict(title="Energy (kWh)"),
                barmode="group",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig4, use_container_width=True)

    else:
        st.warning("Could not locate baseline and optimized detailed time-series output CSVs.")

# Logs expansion
st.markdown("---")
with st.expander("📄 View System Execution & Optimization Logs"):
    log_file = Config.LOGS_DIR / "simulation.log"
    if log_file.exists():
        try:
            log_lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
            log_preview = "\n".join(log_lines[-40:])
            st.code(log_preview, language="log")
        except Exception as e:
            st.error(f"Failed to read logs: {e}")
    else:
        st.info("Log file not generated yet.")
