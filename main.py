import json
import time
from pathlib import Path
from typing import Dict, Any

from config import Config
from utils.logger import logger
from utils.file_manager import clean_output_folders, copy_idf, write_file
from energyplus.runner import EnergyPlusRunner
from energyplus.parser import EnergyPlusParser
from energyplus.idf_editor import IDFEditor
from llm.agent import LLMAgent

def run_optimization_pipeline(
    idf_path: str | Path,
    weather_path: str | Path,
    base_output_dir: str | Path = None,
    opt_output_dir: str | Path = None
) -> Dict[str, Any]:
    """
    Executes the closed-loop building energy optimization pipeline:
    Baseline Run -> Parse -> LLM Decision -> Modify IDF -> Optimized Run -> Savings.
    """
    start_time = time.time()
    logger.info("=========================================")
    logger.info("EcoLoop Building Agent Pipeline Started")
    logger.info("=========================================")
    
    idf_path = Path(idf_path)
    weather_path = Path(weather_path)
    
    # 1. Setup paths
    base_output_dir = Path(base_output_dir or Config.SIMULATIONS_DIR / "baseline")
    opt_output_dir = Path(opt_output_dir or Config.SIMULATIONS_DIR / "optimized")
    optimized_idf_path = opt_output_dir / "optimized_building.idf"

    # Clean previous intermediate simulation output runs (but keep logs)
    clean_output_folders(Config.SIMULATIONS_DIR, keep_logs=True)
    
    # 2. RUN BASELINE SIMULATION
    logger.info("--- Step 1: Running Baseline Simulation ---")
    runner = EnergyPlusRunner(Config.ENERGYPLUS_PATH)
    baseline_result = runner.run(idf_path, weather_path, base_output_dir)
    
    if baseline_result.status != "success":
        logger.error("Baseline simulation failed. Aborting pipeline.")
        raise RuntimeError("Baseline EnergyPlus simulation failed.")

    # 3. PARSE BASELINE OUTPUTS
    logger.info("--- Step 2: Parsing Baseline Output Metrics ---")
    baseline_csv = base_output_dir / "eplusout.csv"
    baseline_metrics = EnergyPlusParser.parse_csv(baseline_csv)
    
    logger.info(
        f"Baseline Results: Energy={baseline_metrics.total_electricity_kwh:.2f} kWh | "
        f"HVAC Energy={baseline_metrics.hvac_electricity_kwh:.2f} kWh | "
        f"Avg Temp={baseline_metrics.avg_indoor_temp:.2f}°C | "
        f"Avg PMV={baseline_metrics.avg_pmv:.2f}"
    )

    # 4. CALL LLM AGENT FOR DYNAMIC SETPOINTS
    logger.info("--- Step 3: Querying AI Agent for Optimal Control Decisions ---")
    agent = LLMAgent()
    
    # Extract comfort indicators
    # We pass the values to LLM to make intelligent adjustments
    # Default occupancy is set to 1.0 (assuming occupied zone during simulation hours)
    decision = agent.get_decision(
        temperature=baseline_metrics.avg_indoor_temp,
        humidity=baseline_metrics.avg_relative_humidity,
        pmv=baseline_metrics.avg_pmv,
        energy=baseline_metrics.total_electricity_kwh * 1000.0 / 24.0, # Approximate average Watts over 24h
        occupancy=1.0  
    )
    
    logger.info(f"AI Decision: Cooling Setpoint={decision['cooling_setpoint']}°C | "
                f"Heating Setpoint={decision['heating_setpoint']}°C | "
                f"Lighting={decision['lighting']} | Ventilation={decision['ventilation']}")
    logger.info(f"AI Rationale: {decision['reason']}")

    # 5. MODIFY IDF WITH OPTIMIZED SETPOINTS
    logger.info("--- Step 4: Applying AI Setpoints to IDF ---")
    editor = IDFEditor()
    container = editor.load_idf(idf_path)
    container = editor.change_cooling_setpoint(container, decision["cooling_setpoint"])
    container = editor.change_heating_setpoint(container, decision["heating_setpoint"])
    container = editor.change_lighting_schedule(container, decision["lighting"])
    
    # Save as new optimized IDF
    saved_idf = editor.save_new_idf(container, optimized_idf_path)
    logger.info(f"Optimized IDF saved: {saved_idf}")

    # 6. RUN OPTIMIZED SIMULATION
    logger.info("--- Step 5: Running Optimized Simulation ---")
    optimized_result = runner.run(saved_idf, weather_path, opt_output_dir)
    
    if optimized_result.status != "success":
        logger.error("Optimized simulation failed. Aborting pipeline.")
        raise RuntimeError("Optimized EnergyPlus simulation failed.")

    # 7. PARSE OPTIMIZED OUTPUTS
    logger.info("--- Step 6: Parsing Optimized Output Metrics ---")
    optimized_csv = opt_output_dir / "eplusout.csv"
    optimized_metrics = EnergyPlusParser.parse_csv(optimized_csv)
    
    logger.info(
        f"Optimized Results: Energy={optimized_metrics.total_electricity_kwh:.2f} kWh | "
        f"HVAC Energy={optimized_metrics.hvac_electricity_kwh:.2f} kWh | "
        f"Avg Temp={optimized_metrics.avg_indoor_temp:.2f}°C | "
        f"Avg PMV={optimized_metrics.avg_pmv:.2f}"
    )

    # 8. CALCULATE SAVINGS
    logger.info("--- Step 7: Calculating Energy and Cost Savings ---")
    
    base_elec = baseline_metrics.total_electricity_kwh
    opt_elec = optimized_metrics.total_electricity_kwh
    
    base_hvac = baseline_metrics.hvac_electricity_kwh
    opt_hvac = optimized_metrics.hvac_electricity_kwh
    
    elec_saved = base_elec - opt_elec
    savings_pct = (elec_saved / base_elec * 100) if base_elec > 0 else 0.0
    
    hvac_saved = base_hvac - opt_hvac
    hvac_savings_pct = (hvac_saved / base_hvac * 100) if base_hvac > 0 else 0.0
    
    # Assume $0.12/kWh electricity rate
    electricity_rate = 0.12
    cost_saved = elec_saved * electricity_rate
    
    comfort_change = abs(optimized_metrics.avg_pmv) - abs(baseline_metrics.avg_pmv)
    
    pipeline_duration = time.time() - start_time
    
    # 9. CONSTRUCT RESULTS SUMMARY
    results_summary = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pipeline_duration_seconds": round(pipeline_duration, 2),
        "ai_model": Config.MODEL_NAME,
        "control_decisions": decision,
        "baseline_metrics": baseline_metrics.metrics_summary,
        "optimized_metrics": optimized_metrics.metrics_summary,
        "savings": {
            "electricity_saved_kwh": round(elec_saved, 2),
            "savings_pct": round(savings_pct, 2),
            "hvac_electricity_saved_kwh": round(hvac_saved, 2),
            "hvac_savings_pct": round(hvac_savings_pct, 2),
            "cost_saved_usd": round(cost_saved, 2),
            "comfort_pmv_change": round(comfort_change, 2)
        }
    }
    
    # 10. STORE RESULTS TO outputs/results.json
    logger.info(f"--- Step 8: Storing Results to {Config.RESULTS_FILE.name} ---")
    write_file(Config.RESULTS_FILE, json.dumps(results_summary, indent=2))
    
    logger.info("=========================================")
    logger.info("EcoLoop Building Agent Pipeline Completed Successfully!")
    logger.info(f"Total Electricity Saved: {elec_saved:.2f} kWh ({savings_pct:.2f}% reduction)")
    logger.info(f"Total Cost Saved: ${cost_saved:.2f}")
    logger.info("=========================================")
    
    return results_summary

