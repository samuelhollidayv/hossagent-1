# HOSS Agent MVP

## Overview
A minimal autonomous research agent that pulls tasks from a queue, uses an LLM (OpenAI) plus web tools to research topics, and writes summaries with cost tracking.

## Project Structure
```
hoss-agent-mvp/
├── app.py          # FastAPI web application with API endpoints
├── main.py         # CLI entrypoint (legacy) - loops over tasks, runs agent
├── agent.py        # ReAct-style agent with OpenAI ChatCompletion API
├── tools.py        # Web tools: web_search (DuckDuckGo) and web_fetch (BeautifulSoup)
├── tasks.py        # In-memory task queue management
├── accounting.py   # CostTracker for token usage and cost calculation
└── requirements.txt
```

## Web Application

### Running the Web App
The web app runs automatically via the workflow. Access it through the Replit webview or public URL.

### API Endpoints
- `GET /` - Serves the main HTML dashboard
- `GET /api/tasks` - Returns all tasks with metrics
- `POST /api/run-once` - Runs the agent on the next pending task

### Features
- Dark-themed dashboard showing all tasks
- Real-time status updates (Idle/Running)
- Metrics cards: Tasks Completed, Total Reward, Total Profit
- Task table with status, costs, and profit
- Modal to view full research results
- "Run Agent on Next Task" button to process tasks

## How It Works

### Task Flow
1. User clicks "Run Agent on Next Task"
2. Backend finds the next pending task
3. Agent uses ReAct-style reasoning with tool commands
4. Results are stored with cost/profit calculations
5. Dashboard updates with new metrics

### Agent Protocol
The agent uses these commands in its responses:
- `USE_SEARCH: <query>` - Search the web via DuckDuckGo (returns real results)
- `USE_FETCH: <url>` - Fetch and parse webpage content with BeautifulSoup
- `FINAL_ANSWER: <summary>` - Return final research summary

### Cost Tracking
- Tokens estimated as `len(text) // 4`
- Default cost: 0.15 cents per 1K tokens
- Profit = Reward - Cost

## Configuration

### Environment Variables
- `OPENAI_API_KEY` - Required for the agent to work (set in Secrets tab)

### Modify Tasks
Edit `tasks.py` to add/modify the `TASKS` list with:
- `id`: Unique task identifier
- `description`: What to research
- `reward_cents`: How much the task pays

### Adjust Agent Behavior
- `MAX_STEPS` in `agent.py`: Maximum reasoning steps (default: 6)
- `cost_per_1k_tokens_cents` in `accounting.py`: Cost calculation rate

## Recent Changes
- Added FastAPI web dashboard (Dec 2025)
- Added real DuckDuckGo search (Dec 2025)
- Added BeautifulSoup for HTML parsing (Dec 2025)
- Added error handling for OpenAI API errors (Dec 2025)
- Initial MVP scaffold (Dec 2025)
