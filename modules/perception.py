# modules/perception.py

from typing import List, Optional
from pydantic import BaseModel
from modules.model_manager import ModelManager
from modules.tools import load_prompt, extract_json_block
from core.context import AgentContext

import json


# Optional logging fallback
try:
    from agent import log
except ImportError:
    import datetime
    def log(stage: str, msg: str):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] [{stage}] {msg}")

model = ModelManager()


prompt_path = "prompts/perception_prompt.txt"

class PerceptionResult(BaseModel):
    intent: str
    entities: List[str] = []
    tool_hint: Optional[str] = None
    tags: List[str] = []
    selected_servers: List[str] = []  # 🆕 NEW field

async def extract_perception(user_input: str, mcp_server_descriptions: dict) -> PerceptionResult:
    """
    Extracts perception details and selects relevant MCP servers based on the user query.
    """

    server_list = []
    for server_id, server_info in mcp_server_descriptions.items():
        description = server_info.get("description", "No description available")
        server_list.append(f"- {server_id}: {description}")

    servers_text = "\n".join(server_list)

    prompt_template = load_prompt(prompt_path)
    

    prompt = prompt_template.format(
        servers_text=servers_text,
        user_input=user_input
    )
    

    try:
        raw = await model.generate_text(prompt)
        raw = raw.strip()
        log("perception", f"Raw output: {raw}")

        # Try parsing into PerceptionResult
        json_block = extract_json_block(raw)
        result = json.loads(json_block)

        # If selected_servers missing, fallback
        if "selected_servers" not in result:
            result["selected_servers"] = list(mcp_server_descriptions.keys())
        print("result", result)

        return PerceptionResult(**result)

    except Exception as e:
        log("perception", f"⚠️ Perception failed: {e}")
        # Fallback: select all servers
        return PerceptionResult(
            intent="unknown",
            entities=[],
            tool_hint=None,
            tags=[],
            selected_servers=list(mcp_server_descriptions.keys())
        )


async def run_perception(context: AgentContext, user_input: Optional[str] = None):

    """
    Clean wrapper to call perception from context.
    Now with heuristics integration and conversation history retrieval.
    """
    query = user_input or context.user_input

    # 🆕 STEP 1: Retrieve relevant conversation history
    conversation_config = context.agent_profile.memory_config.get("conversation_history", {})
    if conversation_config.get("enabled", True):
        history_threshold = conversation_config.get("threshold", 0.75)
        history_top_k = conversation_config.get("top_k", 3)
        history_max_age_days = conversation_config.get("max_age_days", None)

        relevant_conversations = context.memory.find_relevant_conversations(
            query=query,
            top_k=history_top_k,
            threshold=history_threshold,
            max_age_days=history_max_age_days
        )

        if relevant_conversations:
            log("perception", f"🧠 Found {len(relevant_conversations)} relevant past conversations:")
            for turn, score in relevant_conversations:
                log("perception", f"   - Session {turn.session_id[:20]}..., Step {turn.step_number}: '{turn.user_query[:40]}...' (score: {score:.3f})")

            # Store in context for decision module to use
            context.relevant_conversation_history = relevant_conversations
        else:
            context.relevant_conversation_history = []
    else:
        context.relevant_conversation_history = []

    # Apply query heuristics if available
    if context.heuristics:
        query, heuristics_metadata = await context.heuristics.process_query(query)
        log("perception", f"Heuristics metadata: {heuristics_metadata}")

    return await extract_perception(
        user_input=query,
        mcp_server_descriptions=context.mcp_server_descriptions
    )

