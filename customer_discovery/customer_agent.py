"""Customer discovery node compatible with the main team's State v0.4."""
from .integration import run_customer_node


def run(state: dict) -> dict:
    return run_customer_node(state)
