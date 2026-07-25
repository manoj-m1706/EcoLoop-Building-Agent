import os
import subprocess
import time
from pathlib import Path
from dataclasses import dataclass
from utils.logger import logger
from utils.file_manager import create_directory, write_file
import pandas as pd
import numpy as np

class EnergyPlusSimulationError(Exception):
    """Exception raised when an EnergyPlus simulation fails."""
    pass

@dataclass
class SimulationResult:
    """Dataclass to store EnergyPlus simulation execution results."""
    status: str          # "success" or "failed"
    execution_time: float  # In seconds
    output_folder: str
    error_logs: str
    stdout: str
    stderr: str

class EnergyPlusRunner:
    """
    Runner for EnergyPlus simulations. Handles real execution via subprocess
    and a physically realistic thermodynamic mock mode.
    """
    def __init__(self, energyplus_path: str = "") -> None:
        self.energyplus_path = energyplus_path.strip()
        self.is_mock_mode = not bool(self.energyplus_path)
        if not self.is_mock_mode:
            self.energyplus_exe = Path(self.energyplus_path)
            if not self.energyplus_exe.exists():
                raise FileNotFoundError(f"EnergyPlus executable not found: {self.energyplus_path}")
        else:
            logger.info("EnergyPlus runner initialized in MOCK MODE.")

    def run(self, idf_path: str | Path, weather_path: str | Path, output_folder: str | Path) -> SimulationResult:
        """
        Executes the EnergyPlus simulation using subprocess (real) or physics-based simulation (mock).
        """
        idf_path = Path(idf_path)
        weather_path = Path(weather_path)
        output_folder = Path(output_folder)

        if not idf_path.exists():
            raise FileNotFoundError(f"IDF file not found: {idf_path}")
        
        create_directory(output_folder)
        
        start_time = time.time()
        
        if self.is_mock_mode:
            return self._run_mock_simulation(idf_path, weather_path, output_folder, start_time)
        else:
            return self._run_real_simulation(idf_path, weather_path, output_folder, start_time)

    def _run_real_simulation(self, idf_path: Path, weather_path: Path, output_folder: Path, start_time: float) -> SimulationResult:
        """Runs the actual EnergyPlus binary via subprocess."""
        logger.info(f"Launching EnergyPlus simulation for {idf_path.name}...")
        
        # Command syntax: energyplus -d <output_dir> -w <weather_file> <idf_file>
        cmd = [
            str(self.energyplus_exe),
            "-d", str(output_folder),
            "-w", str(weather_path),
            str(idf_path)
        ]
        
        try:
            logger.debug(f"Running command: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False  # We want to capture return code and handle manually
            )
            
            execution_time = time.time() - start_time
            stdout_content = result.stdout
            stderr_content = result.stderr
            
            # Read EnergyPlus .err file if generated
            err_file = output_folder / "eplusout.err"
            error_logs = ""
            if err_file.exists():
                error_logs = err_file.read_text(encoding="utf-8", errors="ignore")
            else:
                error_logs = stderr_content

            status = "success" if result.returncode == 0 else "failed"
            
            logger.info(f"Simulation completed with status: {status} in {execution_time:.2f}s")
            
            if status == "failed":
                logger.error(f"EnergyPlus execution failed. Error logs:\n{error_logs}")
                raise EnergyPlusSimulationError(f"EnergyPlus simulation failed with code {result.returncode}. Logs: {error_logs[:500]}")
                
            return SimulationResult(
                status=status,
                execution_time=execution_time,
                output_folder=str(output_folder),
                error_logs=error_logs,
                stdout=stdout_content,
                stderr=stderr_content
            )
            
        except subprocess.SubprocessError as e:
            execution_time = time.time() - start_time
            logger.exception("Subprocess execution failed")
            raise EnergyPlusSimulationError(f"Failed to start EnergyPlus process: {e}")

    def _run_mock_simulation(self, idf_path: Path, weather_path: Path, output_folder: Path, start_time: float) -> SimulationResult:
        """
        Runs a mock thermodynamic simulation. Reads the current setpoints in the IDF file
        and generates a realistic time-series output CSV.
        """
        logger.info(f"[MOCK] Running thermodynamic simulation for {idf_path.name}...")
        
        # Read the IDF content to discover setpoints
        idf_content = idf_path.read_text(encoding="utf-8", errors="ignore")
        
        # Simple extraction of setpoints and lighting from IDF content
        cooling_sp = 24.0
        heating_sp = 20.0
        lighting_status = "on"
        
        # Parse setpoint overrides written as comments or eppy objects
        # We search for comments like "! - AI cooling setpoint: X" or look directly at setpoint schedule definitions
        for line in idf_content.splitlines():
            if "AI cooling setpoint:" in line:
                try:
                    cooling_sp = float(line.split("AI cooling setpoint:")[1].strip())
                except ValueError:
                    pass
            elif "AI heating setpoint:" in line:
                try:
                    heating_sp = float(line.split("AI heating setpoint:")[1].strip())
                except ValueError:
                    pass
            elif "AI lighting status:" in line:
                lighting_status = line.split("AI lighting status:")[1].strip().lower()

        logger.debug(f"[MOCK] Detected setpoints in IDF: Cooling={cooling_sp}C, Heating={heating_sp}C, Lighting={lighting_status}")

        # Simulate 24 hours of building data
        hours = list(range(24))
        out_temps = []
        in_temps = []
        humidities = []
        pmvs = []
        total_elec = []
        cool_elec = []
        heat_elec = []
        cool_loads = []
        heat_loads = []
        
        # Building parameter defaults
        t_in = 22.0  # Initial indoor temp
        u_val = 0.5  # Building heat loss coefficient (kW/C)
        c_building = 15.0  # Building thermal capacity (kWh/C)
        cop_cool = 3.2
        cop_heat = 1.0  # Resistive heating
        
        for hr in hours:
            # 1. Outdoor drybulb temp (sinusoidal peak at 15:00)
            t_out = 20.0 + 10.0 * np.sin(2 * np.pi * (hr - 9) / 24)
            out_temps.append(t_out)
            
            # 2. Internal heat gains (occupancy 08:00 - 18:00)
            is_occupied = 8 <= hr <= 18
            q_occ = 2.5 if is_occupied else 0.2  # kW
            
            # Solar gains
            q_solar = max(0.0, 5.0 * np.sin(2 * np.pi * (hr - 6) / 24))  # kW
            
            # Lighting gains
            q_light = 0.8 if lighting_status == "on" else 0.05  # kW
            
            # Envelope heat transfer (q = U * (Tout - Tin))
            q_envelope = u_val * (t_out - t_in)  # kW (positive means heat entering)
            
            # Total net heat gain before HVAC
            q_net_passive = q_envelope + q_solar + q_occ + q_light
            
            # Predict indoor temperature if HVAC is off
            t_in_predicted = t_in + q_net_passive / c_building
            
            # HVAC Operations
            q_cool_needed = 0.0
            q_heat_needed = 0.0
            
            if t_in_predicted > cooling_sp:
                # Cooling needed to pull temp down to cooling setpoint
                q_cool_needed = max(0.0, (t_in_predicted - cooling_sp) * c_building)  # kWh
                # Cap cooling system size
                q_cool_needed = min(12.0, q_cool_needed)
                t_in = t_in_predicted - (q_cool_needed / c_building)
            elif t_in_predicted < heating_sp:
                # Heating needed to pull temp up to heating setpoint
                q_heat_needed = max(0.0, (heating_sp - t_in_predicted) * c_building)  # kWh
                # Cap heating system size
                q_heat_needed = min(15.0, q_heat_needed)
                t_in = t_in_predicted + (q_heat_needed / c_building)
            else:
                t_in = t_in_predicted
                
            in_temps.append(t_in)
            cool_loads.append(q_cool_needed * 3600000.0) # J
            heat_loads.append(q_heat_needed * 3600000.0) # J
            
            # 3. Energy consumption calculations
            e_cool_joules = (q_cool_needed / cop_cool) * 3600000.0  # J (1 kWh = 3.6e6 J)
            e_heat_joules = (q_heat_needed / cop_heat) * 3600000.0  # J
            cool_elec.append(e_cool_joules)
            heat_elec.append(e_heat_joules)
            
            # Base electrical loads (lighting, IT, equipment)
            e_base_w = (q_light + 0.5) * 1000.0  # W
            e_hvac_w = ((q_cool_needed / cop_cool) + (q_heat_needed / cop_heat)) * 1000.0  # W
            total_elec.append(e_base_w + e_hvac_w)
            
            # 4. Thermal Comfort (PMV) calculation (simplified Fanger formula)
            # PMV = 0.303 * exp(-0.036 * M) + ... approximated around indoor temp
            # Ideal comfort temp is 22C. Dev from 22C creates discomfort.
            # Comfort also affected by occupancy metabolic rate.
            pmv_val = 0.35 * (t_in - 22.0)
            # Cap PMV between -3 and +3
            pmv_val = max(-3.0, min(3.0, pmv_val))
            pmvs.append(pmv_val)
            
            # Relative Humidity (inversely related to temperature in closed space)
            rh_val = 55.0 - 1.5 * (t_in - 20.0) + (5.0 * np.sin(2 * np.pi * hr / 24))
            rh_val = max(10.0, min(95.0, rh_val))
            humidities.append(rh_val)

        # Build output dataframe
        df = pd.DataFrame({
            "Date/Time": [f"07/25 {hr:02d}:00:00" for hr in hours],
            "Environment:Site Outdoor Air Drybulb Temperature [C](Hourly)": out_temps,
            "Zone Air Temperature [C](Hourly)": in_temps,
            "Zone Air Relative Humidity [%](Hourly)": humidities,
            "Zone Thermal Comfort Fanger Model PMV [](Hourly)": pmvs,
            "Facility Total Electricity Demand Rate [W](Hourly)": total_elec,
            "Cooling:Electricity [J](Hourly)": cool_elec,
            "Heating:Electricity [J](Hourly)": heat_elec,
            "Zone Air System Sensible Cooling Rate [W](Hourly)": cool_loads,
            "Zone Air System Sensible Heating Rate [W](Hourly)": heat_loads
        })
        
        # Save CSV file in output folder
        csv_output_path = output_folder / "eplusout.csv"
        df.to_csv(csv_output_path, index=False)
        
        # Save mock log file
        err_output_path = output_folder / "eplusout.err"
        mock_err_content = (
            "EnergyPlus Warmup completed successfully.\n"
            "Environment:Site Outdoor Air Drybulb Temperature [C](Hourly) variable registered.\n"
            "Simulation completed without errors.\n"
        )
        write_file(err_output_path, mock_err_content)
        
        execution_time = time.time() - start_time
        logger.info(f"[MOCK] Simulation completed in {execution_time:.4f}s. Outputs saved to {csv_output_path}")
        
        return SimulationResult(
            status="success",
            execution_time=execution_time,
            output_folder=str(output_folder),
            error_logs=mock_err_content,
            stdout="[MOCK] EnergyPlus run successful",
            stderr=""
        )
