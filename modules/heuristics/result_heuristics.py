# modules/heuristics/result_heuristics.py

import re
from typing import Any, Dict, Optional, List
from .base import BaseHeuristic, HeuristicResult, HeuristicStatus


class ConfidenceThreshold(BaseHeuristic):
    """
    Heuristic #5: Result Confidence Thresholding
    Only return results that meet minimum confidence/quality criteria.
    """

    def __init__(self, enabled: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "min_confidence": 0.7,
            "check_hedging_words": True,
            "hedging_words": [
                "maybe", "perhaps", "possibly", "might", "could", "probably",
                "uncertain", "unsure", "unclear", "not sure", "don't know"
            ],
            "hedging_threshold": 2
        }
        super().__init__(enabled, {**default_config, **(config or {})})

    def _calculate_confidence(self, result: str) -> float:
        """
        Calculate confidence score based on hedging language.
        Returns score between 0.0 and 1.0.
        """
        if not self.config["check_hedging_words"]:
            return 1.0

        result_lower = result.lower()
        hedging_count = 0

        for word in self.config["hedging_words"]:
            hedging_count += result_lower.count(word)

        # More hedging = lower confidence
        # Simple heuristic: each hedging word reduces confidence by 0.15
        confidence = max(0.0, 1.0 - (hedging_count * 0.15))
        return confidence

    async def check(self, input_data: Any, context: Optional[Dict[str, Any]] = None) -> HeuristicResult:
        """
        Check if result meets confidence threshold.

        Expected input_data: result string or dict with "result" key
        Expected context: {"confidence": float} (optional)
        """
        # Extract result text
        if isinstance(input_data, dict):
            result_text = input_data.get("result", str(input_data))
        else:
            result_text = str(input_data)

        # Get confidence from context or calculate it
        if context and "confidence" in context:
            confidence = context["confidence"]
        else:
            confidence = self._calculate_confidence(result_text)

        min_confidence = self.config["min_confidence"]

        if confidence < min_confidence:
            return HeuristicResult(
                status=HeuristicStatus.FAILED,
                metadata={
                    "confidence": confidence,
                    "min_confidence": min_confidence,
                    "requires_refinement": True
                },
                message=f"Low confidence result: {confidence:.2f} < {min_confidence}"
            )

        if confidence < 0.85:
            return HeuristicResult(
                status=HeuristicStatus.WARNING,
                modified_input=input_data,
                metadata={
                    "confidence": confidence,
                    "warning": "moderate_confidence"
                },
                message=f"Moderate confidence: {confidence:.2f}"
            )

        return HeuristicResult(
            status=HeuristicStatus.PASSED,
            modified_input=input_data,
            metadata={
                "confidence": confidence,
                "high_confidence": True
            }
        )


class HallucinationDetection(BaseHeuristic):
    """
    Heuristic #7: Hallucination Detection
    Catch when LLM generates ungrounded or fabricated information.
    """

    def __init__(self, enabled: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "require_grounding": True,
            "check_specific_claims": True,
            "suspicious_patterns": [
                r"the document (says|states|mentions)",
                r"according to (the|this)",
                r"as stated in",
                r"the data shows",
                r"research indicates"
            ]
        }
        super().__init__(enabled, {**default_config, **(config or {})})

    def _extract_claims(self, result: str) -> List[str]:
        """Extract specific factual claims from result"""
        claims = []

        # Look for sentences with suspicious patterns
        for pattern in self.config["suspicious_patterns"]:
            matches = re.finditer(pattern, result, re.IGNORECASE)
            for match in matches:
                # Extract the sentence containing the match
                start = max(0, result.rfind('.', 0, match.start()) + 1)
                end = result.find('.', match.end())
                if end == -1:
                    end = len(result)
                sentence = result[start:end].strip()
                if sentence:
                    claims.append(sentence)

        return claims

    def _check_grounding(self, result: str, tool_outputs: List[str]) -> Dict[str, Any]:
        """
        Check if claims in result are grounded in tool outputs.

        Returns:
            {
                "grounded": bool,
                "ungrounded_claims": List[str],
                "grounding_score": float
            }
        """
        if not tool_outputs:
            return {
                "grounded": False,
                "ungrounded_claims": [],
                "grounding_score": 0.0,
                "reason": "no_tool_outputs"
            }

        claims = self._extract_claims(result)

        if not claims:
            # No specific claims detected, assume safe
            return {
                "grounded": True,
                "ungrounded_claims": [],
                "grounding_score": 1.0,
                "reason": "no_claims"
            }

        # Check each claim against tool outputs
        ungrounded_claims = []
        combined_outputs = " ".join(tool_outputs).lower()

        for claim in claims:
            # Simple check: see if key phrases from claim appear in outputs
            claim_words = set(claim.lower().split())
            # Remove common words
            claim_words = claim_words - {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at"}

            # Check if at least 50% of claim words appear in outputs
            grounded_words = sum(1 for word in claim_words if word in combined_outputs)
            grounding_ratio = grounded_words / len(claim_words) if claim_words else 0

            if grounding_ratio < 0.5:
                ungrounded_claims.append(claim)

        grounding_score = 1.0 - (len(ungrounded_claims) / len(claims)) if claims else 1.0

        return {
            "grounded": len(ungrounded_claims) == 0,
            "ungrounded_claims": ungrounded_claims,
            "grounding_score": grounding_score,
            "total_claims": len(claims)
        }

    async def check(self, input_data: Any, context: Optional[Dict[str, Any]] = None) -> HeuristicResult:
        """
        Check for hallucinations in result.

        Expected input_data: result string
        Expected context: {"tool_outputs": List[str]}
        """
        result = str(input_data)

        if not self.config["require_grounding"]:
            return HeuristicResult(
                status=HeuristicStatus.PASSED,
                modified_input=input_data,
                message="Grounding check disabled"
            )

        # Get tool outputs from context
        tool_outputs = []
        if context and "tool_outputs" in context:
            tool_outputs = context["tool_outputs"]

        grounding_check = self._check_grounding(result, tool_outputs)

        if not grounding_check["grounded"]:
            return HeuristicResult(
                status=HeuristicStatus.FAILED,
                metadata={
                    "hallucination_detected": True,
                    **grounding_check
                },
                message=f"Potential hallucination: {len(grounding_check['ungrounded_claims'])} ungrounded claims"
            )

        if grounding_check["grounding_score"] < 1.0:
            return HeuristicResult(
                status=HeuristicStatus.WARNING,
                modified_input=input_data,
                metadata={
                    "hallucination_detected": False,
                    **grounding_check
                },
                message=f"Grounding score: {grounding_check['grounding_score']:.2f}"
            )

        return HeuristicResult(
            status=HeuristicStatus.PASSED,
            modified_input=input_data,
            metadata={
                "hallucination_detected": False,
                **grounding_check
            }
        )