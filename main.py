"""
Main entrypoint for hoss-agent-mvp.
Loops over tasks, runs the agent, and prints reward vs cost vs profit.
"""

from tasks import get_next_open_task, complete_task, all_tasks
from agent import run_agent


def main():
    print("=" * 60)
    print("HOSS AGENT MVP - Research Agent")
    print("=" * 60)
    print()
    
    task_count = 0
    total_profit = 0
    
    while True:
        task = get_next_open_task()
        if task is None:
            break
        
        task_count += 1
        print(f"[Task {task_count}] ID: {task['id']}")
        print(f"  Description: {task['description']}")
        print(f"  Reward: {task['reward_cents']} cents")
        print()
        
        result, cost_cents = run_agent(task["description"])
        
        completed = complete_task(task["id"], result, cost_cents)
        
        print()
        print(f"  RESULT: {result[:200]}..." if len(result) > 200 else f"  RESULT: {result}")
        print(f"  Cost: {cost_cents:.2f} cents")
        print(f"  Reward: {completed['reward_cents']} cents")
        print(f"  Profit: {completed['profit_cents']:.2f} cents")
        print("-" * 60)
        print()
        
        total_profit += completed["profit_cents"]
    
    print("=" * 60)
    print("ALL TASKS COMPLETED")
    print("=" * 60)
    print()
    print("Summary:")
    for task in all_tasks():
        status_icon = "[OK]" if task["status"] == "completed" else "[  ]"
        print(f"  {status_icon} {task['id']}: profit={task['profit_cents']:.2f}c")
    print()
    print(f"Total Profit: {total_profit:.2f} cents")
    print()


if __name__ == "__main__":
    main()
