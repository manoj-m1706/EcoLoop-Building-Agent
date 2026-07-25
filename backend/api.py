import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional

from config import Config
from utils.logger import logger
from utils.file_manager import read_file
from energyplus.runner import EnergyPlusRunner
from main import run_optimization_pipeline

# Initialize FastAPI App with Metadata for Swagger UI
app = FastAPI(
    title="EcoLoop Building BMS API",
    description="Autonomous Building Energy Management System (BMS) optimization API powered by EnergyPlus & LLM Agents.",
    version="1.0.0"
)

# Request Models
class RunSimulationRequest(BaseModel):
    idf_path: str = Field(default="demo/sample_building.idf", description="Path to the Input Data File (.idf)")
    weather_path: str = Field(default="demo/weather.epw", description="Path to the weather file (.epw)")
    output_folder: str = Field(default="outputs/custom_run", description="Path to the output folder")

class OptimizeRequest(BaseModel):
    idf_path: str = Field(default="demo/sample_building.idf", description="Path to the Input Data File (.idf)")
    weather_path: str = Field(default="demo/weather.epw", description="Path to the weather file (.epw)")

# Response Models
class HealthResponse(BaseModel):
    status: str
    mode: str
    model: str
    energyplus_path: str

# Endpoints
@app.get("/health", response_model=HealthResponse, tags=["Diagnostics"])
def get_health():
    """Returns the API health status and configuration details."""
    logger.info("Healthcheck endpoint called.")
    return HealthResponse(
        status="healthy",
        mode="Mock" if Config.IS_MOCK_MODE else "Real",
        model=Config.MODEL_NAME,
        energyplus_path=Config.ENERGYPLUS_PATH or "None (Mock Mode)"
    )

@app.post("/run", tags=["Simulations"])
def run_simulation(req: RunSimulationRequest):
    """
    Executes a single standard EnergyPlus simulation.
    Runs in Mock Mode if no ENERGYPLUS_PATH is configured.
    """
    logger.info(f"API /run triggered with IDF: {req.idf_path}")
    try:
        runner = EnergyPlusRunner(Config.ENERGYPLUS_PATH)
        result = runner.run(req.idf_path, req.weather_path, req.output_folder)
        return {
            "status": result.status,
            "execution_time_seconds": round(result.execution_time, 2),
            "output_folder": str(Path(result.output_folder).resolve()),
            "logs_preview": result.error_logs[:500]
        }
    except Exception as e:
        logger.exception("Error during manual simulation run via API")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/optimize", tags=["Optimization"])
def run_optimization(req: OptimizeRequest):
    """
    Triggers the closed-loop optimization pipeline:
    Baseline run -> LLM Optimization -> IDF override -> Optimized run -> Savings summary.
    """
    logger.info(f"API /optimize triggered with IDF: {req.idf_path}")
    try:
        # Run optimization pipeline synchronously to return results directly to API caller
        summary = run_optimization_pipeline(req.idf_path, req.weather_path)
        return summary
    except Exception as e:
        logger.exception("Error during optimization pipeline execution via API")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/results", tags=["Analytics"])
def get_results():
    """Retrieves the latest saved optimization and savings results."""
    logger.info("API /results triggered.")
    results_file = Config.RESULTS_FILE
    if not results_file.exists():
        return {
            "status": "warning",
            "message": "No optimization results found. Please call /optimize first."
        }
    try:
        data = json_loads_file(results_file)
        return data
    except Exception as e:
        logger.error(f"Failed to read results: {e}")
        raise HTTPException(status_code=500, detail="Failed to load results file.")

@app.get("/logs", tags=["Diagnostics"])
def get_logs(lines: int = 150):
    """Retrieves the last N lines of the simulation and API log file."""
    logger.info(f"API /logs triggered for last {lines} lines.")
    log_file = Config.LOGS_DIR / "simulation.log"
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="Log file not found.")
    
    try:
        content = log_file.read_text(encoding="utf-8", errors="ignore")
        log_lines = content.splitlines()
        last_lines = log_lines[-lines:] if len(log_lines) > lines else log_lines
        return {
            "log_file": str(log_file.resolve()),
            "lines_count": len(last_lines),
            "logs": last_lines
        }
    except Exception as e:
        logger.error(f"Failed to read logs: {e}")
        raise HTTPException(status_code=500, detail="Failed to load logs.")

# Helper for JSON loading
import json
def json_loads_file(file_path: Path) -> Dict[str, Any]:
    return json.loads(file_path.read_text(encoding="utf-8"))
