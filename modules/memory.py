# modules/memory.py

import json
import os
import time
import hashlib
import numpy as np
import requests
from typing import List, Optional, Dict, Any, Tuple
from pydantic import BaseModel
from pathlib import Path

# FAISS for vector similarity search
try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    faiss = None

# Optional fallback logger
try:
    from agent import log
except ImportError:
    import datetime
    def log(stage: str, msg: str):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] [{stage}] {msg}")

class MemoryItem(BaseModel):
    """Represents a single memory entry for a session."""
    timestamp: float
    type: str  # run_metadata, tool_call, tool_output, final_answer
    text: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result: Optional[dict] = None
    final_answer: Optional[str] = None
    tags: Optional[List[str]] = []
    success: Optional[bool] = None
    metadata: Optional[dict] = {}  # ✅ ADD THIS LINE BACK
    user_query: Optional[str] = None  # 🆕 Store original user query


# Query caching removed - using conversation history only


class ToolExecution(BaseModel):
    """Represents a single tool execution within a conversation turn."""
    tool_name: str
    tool_args: Dict[str, Any]
    tool_result: Any
    success: bool
    timestamp: float


class ConversationTurn(BaseModel):
    """Represents a complete conversation turn with full context."""
    turn_id: str  # Unique ID for this turn
    session_id: str
    timestamp: float
    user_query: str
    agent_reasoning: Optional[str] = None  # Agent's internal reasoning
    tools_executed: List[ToolExecution] = []
    intermediate_results: List[str] = []  # Results from FURTHER_PROCESSING_REQUIRED
    final_answer: str
    step_number: int
    tags: List[str] = []
    metadata: Dict[str, Any] = {}

    def summarize(self) -> str:
        """Create a concise summary for vector indexing."""
        tools_used = [t.tool_name for t in self.tools_executed]
        tools_str = f"Tools: {', '.join(tools_used)}" if tools_used else "No tools"

        # Extract key entities/topics (simple version)
        answer_preview = self.final_answer[:200] if self.final_answer else ""

        return f"""Query: {self.user_query}
{tools_str}
Result: {answer_preview}"""

    @staticmethod
    def create_turn_id(session_id: str, step: int) -> str:
        """Generate unique turn ID."""
        return f"{session_id}-turn-{step}"


def get_embedding(text: str, embed_url: str = "http://localhost:11434/api/embeddings", model: str = "nomic-embed-text") -> np.ndarray:
    """Generate embedding vector for text using Ollama."""
    try:
        result = requests.post(embed_url, json={"model": model, "prompt": text}, timeout=30)
        result.raise_for_status()
        return np.array(result.json()["embedding"], dtype=np.float32)
    except Exception as e:
        log("memory", f"⚠️ Failed to generate embedding: {e}")
        return np.zeros(768, dtype=np.float32)  # Return zero vector as fallback


