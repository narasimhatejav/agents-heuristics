# modules/heuristics/tool_heuristics.py

import time
from typing import Any, Dict, Optional, List
from .base import BaseHeuristic, HeuristicResult, HeuristicStatus


class ToolAffinityScoring(BaseHeuristic):
    """
    Heuristic #4: Tool Affinity Scoring
    Prefer tools with high historical success rates for specific query types.
    """

    def __init__(self, enabled: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "min_samples": 3,
            "success_threshold": 0.7,
            "boost_factor": 1.5
        }
        super().__init__(enabled, {**default_config, **(config or {})})
        # Track: {(query_intent, tool_name): {"success": int, "total": int}}
        self.affinity_scores: Dict[str, Dict[str, Any]] = {}

    def record_tool_usage(self, query_intent: str, tool_name: str, success: bool):
        """Record tool usage for affinity learning"""
        key = f"{query_intent}:{tool_name}"

        if key not in self.affinity_scores:
            self.affinity_scores[key] = {"success": 0, "total": 0}

        self.affinity_scores[key]["total"] += 1
        if success:
            self.affinity_scores[key]["success"] += 1

    def get_affinity_score(self, query_intent: str, tool_name: str) -> float:
        """Get affinity score for a tool given query intent"""
        key = f"{query_intent}:{tool_name}"

        if key not in self.affinity_scores:
            return 1.0  # Neutral score for new combinations

        data = self.affinity_scores[key]
        if data["total"] < self.config["min_samples"]:
            return 1.0  # Not enough data

        success_rate = data["success"] / data["total"]
        return success_rate

    async def check(self, input_data: Any, context: Optional[Dict[str, Any]] = None) -> HeuristicResult:
        """
        Score and re-rank tools based on affinity.

        Expected input_data: List of tool objects
        Expected context: {"query_intent": str}
        """
        if not isinstance(input_data, list):
            return HeuristicResult(
                status=HeuristicStatus.PASSED,
                modified_input=input_data,
                message="Input is not a tool list"
            )

        tools = input_data
        query_intent = context.get("query_intent", "general") if context else "general"

        # Score each tool
        scored_tools = []
        for tool in tools:
            tool_name = getattr(tool, "name", str(tool))
            score = self.get_affinity_score(query_intent, tool_name)
            scored_tools.append((tool, score))

        # Re-rank if we have meaningful scores
        if any(score != 1.0 for _, score in scored_tools):
            # Sort by score descending
            scored_tools.sort(key=lambda x: x[1], reverse=True)
            reranked_tools = [tool for tool, _ in scored_tools]

            return HeuristicResult(
                status=HeuristicStatus.MODIFIED,
                modified_input=reranked_tools,
                metadata={
                    "reranked": True,
                    "query_intent": query_intent,
                    "scores": {getattr(t, "name", str(t)): s for t, s in scored_tools}
                },
                message=f"Re-ranked {len(tools)} tools by affinity"
            )

        return HeuristicResult(
            status=HeuristicStatus.PASSED,
            modified_input=tools,
            metadata={
                "reranked": False,
                "reason": "no_affinity_data"
            }
        )


class TimeBasedPrioritization(BaseHeuristic):
    """
    Heuristic #6: Time-Based Tool Prioritization
    Deprioritize slow tools unless necessary.
    """

    def __init__(self, enabled: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "slow_threshold_ms": 2000,
            "fast_threshold_ms": 500,
            "prefer_fast_for_simple": True
        }
        super().__init__(enabled, {**default_config, **(config or {})})
        # Track tool latencies: {tool_name: [latency1, latency2, ...]}
        self.latencies: Dict[str, List[float]] = {}

    def record_tool_latency(self, tool_name: str, latency_ms: float):
        """Record tool execution time"""
        if tool_name not in self.latencies:
            self.latencies[tool_name] = []

        self.latencies[tool_name].append(latency_ms)

        # Keep only last 20 measurements
        if len(self.latencies[tool_name]) > 20:
            self.latencies[tool_name] = self.latencies[tool_name][-20:]

    def get_avg_latency(self, tool_name: str) -> Optional[float]:
        """Get average latency for a tool"""
        if tool_name not in self.latencies or not self.latencies[tool_name]:
            return None

        return sum(self.latencies[tool_name]) / len(self.latencies[tool_name])

    async def check(self, input_data: Any, context: Optional[Dict[str, Any]] = None) -> HeuristicResult:
        """
        Prioritize tools by speed.

        Expected input_data: List of tool objects
        Expected context: {"query_complexity": "simple" | "complex"}
        """
        if not isinstance(input_data, list):
            return HeuristicResult(
                status=HeuristicStatus.PASSED,
                modified_input=input_data
            )

        tools = input_data
        query_complexity = context.get("query_complexity", "complex") if context else "complex"

        # Only prioritize for simple queries
        if not self.config["prefer_fast_for_simple"] or query_complexity != "simple":
            return HeuristicResult(
                status=HeuristicStatus.PASSED,
                modified_input=tools,
                message="Skipping time prioritization (complex query)"
            )

        # Categorize tools by speed
        fast_tools = []
        normal_tools = []
        slow_tools = []

        for tool in tools:
            tool_name = getattr(tool, "name", str(tool))
            avg_latency = self.get_avg_latency(tool_name)

            if avg_latency is None:
                normal_tools.append(tool)
            elif avg_latency < self.config["fast_threshold_ms"]:
                fast_tools.append(tool)
            elif avg_latency > self.config["slow_threshold_ms"]:
                slow_tools.append(tool)
            else:
                normal_tools.append(tool)

        # Reorder: fast -> normal -> slow
        if fast_tools or slow_tools:
            reordered = fast_tools + normal_tools + slow_tools
            return HeuristicResult(
                status=HeuristicStatus.MODIFIED,
                modified_input=reordered,
                metadata={
                    "reordered": True,
                    "fast_count": len(fast_tools),
                    "normal_count": len(normal_tools),
                    "slow_count": len(slow_tools)
                },
                message=f"Prioritized {len(fast_tools)} fast tools for simple query"
            )

        return HeuristicResult(
            status=HeuristicStatus.PASSED,
            modified_input=tools
        )


