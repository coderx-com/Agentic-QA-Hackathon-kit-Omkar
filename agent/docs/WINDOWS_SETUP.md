# Windows setup and run guide (cmd)

Every command below is for **Windows cmd** (not PowerShell). Each step ends with **What you should see** so you can confirm before moving on.

---

## Part A: Put the agent into your fork

### A1. Fork and clone the kit

1. On GitHub, open the organizers' `Agentic-QA-Hackathon-kit` repository and click **Fork**.
2. In cmd:

```bat
cd %USERPROFILE%\Documents
git clone https://github.com/Santosh-2195/Agentic-QA-Hackathon-kit.git
cd Agentic-QA-Hackathon-kit
dir
```

**What you should see:** `README.md`, `agent`, `test-website`, and the orientation PDF listed.

### A2. Extract the agent zip into `agent\`

Assuming `agentic-qa-agent.zip` is in your Downloads folder:

```bat
tar -xf %USERPROFILE%\Downloads\agentic-qa-agent.zip -C agent
dir agent
```

**What you should see:** `qa_agent`, `tests`, `docs`, `README.md`, `requirements.txt`, `run_agent.bat`, `.env.example` (the kit's original `agent\README.md` is replaced by the new one, which is intended).

---

## Part B: Install the agent

### B1. Check Python

```bat
python --version
```

**What you should see:** `Python 3.11.x` or newer. If the command is not found, install Python from python.org and tick **Add python.exe to PATH**.

### B2. Create a virtual environment and install

```bat
cd agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

**What you should see:**
* the prompt now starts with `(.venv)`;
* pip ends with `Successfully installed ... playwright-... tzdata-...`;
* Playwright downloads Chromium and finishes without errors.

`tzdata` is required on Windows; without it the Pacific-time date checks cannot run.

### B3. Offline self-test

```bat
python -m unittest -v
```

**What you should see:** `Ran 9 tests ... OK`.

### B4. Optional: enable LLM assistance

```bat
copy .env.example .env
notepad .env
```

Put your key after `ANTHROPIC_API_KEY=` and save. Skip this step to run at $0 cost; every check still runs and the verdicts are the same.

---

## Part C: Run against the hosted target (what you submit)

Use the URL the organizers gave you (the **frontend** URL, the page with "Enter Demo"):

```bat
python -m qa_agent --url https://<organizer-demobank-url> --prd ..\test-website\PRD.md
```

**What you should see:**

```
== DemoBank spec-driven QA agent v1.0.0 ==  run 2026....  tag QA-XXXX
[1/5] INTERPRET  parsing PRD into atomic requirements
      56 requirements from 9 sections
[2/5] PLAN       mapping requirements to checks
      56/56 requirements mapped (0 by LLM), 53 checks planned
[3/5] EXPLORE    discovering pages and controls
      11 pages, API at https://...
[4/5] EXECUTE    53 checks (each in a fresh browser session)
  [01/53] PASS  C-ACC-01   Enter Demo is clearly labeled ...
  ...
[5/5] VERIFY+REPORT
Requirements: .. passed, .. failed, .. unverified (of 56)
...
Report: C:\...\agent\runs\2026....\report.html
```

A full run takes roughly 4–6 minutes. Open the report:

```bat
start runs\<run-folder>\report.html
```

If you see **PREFLIGHT FAILED**, the URL is unreachable. Open it in your normal browser first and check it shows the DemoBank landing page.

---

## Part D (optional): Run the target locally

You need this only for practice or if the hosted site is down. It requires **Docker Desktop** (running), **Python 3.11+** and **Node.js 20+**. Windows has no `make`, so the Docker commands are given directly. Use three cmd windows.

### D1. Window 1: database

```bat
cd %USERPROFILE%\Documents\Agentic-QA-Hackathon-kit\test-website\database
docker compose up -d --wait
```

**What you should see:** `Container database-postgres-1  Healthy`.

### D2. Window 2: backend

```bat
cd %USERPROFILE%\Documents\Agentic-QA-Hackathon-kit\test-website\backend
python -m venv .venv
.venv\Scripts\activate
pip install -e .
set DATABASE_URL=postgresql://demobank:demobank_local@localhost:5432/demobank
set SESSION_SECRET=local-dev-secret-change-me-0123456789
uvicorn demobank.main:app --host 0.0.0.0 --port 8000
```

**What you should see:** `Uvicorn running on http://0.0.0.0:8000`. Visiting http://localhost:8000/health shows `{"status":"ok"}`.

### D3. Window 3: frontend

```bat
cd %USERPROFILE%\Documents\Agentic-QA-Hackathon-kit\test-website\frontend
npm ci
npm run dev
```

**What you should see:** `Local: http://localhost:5173/`. Use `localhost`, not `127.0.0.1`, because the backend only allows CORS from `http://localhost:5173`.

### D4. Run the agent locally

In a fourth window (or Window 3 after stopping nothing; just open a new one):

```bat
cd %USERPROFILE%\Documents\Agentic-QA-Hackathon-kit\agent
.venv\Scripts\activate
python -m qa_agent --url http://localhost:5173 --prd ..\test-website\PRD.md
```

### D5. Reset to the seeded state before a recorded run

```bat
cd %USERPROFILE%\Documents\Agentic-QA-Hackathon-kit\test-website\database
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U demobank -d demobank < migrations\001_create_schema.sql
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U demobank -d demobank < seed.sql
```

**What you should see:** `BEGIN`, `TRUNCATE TABLE`, several `INSERT 0 n`, `COMMIT`. The dashboard then shows Everyday Checking **$4,369.75** and Rainy Day Savings **$8,275.00**.

---

## Useful variants

| Goal | Command |
|---|---|
| Visible browser for the video | `python -m qa_agent --url <URL> --prd ..\test-website\PRD.md --headed --slowmo 300 --only C-ACC-01 C-XFER-02 C-XFER-13 C-HIST-09` |
| Only transfers | `... --only C-XFER` |
| Zero-cost run | `... --llm none` |
| Playwright trace per check | `... --trace`, then `python -m playwright show-trace runs\<run>\traces\C-XFER-13.zip` |
| Shortcut | `run_agent.bat <URL>` |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `'python' is not recognized` | Reinstall Python with "Add to PATH", or use `py` instead of `python` |
| `ZoneInfoNotFoundError: America/Los_Angeles` | `pip install tzdata` (inside the activated `.venv`) |
| `Executable doesn't exist ... chromium` | `python -m playwright install chromium` |
| Many checks UNVERIFIED with timeouts | The target is slow or down; open it in a browser, then rerun. Try `--only` on the affected area |
| Garbled `•` or `−` characters in cmd output | Cosmetic only; run `chcp 65001` first. The reports are UTF-8 and display correctly |
| `.venv\Scripts\activate` blocked | You are in PowerShell; type `cmd` first, or use `.venv\Scripts\activate.bat` |
