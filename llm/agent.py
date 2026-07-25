import json
import requests
from pydantic import BaseModel, Field, ValidationError
from typing import Dict, Any, Optional
from utils.logger import logger
from config import Config

# Ensure the parent directory "llm" exists (handled by write_to_file, but good to keep in mind)

class ControlDecision(BaseModel):
    """Pydantic schema to validate the LLM's control decisions."""
    cooling_setpoint: float = Field(..., ge=21.0, le=29.0, description="Optimal cooling setpoint temp in C")
    heating_setpoint: float = Field(..., ge=15.0, le=22.0, description="Optimal heating setpoint temp in C")
    lighting: str = Field(..., description="Lighting state: 'on', 'off', or 'low'")
    ventilation: str = Field(..., description="Ventilation state: 'low', 'medium', or 'high'")
    reason: str = Field(..., description="Explanation of why this decision was made")

    @classmethod
    def validate_dict(cls, data: Dict[str, Any]) -> "ControlDecision":
        # Normalize lighting and ventilation strings
        data["lighting"] = str(data.get("lighting", "on")).lower()
        data["ventilation"] = str(data.get("ventilation", "medium")).lower()
        if data["lighting"] not in ["on", "off", "low"]:
            data["lighting"] = "on"
        if data["ventilation"] not in ["low", "medium", "high"]:
            data["ventilation"] = "medium"
        return cls(**data)

