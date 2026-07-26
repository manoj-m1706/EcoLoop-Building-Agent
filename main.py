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
import sys

def animate_data_transfer(source: str, target: str, payload: dict):
    """Creates a visual log of live data transfer for the demo."""
    logger.info(f">> LIVE DATA STREAM OPENED: {source} ===> {target}")
    for k, v in payload.items():
        logger.info(f"   [SYNC] Streaming {k: <20} »»» {v}")
    logger.info(f">> STREAM CLOSED")

def run_simulation_with_correction(runner: EnergyPlusRunner, idf_file: Path, weather_file: Path, output_dir: Path) -> Any:
    try:
        return runner.run(idf_file, weather_file, output_dir)
    except Exception as e:
        logger.warning(f"Simulation run on {idf_file.name} encountered a failure: {e}. Launching agentic self-correction loop...")
        from llm.react_agent import ReActAgent
        react_agent = ReActAgent()
        success = react_agent.run_correction_loop(
            idf_path=str(idf_file),
            weather_path=str(weather_file),
            log_path=str(output_dir / "eplusout.err"),
            max_iterations=5
        )
        if success:
            logger.info(f"Self-correction loop successfully corrected {idf_file.name} and ran simulation!")
            return runner.run(idf_file, weather_file, output_dir)
        else:
            logger.error(f"Self-correction loop failed to correct simulation error for {idf_file.name}.")
            raise e

