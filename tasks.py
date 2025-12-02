"""
In-memory task queue with simple management functions.
"""

TASKS = [
    {
        "id": "task_1",
        "description": "Research the top 3 benefits of using Python for web development and summarize them.",
        "reward_cents": 50,
        "status": "pending",
        "result": None,
        "cost_cents": 0,
        "profit_cents": 0,
    },
    {
        "id": "task_2",
        "description": "Find out what FastAPI is and list its main features in a brief summary.",
        "reward_cents": 40,
        "status": "pending",
        "result": None,
        "cost_cents": 0,
        "profit_cents": 0,
    },
    {
        "id": "task_3",
        "description": "Research the differences between REST and GraphQL APIs and provide a short comparison.",
        "reward_cents": 60,
        "status": "pending",
        "result": None,
        "cost_cents": 0,
        "profit_cents": 0,
    },
]


def get_next_pending_task() -> dict | None:
    """Get the next pending task from the queue."""
    for task in TASKS:
        if task["status"] == "pending":
            return task
    return None


def get_next_open_task() -> dict | None:
    """Alias for backward compatibility."""
    return get_next_pending_task()


def set_task_running(task_id: str) -> dict | None:
    """Mark a task as running."""
    for task in TASKS:
        if task["id"] == task_id:
            task["status"] = "running"
            return task
    return None


def complete_task(task_id: str, result_text: str, cost_cents: float) -> dict | None:
    """Mark a task as completed with results."""
    for task in TASKS:
        if task["id"] == task_id:
            task["status"] = "completed"
            task["result"] = result_text
            task["cost_cents"] = round(cost_cents, 2)
            task["profit_cents"] = round(task["reward_cents"] - cost_cents, 2)
            return task
    return None


def all_tasks() -> list[dict]:
    """Return all tasks."""
    return TASKS


def get_metrics() -> dict:
    """Get summary metrics for all tasks."""
    completed = [t for t in TASKS if t["status"] == "completed"]
    return {
        "total_tasks": len(TASKS),
        "completed_tasks": len(completed),
        "total_reward": sum(t["reward_cents"] for t in completed),
        "total_cost": round(sum(t["cost_cents"] for t in completed), 2),
        "total_profit": round(sum(t["profit_cents"] for t in completed), 2),
    }


def reset_tasks() -> None:
    """Reset all tasks to pending state."""
    for task in TASKS:
        task["status"] = "pending"
        task["result"] = None
        task["cost_cents"] = 0
        task["profit_cents"] = 0
