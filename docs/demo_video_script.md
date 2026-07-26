# EcoLoop Building BMS - Demo Video Script

**Target Length:** 3 Minutes
**Deliverable:** 5 (Demo Video)

---

## 0:00 – 0:20 | Introduction & Problem Statement
**Visual:**
- **0:00:** Start on a sleek title slide or the deployed Streamlit Dashboard homepage showing the title "EcoLoop Building BMS".
- **0:10:** Briefly show a generic image of a commercial building or a quick slide stating the problem: "Buildings account for 40% of global energy waste."

**Voiceover (VO):**
*"Welcome to EcoLoop Building BMS. Today, commercial buildings waste massive amounts of energy due to static, unresponsive HVAC schedules. Our solution solves this by introducing an autonomous, AI-driven closed-loop control system that dynamically optimizes building environments in real-time."*

---

## 0:20 – 0:50 | System Architecture
**Visual:**
- **0:20:** Open the GitHub repository and navigate to `docs/system_architecture.md`.
- **0:30:** Scroll to the Architecture Diagram. Highlight the flow with your mouse cursor as you explain it.

**Voiceover (VO):**
*"Here is how the architecture works. We start with a baseline simulation using EnergyPlus. The sensor outputs—like temperature, humidity, and energy demand—are parsed and fed into a Qwen or Gemini LLM through an MCP Server. The AI reasons about thermal comfort and energy tariffs, generates optimization actions, and we patch the IDF file to run the optimized simulation back in EnergyPlus."*

---

## 0:50 – 1:30 | Live Execution (`python main.py`)
**Visual:**
- **0:50:** Switch to your VS Code terminal (or standard terminal). Ensure the font is large enough to read.
- **0:55:** Type `python main.py` and hit enter.
- **1:00-1:30:** Let the terminal output scroll. Use your mouse to highlight key log lines as they appear:
  - `Running baseline simulation...`
  - `Parsing EnergyPlus outputs...`
  - `Querying AI Model...`
  - `Updating IDF schedules...`
  - `Running optimized simulation...`

**Voiceover (VO):**
*"Let’s see it in action. I'm running the main optimization pipeline. First, it executes the baseline building simulation. Then, our parser extracts the results and passes them to the AI agent. You can see the LLM actively reasoning about the current PMV and energy consumption to adjust the cooling and heating setpoints. Finally, the system patches our IDF file and runs the optimized simulation to validate the AI's decisions."*

---

## 1:30 – 2:30 | Dashboard & Results (The "Wow" Factor)
**Visual:**
- **1:30:** Open the browser to the live Streamlit Dashboard (e.g., `http://localhost:8501` or your Streamlit Cloud URL).
- **1:40:** Point to the top metric cards showing **7.7% Savings** and **30 kWh Energy Saved**.
- **1:50:** Scroll down to the AI Reasoning text box. Read a snippet of why the AI made the decision.
- **2:10:** Show the PMV (Predicted Mean Vote) chart, highlighting how the comfort is perfectly maintained within the green ideal band [-0.7 to 0.7].
- **2:20:** Show the Watts comparison chart (Baseline vs Optimized) to visually prove the energy reduction.

**Voiceover (VO):**
*"Now let's look at the results on our live dashboard. As you can see on the top metrics, our AI agent successfully reduced total energy consumption by over 7.7%, saving 30 kilowatt-hours in a single run. If we look at the AI reasoning, we can see exactly why it relaxed the setpoints during unoccupied hours. Most importantly, looking at our Fanger Comfort PMV chart, the building remains perfectly within the ideal human comfort range. We achieved significant energy savings without sacrificing occupant comfort."*

---

## 2:30 – 3:00 | GitHub Walkthrough & Conclusion
**Visual:**
- **2:30:** Switch back to the GitHub repository in your browser.
- **2:40:** Briefly click through the folder structure:
  - Show the detailed `README.md`.
  - Open the `energyplus/idf` folder to prove that the multiple iteration IDF files are being dynamically generated and saved.
  - Show the `dashboard/app.py` or `main.py` briefly to highlight clean code structure.
- **2:50:** Return to the main repo page or dashboard for the final sign-off.

**Voiceover (VO):**
*"All of this is fully open-source and documented on our GitHub. We have comprehensive documentation, the dynamic IDF tracking system, and a modular architecture ready for real-world deployment. EcoLoop is ready to transform how buildings consume energy. Thank you."*