def run_optimization_pipeline(
    idf_path: str | Path,
    weather_path: str | Path,
    base_output_dir: str | Path = None,
    opt_output_dir: str | Path = None
) -> Dict[str, Any]:
    """
    Executes the closed-loop building energy optimization pipeline with multiple iterations:
    Baseline Run -> Parse -> Iteration 1 (LLM -> Modify -> Run) -> Iteration 2 (LLM -> Modify -> Run) -> Final Iteration (LLM -> Modify -> Run) -> Savings.
    """
    start_time = time.time()
    logger.info("=========================================")
    logger.info("EcoLoop Building Agent Pipeline Started (Multi-Iteration)")
    logger.info("=========================================")
    
    idf_path = Path(idf_path)
    weather_path = Path(weather_path)
    
    # 1. Setup paths
    base_output_dir = Path(base_output_dir or Config.SIMULATIONS_DIR / "baseline")
    opt_output_dir = Path(opt_output_dir or Config.SIMULATIONS_DIR / "optimized")
    optimized_idf_path = opt_output_dir / "optimized_building.idf"

    repo_idf_dir = Config.BASE_DIR / "energyplus" / "idf"
    repo_idf_dir.mkdir(parents=True, exist_ok=True)
    
    baseline_idf = repo_idf_dir / "baseline.idf"
    iteration1_idf = repo_idf_dir / "optimized_iteration1.idf"
    iteration2_idf = repo_idf_dir / "optimized_iteration2.idf"
    final_idf = repo_idf_dir / "optimized_final.idf"

    # Copy initial IDF to baseline.idf
    copy_idf(idf_path, baseline_idf)

    # Clean previous intermediate simulation output runs (but keep logs)
    clean_output_folders(Config.SIMULATIONS_DIR, keep_logs=True)
    
    # 2. RUN BASELINE SIMULATION
    logger.info("--- Step 1: Running Baseline Simulation ---")
    runner = EnergyPlusRunner(Config.ENERGYPLUS_PATH)
    baseline_result = runner.run(baseline_idf, weather_path, base_output_dir)
    
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

    agent = LLMAgent()
    editor = IDFEditor()

    # 4. ITERATION 1
    logger.info("--- Step 3: Optimization Iteration 1 ---")
    animate_data_transfer("EnergyPlus Sensors", "LLM Agent", {
        "Avg_Temp": f"{baseline_metrics.avg_indoor_temp:.2f}°C",
        "PMV_Comfort": f"{baseline_metrics.avg_pmv:.2f}",
        "Energy_Demand": f"{baseline_metrics.total_electricity_kwh * 1000.0 / 24.0:.2f} W"
    })
    decision_1 = agent.get_decision(
        temperature=baseline_metrics.avg_indoor_temp,
        humidity=baseline_metrics.avg_relative_humidity,
        pmv=baseline_metrics.avg_pmv,
        energy=baseline_metrics.total_electricity_kwh * 1000.0 / 24.0,
        occupancy=1.0  
    )
    animate_data_transfer("LLM Agent", "EnergyPlus IDF Controls", {
        "Cooling_Setpoint": f"{decision_1['cooling_setpoint']}°C",
        "Heating_Setpoint": f"{decision_1['heating_setpoint']}°C",
        "Lighting": str(decision_1['lighting']).upper()
    })
    logger.info(f"Iteration 1 Decision: Cooling Setpoint={decision_1['cooling_setpoint']}°C | "
                f"Heating Setpoint={decision_1['heating_setpoint']}°C | "
                f"Lighting={decision_1['lighting']}")
    
    container_1 = editor.load_idf(baseline_idf)
    container_1 = editor.change_cooling_setpoint(container_1, decision_1["cooling_setpoint"])
    container_1 = editor.change_heating_setpoint(container_1, decision_1["heating_setpoint"])
    container_1 = editor.change_lighting_schedule(container_1, decision_1["lighting"])
    editor.save_new_idf(container_1, iteration1_idf)

    logger.info("Running simulation for Iteration 1...")
    iter1_output_dir = Config.SIMULATIONS_DIR / "iteration1"
    run_simulation_with_correction(runner, iteration1_idf, weather_path, iter1_output_dir)
    iter1_metrics = EnergyPlusParser.parse_csv(iter1_output_dir / "eplusout.csv")
    logger.info(f"Iteration 1 Results: Energy={iter1_metrics.total_electricity_kwh:.2f} kWh | PMV={iter1_metrics.avg_pmv:.2f}")

    # 5. ITERATION 2
    logger.info("--- Step 4: Optimization Iteration 2 ---")
    animate_data_transfer("EnergyPlus Sensors", "LLM Agent", {
        "Avg_Temp": f"{iter1_metrics.avg_indoor_temp:.2f}°C",
        "PMV_Comfort": f"{iter1_metrics.avg_pmv:.2f}",
        "Energy_Demand": f"{iter1_metrics.total_electricity_kwh * 1000.0 / 24.0:.2f} W"
    })
    decision_2 = agent.get_decision(
        temperature=iter1_metrics.avg_indoor_temp,
        humidity=iter1_metrics.avg_relative_humidity,
        pmv=iter1_metrics.avg_pmv,
        energy=iter1_metrics.total_electricity_kwh * 1000.0 / 24.0,
        occupancy=1.0  
    )
    animate_data_transfer("LLM Agent", "EnergyPlus IDF Controls", {
        "Cooling_Setpoint": f"{decision_2['cooling_setpoint']}°C",
        "Heating_Setpoint": f"{decision_2['heating_setpoint']}°C",
        "Lighting": str(decision_2['lighting']).upper()
    })
    logger.info(f"Iteration 2 Decision: Cooling Setpoint={decision_2['cooling_setpoint']}°C | "
                f"Heating Setpoint={decision_2['heating_setpoint']}°C | "
                f"Lighting={decision_2['lighting']}")
    
    container_2 = editor.load_idf(iteration1_idf)
    container_2 = editor.change_cooling_setpoint(container_2, decision_2["cooling_setpoint"])
    container_2 = editor.change_heating_setpoint(container_2, decision_2["heating_setpoint"])
    container_2 = editor.change_lighting_schedule(container_2, decision_2["lighting"])
    editor.save_new_idf(container_2, iteration2_idf)

    logger.info("Running simulation for Iteration 2...")
    iter2_output_dir = Config.SIMULATIONS_DIR / "iteration2"
    run_simulation_with_correction(runner, iteration2_idf, weather_path, iter2_output_dir)
    iter2_metrics = EnergyPlusParser.parse_csv(iter2_output_dir / "eplusout.csv")
    logger.info(f"Iteration 2 Results: Energy={iter2_metrics.total_electricity_kwh:.2f} kWh | PMV={iter2_metrics.avg_pmv:.2f}")

    # 6. FINAL ITERATION
    logger.info("--- Step 5: Optimization Final Iteration ---")
    animate_data_transfer("EnergyPlus Sensors", "LLM Agent", {
        "Avg_Temp": f"{iter2_metrics.avg_indoor_temp:.2f}°C",
        "PMV_Comfort": f"{iter2_metrics.avg_pmv:.2f}",
        "Energy_Demand": f"{iter2_metrics.total_electricity_kwh * 1000.0 / 24.0:.2f} W"
    })
    decision_final = agent.get_decision(
        temperature=iter2_metrics.avg_indoor_temp,
        humidity=iter2_metrics.avg_relative_humidity,
        pmv=iter2_metrics.avg_pmv,
        energy=iter2_metrics.total_electricity_kwh * 1000.0 / 24.0,
        occupancy=1.0  
    )
    animate_data_transfer("LLM Agent", "EnergyPlus IDF Controls", {
        "Cooling_Setpoint": f"{decision_final['cooling_setpoint']}°C",
        "Heating_Setpoint": f"{decision_final['heating_setpoint']}°C",
        "Lighting": str(decision_final['lighting']).upper()
    })
    logger.info(f"Final Decision: Cooling Setpoint={decision_final['cooling_setpoint']}°C | "
                f"Heating Setpoint={decision_final['heating_setpoint']}°C | "
                f"Lighting={decision_final['lighting']}")
    
    container_final = editor.load_idf(iteration2_idf)
    container_final = editor.change_cooling_setpoint(container_final, decision_final["cooling_setpoint"])
    container_final = editor.change_heating_setpoint(container_final, decision_final["heating_setpoint"])
    container_final = editor.change_lighting_schedule(container_final, decision_final["lighting"])
    editor.save_new_idf(container_final, final_idf)

    logger.info("Running simulation for Final Iteration...")
    run_simulation_with_correction(runner, final_idf, weather_path, opt_output_dir)
    
    # Copy final IDF to optimized_idf_path for downstream compatibility
    copy_idf(final_idf, optimized_idf_path)

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

    decision = decision_final

    # 8. CALCULATE SAVINGS
    logger.info("--- Step 7: Calculating Energy and Cost Savings ---")
    
    base_elec = baseline_metrics.total_electricity_kwh + baseline_metrics.hvac_electricity_kwh
    opt_elec = optimized_metrics.total_electricity_kwh + optimized_metrics.hvac_electricity_kwh
    
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
