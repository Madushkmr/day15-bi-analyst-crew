"""
Shared agent interface.

Every agent in the crew is a small class with:
  - `name`            a stable identifier used in config, logs, and traces
  - `run(context)`     does its work by reading prior agents' outputs off
                       the shared `context` blackboard and returning a
                       JSON-serializable dict of its own findings

Agents are intentionally "dumb" and single-purpose (profile, check
quality, detect anomalies, forecast, ...) - the orchestrator is what
gives the crew its adaptive behavior, by deciding which agents to run
and how to handle failures.

An agent should raise a plain Exception (or subclass) on failure; it
should NOT swallow errors itself. Retry/fallback/skip logic lives
entirely in the orchestrator so it applies uniformly to every agent.
"""


class AgentError(Exception):
    """Raised by an agent when it cannot complete its work."""


class Agent:
    name = "base"

    def run(self, context: dict) -> dict:
        raise NotImplementedError
