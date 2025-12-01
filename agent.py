"""
ReAct-style agent that uses OpenAI ChatCompletion API with tool commands.
"""

import os
import re
from openai import OpenAI
from accounting import CostTracker
from tools import web_search, web_fetch

SYSTEM_PROMPT = """You are a research agent. Your job is to answer questions by researching information.

PROTOCOL:
1. Think step by step about what information you need.
2. If you need to search the web, respond with: USE_SEARCH: <your search query>
3. If you need to fetch a specific webpage, respond with: USE_FETCH: <url>
4. After receiving OBSERVATION: messages with tool results, continue reasoning.
5. When you have enough information to answer, respond with: FINAL_ANSWER: <your summary>

RULES:
- You can use USE_SEARCH and USE_FETCH multiple times as needed.
- Always think before using tools.
- Keep your final answer concise but informative.
- Do not make up information - use the tools to find real data.
"""

MAX_STEPS = 6


def run_agent(task_description: str) -> tuple[str, float]:
    """
    Run the agent on a task and return (result_text, cost_cents).
    """
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    cost_tracker = CostTracker()
    
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"TASK: {task_description}"},
    ]
    
    for msg in messages:
        cost_tracker.add_tokens(msg["content"])
    
    for step in range(MAX_STEPS):
        print(f"  [Step {step + 1}/{MAX_STEPS}]")
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,  # type: ignore
            temperature=0.7,
            max_tokens=1000,
        )
        
        reply = response.choices[0].message.content or ""
        cost_tracker.add_tokens(reply)
        
        print(f"  Agent: {reply[:100]}...")
        
        messages.append({"role": "assistant", "content": reply})
        
        if "FINAL_ANSWER:" in reply:
            match = re.search(r'FINAL_ANSWER:\s*(.+)', reply, re.DOTALL)
            if match:
                final_answer = match.group(1).strip()
                return final_answer, cost_tracker.get_cost_cents()
        
        if "USE_SEARCH:" in reply:
            match = re.search(r'USE_SEARCH:\s*(.+?)(?:\n|$)', reply)
            if match:
                query = match.group(1).strip()
                print(f"  [Tool] Searching: {query}")
                results = web_search(query)
                observation = f"OBSERVATION: Search results for '{query}':\n"
                for r in results:
                    observation += f"- {r['title']}: {r['url']}\n"
                messages.append({"role": "user", "content": observation})
                cost_tracker.add_tokens(observation)
                continue
        
        if "USE_FETCH:" in reply:
            match = re.search(r'USE_FETCH:\s*(.+?)(?:\n|$)', reply)
            if match:
                url = match.group(1).strip()
                print(f"  [Tool] Fetching: {url}")
                content = web_fetch(url)
                observation = f"OBSERVATION: Content from {url}:\n{content[:2000]}"
                messages.append({"role": "user", "content": observation})
                cost_tracker.add_tokens(observation)
                continue
        
        if "FINAL_ANSWER:" not in reply and "USE_SEARCH:" not in reply and "USE_FETCH:" not in reply:
            messages.append({
                "role": "user",
                "content": "Please continue. Use USE_SEARCH: or USE_FETCH: if you need more information, or provide FINAL_ANSWER: if you're ready."
            })
    
    return "Agent reached maximum steps without final answer.", cost_tracker.get_cost_cents()
