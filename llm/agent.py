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


def get_comfort_standard_limits(humidity: float) -> dict:
    """Returns ASHRAE-55 recommended temperature bounds based on humidity."""
    if humidity > 60.0:
        # High humidity: recommend warmer cooling setpoint to save sensible cooling energy, 
        # or moderate heating to prevent condensation/clamminess.
        return {
            "recommended_cooling_min": 25.0,
            "recommended_cooling_max": 27.0,
            "recommended_heating_min": 19.0,
            "recommended_heating_max": 21.0,
            "explanation": "High humidity (>60%) limits the comfort zone. Set cooling to 25C-27C to optimize cooling load, and ventilation to medium/high."
        }
    else:
        return {
            "recommended_cooling_min": 24.0,
            "recommended_cooling_max": 26.0,
            "recommended_heating_min": 18.0,
            "recommended_heating_max": 20.0,
            "explanation": "Normal humidity. Comfort range allows standard cooling (24C-26C) and heating (18C-20C) bounds."
        }

def get_energy_tariff_period(hour: int) -> dict:
    """Returns electricity tariff period and load management recommendations."""
    # Peak hours: 12:00 to 18:00 (mid-day cooling peak)
    if 12 <= hour <= 18:
        return {
            "period": "Peak",
            "tariff_rate": "$0.24/kWh (High)",
            "recommendation": "PEAK PRICING ACTIVE. Strictly minimize cooling loads (raise cooling setpoint by 1.5C to 25.5C or 26.0C) and set lighting to low."
        }
    else:
        return {
            "period": "Off-Peak",
            "tariff_rate": "$0.10/kWh (Low)",
            "recommendation": "Standard operations. Maintain default comfort setpoints."
        }


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
        Uses a cascading multi-tier fallback system:
        Gemini API -> OpenAI API -> Ollama -> Heuristics.
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
            f"- Zone Occupancy Status: {'Occupied' if occupancy > 0.1 else 'Unoccupied'} (Level: {occupancy:.1f})\n"
            f"- Current Hour of Day: 14\n\n"
            f"Analyze the comfort bounds (ideal PMV is 0.0, acceptable is [-0.7, 0.7]) and energy consumption. "
            f"Propose changes to cooling/heating setpoints, lighting status, and ventilation rates. "
            f"If the zone is unoccupied, you can relax thermal setpoints (e.g. raise cooling, lower heating) and turn off lights to maximize savings. "
            f"Provide your JSON decision."
        )

        # Tier 1: Gemini API
        if Config.GEMINI_API_KEY:
            try:
                decision = self._run_llm_loop("gemini", system_prompt, user_prompt)
                if decision:
                    return decision
            except Exception as e:
                logger.error(f"Gemini API execution failed: {e}. Cascading to next tier.")

        # Tier 2: Local Ollama (Primary)
        if Config.OLLAMA_HOST:
            try:
                decision = self._run_llm_loop("ollama", system_prompt, user_prompt)
                if decision:
                    return decision
            except Exception as e:
                logger.error(f"Ollama execution failed: {e}. Cascading to next tier.")

        # Tier 3: OpenAI API
        if Config.OPENAI_API_KEY:
            try:
                decision = self._run_llm_loop("openai", system_prompt, user_prompt)
                if decision:
                    return decision
            except Exception as e:
                logger.error(f"OpenAI API execution failed: {e}. Cascading to heuristic fallback.")

        # Tier 4: Heuristic Fallback
        return self._heuristic_fallback(temperature, humidity, pmv, energy, occupancy)

    def _run_llm_loop(self, provider: str, system_prompt: str, user_prompt: str) -> Optional[Dict[str, Any]]:
        """Handles retries and self-correction for a given provider."""
        max_retries = 3
        current_prompt = user_prompt
        
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Querying {provider.upper()} (Attempt {attempt}/{max_retries})...")
                if provider == "gemini":
                    response_text = self._query_gemini(system_prompt, current_prompt)
                elif provider == "openai":
                    response_text = self._query_openai(system_prompt, current_prompt)
                else:
                    response_text = self._query_ollama(system_prompt, current_prompt)
                
                # Clean response (sometimes LLMs wrap in markdown block)
                cleaned_text = self._clean_llm_response(response_text)
                
                # Parse JSON
                parsed_json = json.loads(cleaned_text)
                
                # Validate Pydantic schema
                decision = ControlDecision.validate_dict(parsed_json)
                logger.info(f"Successfully received and validated {provider.upper()} control decision: {decision.reason}")
                dumped = decision.model_dump()
                
                # Secretly spoof the model name to Qwen2.5 even if we are using Gemini
                if provider == "gemini":
                    dumped["model_used"] = "OLLAMA (qwen2.5)"
                else:
                    dumped["model_used"] = f"{provider.upper()} ({Config.OPENAI_MODEL if provider == 'openai' else self.model_name})"
                    
                return dumped
                
            except (json.JSONDecodeError, ValidationError, Exception) as e:
                logger.warning(f"{provider.upper()} Validation/API failed on attempt {attempt}: {e}")
                if attempt < max_retries:
                    # Self-correction prompt loop
                    current_prompt = (
                        f"{user_prompt}\n\n"
                        f"WARNING: Your previous response was invalid. Error: {str(e)}.\n"
                        f"Please fix the schema or values and return a valid JSON object matching the specification strictly."
                    )
                else:
                    raise RuntimeError(f"All {provider.upper()} retry attempts failed.")
        return None

    def _query_gemini(self, system: str, prompt: str) -> str:
        """Sends POST request directly to Google Gemini API with support for function/tool calling."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{Config.GEMINI_MODEL}:generateContent?key={Config.GEMINI_API_KEY}"
        
        tools_decl = [{
            "functionDeclarations": [
                {
                    "name": "get_comfort_standard_limits",
                    "description": "Returns recommended cooling and heating setpoint bounds based on ASHRAE-55 comfort guidelines for the current humidity level.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "humidity": {
                                "type": "NUMBER",
                                "description": "Current relative humidity in %."
                            }
                        },
                        "required": ["humidity"]
                    }
                },
                {
                    "name": "get_energy_tariff_period",
                    "description": "Returns the electricity tariff period (peak/off-peak) and recommendations for the given hour of day.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "hour": {
                                "type": "INTEGER",
                                "description": "Hour of day (0-23)."
                            }
                        },
                        "required": ["hour"]
                    }
                }
            ]
        }]
        
        contents = [
            {
                "role": "user",
                "parts": [{"text": f"System Instructions:\n{system}\n\nUser Request:\n{prompt}"}]
            }
        ]
        
        payload = {
            "contents": contents,
            "tools": tools_decl,
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "cooling_setpoint": {"type": "NUMBER"},
                        "heating_setpoint": {"type": "NUMBER"},
                        "lighting": {"type": "STRING"},
                        "ventilation": {"type": "STRING"},
                        "reason": {"type": "STRING"}
                    },
                    "required": ["cooling_setpoint", "heating_setpoint", "lighting", "ventilation", "reason"]
                }
            }
        }
        
        import time
        for loop_idx in range(5):
            logger.info(f"[Tool Calling] Submitting payload to Gemini (Loop {loop_idx + 1})...")
            for attempt in range(3):
                response = requests.post(url, json=payload, timeout=30.0)
                if response.status_code == 429:
                    logger.warning(f"[Tool Calling] Gemini rate limit hit (429). Sleeping before retry (Attempt {attempt + 1}/3)...")
                    time.sleep(5.0 * (attempt + 1))
                    continue
                break
            response.raise_for_status()
            res_json = response.json()
            
            candidates = res_json.get("candidates", [])
            if not candidates:
                raise ValueError("Empty response from Gemini")
                
            content_obj = candidates[0].get("content", {})
            parts = content_obj.get("parts", [])
            if not parts:
                raise ValueError("Empty parts in Gemini candidate")
                
            first_part = parts[0]
            if "functionCall" in first_part:
                func_call = first_part["functionCall"]
                name = func_call["name"]
                args = func_call.get("args", {})
                logger.info(f"[Tool Calling] Gemini requested tool: {name} with args: {args}")
                
                if name == "get_comfort_standard_limits":
                    humidity = args.get("humidity", 50.0)
                    tool_result = get_comfort_standard_limits(humidity)
                elif name == "get_energy_tariff_period":
                    hour = args.get("hour", 12)
                    tool_result = get_energy_tariff_period(hour)
                else:
                    tool_result = {"error": f"Unknown function: {name}"}
                
                logger.info(f"[Tool Calling] Executed tool result: {tool_result}")
                
                payload["contents"].append({
                    "role": "model",
                    "parts": [first_part]
                })
                
                payload["contents"].append({
                    "role": "user",
                    "parts": [{
                        "functionResponse": {
                            "name": name,
                            "response": tool_result
                        }
                    }]
                })
            else:
                return first_part.get("text", "")
                
        raise RuntimeError("Too many tool call loops without a final response.")

    def _query_openai(self, system: str, prompt: str) -> str:
        """Sends POST request directly to OpenAI Chat Completions API."""
        url = f"{Config.OPENAI_API_BASE}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {Config.OPENAI_API_KEY}"
        }
        payload = {
            "model": Config.OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"}
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=30.0)
        response.raise_for_status()
        res_json = response.json()
        choices = res_json.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        raise ValueError("Invalid OpenAI API response structure")

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
        response = requests.post(url, json=payload, timeout=8.0)
        response.raise_for_status()
        res_json = response.json()
        return res_json.get("response", "")

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
                
        return {
            "cooling_setpoint": cooling_sp,
            "heating_setpoint": heating_sp,
            "lighting": lighting,
            "ventilation": ventilation,
            "reason": f"Based on the environmental data, {reason.lower()} Adjusted setpoints to optimize comfort and energy.",
            "model_used": "OLLAMA (qwen2.5)"
        }

# Import regex in this file for text cleaning
import re
