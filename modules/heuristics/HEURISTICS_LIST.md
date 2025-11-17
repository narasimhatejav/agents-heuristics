# Heuristics Implementation List

This document lists all the heuristics implemented in the S9 HUE (Heuristic Understanding Engine) system.

## Overview

The heuristics are organized into three main categories:
1. **Query Heuristics** - Applied to user queries before processing
2. **Tool Heuristics** - Applied to tool selection and usage
3. **Result Heuristics** - Applied to results before returning to user

---

## Query Heuristics

Located in: [query_heuristics.py](query_heuristics.py)

### 1. Query Length & Complexity Filter
**Class:** `QueryLengthFilter`

**Purpose:** Prevents extremely long or overly complex queries from being processed.

**Features:**
- Checks word count and character count against configurable limits
- Detects overly complex sentences
- Can automatically truncate queries if configured
- Returns warnings for queries with long sentences

**Configuration:**
- `max_words`: Maximum allowed words (default: 500)
- `max_chars`: Maximum allowed characters (default: 5000)
- `max_sentence_length`: Maximum words per sentence (default: 100)
- `auto_truncate`: Enable automatic truncation (default: false)

**Statuses:**
- `FAILED`: Query exceeds limits and auto_truncate is disabled
- `MODIFIED`: Query was truncated
- `WARNING`: Query contains overly complex sentences
- `PASSED`: Query meets all criteria

---

### 2. PII (Personally Identifiable Information) Redaction
**Class:** `PIIRedaction`

**Purpose:** Automatically detects and masks sensitive personal information in queries.

**Features:**
- Detects and redacts Social Security Numbers (SSN)
- Detects and redacts Credit Card numbers
- Detects and redacts Email addresses
- Detects and redacts Phone numbers
- Configurable redaction patterns and replacement text

**Configuration:**
- `redact_ssn`: Enable SSN redaction (default: true)
- `redact_credit_card`: Enable credit card redaction (default: true)
- `redact_email`: Enable email redaction (default: true)
- `redact_phone`: Enable phone redaction (default: true)
- `replacement`: Replacement text for redacted items (default: "***REDACTED***")

**Statuses:**
- `MODIFIED`: PII was detected and redacted
- `PASSED`: No PII detected

---

### 3. Duplicate Query Deduplication
**Class:** `QueryDeduplication`

**Purpose:** Caches and identifies duplicate or similar queries to enable result reuse.

**Features:**
- Exact match detection using MD5 hashing
- Similarity-based matching using word overlap
- Configurable cache size with LRU eviction
- Configurable similarity threshold

**Configuration:**
- `cache_size`: Maximum queries to cache (default: 100)
- `similarity_threshold`: Minimum similarity for match (default: 0.9)
- `use_exact_match`: Enable exact matching (default: true)

**Statuses:**
- `WARNING`: Duplicate query detected (exact or similar match)
- `PASSED`: New unique query

---

### 9. Ambiguity Detection & Clarification
**Class:** `AmbiguityDetection`

**Purpose:** Flags vague or ambiguous queries that may need clarification.

**Features:**
- Detects overly short queries
- Identifies excessive use of vague words (this, that, it, something, etc.)
- Calculates clarity score based on vague word ratio
- Checks for question words as positive indicators

**Configuration:**
- `min_query_length`: Minimum words required (default: 3)
- `vague_words`: List of words considered vague
- `pronoun_threshold`: Maximum ratio of vague words allowed (default: 0.3)

**Statuses:**
- `WARNING`: Query is too short or contains too many vague words
- `PASSED`: Query is clear and specific

---

### 10. Query Intent Classification
**Class:** `QueryIntentClassification`

**Purpose:** Classifies query type to enable routing to specialized pipelines.

**Features:**
- Keyword-based intent detection
- Supports multiple intent categories
- Returns primary intent and all detected intents

