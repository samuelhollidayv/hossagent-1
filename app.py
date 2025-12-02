"""
FastAPI web application for the Hoss Agent demo.
"""

from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
import os

from tasks import (
    all_tasks, 
    get_next_pending_task, 
    set_task_running, 
    complete_task,
    get_metrics
)
from agent import run_agent

app = FastAPI(title="Hoss Agent Demo")


class TaskResponse(BaseModel):
    id: str
    description: str
    status: str
    reward: float
    cost: float
    profit: float
    result: Optional[str] = None


class RunResponse(BaseModel):
    message: str
    task: Optional[TaskResponse] = None


def format_task(task: dict) -> dict:
    """Format a task for API response."""
    return {
        "id": task["id"],
        "description": task["description"],
        "status": task["status"],
        "reward": task["reward_cents"],
        "cost": task["cost_cents"],
        "profit": task["profit_cents"],
        "result": task["result"][:500] if task["result"] else None
    }


@app.get("/api/tasks")
async def get_tasks():
    """Get all tasks with their current status."""
    tasks = all_tasks()
    metrics = get_metrics()
    return {
        "tasks": [format_task(t) for t in tasks],
        "metrics": metrics
    }


@app.post("/api/run-once")
async def run_once():
    """Run the agent on the next pending task."""
    task = get_next_pending_task()
    
    if task is None:
        return JSONResponse(
            content={"message": "No pending tasks available", "task": None},
            status_code=200
        )
    
    set_task_running(task["id"])
    
    try:
        result, cost_cents = run_agent(task["description"])
        completed_task = complete_task(task["id"], result, cost_cents)
        
        if completed_task:
            return {
                "message": f"Processed {task['id']}, status=completed",
                "task": format_task(completed_task)
            }
        else:
            return JSONResponse(
                content={"message": "Error completing task", "task": None},
                status_code=500
            )
    except Exception as e:
        complete_task(task["id"], f"Error: {str(e)}", 0)
        return JSONResponse(
            content={"message": f"Error running agent: {str(e)}", "task": None},
            status_code=500
        )


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the main HTML page."""
    html_content = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hoss Agent - Autonomous Research Worker</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: #0e1525;
            color: #e1e4e8;
            min-height: 100vh;
            line-height: 1.6;
        }
        
        .container {
            max-width: 950px;
            margin: 0 auto;
            padding: 40px 20px;
        }
        
        header {
            text-align: center;
            margin-bottom: 40px;
        }
        
        .logo {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            margin-bottom: 8px;
        }
        
        .logo-icon {
            width: 48px;
            height: 48px;
            background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
        }
        
        h1 {
            font-size: 2.5rem;
            font-weight: 700;
            background: linear-gradient(135deg, #6366f1 0%, #a78bfa 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .subtitle {
            color: #8b949e;
            font-size: 1.1rem;
            margin-top: 8px;
        }
        
        .status-pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 0.875rem;
            font-weight: 500;
            margin-top: 16px;
        }
        
        .status-pill.idle {
            background: rgba(34, 197, 94, 0.15);
            color: #22c55e;
        }
        
        .status-pill.running {
            background: rgba(234, 179, 8, 0.15);
            color: #eab308;
        }
        
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: currentColor;
        }
        
        .status-pill.running .status-dot {
            animation: pulse 1.5s ease-in-out infinite;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
        }
        
        .explainer {
            background: rgba(99, 102, 241, 0.08);
            border: 1px solid rgba(99, 102, 241, 0.2);
            border-radius: 12px;
            padding: 20px 24px;
            margin-bottom: 32px;
            font-size: 0.95rem;
            color: #b0b8c4;
        }
        
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 16px;
            margin-bottom: 32px;
        }
        
        .metric-card {
            background: #161b2e;
            border: 1px solid #2d3548;
            border-radius: 12px;
            padding: 20px;
            text-align: center;
        }
        
        .metric-value {
            font-size: 2rem;
            font-weight: 700;
            color: #fff;
            margin-bottom: 4px;
        }
        
        .metric-value.profit {
            color: #22c55e;
        }
        
        .metric-label {
            font-size: 0.875rem;
            color: #8b949e;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .controls {
            display: flex;
            gap: 12px;
            margin-bottom: 24px;
        }
        
        button {
            padding: 12px 24px;
            border-radius: 8px;
            font-size: 0.95rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            border: none;
        }
        
        .btn-primary {
            background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
            color: white;
        }
        
        .btn-primary:hover:not(:disabled) {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(99, 102, 241, 0.4);
        }
        
        .btn-primary:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        .btn-secondary {
            background: #2d3548;
            color: #e1e4e8;
            border: 1px solid #3d4560;
        }
        
        .btn-secondary:hover {
            background: #3d4560;
        }
        
        .tasks-table {
            width: 100%;
            border-collapse: collapse;
            background: #161b2e;
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid #2d3548;
        }
        
        .tasks-table th,
        .tasks-table td {
            padding: 14px 16px;
            text-align: left;
        }
        
        .tasks-table th {
            background: #1e2438;
            font-weight: 600;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #8b949e;
        }
        
        .tasks-table tr:not(:last-child) td {
            border-bottom: 1px solid #2d3548;
        }
        
        .tasks-table td {
            font-size: 0.9rem;
        }
        
        .task-id {
            font-family: 'SF Mono', Monaco, 'Courier New', monospace;
            color: #a78bfa;
            font-weight: 500;
        }
        
        .task-desc {
            max-width: 280px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .status-badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
        }
        
        .status-badge.pending {
            background: rgba(156, 163, 175, 0.15);
            color: #9ca3af;
        }
        
        .status-badge.running {
            background: rgba(234, 179, 8, 0.15);
            color: #eab308;
        }
        
        .status-badge.completed {
            background: rgba(34, 197, 94, 0.15);
            color: #22c55e;
        }
        
        .money {
            font-family: 'SF Mono', Monaco, 'Courier New', monospace;
        }
        
        .money.positive {
            color: #22c55e;
        }
        
        .view-btn {
            background: rgba(99, 102, 241, 0.15);
            color: #a78bfa;
            border: none;
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 0.8rem;
            cursor: pointer;
            transition: background 0.2s;
        }
        
        .view-btn:hover {
            background: rgba(99, 102, 241, 0.25);
        }
        
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.7);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            opacity: 0;
            visibility: hidden;
            transition: all 0.2s;
        }
        
        .modal-overlay.active {
            opacity: 1;
            visibility: visible;
        }
        
        .modal {
            background: #161b2e;
            border: 1px solid #2d3548;
            border-radius: 16px;
            max-width: 600px;
            width: 90%;
            max-height: 80vh;
            overflow: hidden;
        }
        
        .modal-header {
            padding: 20px 24px;
            border-bottom: 1px solid #2d3548;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .modal-header h3 {
            font-size: 1.1rem;
            color: #a78bfa;
        }
        
        .modal-close {
            background: none;
            border: none;
            color: #8b949e;
            font-size: 1.5rem;
            cursor: pointer;
            padding: 0;
            line-height: 1;
        }
        
        .modal-close:hover {
            color: #fff;
        }
        
        .modal-body {
            padding: 24px;
            overflow-y: auto;
            max-height: 60vh;
        }
        
        .result-text {
            background: #0e1525;
            border-radius: 8px;
            padding: 16px;
            font-size: 0.9rem;
            line-height: 1.7;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        
        @media (max-width: 768px) {
            .metrics-grid {
                grid-template-columns: 1fr;
            }
            
            h1 {
                font-size: 1.8rem;
            }
            
            .tasks-table {
                font-size: 0.85rem;
            }
            
            .task-desc {
                max-width: 150px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo">
                <div class="logo-icon">&#129302;</div>
                <h1>Hoss Agent</h1>
            </div>
            <p class="subtitle">Autonomous Research Worker</p>
            <div id="status-pill" class="status-pill idle">
                <span class="status-dot"></span>
                <span id="status-text">Idle</span>
            </div>
        </header>
        
        <div class="explainer">
            <strong>How it works:</strong> Each research task has a reward value. The agent uses OpenAI's API to complete tasks autonomously, and we track the exact token cost. Profit = Reward - Cost. This demo shows an AI worker doing paid tasks with real cost accounting.
        </div>
        
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-value" id="completed-count">0</div>
                <div class="metric-label">Tasks Completed</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" id="total-reward">0&cent;</div>
                <div class="metric-label">Total Reward</div>
            </div>
            <div class="metric-card">
                <div class="metric-value profit" id="total-profit">0&cent;</div>
                <div class="metric-label">Total Profit</div>
            </div>
        </div>
        
        <div class="controls">
            <button id="run-btn" class="btn-primary" onclick="runAgent()">
                Run Agent on Next Task
            </button>
            <button class="btn-secondary" onclick="refreshTasks()">
                Refresh
            </button>
        </div>
        
        <table class="tasks-table">
            <thead>
                <tr>
                    <th>Task ID</th>
                    <th>Description</th>
                    <th>Status</th>
                    <th>Reward</th>
                    <th>Cost</th>
                    <th>Profit</th>
                    <th></th>
                </tr>
            </thead>
            <tbody id="tasks-body">
                <tr>
                    <td colspan="7" style="text-align: center; color: #8b949e;">Loading tasks...</td>
                </tr>
            </tbody>
        </table>
    </div>
    
    <div id="modal-overlay" class="modal-overlay" onclick="closeModal(event)">
        <div class="modal" onclick="event.stopPropagation()">
            <div class="modal-header">
                <h3 id="modal-title">Task Result</h3>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div class="modal-body">
                <div id="modal-result" class="result-text"></div>
            </div>
        </div>
    </div>
    
    <script>
        let isRunning = false;
        let tasksData = [];
        
        function setStatus(running) {
            isRunning = running;
            const pill = document.getElementById('status-pill');
            const text = document.getElementById('status-text');
            const btn = document.getElementById('run-btn');
            
            if (running) {
                pill.className = 'status-pill running';
                text.textContent = 'Running...';
                btn.disabled = true;
                btn.textContent = 'Running...';
            } else {
                pill.className = 'status-pill idle';
                text.textContent = 'Idle';
                btn.disabled = false;
                btn.textContent = 'Run Agent on Next Task';
            }
        }
        
        function updateMetrics(metrics) {
            document.getElementById('completed-count').textContent = metrics.completed_tasks;
            document.getElementById('total-reward').innerHTML = metrics.total_reward + '&cent;';
            document.getElementById('total-profit').innerHTML = metrics.total_profit.toFixed(2) + '&cent;';
        }
        
        function renderTasks(tasks) {
            tasksData = tasks;
            const tbody = document.getElementById('tasks-body');
            
            if (tasks.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: #8b949e;">No tasks available</td></tr>';
                return;
            }
            
            tbody.innerHTML = tasks.map((task, index) => `
                <tr>
                    <td class="task-id">${task.id}</td>
                    <td class="task-desc" title="${task.description}">${task.description}</td>
                    <td><span class="status-badge ${task.status}">${task.status}</span></td>
                    <td class="money">${task.reward}&cent;</td>
                    <td class="money">${task.cost.toFixed(2)}&cent;</td>
                    <td class="money ${task.profit > 0 ? 'positive' : ''}">${task.profit.toFixed(2)}&cent;</td>
                    <td>
                        ${task.status === 'completed' && task.result ? 
                            `<button class="view-btn" onclick="viewResult(${index})">View</button>` : 
                            ''}
                    </td>
                </tr>
            `).join('');
        }
        
        async function refreshTasks() {
            try {
                const response = await fetch('/api/tasks');
                const data = await response.json();
                renderTasks(data.tasks);
                updateMetrics(data.metrics);
            } catch (error) {
                console.error('Error fetching tasks:', error);
            }
        }
        
        async function runAgent() {
            if (isRunning) return;
            
            setStatus(true);
            
            try {
                const response = await fetch('/api/run-once', { method: 'POST' });
                const data = await response.json();
                console.log('Run result:', data);
            } catch (error) {
                console.error('Error running agent:', error);
            } finally {
                setStatus(false);
                await refreshTasks();
            }
        }
        
        function viewResult(index) {
            const task = tasksData[index];
            document.getElementById('modal-title').textContent = `Result: ${task.id}`;
            document.getElementById('modal-result').textContent = task.result || 'No result available';
            document.getElementById('modal-overlay').classList.add('active');
        }
        
        function closeModal(event) {
            if (event && event.target !== event.currentTarget) return;
            document.getElementById('modal-overlay').classList.remove('active');
        }
        
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closeModal();
        });
        
        // Initial load
        refreshTasks();
    </script>
</body>
</html>'''
    return html_content


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
