# modules/heuristics/query_heuristics.py

import re
import hashlib
from typing import Any, Dict, Optional, List
from .base import BaseHeuristic, HeuristicResult, HeuristicStatus


class QueryLengthFilter(BaseHeuristic):
    """
    Heuristic #1: Query Length & Complexity Filter
    Prevents extremely long or overly complex queries.
    """

    def __init__(self, enabled: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "max_words": 500,
            "max_chars": 5000,
            "max_sentence_length": 100,
            "auto_truncate": False
        }
        super().__init__(enabled, {**default_config, **(config or {})})

    async def check(self, input_data: Any, context: Optional[Dict[str, Any]] = None) -> HeuristicResult:
        query = str(input_data)
        words = query.split()
        word_count = len(words)
        char_count = len(query)

        max_words = self.config["max_words"]
        max_chars = self.config["max_chars"]

        # Check length violations
        if word_count > max_words or char_count > max_chars:
            if self.config["auto_truncate"]:
                # Truncate to max
                truncated_words = words[:max_words]
                truncated_query = " ".join(truncated_words)[:max_chars]
                return HeuristicResult(
                    status=HeuristicStatus.MODIFIED,
                    modified_input=truncated_query,
                    metadata={
                        "original_word_count": word_count,
                        "original_char_count": char_count,
                        "truncated": True
                    },
                    message=f"Query truncated from {word_count} words to {max_words}"
                )
            else:
                return HeuristicResult(
                    status=HeuristicStatus.FAILED,
                    metadata={
                        "word_count": word_count,
                        "char_count": char_count,
                        "max_words": max_words,
                        "max_chars": max_chars
                    },
                    message=f"Query too long: {word_count} words (max: {max_words})"
                )

        # Check sentence complexity
        sentences = re.split(r'[.!?]+', query)
        long_sentences = [s for s in sentences if len(s.split()) > self.config["max_sentence_length"]]

        if long_sentences:
            return HeuristicResult(
                status=HeuristicStatus.WARNING,
                modified_input=query,
                metadata={
                    "long_sentence_count": len(long_sentences),
                    "complexity_warning": True
                },
                message=f"Query contains {len(long_sentences)} overly complex sentences"
            )

        return HeuristicResult(
            status=HeuristicStatus.PASSED,
            modified_input=query,
            metadata={
                "word_count": word_count,
                "char_count": char_count
            }
        )


class PIIRedaction(BaseHeuristic):
    """
    Heuristic #2: PII (Personally Identifiable Information) Redaction
    Automatically detects and masks sensitive information.
    """

    def __init__(self, enabled: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "redact_ssn": True,
            "redact_credit_card": True,
            "redact_email": True,
            "redact_phone": True,
            "replacement": "***REDACTED***"
        }
        super().__init__(enabled, {**default_config, **(config or {})})

        # PII patterns
        self.patterns = {
            "ssn": r'\b\d{3}-\d{2}-\d{4}\b',
            "credit_card": r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b',
            "email": r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            "phone": r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'
        }

    async def check(self, input_data: Any, context: Optional[Dict[str, Any]] = None) -> HeuristicResult:
        query = str(input_data)
        original_query = query
        redacted_items = []

        # Apply redactions
        if self.config["redact_ssn"]:
            matches = re.findall(self.patterns["ssn"], query)
            if matches:
                query = re.sub(self.patterns["ssn"], self.config["replacement"], query)
                redacted_items.extend([("ssn", m) for m in matches])

        if self.config["redact_credit_card"]:
            matches = re.findall(self.patterns["credit_card"], query)
            if matches:
                query = re.sub(self.patterns["credit_card"], self.config["replacement"], query)
                redacted_items.extend([("credit_card", m) for m in matches])

        if self.config["redact_email"]:
            matches = re.findall(self.patterns["email"], query)
            if matches:
                query = re.sub(self.patterns["email"], self.config["replacement"], query)
                redacted_items.extend([("email", m) for m in matches])

        if self.config["redact_phone"]:
            matches = re.findall(self.patterns["phone"], query)
            if matches:
                query = re.sub(self.patterns["phone"], self.config["replacement"], query)
                redacted_items.extend([("phone", m) for m in matches])

        if redacted_items:
            return HeuristicResult(
                status=HeuristicStatus.MODIFIED,
                modified_input=query,
                metadata={
                    "redacted_count": len(redacted_items),
                    "redacted_types": list(set([item[0] for item in redacted_items]))
                },
                message=f"Redacted {len(redacted_items)} PII items"
            )

        return HeuristicResult(
            status=HeuristicStatus.PASSED,
            modified_input=query
        )