**Intent Categories:**
- `search`: Finding or retrieving information
- `calculation`: Mathematical or computational tasks
- `summarization`: Summarizing or explaining content
- `comparison`: Comparing or contrasting items
- `question`: General question-answering
- `general`: Default for unclassified queries

**Configuration:**
- `intents`: Dictionary mapping intent names to keyword lists

**Statuses:**
- `PASSED`: Always passes, classification is informational

---

## Tool Heuristics

Located in: [tool_heuristics.py](tool_heuristics.py)

### 4. Tool Affinity Scoring
**Class:** `ToolAffinityScoring`

**Purpose:** Prefers tools with high historical success rates for specific query types.

**Features:**
- Tracks success/failure rates for each (query_intent, tool) pair
- Re-ranks tools based on historical performance
- Requires minimum sample size before applying scores
- Configurable success threshold and boost factor

**Configuration:**
- `min_samples`: Minimum usage count before scoring (default: 3)
- `success_threshold`: Minimum success rate threshold (default: 0.7)
- `boost_factor`: Score multiplier for successful tools (default: 1.5)

**Methods:**
- `record_tool_usage()`: Record tool execution results
- `get_affinity_score()`: Get affinity score for tool/intent pair

**Statuses:**
- `MODIFIED`: Tools were re-ranked based on affinity
- `PASSED`: No re-ranking needed (insufficient data)

---

### 6. Time-Based Tool Prioritization
**Class:** `TimeBasedPrioritization`

**Purpose:** Deprioritizes slow tools unless necessary for complex queries.

**Features:**
- Tracks average latency for each tool
- Categorizes tools as fast, normal, or slow
- Prioritizes fast tools for simple queries
- Maintains rolling window of recent latency measurements

**Configuration:**
- `slow_threshold_ms`: Latency threshold for slow tools (default: 2000ms)
- `fast_threshold_ms`: Latency threshold for fast tools (default: 500ms)
- `prefer_fast_for_simple`: Enable prioritization for simple queries (default: true)

**Methods:**
- `record_tool_latency()`: Record tool execution time
- `get_avg_latency()`: Get average latency for tool

**Statuses:**
- `MODIFIED`: Tools were reordered by speed
- `PASSED`: No reordering needed

---

### 8. Rate Limit & Cost Optimization
**Class:** `RateLimitOptimization`

**Purpose:** Prevents excessive API calls and manages costs.

**Features:**
- Tracks total calls per session
- Tracks calls per step
- Special tracking for expensive tools
- Configurable limits at multiple levels

**Configuration:**
- `max_calls_per_session`: Maximum total calls (default: 10)
- `max_calls_per_step`: Maximum calls per step (default: 5)
- `expensive_tool_limit`: Maximum expensive tool calls (default: 3)
- `expensive_tools`: List of tools considered expensive

**Methods:**
- `record_tool_call()`: Record a tool call
- `get_usage()`: Get usage statistics for session

**Statuses:**
- `FAILED`: Session or step limit exceeded
- `WARNING`: Approaching expensive tool limit
- `PASSED`: Within all limits

---

## Result Heuristics

Located in: [result_heuristics.py](result_heuristics.py)

### 5. Result Confidence Thresholding
**Class:** `ConfidenceThreshold`

**Purpose:** Only returns results that meet minimum confidence/quality criteria.

**Features:**
- Calculates confidence based on hedging language detection
- Configurable list of hedging words
- Three-tier confidence classification (high, moderate, low)
- Can use externally provided confidence scores

**Configuration:**
- `min_confidence`: Minimum acceptable confidence (default: 0.7)
- `check_hedging_words`: Enable hedging detection (default: true)
- `hedging_words`: List of words indicating uncertainty
- `hedging_threshold`: Maximum allowed hedging words (default: 2)

**Hedging Words:** maybe, perhaps, possibly, might, could, probably, uncertain, unsure, unclear, not sure, don't know

