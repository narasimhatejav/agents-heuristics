from typing import List, Optional
from modules.perception import PerceptionResult
from modules.memory import MemoryItem
from modules.model_manager import ModelManager
from modules.tools import load_prompt
import re

# Optional logging fallback
try:
    from agent import log
except ImportError:
    import datetime
    def log(stage: str, msg: str):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] [{stage}] {msg}")

model = ModelManager()


# prompt_path = "prompts/decision_prompt.txt"

def format_conversation_history(context) -> str:
    """Format relevant conversation history for the prompt."""
    if not hasattr(context, 'relevant_conversation_history') or not context.relevant_conversation_history:
        return ""

    history_lines = ["📚 Relevant Past Conversations (for context):"]
    history_lines.append("The following past conversations may provide useful context or approaches:")
    history_lines.append("")

    for turn, similarity in context.relevant_conversation_history:
        tools_used = ", ".join([t.tool_name for t in turn.tools_executed]) if turn.tools_executed else "No tools"
        history_lines.append(f"🔹 Past Query: \"{turn.user_query}\"")
        history_lines.append(f"   Tools Used: {tools_used}")
        history_lines.append(f"   Result: {turn.final_answer[:150]}...")
        history_lines.append(f"   (Similarity: {similarity:.2f})")
        history_lines.append("")

    history_lines.append("💡 Use this context to inform your approach, but always solve the current query directly.")
    history_lines.append("")

    return "\n".join(history_lines)


async def generate_plan(
    user_input: str,
    perception: PerceptionResult,
    memory_items: List[MemoryItem],
    tool_descriptions: Optional[str],
    prompt_path: str,
    step_num: int = 1,
    max_steps: int = 3,
    context = None,  # Add context parameter
) -> str:

    """Generates the full solve() function plan for the agent."""

    memory_texts = "\n".join(f"- {m.text}" for m in memory_items) or "None"

    # Format conversation history if available
    conversation_history_section = ""
    if context:
        conversation_history_section = format_conversation_history(context)

    prompt_template = load_prompt(prompt_path)

    prompt = prompt_template.format(
        tool_descriptions=tool_descriptions,
        user_input=user_input,
        conversation_history_section=conversation_history_section
    )


    try:
        raw = (await model.generate_text(prompt)).strip()
        log("plan", f"LLM output: {raw}")

        # If fenced in ```python ... ```, extract
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
            if raw.lower().startswith("python"):
                raw = raw[len("python"):].strip()

        if re.search(r"^\s*(async\s+)?def\s+solve\s*\(", raw, re.MULTILINE):
            return raw  # ✅ Correct, it's a full function
        else:
            log("plan", "⚠️ LLM did not return a valid solve(). Defaulting to FINAL_ANSWER")
            return "FINAL_ANSWER: [Could not generate valid solve()]"


    except Exception as e:
        log("plan", f"⚠️ Planning failed: {e}")
        return "FINAL_ANSWER: [unknown]"