class MemoryManager:
    """Manages session memory (read/write/append) and conversation history with FAISS vector search."""

    # Class-level conversation history storage
    _conversation_history: Optional[List[ConversationTurn]] = None
    _conversation_index = None  # FAISS index for conversation vectors
    _conversation_cache_dir = "memory/conversation_index"
    _conversation_index_file = "memory/conversation_index/conversations.index"
    _conversation_metadata_file = "memory/conversation_index/conversations.json"
    _use_vectors = FAISS_AVAILABLE

    def __init__(self, session_id: str, memory_dir: str = "memory"):
        self.session_id = session_id
        self.memory_dir = memory_dir
        self.memory_path = os.path.join('memory', session_id.split('-')[0], session_id.split('-')[1], session_id.split('-')[2], f'session-{session_id}.json')
        self.items: List[MemoryItem] = []

        if not os.path.exists(self.memory_dir):
            os.makedirs(self.memory_dir)

        self.load()

        # Load conversation history index (once per class)
        if MemoryManager._use_vectors and MemoryManager._conversation_index is None:
            self._load_conversation_index()

    def load(self):
        if os.path.exists(self.memory_path):
            with open(self.memory_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                self.items = [MemoryItem(**item) for item in raw]
        else:
            self.items = []

    def save(self):
        # Before opening the file for writing
        os.makedirs(os.path.dirname(self.memory_path), exist_ok=True)
        with open(self.memory_path, "w", encoding="utf-8") as f:
            raw = [item.dict() for item in self.items]
            json.dump(raw, f, indent=2)

    def add(self, item: MemoryItem):
        self.items.append(item)
        self.save()

    def add_tool_call(
        self, tool_name: str, tool_args: dict, tags: Optional[List[str]] = None
    ):
        item = MemoryItem(
            timestamp=time.time(),
            type="tool_call",
            text=f"Called {tool_name} with {tool_args}",
            tool_name=tool_name,
            tool_args=tool_args,
            tags=tags or [],
        )
        self.add(item)

    def add_tool_output(
        self, tool_name: str, tool_args: dict, tool_result: dict, success: bool, tags: Optional[List[str]] = None
    ):
        item = MemoryItem(
            timestamp=time.time(),
            type="tool_output",
            text=f"Output of {tool_name}: {tool_result}",
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            success=success,  # 🆕 Track success!
            tags=tags or [],
        )
        self.add(item)

    def add_final_answer(self, text: str):
        item = MemoryItem(
            timestamp=time.time(),
            type="final_answer",
            text=text,
            final_answer=text,
        )
        self.add(item)

    def find_recent_successes(self, limit: int = 5) -> List[str]:
        """Find tool names which succeeded recently."""
        tool_successes = []

        # Search from newest to oldest
        for item in reversed(self.items):
            if item.type == "tool_output" and item.success:
                if item.tool_name and item.tool_name not in tool_successes:
                    tool_successes.append(item.tool_name)
            if len(tool_successes) >= limit:
                break

        return tool_successes

    def add_tool_success(self, tool_name: str, success: bool):
        """Patch last tool call or output for a given tool with success=True/False."""

        # Search backwards for latest matching tool call/output
        for item in reversed(self.items):
            if item.tool_name == tool_name and item.type in {"tool_call", "tool_output"}:
                item.success = success
                log("memory", f"✅ Marked {tool_name} as success={success}")
                self.save()
                return

        log("memory", f"⚠️ Tried to mark {tool_name} as success={success} but no matching memory found.")

    def get_session_items(self) -> List[MemoryItem]:
        """
        Return all memory items for current session.
        """
        return self.items

    # ==================== CONVERSATION HISTORY INDEX METHODS ====================

    def _load_conversation_index(self):
        """Load conversation history FAISS index and metadata from disk."""
        os.makedirs(MemoryManager._conversation_cache_dir, exist_ok=True)

        # Load metadata
        if os.path.exists(MemoryManager._conversation_metadata_file):
            try:
                with open(MemoryManager._conversation_metadata_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    MemoryManager._conversation_history = [ConversationTurn(**item) for item in raw]
                log("memory", f"✅ Loaded {len(MemoryManager._conversation_history)} conversation turns")
            except Exception as e:
                log("memory", f"⚠️ Failed to load conversation metadata: {e}")
                MemoryManager._conversation_history = []
        else:
            MemoryManager._conversation_history = []
            log("memory", "No existing conversation history found. Starting fresh.")

        # Load FAISS index
        if os.path.exists(MemoryManager._conversation_index_file):
            try:
                MemoryManager._conversation_index = faiss.read_index(MemoryManager._conversation_index_file)
                log("memory", f"✅ Loaded conversation FAISS index with {MemoryManager._conversation_index.ntotal} vectors")

                # Verify alignment
                if len(MemoryManager._conversation_history) != MemoryManager._conversation_index.ntotal:
                    log("memory", f"⚠️ MISMATCH! Metadata: {len(MemoryManager._conversation_history)}, Vectors: {MemoryManager._conversation_index.ntotal}")
                    log("memory", "🔧 Rebuilding conversation index...")
                    self._rebuild_conversation_index()
                else:
                    log("memory", f"✅ Conversation index aligned: {len(MemoryManager._conversation_history)} entries")
            except Exception as e:
                log("memory", f"⚠️ Failed to load conversation FAISS index: {e}")
                self._create_conversation_index()
        else:
            self._create_conversation_index()
            if MemoryManager._conversation_history:
                log("memory", f"📦 Found {len(MemoryManager._conversation_history)} conversation metadata without vectors")
                log("memory", "🔧 Building conversation FAISS index...")
                self._rebuild_conversation_index()

    def _save_conversation_index(self):
        """Save conversation history FAISS index and metadata to disk."""
        try:
            os.makedirs(MemoryManager._conversation_cache_dir, exist_ok=True)

            # Save metadata
            with open(MemoryManager._conversation_metadata_file, "w", encoding="utf-8") as f:
                raw = [turn.dict() for turn in MemoryManager._conversation_history]
                json.dump(raw, f, indent=2)

            # Save FAISS index
            if MemoryManager._conversation_index:
                faiss.write_index(MemoryManager._conversation_index, MemoryManager._conversation_index_file)

            log("memory", f"💾 Saved {len(MemoryManager._conversation_history)} conversation turns + FAISS index")
        except Exception as e:
            log("memory", f"⚠️ Failed to save conversation index: {e}")

    def _create_conversation_index(self):
        """Create a new FAISS index for conversation vectors."""
        dimension = 768  # nomic-embed-text dimension
        MemoryManager._conversation_index = faiss.IndexFlatIP(dimension)
        log("memory", "Created new conversation FAISS index (dimension: 768)")

    def _rebuild_conversation_index(self):
        """Rebuild conversation FAISS index from all cached metadata."""
        if not MemoryManager._use_vectors:
            log("memory", "⚠️ FAISS not available")
            return

        log("memory", "🔄 Rebuilding conversation FAISS index...")

        # Create new index
        self._create_conversation_index()

        if not MemoryManager._conversation_history:
            log("memory", "No conversation metadata to index")
            return

        try:
            # Generate embeddings for all conversation summaries
            embeddings = []
            for turn in MemoryManager._conversation_history:
                summary = turn.summarize()
                embedding = get_embedding(summary)
                embeddings.append(embedding)

            # Stack and normalize
            embeddings_matrix = np.vstack(embeddings)
            faiss.normalize_L2(embeddings_matrix)

            # Add to index
            MemoryManager._conversation_index.add(embeddings_matrix)

            log("memory", f"✅ Rebuilt conversation FAISS index with {MemoryManager._conversation_index.ntotal} vectors")
        except Exception as e:
            log("memory", f"⚠️ Failed to rebuild conversation index: {e}")

    def add_conversation_turn(self, turn: ConversationTurn):
        """
        Add a new conversation turn to the history index.
        """
        if not MemoryManager._use_vectors:
            log("memory", "⚠️ FAISS not available for conversation indexing")
            return

        try:
            # Add to metadata
            MemoryManager._conversation_history.append(turn)

            # Generate and add vector
            summary = turn.summarize()
            embedding = get_embedding(summary)
            embedding_normalized = embedding.reshape(1, -1).copy()
            faiss.normalize_L2(embedding_normalized)
            MemoryManager._conversation_index.add(embedding_normalized)

            new_idx = len(MemoryManager._conversation_history) - 1
            log("memory", f"➕ Added conversation turn #{new_idx} to history index")

            # Verify alignment
            if MemoryManager._conversation_index.ntotal != len(MemoryManager._conversation_history):
                log("memory", f"⚠️ MISMATCH after add! Rebuilding...")
                self._rebuild_conversation_index()

            # Save to disk
            self._save_conversation_index()

        except Exception as e:
            log("memory", f"⚠️ Failed to add conversation turn: {e}")
            # Remove from metadata if vector add failed
            if MemoryManager._conversation_history and MemoryManager._conversation_history[-1] == turn:
                MemoryManager._conversation_history.pop()

    def find_relevant_conversations(
        self,
        query: str,
        top_k: int = 3,
        threshold: float = 0.75,
        max_age_days: Optional[int] = None
    ) -> List[Tuple[ConversationTurn, float]]:
        """
        Find relevant past conversations using FAISS vector similarity.

        Args:
            query: The current user query
            top_k: Number of similar conversations to return
            threshold: Minimum similarity score (0-1)
            max_age_days: Optional filter for recent conversations only

        Returns:
            List of (ConversationTurn, similarity_score) tuples
        """
        if not MemoryManager._use_vectors or MemoryManager._conversation_index is None:
            log("memory", "⚠️ Conversation index not available")
            return []

        if MemoryManager._conversation_index.ntotal == 0:
            log("memory", "No conversations in history yet")
            return []

        try:
            # Generate query embedding
            query_embedding = get_embedding(query)

            # Normalize for cosine similarity
            query_normalized = query_embedding.reshape(1, -1).copy()
            faiss.normalize_L2(query_normalized)

            # Search FAISS index
            similarities, indices = MemoryManager._conversation_index.search(query_normalized, top_k)

            # Filter by threshold and optionally by age
            results = []
            current_time = time.time()

            for similarity, idx in zip(similarities[0], indices[0]):
                if idx >= 0 and idx < len(MemoryManager._conversation_history) and similarity >= threshold:
                    turn = MemoryManager._conversation_history[idx]

                    # Check age filter if specified
                    if max_age_days:
                        age_seconds = current_time - turn.timestamp
                        age_days = age_seconds / (24 * 3600)
                        if age_days > max_age_days:
                            continue

                    results.append((turn, float(similarity)))

            if results:
                log("memory", f"🔍 Found {len(results)} relevant conversations (threshold: {threshold})")
                for turn, score in results:
                    log("memory", f"   - Session {turn.session_id}, Step {turn.step_number}: '{turn.user_query[:50]}...' (similarity: {score:.3f})")

            return results

        except Exception as e:
            log("memory", f"⚠️ Conversation search failed: {e}")
            return []

    def get_conversation_history_stats(self) -> Dict[str, Any]:
        """Get statistics about the conversation history."""
        if not MemoryManager._conversation_history:
            return {
                "total_conversations": 0,
                "oldest": None,
                "newest": None
            }

        timestamps = [turn.timestamp for turn in MemoryManager._conversation_history]
        sessions = set(turn.session_id for turn in MemoryManager._conversation_history)

        stats = {
            "total_conversations": len(MemoryManager._conversation_history),
            "unique_sessions": len(sessions),
            "oldest": min(timestamps),
            "newest": max(timestamps),
            "vector_search_enabled": MemoryManager._use_vectors
        }

        if MemoryManager._use_vectors and MemoryManager._conversation_index:
            stats["vector_index_size"] = MemoryManager._conversation_index.ntotal

        return stats