class QueryDeduplication(BaseHeuristic):
    """
    Heuristic #3: Duplicate Query Deduplication
    Caches and reuses results for identical/similar queries.
    """

    def __init__(self, enabled: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "cache_size": 100,
            "similarity_threshold": 0.9,
            "use_exact_match": True
        }
        super().__init__(enabled, {**default_config, **(config or {})})
        self.cache: Dict[str, Any] = {}
        self.query_hashes: List[str] = []

    def _compute_hash(self, query: str) -> str:
        """Compute hash of normalized query"""
        normalized = query.lower().strip()
        return hashlib.md5(normalized.encode()).hexdigest()

    def _compute_similarity(self, query1: str, query2: str) -> float:
        """Simple word-based similarity"""
        words1 = set(query1.lower().split())
        words2 = set(query2.lower().split())

        if not words1 or not words2:
            return 0.0

        intersection = words1.intersection(words2)
        union = words1.union(words2)
        return len(intersection) / len(union) if union else 0.0

    async def check(self, input_data: Any, context: Optional[Dict[str, Any]] = None) -> HeuristicResult:
        query = str(input_data)
        query_hash = self._compute_hash(query)

        # Exact match check
        if self.config["use_exact_match"] and query_hash in self.cache:
            return HeuristicResult(
                status=HeuristicStatus.WARNING,
                modified_input=query,
                metadata={
                    "duplicate": True,
                    "match_type": "exact",
                    "cached_hash": query_hash
                },
                message="Exact duplicate query detected"
            )

        # Similarity check
        threshold = self.config["similarity_threshold"]
        for cached_hash in self.query_hashes:
            if cached_hash in self.cache:
                cached_query = self.cache[cached_hash].get("query", "")
                similarity = self._compute_similarity(query, cached_query)

                if similarity >= threshold:
                    return HeuristicResult(
                        status=HeuristicStatus.WARNING,
                        modified_input=query,
                        metadata={
                            "duplicate": True,
                            "match_type": "similar",
                            "similarity": similarity,
                            "cached_hash": cached_hash
                        },
                        message=f"Similar query detected (similarity: {similarity:.2f})"
                    )

        # Add to cache
        self.cache[query_hash] = {"query": query}
        self.query_hashes.append(query_hash)

        # Maintain cache size
        if len(self.query_hashes) > self.config["cache_size"]:
            oldest_hash = self.query_hashes.pop(0)
            del self.cache[oldest_hash]

        return HeuristicResult(
            status=HeuristicStatus.PASSED,
            modified_input=query,
            metadata={
                "duplicate": False,
                "query_hash": query_hash
            }
        )


class AmbiguityDetection(BaseHeuristic):
    """
    Heuristic #9: Ambiguity Detection & Clarification
    Flags vague queries that need clarification.
    """

    def __init__(self, enabled: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "min_query_length": 3,
            "vague_words": ["this", "that", "it", "something", "stuff", "thing", "things"],
            "pronoun_threshold": 0.3
        }
        super().__init__(enabled, {**default_config, **(config or {})})

    async def check(self, input_data: Any, context: Optional[Dict[str, Any]] = None) -> HeuristicResult:
        query = str(input_data)
        words = query.lower().split()

        # Too short
        if len(words) < self.config["min_query_length"]:
            return HeuristicResult(
                status=HeuristicStatus.WARNING,
                modified_input=query,
                metadata={
                    "ambiguous": True,
                    "reason": "too_short",
                    "word_count": len(words)
                },
                message="Query too short - may be ambiguous"
            )

        # Check for vague words
        vague_words = self.config["vague_words"]
        vague_count = sum(1 for word in words if word in vague_words)
        vague_ratio = vague_count / len(words) if words else 0

        if vague_ratio > self.config["pronoun_threshold"]:
            return HeuristicResult(
                status=HeuristicStatus.WARNING,
                modified_input=query,
                metadata={
                    "ambiguous": True,
                    "reason": "too_vague",
                    "vague_word_ratio": vague_ratio,
                    "vague_words_found": [w for w in words if w in vague_words]
                },
                message=f"Query contains too many vague words ({vague_ratio:.1%})"
            )

        # Check for question words (good sign)
        question_words = ["what", "when", "where", "who", "why", "how", "which"]
        has_question = any(word in words for word in question_words)

        return HeuristicResult(
            status=HeuristicStatus.PASSED,
            modified_input=query,
            metadata={
                "ambiguous": False,
                "has_question_word": has_question,
                "clarity_score": 1.0 - vague_ratio
            }
        )


class QueryIntentClassification(BaseHeuristic):
    """
    Heuristic #10: Query Intent Classification
    Classifies query type for routing to specialized pipelines.
    """

    def __init__(self, enabled: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "intents": {
                "search": ["find", "search", "look for", "show me", "get"],
                "calculation": ["calculate", "compute", "sum", "total", "average", "mean"],
                "summarization": ["summarize", "summary", "overview", "explain", "describe"],
                "comparison": ["compare", "difference", "versus", "vs", "better", "worse"],
                "question": ["what", "when", "where", "who", "why", "how", "which"]
            }
        }
        super().__init__(enabled, {**default_config, **(config or {})})

    async def check(self, input_data: Any, context: Optional[Dict[str, Any]] = None) -> HeuristicResult:
        query = str(input_data).lower()
        detected_intents = []

        for intent, keywords in self.config["intents"].items():
            for keyword in keywords:
                if keyword in query:
                    detected_intents.append(intent)
                    break

        # Determine primary intent
        if not detected_intents:
            primary_intent = "general"
        else:
            # Most frequent or first detected
            primary_intent = detected_intents[0]

        return HeuristicResult(
            status=HeuristicStatus.PASSED,
            modified_input=input_data,
            metadata={
                "primary_intent": primary_intent,
                "all_intents": detected_intents,
                "intent_count": len(detected_intents)
            },
            message=f"Classified as '{primary_intent}' intent"
        )