class LLMAgent:
    """
    LLM Agent using Ollama (Qwen3). Formulates BMS optimization prompts,
    parses JSON output, validates schemas, and handles API errors gracefully.
    """
    def __init__(self, model_name: str = "", host: str = "") -> None:
        self.model_name = model_name or Config.MODEL_NAME
        self.host = host or Config.OLLAMA_HOST
        logger.info(f"LLM Agent initialized using Ollama model '{self.model_name}' at {self.host}")

    def get_decision(self, temperature: float, humidity: float, pmv: float, energy: float, occupancy: float) -> Dict[str, Any]:
        """
        Submits building status to the LLM and returns the validated control decisions.
        """
        # Formulate prompt
        system_prompt = (
            "You are a Building Management System (BMS) AI agent optimizing energy efficiency and thermal comfort.\n"
            "You must output a single, raw JSON object ONLY. Do not include any explanations outside the JSON structure. "
            "Do not wrap your answer in markdown formatting like ```json ... ```. Output raw JSON.\n\n"
            "JSON structure:\n"
            "{\n"
            '  "cooling_setpoint": float, // range: [21.0, 29.0]\n'
            '  "heating_setpoint": float, // range: [15.0, 22.0]\n'
            '  "lighting": "on" | "off" | "low",\n'
            '  "ventilation": "low" | "medium" | "high",\n'
            '  "reason": "text explanation"\n'
            "}"
        )
        
        user_prompt = (
            f"BMS Sensor Readings:\n"
            f"- Zone Indoor Temperature: {temperature:.2f} C\n"
            f"- Relative Humidity: {humidity:.2f} %\n"
            f"- Thermal Comfort Index (PMV): {pmv:.2f}\n"
            f"- Current Total Energy Consumption Rate: {energy:.2f} W\n"
            f"- Zone Occupancy Status: {'Occupied' if occupancy > 0.1 else 'Unoccupied'} (Level: {occupancy:.1f})\n\n"
            f"Analyze the comfort bounds (ideal PMV is 0.0, acceptable is [-0.7, 0.7]) and energy consumption. "
            f"Propose changes to cooling/heating setpoints, lighting status, and ventilation rates. "
            f"If the zone is unoccupied, you can relax thermal setpoints (e.g. raise cooling, lower heating) and turn off lights to maximize savings. "
            f"Provide your JSON decision."
        )

        max_retries = 3
        current_prompt = user_prompt
        
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Querying Ollama (Attempt {attempt}/{max_retries})...")
                response_text = self._query_ollama(system_prompt, current_prompt)
                
                # Clean response (sometimes LLMs wrap in markdown block)
                cleaned_text = self._clean_llm_response(response_text)
                
                # Parse JSON
                parsed_json = json.loads(cleaned_text)
                
                # Validate Pydantic schema
                decision = ControlDecision.validate_dict(parsed_json)
                logger.info(f"Successfully received and validated control decision: {decision.reason}")
                return decision.model_dump()
                
            except (json.JSONDecodeError, ValidationError, Exception) as e:
                logger.warning(f"Validation failed on attempt {attempt}: {e}")
                if attempt < max_retries:
                    # Self-correction prompt loop
                    current_prompt = (
                        f"{user_prompt}\n\n"
                        f"WARNING: Your previous response was invalid. Error: {str(e)}.\n"
                        f"Please fix the schema or values and return a valid JSON object matching the specification strictly."
                    )
                else:
                    logger.error("All LLM retry attempts failed or Ollama is unreachable. Using Rule-Based fallback optimizer.")
                    
        return self._heuristic_fallback(temperature, humidity, pmv, energy, occupancy)

    def _query_ollama(self, system: str, prompt: str) -> str:
        """Sends POST request directly to local Ollama API endpoint."""
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model_name,
            "prompt": f"System: {system}\n\nUser: {prompt}",
            "stream": False,
            "options": {
                "temperature": 0.2  # Low temperature for highly deterministic outputs
            }
        }
        
        # Test connection with short timeout to prevent hanging the pipeline
        try:
            response = requests.post(url, json=payload, timeout=8.0)
            response.raise_for_status()
            res_json = response.json()
            return res_json.get("response", "")
        except requests.RequestException as e:
            logger.warning(f"Ollama connection error: {e}")
            raise RuntimeError("Ollama server unreachable")

    def _clean_llm_response(self, text: str) -> str:
        """Strips markdown code fences and whitespace from LLM response."""
        text = text.strip()
        if text.startswith("```"):
            # Remove start fence
            text = re.sub(r"^```(?:json)?", "", text, flags=re.MULTILINE)
            # Remove end fence
            text = re.sub(r"```$", "", text, flags=re.MULTILINE)
        return text.strip()

    def _heuristic_fallback(self, temp: float, humidity: float, pmv: float, energy: float, occupancy: float) -> Dict[str, Any]:
        """
        Rule-based optimizer fallback that executes standard energy conservation measures (ECMs).
        Acts as the fail-safe brain of the BMS.
        """
        logger.info("[FALLBACK] Executing heuristic energy-saving controls...")
        
        is_occupied = occupancy > 0.1
        
        if not is_occupied:
            # Unoccupied: Widely relax setpoints and turn off systems
            cooling_sp = 27.5
            heating_sp = 16.0
            lighting = "off"
            ventilation = "low"
            reason = "Zone is unoccupied. Relaxed HVAC setpoints and turned off lighting to minimize base load."
        else:
            # Occupied: Balance comfort and energy
            lighting = "low" if pmv > 0 else "on"
            ventilation = "medium"
            
            # Simple PMV-based temperature adjustments
            if pmv > 0.7:
                # Too hot: Need cooling
                cooling_sp = 23.5
                heating_sp = 18.0
                ventilation = "high"
                reason = "Occupied and warm (PMV > 0.7). Lowering cooling setpoint and increasing ventilation."
            elif pmv < -0.7:
                # Too cold: Need heating
                cooling_sp = 26.0
                heating_sp = 21.0
                reason = "Occupied and cold (PMV < -0.7). Raising heating setpoint to restore comfort."
            else:
                # Comfortable: Slight optimization to save energy
                cooling_sp = 24.5  # Slightly higher than baseline 24.0
                heating_sp = 19.5  # Slightly lower than baseline 20.0
                reason = "Occupied and comfortable (PMV in comfort zone). Slightly relaxed setpoints to optimize energy consumption."
                
        decision = {
            "cooling_setpoint": cooling_sp,
            "heating_setpoint": heating_sp,
            "lighting": lighting,
            "ventilation": ventilation,
            "reason": f"[Heuristic Optimizer] {reason}"
        }
        
        logger.info(f"[FALLBACK] Generated decision: {decision}")
        return decision

# Import regex in this file for text cleaning
import re
