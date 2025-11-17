# modules/heuristics/base.py

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from enum import Enum


class HeuristicStatus(str, Enum):
    """Status of heuristic execution"""
    PASSED = "passed"
    FAILED = "failed"
    MODIFIED = "modified"
    WARNING = "warning"


class HeuristicResult(BaseModel):
    """Result from a heuristic check"""
    status: HeuristicStatus
    modified_input: Optional[Any] = None
    metadata: Dict[str, Any] = {}
    message: Optional[str] = None

    class Config:
        use_enum_values = True


class BaseHeuristic(ABC):
    """
    Base class for all heuristics.

    Heuristics can:
    - Validate input/output
    - Modify input/output
    - Provide metadata for decision making
    """

    def __init__(self, enabled: bool = True, config: Optional[Dict[str, Any]] = None):
        self.enabled = enabled
        self.config = config or {}
        self.stats = {
            "total_runs": 0,
            "passed": 0,
            "failed": 0,
            "modified": 0,
            "warnings": 0
        }

    @abstractmethod
    async def check(self, input_data: Any, context: Optional[Dict[str, Any]] = None) -> HeuristicResult:
        """
        Main heuristic logic.

        Args:
            input_data: The data to check (query, result, tool list, etc.)
            context: Optional context information

        Returns:
            HeuristicResult with status and any modifications
        """
        pass

    async def execute(self, input_data: Any, context: Optional[Dict[str, Any]] = None) -> HeuristicResult:
        """
        Execute the heuristic with stats tracking.
        """
        if not self.enabled:
            return HeuristicResult(
                status=HeuristicStatus.PASSED,
                modified_input=input_data,
                message="Heuristic disabled"
            )

        self.stats["total_runs"] += 1
        result = await self.check(input_data, context)

        # Update stats
        if result.status == HeuristicStatus.PASSED:
            self.stats["passed"] += 1
        elif result.status == HeuristicStatus.FAILED:
            self.stats["failed"] += 1
        elif result.status == HeuristicStatus.MODIFIED:
            self.stats["modified"] += 1
        elif result.status == HeuristicStatus.WARNING:
            self.stats["warnings"] += 1

        return result

    def get_stats(self) -> Dict[str, int]:
        """Get statistics for this heuristic"""
        return self.stats.copy()

    def reset_stats(self):
        """Reset statistics"""
        self.stats = {
            "total_runs": 0,
            "passed": 0,
            "failed": 0,
            "modified": 0,
            "warnings": 0
        }


class HeuristicPipeline:
    """
    Pipeline to run multiple heuristics in sequence.
    Supports both query-level and result-level heuristics.
    """

    def __init__(self, heuristics: Optional[List[BaseHeuristic]] = None):
        self.heuristics = heuristics or []

    def add_heuristic(self, heuristic: BaseHeuristic):
        """Add a heuristic to the pipeline"""
        self.heuristics.append(heuristic)

    async def run(self, input_data: Any, context: Optional[Dict[str, Any]] = None,
                  stop_on_failure: bool = False) -> tuple[Any, List[HeuristicResult]]:
        """
        Run all heuristics in sequence.

        Args:
            input_data: Initial input
            context: Optional context
            stop_on_failure: If True, stop pipeline on first failure

        Returns:
            (final_data, list_of_results)
        """
        current_data = input_data
        results = []

        for heuristic in self.heuristics:
            result = await heuristic.execute(current_data, context)
            results.append(result)

            # If heuristic modified the data, use the modified version
            if result.modified_input is not None:
                current_data = result.modified_input

            # Stop on failure if requested
            if stop_on_failure and result.status == HeuristicStatus.FAILED:
                break

        return current_data, results

    def get_all_stats(self) -> Dict[str, Dict[str, int]]:
        """Get stats for all heuristics"""
        return {
            heuristic.__class__.__name__: heuristic.get_stats()
            for heuristic in self.heuristics
        }
