import re
import sys
from pathlib import Path
from typing import Any, Optional
from utils.logger import logger
from utils.file_manager import read_file, write_file
from config import Config

# Try to import eppy
try:
    from eppy.modeleditor import IDF
    EPPY_AVAILABLE = True
except ImportError:
    EPPY_AVAILABLE = False
    logger.warning("eppy library not installed. IDF Editor will default to Regex Fallback mode.")

class IDFEditorError(Exception):
    """Exception raised when IDF modification fails."""
    pass

class IDFContainer:
    """Wrapper that holds either an eppy IDF object or raw text content of an IDF."""
    def __init__(self, filepath: Path, content_str: str, eppy_idf: Optional[Any] = None) -> None:
        self.filepath = filepath
        self.content_str = content_str
        self.eppy_idf = eppy_idf
        self.is_eppy = eppy_idf is not None

class IDFEditor:
    """
    Manages loading, modifying, and saving EnergyPlus input data files (IDF).
    Uses eppy when available with a valid IDD; falls back to regex-based replacement.
    """
    def __init__(self) -> None:
        self.use_eppy = False
        
        if EPPY_AVAILABLE and not Config.IS_MOCK_MODE:
            # Try to resolve IDD file path in EnergyPlus installation directory
            ep_bin = Path(Config.ENERGYPLUS_PATH)
            idd_path = ep_bin.parent / "Energy+.idd"
            if idd_path.exists():
                try:
                    IDF.setiddname(str(idd_path))
                    self.use_eppy = True
                    logger.info(f"IDF Editor initialized with eppy using IDD: {idd_path}")
                except Exception as e:
                    logger.warning(f"Failed to set IDD for eppy: {e}. Falling back to Regex mode.")
            else:
                logger.warning(f"Energy+.idd not found at {idd_path}. eppy cannot be initialized. Falling back to Regex mode.")
        else:
            logger.info("IDF Editor running in Regex Fallback mode.")

    def load_idf(self, idf_path: str | Path) -> IDFContainer:
        """Loads an IDF file and returns an IDFContainer."""
        path = Path(idf_path)
        if not path.exists():
            raise FileNotFoundError(f"IDF file not found: {path}")
            
        content = read_file(path)
        
        if self.use_eppy:
            try:
                # Eppy requires a dummy or real weather file to initialize, but we can pass None or empty string for basic loading
                eppy_idf = IDF(str(path))
                logger.info(f"Loaded IDF '{path.name}' via eppy.")
                return IDFContainer(path, content, eppy_idf)
            except Exception as e:
                logger.error(f"Failed to load IDF with eppy: {e}. Falling back to Regex representation.")
                return IDFContainer(path, content, None)
        else:
            return IDFContainer(path, content, None)

    def change_cooling_setpoint(self, container: IDFContainer, setpoint: float) -> IDFContainer:
        """Modifies the cooling setpoint in the IDF file."""
        logger.info(f"Updating cooling setpoint to {setpoint}°C")
        if container.is_eppy:
            try:
                # Try modifying eppy objects
                modified = False
                # Look in Schedule:Constant or Schedule:Compact
                for sched_type in ['SCHEDULE:CONSTANT', 'SCHEDULE:COMPACT']:
                    if sched_type in container.eppy_idf.idfobjects:
                        for s in container.eppy_idf.idfobjects[sched_type]:
                            name_lower = s.Name.lower()
                            if ('cooling' in name_lower or 'clg' in name_lower) and ('setpoint' in name_lower or 'setp' in name_lower or 'clg-setp' in name_lower):
                                if sched_type == 'SCHEDULE:CONSTANT':
                                    s.Hourly_Value = setpoint
                                else: # Compact
                                    # Typically Schedule:Compact has Field 4 or similar, eppy allows indexing
                                    # Find the index containing temperature and replace it. 
                                    # For safety, let's update all fields that look like temperature numbers.
                                    for i in range(len(s.fieldvalues)):
                                        val = s.fieldvalues[i]
                                        try:
                                            fval = float(val)
                                            # Only modify daytime comfort setpoint (typically 22-25C), not night setback (typically 28-30C)
                                            if 18.0 <= fval <= 26.0:
                                                s.fieldvalues[i] = setpoint
                                        except (ValueError, TypeError):
                                            pass
                                modified = True
                if modified:
                    logger.debug("Cooling setpoint updated via eppy.")
                    # Keep text representation in sync if needed, or serialize eppy later
                    return container
                else:
                    logger.warning("Cooling setpoint schedule not found in eppy objects. Trying Regex fallback.")
            except Exception as e:
                logger.warning(f"Error modifying with eppy: {e}. Trying Regex fallback.")
        
        # Regex Fallback
        # Match: Schedule:Constant, CoolingSetpointSchedule, Temperature, 24.0;
        # Also handles Schedule:Compact with comment markers for AI
        content = container.content_str
        
        # 1. Update our AI comment metadata for the Mock Runner
        if "! - AI cooling setpoint:" in content:
            content = re.sub(r"! - AI cooling setpoint:.*", f"! - AI cooling setpoint: {setpoint}", content)
        else:
            content = "! - AI cooling setpoint: " + str(setpoint) + "\n" + content
            
        # 2. Update actual Schedule:Constant objects in IDF
        pattern = r"(Schedule:Constant,\s*(?:CoolingSetpointSchedule|Cooling Setpoint Schedule|Cooling_Setpoint_Schedule),\s*[^,\n]*,?\s*)([\d.]+);"
        if re.search(pattern, content, re.IGNORECASE):
            content = re.sub(pattern, rf"\g<1>{setpoint};", content, flags=re.IGNORECASE)
            logger.debug("Cooling setpoint object updated via regex.")
        else:
            # Fallback regex for generic Cooling setpoint schedule
            generic_pattern = r"(Schedule:Constant,\s*[^,]*Cooling[^,]*,\s*[^,\n]*,?\s*)([\d.]+);"
            content = re.sub(generic_pattern, rf"\g<1>{setpoint};", content, flags=re.IGNORECASE)
            logger.debug("Cooling setpoint object updated via generic regex.")

        container.content_str = content
        return container

    def change_heating_setpoint(self, container: IDFContainer, setpoint: float) -> IDFContainer:
        """Modifies the heating setpoint in the IDF file."""
        logger.info(f"Updating heating setpoint to {setpoint}°C")
        if container.is_eppy:
            try:
                modified = False
                for sched_type in ['SCHEDULE:CONSTANT', 'SCHEDULE:COMPACT']:
                    if sched_type in container.eppy_idf.idfobjects:
                        for s in container.eppy_idf.idfobjects[sched_type]:
                            name_lower = s.Name.lower()
                            if ('heating' in name_lower or 'htg' in name_lower) and ('setpoint' in name_lower or 'setp' in name_lower or 'htg-setp' in name_lower):
                                if sched_type == 'SCHEDULE:CONSTANT':
                                    s.Hourly_Value = setpoint
                                else:
                                    for i in range(len(s.fieldvalues)):
                                        val = s.fieldvalues[i]
                                        try:
                                            fval = float(val)
                                            # Only modify daytime comfort setpoint (typically 19-22C), not night setback (typically 12-16C)
                                            if 18.0 <= fval <= 23.0:
                                                s.fieldvalues[i] = setpoint
                                        except (ValueError, TypeError):
                                            pass
                                modified = True
                if modified:
                    logger.debug("Heating setpoint updated via eppy.")
                    return container
                else:
                    logger.warning("Heating setpoint schedule not found in eppy objects. Trying Regex fallback.")
            except Exception as e:
                logger.warning(f"Error modifying heating setpoint with eppy: {e}. Trying Regex fallback.")

        # Regex Fallback
        content = container.content_str
        
        # 1. Update AI comment metadata
        if "! - AI heating setpoint:" in content:
            content = re.sub(r"! - AI heating setpoint:.*", f"! - AI heating setpoint: {setpoint}", content)
        else:
            content = "! - AI heating setpoint: " + str(setpoint) + "\n" + content
            
        # 2. Update actual Schedule:Constant objects
        pattern = r"(Schedule:Constant,\s*(?:HeatingSetpointSchedule|Heating Setpoint Schedule|Heating_Setpoint_Schedule),\s*[^,\n]*,?\s*)([\d.]+);"
        if re.search(pattern, content, re.IGNORECASE):
            content = re.sub(pattern, rf"\g<1>{setpoint};", content, flags=re.IGNORECASE)
            logger.debug("Heating setpoint object updated via regex.")
        else:
            generic_pattern = r"(Schedule:Constant,\s*[^,]*Heating[^,]*,\s*[^,\n]*,?\s*)([\d.]+);"
            content = re.sub(generic_pattern, rf"\g<1>{setpoint};", content, flags=re.IGNORECASE)
            logger.debug("Heating setpoint object updated via generic regex.")

        container.content_str = content
        return container

    def change_lighting_schedule(self, container: IDFContainer, status: str) -> IDFContainer:
        """
        Modifies the lighting schedule in the IDF.
        Status values: "on" -> 1.0, "off" -> 0.0, "low" -> 0.3.
        """
        status_val = 1.0
        if status.lower() == "off":
            status_val = 0.0
        elif status.lower() == "low":
            status_val = 0.3
            
        logger.info(f"Updating lighting status to '{status}' (Value: {status_val})")
        
        if status.lower() == "on":
            logger.info("Lighting status is 'on'. Keeping standard baseline schedule.")
            return container
        
        if container.is_eppy:
            try:
                modified = False
                for sched_type in ['SCHEDULE:CONSTANT', 'SCHEDULE:COMPACT']:
                    if sched_type in container.eppy_idf.idfobjects:
                        for s in container.eppy_idf.idfobjects[sched_type]:
                            if 'lighting' in s.Name.lower() or 'light' in s.Name.lower():
                                if sched_type == 'SCHEDULE:CONSTANT':
                                    s.Hourly_Value = status_val
                                else:
                                    for i in range(len(s.fieldvalues)):
                                        val = s.fieldvalues[i]
                                        try:
                                            fval = float(val)
                                            if 0.0 <= fval <= 1.0:
                                                if fval > 0.1:
                                                    s.fieldvalues[i] = status_val
                                                else:
                                                    # Keep low off-hours value
                                                    s.fieldvalues[i] = min(fval, status_val)
                                        except (ValueError, TypeError):
                                            pass
                                modified = True
                if modified:
                    logger.debug("Lighting schedule updated via eppy.")
                    return container
                else:
                    logger.warning("Lighting schedule not found in eppy objects. Trying Regex fallback.")
            except Exception as e:
                logger.warning(f"Error modifying lighting with eppy: {e}. Trying Regex fallback.")

        # Regex Fallback
        content = container.content_str
        
        # 1. Update AI comment metadata
        if "! - AI lighting status:" in content:
            content = re.sub(r"! - AI lighting status:.*", f"! - AI lighting status: {status}", content)
        else:
            content = "! - AI lighting status: " + status + "\n" + content
            
        # 2. Update actual Schedule:Constant objects
        pattern = r"(Schedule:Constant,\s*(?:LightingSchedule|Lighting Schedule|Lighting_Schedule|Lights_Schedule|LightsSchedule),\s*[^,\n]*,?\s*)([\d.]+);"
        if re.search(pattern, content, re.IGNORECASE):
            content = re.sub(pattern, rf"\g<1>{status_val};", content, flags=re.IGNORECASE)
            logger.debug("Lighting schedule updated via regex.")
        else:
            generic_pattern = r"(Schedule:Constant,\s*[^,]*Light[^,]*,\s*[^,\n]*,?\s*)([\d.]+);"
            content = re.sub(generic_pattern, rf"\g<1>{status_val};", content, flags=re.IGNORECASE)
            logger.debug("Lighting schedule updated via generic regex.")

        container.content_str = content
        return container

    def save_new_idf(self, container: IDFContainer, output_path: str | Path) -> str:
        """
        Saves the modified IDF file to a new location.
        Returns the absolute string path to the new file.
        """
        out_path = Path(output_path)
        try:
            logger.info(f"Saving optimized IDF file to {out_path}...")
            
            if container.is_eppy:
                # Save via eppy
                container.eppy_idf.saveas(str(out_path))
                # Sync text representation in case we read it later
                container.content_str = out_path.read_text(encoding="utf-8")
            else:
                # Save raw text content
                write_file(out_path, container.content_str)
                
            logger.info(f"Optimized IDF saved successfully: {out_path.name}")
            return str(out_path.resolve())
            
        except Exception as e:
            logger.error(f"Failed to save modified IDF file: {e}")
            raise IDFEditorError(f"Failed to save modified IDF to {out_path}: {e}")