if __name__ == "__main__":
    # Ensure demo files exist or we generate placeholders
    demo_idf = Config.BASE_DIR / "demo" / "sample_building.idf"
    demo_epw = Config.BASE_DIR / "demo" / "weather.epw"
    
    # Generate minimal dummy files if they don't exist yet, to allow running main.py directly
    if not demo_idf.parent.exists():
        demo_idf.parent.mkdir(parents=True, exist_ok=True)
        
    if not demo_idf.exists():
        # Minimal IDF text that contains the expected constant schedules
        dummy_idf_content = """
! - AI cooling setpoint: 24.0
! - AI heating setpoint: 20.0
! - AI lighting status: on

Schedule:Constant,
  CoolingSetpointSchedule, !- Name
  Temperature,             !- Schedule Type Limits Name
  24.0;                    !- Hourly Value

Schedule:Constant,
  HeatingSetpointSchedule, !- Name
  Temperature,             !- Schedule Type Limits Name
  20.0;                    !- Hourly Value

Schedule:Constant,
  LightingSchedule,        !- Name
  Any Number,              !- Schedule Type Limits Name
  1.0;                     !- Hourly Value
"""
        write_file(demo_idf, dummy_idf_content.strip())
        logger.info(f"Created placeholder IDF file at: {demo_idf}")
        
    if not demo_epw.exists():
        write_file(demo_epw, "Dummy EPW Weather File Content")
        logger.info(f"Created placeholder EPW file at: {demo_epw}")

    try:
        results = run_optimization_pipeline(demo_idf, demo_epw)
        print("\nPipeline execution summary:")
        print(json.dumps(results["savings"], indent=2))
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        import sys
        sys.exit(1)
