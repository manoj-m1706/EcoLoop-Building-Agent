import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    """
    Configuration manager for the EcoLoop Building Agent.
    Validates directories and exposes environment variables.
    """
    # Base workspace directory
    BASE_DIR = Path(__file__).resolve().parent

    # Environment Variables
    ENERGYPLUS_PATH = os.getenv("ENERGYPLUS_PATH", "").strip()
    WEATHER_FILE = os.getenv("WEATHER_FILE", "demo/weather.epw").strip()
    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip()
    MODEL_NAME = os.getenv("MODEL_NAME", "qwen3").strip()
    OUTPUT_FOLDER_NAME = os.getenv("OUTPUT_FOLDER", "outputs").strip()

    # Derived Paths
    OUTPUT_DIR = BASE_DIR / OUTPUT_FOLDER_NAME
    LOGS_DIR = OUTPUT_DIR / "logs"
    SIMULATIONS_DIR = OUTPUT_DIR / "simulations"
    RESULTS_FILE = OUTPUT_DIR / "results.json"

    # Simulation Modes
    IS_MOCK_MODE = not bool(ENERGYPLUS_PATH)

    @classmethod
    def validate_and_setup(cls) -> None:
        """
        Validates configured paths and creates necessary directories.
        """
        # Create output directories
        cls.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cls.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        cls.SIMULATIONS_DIR.mkdir(parents=True, exist_ok=True)

        # Log system mode
        if cls.IS_MOCK_MODE:
            print("System configured in: MOCK MODE (No ENERGYPLUS_PATH provided)")
        else:
            # Validate EnergyPlus binary path
            ep_path = Path(cls.ENERGYPLUS_PATH)
            if not ep_path.exists():
                raise FileNotFoundError(
                    f"EnergyPlus executable not found at specified path: {cls.ENERGYPLUS_PATH}. "
                    f"Please verify ENERGYPLUS_PATH in .env or clear it to use Mock Mode."
                )

# Setup directories upon import
Config.validate_and_setup()
