# EcoLoop Building Agent - System Architecture Document

This document details the system architecture, protocol standard, cognitive reasoning logic, latency management, and data handling strategies implemented in the **EcoLoop Building Agent**.

---

## 1. Tool-Calling & Protocol (MCP) Architecture

To establish a standard interface between the cognitive engine (LLM) and the physical simulation engine (EnergyPlus), the project implements the **Model Context Protocol (MCP)** using `FastMCP`.

```mermaid
graph TD
    SubGraph1[MCP Host / LLM Client] -->|stdio JSON-RPC| MCP[FastMCP Server]
    MCP -->|run_energyplus| Runner[EnergyPlus Runner]
    MCP -->|read_simulation| Parser[EnergyPlus Parser]
    MCP -->|modify_idf| Editor[IDF Editor]
    MCP -->|calculate_savings| Calculator[Savings Calculator]
    
    Runner -->|Subprocess / Binary| EPlus[EnergyPlus Executable]
    Parser -->|Pandas Mapping| EPlusCSV[eplusout.csv]
    Editor -->|eppy / Regex| BuildingIDF[building.idf]
```

The MCP Server ([server.py](file:///c:/Users/matur/OneDrive/Desktop/EcoLoop-Building-Agent/mcp/server.py)) runs over `stdio` transport, enabling external MCP-compatible editors, agents, or orchestration pipelines to interact with the building management tools. Four key tools are registered:

1. **`run_energyplus`**: Triggers the execution of the building energy model. It launches the physical simulator via Python's `subprocess` or runs the physics-based thermodynamic mock simulator.
2. **`read_simulation`**: Parses the raw CSV logs from the simulation, extracting aggregate performance statistics (PMV comfort, indoor temperatures, HVAC electricity usage).
3. **`modify_idf`**: Reads the `.idf` input data file, updates schedule constants representing heating/cooling setpoints and lighting, and writes out the modified configuration.
4. **`calculate_savings`**: Computes energy reductions (kWh), percentage savings, and cost benefits between the baseline and optimized simulation runs.

---

## 2. Prompt Engineering & Control Logic

The cognitive brain of the system ([agent.py](file:///c:/Users/matur/OneDrive/Desktop/EcoLoop-Building-Agent/llm/agent.py)) uses structured JSON prompt formatting and Pydantic schema enforcement to make intelligent decisions.

### Prompt Strategy
The LLM is prompted as a specialized **Building Management System (BMS) Agent** seeking to maximize savings while maintaining human thermal comfort:
- **System Instructions**: Enforce returning a single, valid JSON object containing `cooling_setpoint`, `heating_setpoint`, `lighting`, `ventilation`, and `reason` fields.
- **Sensor Context**: Provides the current thermodynamic variables:
  * Zone Temperature (°C)
  * Relative Humidity (%)
  * Fanger Predicted Mean Vote (PMV) thermal comfort index
  * Current Total Energy Consumption (W)
  * Occupancy Status (Occupied/Unoccupied)
- **Constraint Boundaries**: Specifies the acceptable thermal bounds (ideal PMV: 0.0, acceptable range: `[-0.7, 0.7]`) and allows the LLM to relax setpoints and turn off lights during unoccupied periods.

### Pydantic Validation & Self-Correction Loop
To guarantee execution safety and prevent parsing errors:
1. The response is stripped of Markdown code fences and parsed into a Python dictionary.
2. The data is validated against a Pydantic schema ([ControlDecision](file:///c:/Users/matur/OneDrive/Desktop/EcoLoop-Building-Agent/llm/agent.py#L10)):
   * `cooling_setpoint`: Validated to be within `[21.0, 29.0]` °C.
   * `heating_setpoint`: Validated to be within `[15.0, 22.0]` °C.
   * Enums for `lighting` (`on`, `off`, `low`) and `ventilation` (`low`, `medium`, `high`) are normalized and validated.
3. **Self-Correction Feedback Loop**: If validation fails (due to malformed JSON, out-of-range setpoints, or bad values), the system catches the exception and queries the LLM again, appending the specific validation error message to guide the LLM's self-correction. The loop permits up to **3 retry attempts**.

---

## 3. Multi-Tier Cognitive Routing & Fail-Safe Fallbacks

LLM generation speed, API availability, and local service status are common failure modes in real-time BMS control loops. EcoLoop manages these concerns through a robust **Multi-Tier Cascade Routing System** that prioritizes the highest capability cloud models, falls back to local models, and ultimately defaults to a physical heuristic fallback to ensure the building loop never hangs or crashes.

```mermaid
graph TD
    Start[Get Decision] --> C1{Gemini Key present?}
    C1 -->|Yes| Gemini[Query Gemini API]
    C1 -->|No| C2{OpenAI Key present?}
    Gemini -->|Success| Valid{Valid JSON?}
    Gemini -->|Failure| C2
    C2 -->|Yes| OpenAI[Query OpenAI API]
    C2 -->|No| Ollama[Query Local Ollama]
    OpenAI -->|Success| Valid
    OpenAI -->|Failure| Ollama
    Ollama -->|Success| Valid
    Ollama -->|Failure| Heuristic[Run Heuristic Fallback]
    Valid -->|Yes| Return[Apply Setpoints]
    Valid -->|No| Retry[Self-Correction Loop / 3 Retries]
    Retry -->|Fails 3x| Heuristic
    Heuristic --> Return
```

### Cascading Tiers

1. **Google Gemini API (SOTA)**:
   - Uses `gemini-1.5-flash` (or pro).
   - Enforces 100% reliable schema conformance by passing Pydantic fields via Gemini's native `responseSchema` and `responseMimeType` parameter in the API payload, completely eliminating parsing issues.
2. **OpenAI API (SOTA)**:
   - Uses `gpt-4o-mini` (or similar) with JSON object response formatting.
3. **Local Ollama (Offline)**:
   - Queries a locally-hosted LLM model (e.g. Qwen) via HTTP REST.
4. **Intelligent Heuristic Optimizer (Ultimate Fail-Safe)**:
   - If all API keys are unconfigured, connections timeout, or the self-correction loop fails to produce a valid schema after 3 attempts, the agent automatically falls back to standard heuristic thermodynamic rules.

### Latency Optimization & Timeouts
- **Deterministic Generation**: LLM temperature is set to a low value (`0.2`) to produce highly structured and concise decisions quickly, minimizing token generation time.
- **Strict Timeouts**: Cloud and local HTTP requests are configured with strict connection and read timeout limits (`10.0` seconds for Gemini/OpenAI, `8.0` seconds for Ollama) to prevent the BMS loop from hanging indefinitely.
- **Fail-Safe Heuristic Controls**:
  - Evaluates thermodynamic constraints and occupancy rules directly via Python logic.
  - If unoccupied, it aggressively relaxes cooling to `27.5`°C and heating to `16.0`°C while turning off lights and lowering ventilation to save energy.
  - If occupied, it dynamically adjusts setpoints based on the current PMV deviation (e.g., if PMV is hot ($>0.7$), it triggers higher ventilation and lowers cooling setpoints; if comfortable, it slightly relaxes setpoints for marginal savings).

---

## 4. Technical Approach to Handling Lengthy Simulation Logs & Outputs

EnergyPlus simulations produce extremely verbose log outputs, error statements, and high-frequency time-series datasets that can quickly saturate system memory, crash LLM contexts, or overwhelm standard output buffers.

### Column-Filtered CSV Parsing
Instead of loading massive files blindly, the [EnergyPlusParser](file:///c:/Users/matur/OneDrive/Desktop/EcoLoop-Building-Agent/energyplus/parser.py#L19):
- Reads simulation output CSVs using a pandas-based dynamic search strategy that maps column headers based on substrings (e.g., looking for columns containing `pmv` or `zone air temperature` and stripping trailing whitespaces).
- Computes aggregates (means and sums) directly on the relevant columns, discard unused columns, and releases memory immediately.

### Log Truncation & Previewing
- The `.err` log file produced by EnergyPlus can span thousands of lines if warnings arise. In the MCP tool [run_energyplus](file:///c:/Users/matur/OneDrive/Desktop/EcoLoop-Building-Agent/mcp/server.py#L15), the error logs are truncated to the first **300 characters** for the JSON payload preview (`logs_preview`).
- Full verbose logs remain written inside isolated directory folders (`outputs/logs/`), while clean, high-level results are consolidated in a lightweight final metadata JSON file ([results.json](file:///c:/Users/matur/OneDrive/Desktop/EcoLoop-Building-Agent/outputs/results.json)).

### Automated Output Cleanup
- To prevent storage bloat on the host machine, the orchestrator invokes [clean_output_folders](file:///c:/Users/matur/OneDrive/Desktop/EcoLoop-Building-Agent/utils/file_manager.py) at the start of each pipeline execution.
- It removes intermediate `.csv`, `.eso`, `.shd`, and other heavy transient EnergyPlus runtime files from the simulations folder while optionally preserving persistent logging files in a separate directory.
- This ensures only the necessary active files are staged in the workspace.

---

## 5. Autonomous ReAct Agentic Self-Correction Loop

To guarantee absolute system autonomy and eliminate human-in-the-loop debugging, the EcoLoop Building Agent implements an autonomous **Reasoning and Acting (ReAct)** self-correction framework ([react_agent.py](file:///c:/Users/matur/OneDrive/Desktop/EcoLoop-Building-Agent/llm/react_agent.py)).

### ReAct Workflow & Execution Architecture
If the simulation run fails (e.g., due to syntax errors, corrupt coordinates, or missing schedules in the building model), the pipeline catches the error and spawns the `ReActAgent` to execute the self-correction cycle:

```mermaid
graph TD
    SimFail[Simulation Fails] --> SpReAct[Spawn ReAct Agent]
    SpReAct --> ReActLoop[ReAct Loop Starts]
    ReActLoop --> ReadErrors[Call tool_extract_errors]
    ReadErrors --> ParseLogs[Extract error details from eplusout.err]
    ParseLogs --> LLMReason[LLM reasons about fix]
    LLMReason --> PatchIDF[Call tool_patch_idf]
    PatchIDF --> RunSim[Call tool_run_simulation]
    RunSim --> CheckSuccess{Simulation successful?}
    CheckSuccess -->|Yes| Success[Final Answer: Success]
    CheckSuccess -->|No| ReActLoop
```

### Custom Agentic Tools
The ReAct agent operates in a closed loop utilizing standard agentic tools defined in [tools.py](file:///c:/Users/matur/OneDrive/Desktop/EcoLoop-Building-Agent/llm/tools.py):
1. **`tool_read_file(filepath)`**: Parses and reads any files on the filesystem.
2. **`tool_extract_errors(log_path)`**: Scans simulation error files (`eplusout.err`) and extracts critical warnings, severe, and fatal statements.
3. **`tool_patch_idf(idf_path, target, replacement)`**: Performs dynamic drop-in code replacements in the IDF file to correct building configurations.
4. **`tool_run_simulation(idf_path, weather_path)`**: Re-runs the physical simulation and reports success or fail codes.

This closed-loop system allows the local or cloud LLM to run diagnostics, execute tasks, parse output logs, patch IDF files, and verify physical outcomes completely autonomously.
