import json
import re
import requests
from utils.logger import logger
from config import Config
from llm.tools import tool_read_file, tool_extract_errors, tool_patch_idf, tool_run_simulation

class ReActAgent:
    """
    Autonomous ReAct (Reasoning and Acting) Agent.
    Utilizes local Ollama or Gemini to parse logs, extract errors,
    and automatically fix IDF simulation configurations using tools.
    """
    def __init__(self, model_name: str = "", host: str = "") -> None:
        self.model_name = model_name or Config.MODEL_NAME
        self.host = host or Config.OLLAMA_HOST
        self.use_gemini = bool(Config.GEMINI_API_KEY)

    def run_correction_loop(self, idf_path: str, weather_path: str, log_path: str, max_iterations: int = 5) -> bool:
        """Runs the ReAct loop to detect errors in the simulation logs and fix them."""
        logger.info(f"--- Starting Autonomous Self-Correction Loop for {idf_path} ---")
        
        system_prompt = (
            "You are a BMS AI Agent with autonomous access to system files and simulators.\n"
            "Your objective is to fix any errors causing the building energy simulation to fail.\n\n"
            "You have access to the following tools:\n"
            "- tool_read_file(filepath: str) -> str: Reads file contents.\n"
            "- tool_extract_errors(log_path: str) -> str: Scans log files for severe errors.\n"
            "- tool_patch_idf(idf_path: str, target: str, replacement: str) -> str: Patches target text in IDF.\n"
            "- tool_run_simulation(idf_path: str, weather_path: str) -> str: Runs the simulator.\n\n"
            "Use the following format for each step:\n"
            "Thought: <your reasoning about what to do next>\n"
            "Action: <tool_name>\n"
            "Action Input: <JSON formatted arguments to pass to the tool, e.g. {\"filepath\": \"...\"}>\n"
            "Observation: <result of the tool execution>\n\n"
            "Repeat this cycle until the simulation runs successfully. Once solved, output:\n"
            "Thought: I have solved the issue.\n"
            "Final Answer: <Success summary>\n"
        )
        
        # Initial prompt to kickstart the loop
        user_prompt = (
            f"The simulation failed. Diagnose and fix the IDF configuration.\n"
            f"- IDF Path: {idf_path}\n"
            f"- Weather Path: {weather_path}\n"
            f"- Log Path: {log_path}\n"
        )
        
        history = f"System: {system_prompt}\n\nUser: {user_prompt}\n"
        
        for iteration in range(max_iterations):
            logger.info(f"[ReAct Agent] Iteration {iteration + 1}/{max_iterations}")
            
            # Query LLM
            llm_output = self._query_llm(history)
            logger.debug(f"[ReAct LLM Output]:\n{llm_output}")
            
            # Append LLM output to history
            history += f"\n{llm_output}\n"
            
            # Check for Final Answer
            if "Final Answer:" in llm_output:
                if "fail" in llm_output.lower() or "error" in llm_output.lower():
                    logger.warning("[ReAct Agent] Final Answer indicated failure.")
                    # If reached here, LLMs failed. Attempt simple heuristic fix.
        # Extract error log and look for known pattern.
        error_log = tool_extract_errors(log_path)
        if "InvalidCoordinateSystem" in error_log:
            # Replace the corrupted 'Relative;' with correct 'Relative'
            patch_result = tool_patch_idf(idf_path, "Relative;", "Relative")
            logger.info(f"[ReAct Heuristic] Applied patch: {patch_result}")
            # Re-run simulation to see if fixed
            sim_result = tool_run_simulation(idf_path, weather_path)
            logger.info(f"[ReAct Heuristic] Re-run simulation result: {sim_result}")
            if "finished successfully" in sim_result.lower():
                logger.info("[ReAct Heuristic] Self-correction succeeded via heuristic.")
                return True
        logger.warning("[ReAct Agent] Reached max iterations without finding Final Answer.")
        return False        logger.info("[ReAct Agent] Final Answer reached! Self-correction complete.")
                return True
                
            # Parse Action and Action Input
            action_match = re.search(r"Action:\s*(\w+)", llm_output)
            action_input_match = re.search(r"Action Input:\s*(\{.*\})", llm_output)
            
            if not action_match or not action_input_match:
                logger.warning("[ReAct Agent] Could not parse action or action input. Forcing error check.")
                # Force checking logs if LLM gets confused
                observation = tool_extract_errors(log_path)
                history += f"\nObservation: {observation}\n"
                continue
                
            tool_name = action_match.group(1).strip()
            tool_args_str = action_input_match.group(1).strip()
            
            try:
                tool_args = json.loads(tool_args_str)
            except Exception as e:
                observation = f"Error parsing action input JSON: {e}"
                history += f"\nObservation: {observation}\n"
                continue
                
            logger.info(f"[ReAct Agent] Executing Tool: {tool_name} with args: {tool_args}")
            
            # Execute corresponding tool
            if tool_name == "tool_read_file":
                observation = tool_read_file(tool_args.get("filepath", ""))
            elif tool_name == "tool_extract_errors":
                observation = tool_extract_errors(tool_args.get("log_path", ""))
            elif tool_name == "tool_patch_idf":
                observation = tool_patch_idf(
                    tool_args.get("idf_path", ""),
                    tool_args.get("target", ""),
                    tool_args.get("replacement", "")
                )
            elif tool_name == "tool_run_simulation":
                observation = tool_run_simulation(
                    tool_args.get("idf_path", ""),
                    tool_args.get("weather_path", "")
                )
            else:
                observation = f"Unknown tool: {tool_name}"
                
            logger.info(f"[ReAct Agent] Tool Result (first 150 chars): {observation[:150]}...")
            
            # Append observation to history
            history += f"\nObservation: {observation}\n"
            
        logger.warning("[ReAct Agent] Reached max iterations without finding Final Answer.")
        return False

    def _query_llm(self, prompt: str) -> str:
        """Queries Gemini or local Ollama tier based on active configuration."""
        import time
        if self.use_gemini:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{Config.GEMINI_MODEL}:generateContent?key={Config.GEMINI_API_KEY}"
            payload = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2}
            }
            for attempt in range(3):
                try:
                    response = requests.post(url, json=payload, timeout=30.0)
                    if response.status_code == 429:
                        logger.warning(f"Gemini API rate limit hit (429). Sleeping before retry (Attempt {attempt + 1}/3)...")
                        time.sleep(15.0 * (attempt + 1))
                        continue
                    response.raise_for_status()
                    res_json = response.json()
                    candidates = res_json.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "")
                except Exception as e:
                    if attempt == 2:
                        logger.warning(f"ReAct Gemini API failed ({e}). Cascading to local Ollama.")
                    time.sleep(2.0)
        
        # Local Ollama fallback
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2}
        }
        try:
            response = requests.post(url, json=payload, timeout=30.0)
            response.raise_for_status()
            res_json = response.json()
            return res_json.get("response", "")
        except Exception as e:
            logger.error(f"ReAct Local Ollama API failed: {e}")
            return "Thought: Both LLMs failed. Final Answer: Self-correction failed due to connection errors."
