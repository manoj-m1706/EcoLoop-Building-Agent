import pandas as pd
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, List
from utils.logger import logger

@dataclass
class SimulationOutput:
    """Dataclass holding extracted metrics from an EnergyPlus CSV output."""
    total_electricity_kwh: float = 0.0
    hvac_electricity_kwh: float = 0.0
    avg_indoor_temp: float = 0.0
    avg_relative_humidity: float = 0.0
    avg_pmv: float = 0.0
    total_cooling_load_kwh: float = 0.0
    total_heating_load_kwh: float = 0.0
    metrics_summary: Dict[str, Any] = field(default_factory=dict)

class EnergyPlusParser:
    """
    Parses EnergyPlus output CSV files. Maps columns dynamically using substrings
    and compiles energy statistics into a SimulationOutput dataclass.
    """
    @staticmethod
    def parse_csv(csv_path: str | Path) -> SimulationOutput:
        """
        Parses the EnergyPlus output CSV file and extracts key variables.
        """
        csv_path = Path(csv_path)
        if not csv_path.exists():
            raise FileNotFoundError(f"Simulation output CSV file not found: {csv_path}")

        try:
            logger.info(f"Parsing simulation results from {csv_path.name}...")
            # Load CSV (EnergyPlus might have trailing whitespaces in column names)
            df = pd.read_csv(csv_path)
            df.columns = [col.strip() for col in df.columns]

            # Helpers for column matching
            def find_columns_by_substrings(substrings: List[str]) -> List[str]:
                matched = []
                for col in df.columns:
                    if any(sub.lower() in col.lower() for sub in substrings):
                        matched.append(col)
                return matched

            # 1. Total Electricity Consumption
            # Can be in J (Joules) or average W (Watts). If Watts, we multiply average W by hours / 1000 to get kWh.
            elec_cols = find_columns_by_substrings(["electricity:facility", "facility total electricity demand rate", "total electricity"])
            total_elec_kwh = 0.0
            if elec_cols:
                col = elec_cols[0]
                if "[J]" in col or "joule" in col.lower():
                    # Sum of all rows in Joules converted to kWh (1 J = 2.77778e-7 kWh)
                    total_elec_kwh = df[col].sum() * 2.77778e-7
                else:
                    # Assume Watts or kW. If Watts, average Watts * hours / 1000
                    mean_w = df[col].mean()
                    hours = len(df)
                    total_elec_kwh = (mean_w * hours) / 1000.0
                logger.debug(f"Matched total electricity column: '{col}' = {total_elec_kwh:.2f} kWh")
            else:
                logger.warning("Total electricity column not found in output CSV.")

            # 2. HVAC Electricity
            # Often split into Cooling:Electricity [J] and Heating:Electricity [J]
            hvac_cols = find_columns_by_substrings(["cooling:electricity", "heating:electricity", "hvac:electricity", "fan:electricity", "sensible cooling energy", "sensible heating energy", "ideal loads zone sensible cooling energy", "ideal loads zone sensible heating energy"])
            hvac_elec_kwh = 0.0
            for col in hvac_cols:
                if "[J]" in col or "joule" in col.lower():
                    hvac_elec_kwh += df[col].sum() * 2.77778e-7
                else:
                    mean_w = df[col].mean()
                    hours = len(df)
                    hvac_elec_kwh += (mean_w * hours) / 1000.0
            logger.debug(f"Matched HVAC electricity columns: {hvac_cols} = {hvac_elec_kwh:.2f} kWh")

            # 3. Indoor Temperature
            temp_cols = find_columns_by_substrings(["zone air temperature", "zone mean air temperature", "indoor temperature"])
            avg_temp = 22.0
            if temp_cols:
                avg_temp = df[temp_cols].mean().mean() # average across all matching zones and timesteps
                logger.debug(f"Matched indoor temp columns: {temp_cols} = {avg_temp:.2f} °C")
            else:
                logger.warning("Indoor temperature column not found. Defaulting to 22.0 °C")

            # 4. Relative Humidity
            rh_cols = find_columns_by_substrings(["zone air relative humidity", "zone relative humidity", "humidity"])
            avg_rh = 50.0
            if rh_cols:
                avg_rh = df[rh_cols].mean().mean()
                logger.debug(f"Matched relative humidity columns: {rh_cols} = {avg_rh:.2f} %")
            else:
                logger.warning("Relative humidity column not found. Defaulting to 50.0%")

            # 5. Thermal Comfort (PMV)
            pmv_cols = find_columns_by_substrings(["pmv", "fanger model pmv", "thermal comfort fanger"])
            avg_pmv = 0.0
            if pmv_cols:
                avg_pmv = df[pmv_cols].mean().mean()
                logger.debug(f"Matched PMV columns: {pmv_cols} = {avg_pmv:.2f}")
            else:
                if temp_cols and rh_cols:
                    # Estimate PMV using Fanger approximation: PMV = (T - 23.5) * 0.33 + (RH - 50.0) * 0.005
                    avg_pmv = (avg_temp - 23.5) * 0.33 + (avg_rh - 50.0) * 0.005
                    logger.info(f"Calculated average PMV comfort fallback: {avg_pmv:.2f} (T: {avg_temp:.2f} C, RH: {avg_rh:.2f}%)")
                else:
                    logger.warning("PMV comfort column not found. Defaulting to 0.0 (Neutral)")

            # 6. Cooling Load
            cool_load_cols = find_columns_by_substrings(["sensible cooling rate", "cooling load", "cooling rate", "sensible cooling energy"])
            total_cool_load_kwh = 0.0
            for col in cool_load_cols:
                # If rate in Watts: mean W * hours / 1000 = kWh
                # If energy in Joules: sum J * 2.77778e-7 = kWh
                if "[J]" in col or "joule" in col.lower() or "energy" in col.lower():
                    total_cool_load_kwh += df[col].sum() * 2.77778e-7
                else:
                    total_cool_load_kwh += (df[col].mean() * len(df)) / 1000.0
            logger.debug(f"Matched cooling load columns: {cool_load_cols} = {total_cool_load_kwh:.2f} kWh")

            # 7. Heating Load
            heat_load_cols = find_columns_by_substrings(["sensible heating rate", "heating load", "heating rate", "sensible heating energy"])
            total_heat_load_kwh = 0.0
            for col in heat_load_cols:
                if "[J]" in col or "joule" in col.lower() or "energy" in col.lower():
                    total_heat_load_kwh += df[col].sum() * 2.77778e-7
                else:
                    total_heat_load_kwh += (df[col].mean() * len(df)) / 1000.0
            logger.debug(f"Matched heating load columns: {heat_load_cols} = {total_heat_load_kwh:.2f} kWh")

            summary = {
                "total_elec_kwh": round(total_elec_kwh, 2),
                "hvac_elec_kwh": round(hvac_elec_kwh, 2),
                "avg_indoor_temp": round(avg_temp, 2),
                "avg_relative_humidity": round(avg_rh, 2),
                "avg_pmv": round(avg_pmv, 2),
                "cooling_load_kwh": round(total_cool_load_kwh, 2),
                "heating_load_kwh": round(total_heat_load_kwh, 2),
                "rows_processed": len(df)
            }

            logger.info("Simulation outputs parsed successfully.")
            return SimulationOutput(
                total_electricity_kwh=total_elec_kwh,
                hvac_electricity_kwh=hvac_elec_kwh,
                avg_indoor_temp=avg_temp,
                avg_relative_humidity=avg_rh,
                avg_pmv=avg_pmv,
                total_cooling_load_kwh=total_cool_load_kwh,
                total_heating_load_kwh=total_heat_load_kwh,
                metrics_summary=summary
            )

        except Exception as e:
            logger.error(f"Error occurred while parsing output CSV: {e}")
            raise
