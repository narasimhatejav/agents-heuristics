# modules/action.py

from typing import Dict, Any, Union
from pydantic import BaseModel
import asyncio
import types
import json


# Optional logging fallback
try:
    from agent import log
except ImportError:
    import datetime
    def log(stage: str, msg: str):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] [{stage}] {msg}")

class ToolCallResult(BaseModel):
    tool_name: str
    arguments: Dict[str, Any]
    result: Union[str, list, dict]
    raw_response: Any

MAX_TOOL_CALLS_PER_PLAN = 5

async def run_python_sandbox(code: str, dispatcher: Any) -> Dict[str, Any]:
    print("[action] 🔍 Entered run_python_sandbox()")

    # Create a fresh module scope
    sandbox = types.ModuleType("sandbox")

    # Track tool outputs for heuristics validation
    tool_outputs = []

    try:
        # Patch MCP client with real dispatcher
        class SandboxMCP:
            def __init__(self, dispatcher, tool_outputs_tracker):
                self.dispatcher = dispatcher
                self.call_count = 0
                self.tool_outputs_tracker = tool_outputs_tracker

            async def call_tool(self, tool_name: str, input_dict: dict):
                self.call_count += 1
                if self.call_count > MAX_TOOL_CALLS_PER_PLAN:
                    raise RuntimeError(f"Exceeded max tool calls ({MAX_TOOL_CALLS_PER_PLAN}) in solve() plan.")
                # REAL tool call now
                result = await self.dispatcher.call_tool(tool_name, input_dict)

                # Track tool output for heuristics
                try:
                    if hasattr(result, 'content') and result.content:
                        # MCP result format
                        output_text = str(result.content[0].text if result.content else "")
                        self.tool_outputs_tracker.append(output_text)
                    else:
                        self.tool_outputs_tracker.append(str(result))
                except Exception:
                    pass  # Silently fail if we can't extract output

                return result

        sandbox.mcp = SandboxMCP(dispatcher, tool_outputs)

        # Preload safe built-ins into the sandbox
        import json, re
        sandbox.__dict__["json"] = json
        sandbox.__dict__["re"] = re

        # Execute solve fn dynamically
        exec(compile(code, "<solve_plan>", "exec"), sandbox.__dict__)

        solve_fn = sandbox.__dict__.get("solve")
        if solve_fn is None:
            raise ValueError("No solve() function found in plan.")

        if asyncio.iscoroutinefunction(solve_fn):
            result = await solve_fn()
        else:
            result = solve_fn()

        # Clean result formatting
        formatted_result = ""
        if isinstance(result, dict) and "result" in result:
            formatted_result = f"{result['result']}"
        elif isinstance(result, dict):
            formatted_result = f"{json.dumps(result)}"
        elif isinstance(result, list):
            formatted_result = f"{' '.join(str(r) for r in result)}"
        else:
            formatted_result = f"{result}"

        # Return both result and tool outputs
        return {
            "result": formatted_result,
            "tool_outputs": tool_outputs
        }






    except Exception as e:
        log("sandbox", f"⚠️ Execution error: {e}")
        return {
            "result": f"[sandbox error: {str(e)}]",
            "tool_outputs": tool_outputs
        }
