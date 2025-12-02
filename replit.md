# HOSS Agent MVP

## Overview
A minimal autonomous research agent that pulls tasks from a queue, uses an LLM (OpenAI) plus web tools to research topics, and writes summaries with cost tracking.

## Project Structure
```
hoss-agent-mvp/
├── main.py         # Entrypoint - loops over tasks, runs agent, prints metrics
├── agent.py        # ReAct-style agent with OpenAI ChatCompletion API
├── tools.py        # Web tools: web_search (DuckDuckGo) and web_fetch (BeautifulSoup)
├── tasks.py        # In-memory task queue management
├── accounting.py   # CostTracker for token usage and cost calculation
└── requirements.txt
```

## How It Works

### Task Flow
1. `main.py` fetches open tasks from the queue
2. For each task, it runs the agent with the task description
3. Agent uses ReAct-style reasoning with tool commands
4. Results are stored with cost/profit calculations
5. Summary is printed at the end

### Agent Protocol
The agent uses these commands in its responses:
- `USE_SEARCH: <query>` - Search the web via DuckDuckGo (returns real results)
- `USE_FETCH: <url>` - Fetch and parse webpage content with BeautifulSoup
- `FINAL_ANSWER: <summary>` - Return final research summary

### Cost Tracking
- Tokens estimated as `len(text) // 4`
- Default cost: 0.15 cents per 1K tokens
- Profit = Reward - Cost

## Running the Agent

### Prerequisites
Set the `OPENAI_API_KEY` environment variable (use Secrets tab in Replit)

### Run
```bash
python main.py
```

## Configuration

### Modify Tasks
Edit `tasks.py` to add/modify the `TASKS` list with:
- `id`: Unique task identifier
- `description`: What to research
- `reward_cents`: How much the task pays

### Adjust Agent Behavior
- `MAX_STEPS` in `agent.py`: Maximum reasoning steps (default: 6)
- `cost_per_1k_tokens_cents` in `accounting.py`: Cost calculation rate

## Recent Changes
- Added real DuckDuckGo search (Dec 2025)
- Added BeautifulSoup for HTML parsing (Dec 2025)
- Added error handling for OpenAI API errors (Dec 2025)
- Initial MVP scaffold (Dec 2025)
