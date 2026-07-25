import json
from pathlib import Path
from mcp.server.fastmcp import FastMCP

from config import Config
from utils.logger import logger
from energyplus.runner import EnergyPlusRunner, EnergyPlusSimulationError
from energyplus.parser import EnergyPlusParser
from energyplus.idf_editor import IDFEditor

# Initialize FastMCP Server
mcp = FastMCP("EcoLoop Building BMS Server")

@mcp.tool()
def run_energyplus(idf_path: str, weather_path: str, output_folder: str) -> str:
    """
    Executes an EnergyPlus simulation.
    
    Args:
        idf_path: Absolute or relative path to the Input Data File (.idf)
        weather_path: Absolute or relative path to the weather data file (.epw)
        output_folder: Absolute or relative path where simulation outputs will be written
        
    Returns:
        JSON string representing the simulation execution status, time, and folders.
    """
    logger.info(f"MCP Tool 'run_energyplus' called for IDF: {idf_path}")
    try:
        runner = EnergyPlusRunner(Config.ENERGYPLUS_PATH)
        result = runner.run(idf_path, weather_path, output_folder)
        return json.dumps({
            "status": result.status,
            "execution_time_seconds": round(result.execution_time, 2),
            "output_folder": str(Path(result.output_folder).resolve()),
            "logs_preview": result.error_logs[:300] + "..." if len(result.error_logs) > 300 else result.error_logs
        }, indent=2)
    except Exception as e:
        logger.exception("Error in run_energyplus tool execution")
        return json.dumps({
            "status": "failed",
            "error": str(e)
        }, indent=2)

@mcp.tool()
def read_simulation(csv_path: str) -> str:
    """
    Parses the output CSV from an EnergyPlus run to extract energy, temperature, and comfort metrics.
    
    Args:
        csv_path: Absolute or relative path to the eplusout.csv file
        
    Returns:
        JSON string summarizing comfort indices (PMV), electricity consumption, and HVAC loads.
    """
    logger.info(f"MCP Tool 'read_simulation' called for CSV: {csv_path}")
    try:
        output = EnergyPlusParser.parse_csv(csv_path)
        return json.dumps(output.metrics_summary, indent=2)
    except Exception as e:
        logger.exception("Error in read_simulation tool execution")
        return json.dumps({
            "status": "failed",
            "error": str(e)
        }, indent=2)

@mcp.tool()
def modify_idf(idf_path: str, cooling_sp: float, heating_sp: float, lighting_status: str, output_path: str) -> str:
    """
    Modifies setpoints and lighting schedules in an EnergyPlus IDF file, saving to a new location.
    
    Args:
        idf_path: Source IDF file path
        cooling_sp: Target cooling temperature setpoint (°C)
        heating_sp: Target heating temperature setpoint (°C)
        lighting_status: Lighting state ('on', 'off', or 'low')
        output_path: Destination path for the updated optimized IDF file
        
    Returns:
        JSON string containing the new IDF filepath.
    """
    logger.info(f"MCP Tool 'modify_idf' called. New Cooling={cooling_sp}, Heating={heating_sp}, Lighting={lighting_status}")
    try:
        editor = IDFEditor()
        container = editor.load_idf(idf_path)
        container = editor.change_cooling_setpoint(container, cooling_sp)
        container = editor.change_heating_setpoint(container, heating_sp)
        container = editor.change_lighting_schedule(container, lighting_status)
        saved_path = editor.save_new_idf(container, output_path)
        return json.dumps({
            "status": "success",
            "modified_idf_path": saved_path
        }, indent=2)
    except Exception as e:
        logger.exception("Error in modify_idf tool execution")
        return json.dumps({
            "status": "failed",
            "error": str(e)
        }, indent=2)

@mcp.tool()
def calculate_savings(baseline_csv: str, optimized_csv: str) -> str:
    """
    Compares baseline and optimized simulation CSV outputs and calculates percentage energy/cost savings and thermal comfort impact.
    
    Args:
        baseline_csv: Path to baseline eplusout.csv
        optimized_csv: Path to optimized eplusout.csv
        
    Returns:
        JSON string containing percentage energy savings, temperature comparisons, and comfort deviations.
    """
    logger.info("MCP Tool 'calculate_savings' called.")
    try:
        base_out = EnergyPlusParser.parse_csv(baseline_csv)
        opt_out = EnergyPlusParser.parse_csv(optimized_csv)
        
        base_elec = base_out.total_electricity_kwh
        opt_elec = opt_out.total_electricity_kwh
        
        base_hvac = base_out.hvac_electricity_kwh
        opt_hvac = opt_out.hvac_electricity_kwh
        
        # Calculate reductions
        elec_saved = base_elec - opt_elec
        elec_savings_pct = (elec_saved / base_elec * 100) if base_elec > 0 else 0.0
        
        hvac_saved = base_hvac - opt_hvac
        hvac_savings_pct = (hvac_saved / base_hvac * 100) if base_hvac > 0 else 0.0
        
        # Calculate cost savings (assume flat rate of $0.12 per kWh)
        kwh_rate = 0.12
        cost_saved = elec_saved * kwh_rate
        
        # Comfort change (average PMV deviation from neutral 0.0)
        base_pmv_dev = abs(base_out.avg_pmv)
        opt_pmv_dev = abs(opt_out.avg_pmv)
        pmv_diff = opt_pmv_dev - base_pmv_dev  # Negative is better (closer to neutral comfort)
        
        summary = {
            "baseline_total_elec_kwh": round(base_elec, 2),
            "optimized_total_elec_kwh": round(opt_elec, 2),
            "total_electricity_saved_kwh": round(elec_saved, 2),
            "total_savings_pct": round(elec_savings_pct, 2),
            
            "baseline_hvac_elec_kwh": round(base_hvac, 2),
            "optimized_hvac_elec_kwh": round(opt_hvac, 2),
            "hvac_electricity_saved_kwh": round(hvac_saved, 2),
            "hvac_savings_pct": round(hvac_savings_pct, 2),
            
            "cost_saved_usd": round(cost_saved, 2),
            
            "baseline_avg_temp": round(base_out.avg_indoor_temp, 2),
            "optimized_avg_temp": round(opt_out.avg_indoor_temp, 2),
            
            "baseline_avg_pmv": round(base_out.avg_pmv, 2),
            "optimized_avg_pmv": round(opt_out.avg_pmv, 2),
            "pmv_comfort_change": round(pmv_diff, 2)
        }
        
        logger.info(f"Savings calculated: {elec_savings_pct:.2f}% energy savings achieved.")
        return json.dumps(summary, indent=2)
        
    except Exception as e:
        logger.exception("Error in calculate_savings tool execution")
        return json.dumps({
            "status": "failed",
            "error": str(e)
        }, indent=2)

if __name__ == "__main__":
    logger.info("Starting FastMCP EcoLoop Server...")
    mcp.run()