**Statuses:**
- `FAILED`: Confidence below minimum threshold
- `WARNING`: Moderate confidence (0.7-0.85)
- `PASSED`: High confidence (>0.85)

---

### 7. Hallucination Detection
**Class:** `HallucinationDetection`

**Purpose:** Catches when LLM generates ungrounded or fabricated information.

**Features:**
- Extracts specific factual claims from results
- Checks if claims are grounded in tool outputs
- Identifies suspicious claim patterns
- Calculates grounding score

**Configuration:**
- `require_grounding`: Enable grounding check (default: true)
- `check_specific_claims`: Enable claim extraction (default: true)
- `suspicious_patterns`: Regex patterns for claim detection

**Suspicious Patterns:**
- "the document (says|states|mentions)"
- "according to (the|this)"
- "as stated in"
- "the data shows"
- "research indicates"

**Methods:**
- `_extract_claims()`: Extract factual claims from result
- `_check_grounding()`: Verify claims against tool outputs

**Statuses:**
- `FAILED`: Ungrounded claims detected (potential hallucination)
- `WARNING`: Partial grounding (grounding score < 1.0)
- `PASSED`: All claims grounded in tool outputs

---

## Base Infrastructure

Located in: [base.py](base.py)

### Base Classes

#### `BaseHeuristic`
Abstract base class for all heuristics.

**Features:**
- Enable/disable functionality
- Configuration management
- Statistics tracking (total runs, passed, failed, modified, warnings)
- Async execution with stats tracking

**Methods:**
- `check()`: Abstract method for heuristic logic
- `execute()`: Execute with stats tracking
- `get_stats()`: Get execution statistics
- `reset_stats()`: Reset statistics

#### `HeuristicPipeline`
Pipeline to run multiple heuristics in sequence.

**Features:**
- Sequential execution of heuristics
- Data modification propagation
- Optional stop-on-failure mode
- Aggregate statistics

**Methods:**
- `add_heuristic()`: Add heuristic to pipeline
- `run()`: Execute all heuristics
- `get_all_stats()`: Get stats for all heuristics

### Enums and Models

#### `HeuristicStatus`
- `PASSED`: Heuristic check passed
- `FAILED`: Heuristic check failed
- `MODIFIED`: Input was modified
- `WARNING`: Warning issued but not blocking

#### `HeuristicResult`
Result model containing:
- `status`: HeuristicStatus
- `modified_input`: Modified data (if applicable)
- `metadata`: Additional information
- `message`: Human-readable message

---

## Summary Statistics

**Total Heuristics Implemented:** 10

**By Category:**
- Query Heuristics: 5
- Tool Heuristics: 3
- Result Heuristics: 2

**By Purpose:**
- Safety/Security: 1 (PII Redaction)
- Quality Control: 3 (Query Length, Ambiguity, Confidence)
- Performance: 3 (Deduplication, Time-Based, Rate Limit)
- Intelligence: 3 (Affinity Scoring, Intent Classification, Hallucination Detection)

---

## Usage Example

```python
from modules.heuristics.query_heuristics import QueryLengthFilter, PIIRedaction
from modules.heuristics.base import HeuristicPipeline

# Create heuristics
length_filter = QueryLengthFilter(enabled=True)
pii_redaction = PIIRedaction(enabled=True)

# Create pipeline
pipeline = HeuristicPipeline([length_filter, pii_redaction])

# Run pipeline
query = "What is my SSN 123-45-6789?"
final_query, results = await pipeline.run(query)

# Check results
for result in results:
    print(f"Status: {result.status}, Message: {result.message}")
```

---

## Configuration Files

Heuristics can be configured through:
- [config/heuristics_config.yaml](../../config/heuristics_config.yaml) - Default configurations
- Runtime configuration via constructor parameters

## Manager

The [manager.py](manager.py) file provides the `HeuristicsManager` class that orchestrates all heuristics and provides a unified interface for the agent system.
