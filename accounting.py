"""
CostTracker: Tracks approximate token usage and converts to cost in cents.
"""


class CostTracker:
    def __init__(self, cost_per_1k_tokens_cents: float = 0.15):
        self.total_tokens = 0
        self.cost_per_1k_tokens_cents = cost_per_1k_tokens_cents

    def add_tokens(self, text: str) -> int:
        tokens = len(text) // 4
        self.total_tokens += tokens
        return tokens

    def get_total_tokens(self) -> int:
        return self.total_tokens

    def get_cost_cents(self) -> float:
        return (self.total_tokens / 1000) * self.cost_per_1k_tokens_cents

    def reset(self):
        self.total_tokens = 0
