# modules/heuristics/manager.py

import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional
from .base import HeuristicPipeline, HeuristicResult
from .query_heuristics import (
    QueryLengthFilter,
    PIIRedaction,
    QueryDeduplication,
    AmbiguityDetection,
    QueryIntentClassification
)
from .result_heuristics import (
    ConfidenceThreshold,
    HallucinationDetection
)
from .tool_heuristics import (
    ToolAffinityScoring,
    TimeBasedPrioritization,
    RateLimitOptimization
)

try:
    from agent import log
except ImportError:
    import datetime
    def log(stage: str, msg: str):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] [{stage}] {msg}")


class HeuristicManager:
    """
    Central manager for all heuristics.
    Provides easy integration with existing agent system.
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize heuristic manager.

        Args:
            config_path: Path to heuristics config YAML file
        """
        self.config_path = config_path or "config/heuristics_config.yaml"
        self.config = self._load_config()
        self.enabled = self.config.get("enabled", True)
        self.log_results = self.config.get("log_heuristic_results", True)

        # Initialize pipelines
        self.query_pipeline = HeuristicPipeline()
        self.tool_pipeline = HeuristicPipeline()
        self.result_pipeline = HeuristicPipeline()

        # Initialize individual heuristics
        self._init_heuristics()

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        try:
            with open(self.config_path, "r") as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            log("heuristics", f"⚠️ Config file not found: {self.config_path}, using defaults")
            return {"enabled": True, "log_heuristic_results": True}

    def _init_heuristics(self):
        """Initialize all heuristics based on config"""
        if not self.enabled:
            log("heuristics", "Heuristics system disabled")
            return

        # Query heuristics
        query_config = self.config.get("query_heuristics", {})

        if query_config.get("query_length_filter", {}).get("enabled", True):
            self.query_pipeline.add_heuristic(
                QueryLengthFilter(config=query_config.get("query_length_filter", {}))
            )

        if query_config.get("pii_redaction", {}).get("enabled", True):
            self.query_pipeline.add_heuristic(
                PIIRedaction(config=query_config.get("pii_redaction", {}))
            )

        if query_config.get("query_deduplication", {}).get("enabled", True):
            self.query_deduplication = QueryDeduplication(
                config=query_config.get("query_deduplication", {})
            )
            self.query_pipeline.add_heuristic(self.query_deduplication)

        if query_config.get("ambiguity_detection", {}).get("enabled", True):
            self.query_pipeline.add_heuristic(
                AmbiguityDetection(config=query_config.get("ambiguity_detection", {}))
            )

        if query_config.get("query_intent_classification", {}).get("enabled", True):
            self.query_intent_classifier = QueryIntentClassification(
                config=query_config.get("query_intent_classification", {})
            )
            self.query_pipeline.add_heuristic(self.query_intent_classifier)

        # Tool heuristics
        tool_config = self.config.get("tool_heuristics", {})

        if tool_config.get("tool_affinity_scoring", {}).get("enabled", True):
            self.tool_affinity = ToolAffinityScoring(
                config=tool_config.get("tool_affinity_scoring", {})
            )
            self.tool_pipeline.add_heuristic(self.tool_affinity)

        if tool_config.get("time_based_prioritization", {}).get("enabled", True):
            self.time_prioritization = TimeBasedPrioritization(
                config=tool_config.get("time_based_prioritization", {})
            )
            self.tool_pipeline.add_heuristic(self.time_prioritization)

        if tool_config.get("rate_limit_optimization", {}).get("enabled", True):
            self.rate_limiter = RateLimitOptimization(
                config=tool_config.get("rate_limit_optimization", {})
            )
            self.tool_pipeline.add_heuristic(self.rate_limiter)

        # Result heuristics
        result_config = self.config.get("result_heuristics", {})

        if result_config.get("confidence_threshold", {}).get("enabled", True):
            self.result_pipeline.add_heuristic(
                ConfidenceThreshold(config=result_config.get("confidence_threshold", {}))
            )

        if result_config.get("hallucination_detection", {}).get("enabled", True):
            self.result_pipeline.add_heuristic(
                HallucinationDetection(config=result_config.get("hallucination_detection", {}))
            )

        log("heuristics", f"✅ Initialized {len(self.query_pipeline.heuristics)} query, "
                          f"{len(self.tool_pipeline.heuristics)} tool, "
                          f"{len(self.result_pipeline.heuristics)} result heuristics")

    async def process_query(self, query: str, context: Optional[Dict[str, Any]] = None) -> tuple[str, Dict[str, Any]]:
        """
        Process query through query heuristics.

        Returns:
            (processed_query, metadata)
        """
        if not self.enabled:
            return query, {}

        processed_query, results = await self.query_pipeline.run(query, context)

        if self.log_results:
            self._log_results("query", results)

        # Extract metadata
        metadata = self._extract_metadata(results)

        return processed_query, metadata

    async def process_tools(self, tools: List[Any], context: Optional[Dict[str, Any]] = None) -> tuple[List[Any], Dict[str, Any]]:
        """
        Process tools through tool heuristics.

        Returns:
            (processed_tools, metadata)
        """
        if not self.enabled:
            return tools, {}

        processed_tools, results = await self.tool_pipeline.run(tools, context)

        if self.log_results:
            self._log_results("tool", results)

        metadata = self._extract_metadata(results)

        return processed_tools, metadata

    async def process_result(self, result: Any, context: Optional[Dict[str, Any]] = None) -> tuple[Any, Dict[str, Any]]:
        """
        Process result through result heuristics.

        Returns:
            (processed_result, metadata)
        """
        if not self.enabled:
            return result, {}

        processed_result, results = await self.result_pipeline.run(result, context)

        if self.log_results:
            self._log_results("result", results)

        metadata = self._extract_metadata(results)

        return processed_result, metadata

    def _log_results(self, stage: str, results: List[HeuristicResult]):
        """Log heuristic results"""
        for i, result in enumerate(results):
            if result.status != "passed":
                heuristic_name = self._get_heuristic_name(stage, i)
                log("heuristics", f"[{stage}] {heuristic_name}: {result.status} - {result.message}")

    def _get_heuristic_name(self, stage: str, index: int) -> str:
        """Get heuristic name by stage and index"""
        if stage == "query":
            heuristics = self.query_pipeline.heuristics
        elif stage == "tool":
            heuristics = self.tool_pipeline.heuristics
        elif stage == "result":
            heuristics = self.result_pipeline.heuristics
        else:
            return "unknown"

        if index < len(heuristics):
            return heuristics[index].__class__.__name__
        return "unknown"

    def _extract_metadata(self, results: List[HeuristicResult]) -> Dict[str, Any]:
        """Extract combined metadata from all results"""
        metadata = {
            "heuristics_run": len(results),
            "passed": sum(1 for r in results if r.status == "passed"),
            "failed": sum(1 for r in results if r.status == "failed"),
            "modified": sum(1 for r in results if r.status == "modified"),
            "warnings": sum(1 for r in results if r.status == "warning"),
        }

        # Collect all individual metadata
        all_metadata = {}
        for i, result in enumerate(results):
            heuristic_name = f"heuristic_{i}"
            all_metadata[heuristic_name] = result.metadata

        metadata["details"] = all_metadata

        return metadata

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics for all heuristics"""
        return {
            "query_heuristics": self.query_pipeline.get_all_stats(),
            "tool_heuristics": self.tool_pipeline.get_all_stats(),
            "result_heuristics": self.result_pipeline.get_all_stats()
        }

    # Convenience methods for tracking (used by integration layer)
    def record_tool_usage(self, query_intent: str, tool_name: str, success: bool):
        """Record tool usage for affinity scoring"""
        if hasattr(self, 'tool_affinity'):
            self.tool_affinity.record_tool_usage(query_intent, tool_name, success)

    def record_tool_latency(self, tool_name: str, latency_ms: float):
        """Record tool latency for time-based prioritization"""
        if hasattr(self, 'time_prioritization'):
            self.time_prioritization.record_tool_latency(tool_name, latency_ms)

    def record_tool_call(self, session_id: str, tool_name: str, step: int):
        """Record tool call for rate limiting"""
        if hasattr(self, 'rate_limiter'):
            self.rate_limiter.record_tool_call(session_id, tool_name, step)