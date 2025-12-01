"""
In-memory task queue with simple management functions.
"""

TASKS = [
    {
        "id": "task_1",
        "description": "Research the top 3 benefits of using Python for web development and summarize them.",
        "reward_cents": 50,
        "status": "open",
        "result": None,
        "cost_cents": 0,
        "profit_cents": 0,
    },
    {
        "id": "task_2",
        "description": "Find out what FastAPI is and list its main features in a brief summary.",
        "reward_cents": 40,
        "status": "open",
        "result": None,
        "cost_cents": 0,
        "profit_cents": 0,
    },
    {
        "id": "task_3",
        "description": "Research the differences between REST and GraphQL APIs and provide a short comparison.",
        "reward_cents": 60,
        "status": "open",
        "result": None,
        "cost_cents": 0,
        "profit_cents": 0,
    },
]


def get_next_open_task() -> dict | None:
    for task in TASKS:
        if task["status"] == "open":
            return task
    return None


def complete_task(task_id: str, result_text: str, cost_cents: float) -> dict | None:
    for task in TASKS:
        if task["id"] == task_id:
            task["status"] = "completed"
            task["result"] = result_text
            task["cost_cents"] = cost_cents
            task["profit_cents"] = task["reward_cents"] - cost_cents
            return task
    return None


def all_tasks() -> list[dict]:
    return TASKS