class RateLimitOptimization(BaseHeuristic):
    """
    Heuristic #8: Rate Limit & Cost Optimization
    Prevent excessive API calls and manage costs.
    """

    def __init__(self, enabled: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "max_calls_per_session": 10,
            "max_calls_per_step": 5,
            "expensive_tool_limit": 3,
            "expensive_tools": []  # List of tool names considered expensive
        }
        super().__init__(enabled, {**default_config, **(config or {})})
        # Track usage: {session_id: {"total": int, "expensive": int, "by_step": {step: int}}}
        self.usage_tracking: Dict[str, Dict[str, Any]] = {}

    def record_tool_call(self, session_id: str, tool_name: str, step: int):
        """Record a tool call"""
        if session_id not in self.usage_tracking:
            self.usage_tracking[session_id] = {
                "total": 0,
                "expensive": 0,
                "by_step": {}
            }

        data = self.usage_tracking[session_id]
        data["total"] += 1

        if tool_name in self.config["expensive_tools"]:
            data["expensive"] += 1

        if step not in data["by_step"]:
            data["by_step"][step] = 0
        data["by_step"][step] += 1

    def get_usage(self, session_id: str) -> Dict[str, Any]:
        """Get usage stats for a session"""
        return self.usage_tracking.get(session_id, {
            "total": 0,
            "expensive": 0,
            "by_step": {}
        })

    async def check(self, input_data: Any, context: Optional[Dict[str, Any]] = None) -> HeuristicResult:
        """
        Check if tool calls are within limits.

        Expected input_data: tool_name (str) or list of tools
        Expected context: {"session_id": str, "step": int}
        """
        if not context:
            return HeuristicResult(
                status=HeuristicStatus.PASSED,
                modified_input=input_data,
                message="No context provided"
            )

        session_id = context.get("session_id", "default")
        step = context.get("step", 0)
        usage = self.get_usage(session_id)

        # Check session limit
        if usage["total"] >= self.config["max_calls_per_session"]:
            return HeuristicResult(
                status=HeuristicStatus.FAILED,
                metadata={
                    "reason": "session_limit_exceeded",
                    "total_calls": usage["total"],
                    "limit": self.config["max_calls_per_session"]
                },
                message=f"Session limit exceeded: {usage['total']}/{self.config['max_calls_per_session']}"
            )

        # Check step limit
        step_calls = usage["by_step"].get(step, 0)
        if step_calls >= self.config["max_calls_per_step"]:
            return HeuristicResult(
                status=HeuristicStatus.FAILED,
                metadata={
                    "reason": "step_limit_exceeded",
                    "step_calls": step_calls,
                    "limit": self.config["max_calls_per_step"]
                },
                message=f"Step limit exceeded: {step_calls}/{self.config['max_calls_per_step']}"
            )

        # Check expensive tool limit
        if isinstance(input_data, str) and input_data in self.config["expensive_tools"]:
            if usage["expensive"] >= self.config["expensive_tool_limit"]:
                return HeuristicResult(
                    status=HeuristicStatus.WARNING,
                    modified_input=input_data,
                    metadata={
                        "reason": "expensive_limit_warning",
                        "expensive_calls": usage["expensive"],
                        "limit": self.config["expensive_tool_limit"]
                    },
                    message=f"Expensive tool limit approaching: {usage['expensive']}/{self.config['expensive_tool_limit']}"
                )

        return HeuristicResult(
            status=HeuristicStatus.PASSED,
            modified_input=input_data,
            metadata={
                "usage": usage,
                "within_limits": True
            }
        )