import re
from pathlib import Path
from utils.logger import logger

def tool_read_file(filepath: str) -> str:
    """Reads the contents of a file on disk."""
    path = Path(filepath)
    if not path.exists():
        return f"Error: File {filepath} does not exist."
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return f"Error reading file: {e}"

def tool_extract_errors(log_path: str) -> str:
    """Parses a log file (e.g. eplusout.err) and returns severe or fatal errors."""
    path = Path(log_path)
    if not path.exists():
        return f"Log file {log_path} not found."
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        errors = []
        for line in content.splitlines():
            if "severe" in line.lower() or "fatal" in line.lower() or "error" in line.lower():
                errors.append(line)
        if not errors:
            return "No critical errors found in logs."
        return "\n".join(errors)
    except Exception as e:
        return f"Error extracting logs: {e}"

def tool_patch_idf(idf_path: str, target: str, replacement: str) -> str:
    """Replaces a specific substring or pattern in the IDF file to correct building configurations."""
    path = Path(idf_path)
    if not path.exists():
        return f"Error: IDF file {idf_path} not found."
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        if target not in content:
            # Try case-insensitive search
            matches = re.findall(re.escape(target), content, re.IGNORECASE)
            if matches:
                target = matches[0]
            else:
                return f"Warning: Target string '{target}' not found in IDF."
        
        updated_content = content.replace(target, replacement)
        path.write_text(updated_content, encoding="utf-8")
        return f"Success: Successfully replaced '{target}' with '{replacement}' in IDF."
    except Exception as e:
        return f"Error patching IDF: {e}"

def tool_run_simulation(idf_path: str, weather_path: str) -> str:
    """Executes the EnergyPlus building simulation run and returns execution status."""
    from energyplus.runner import EnergyPlusRunner
    from config import Config
    try:
        runner = EnergyPlusRunner(Config.ENERGYPLUS_PATH)
        output_folder = Path(idf_path).parent
        result = runner.run_simulation(Path(idf_path), Path(weather_path), output_folder)
        return f"Simulation finished successfully. Output CSV generated: {result.csv_path}"
    except Exception as e:
        return f"Simulation failed with error: {e}"
