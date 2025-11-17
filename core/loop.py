# modules/loop.py

import asyncio
import time
from modules.perception import run_perception
from modules.decision import generate_plan
from modules.action import run_python_sandbox
from modules.model_manager import ModelManager
from core.session import MultiMCP
from core.strategy import select_decision_prompt_path
from core.context import AgentContext
from modules.tools import summarize_tools
from modules.memory import ConversationTurn, ToolExecution
import re

try:
    from agent import log
except ImportError:
    import datetime
    def log(stage: str, msg: str):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] [{stage}] {msg}")

class AgentLoop:
    def __init__(self, context: AgentContext):
        self.context = context
        self.mcp = self.context.dispatcher
        self.model = ModelManager()
        self.current_turn_tools = []  # Track tools executed in current turn

    def _capture_conversation_turn(self, step: int, plan: str, result: str):
        """Capture the full conversation turn and add to history index."""
        try:
            turn = ConversationTurn(
                turn_id=ConversationTurn.create_turn_id(self.context.session_id, step),
                session_id=self.context.session_id,
                timestamp=time.time(),
                user_query=self.context.user_input,
                agent_reasoning=plan[:500] if plan else None,  # Store first 500 chars of plan
                tools_executed=self.current_turn_tools,
                final_answer=result,
                step_number=step,
                tags=["completed"],
                metadata={
                    "max_steps": self.context.agent_profile.strategy.max_steps,
                    "planning_mode": self.context.agent_profile.strategy.planning_mode
                }
            )

            # Add to conversation history index
            self.context.memory.add_conversation_turn(turn)
            log("loop", f"📝 Captured conversation turn for step {step}")

        except Exception as e:
            log("loop", f"⚠️ Failed to capture conversation turn: {e}")

    async def run(self):
        max_steps = self.context.agent_profile.strategy.max_steps

        for step in range(max_steps):
            print(f"🔁 Step {step+1}/{max_steps} starting...")
            self.context.step = step
            lifelines_left = self.context.agent_profile.strategy.max_lifelines_per_step

            while lifelines_left >= 0:
                # === Perception ===
                user_input_override = getattr(self.context, "user_input_override", None)
                perception = await run_perception(context=self.context, user_input=user_input_override or self.context.user_input)

                print(f"[perception] {perception}")

                selected_servers = perception.selected_servers
                selected_tools = self.mcp.get_tools_from_servers(selected_servers)

                # Apply tool heuristics if available
                if self.context.heuristics and selected_tools:
                    tool_context = {
                        "session_id": self.context.session_id,
                        "step": step,
                        "query_intent": perception.intent if hasattr(perception, 'intent') else "general"
                    }
                    selected_tools, tool_metadata = await self.context.heuristics.process_tools(
                        selected_tools, tool_context
                    )
                    log("loop", f"Tool heuristics applied: {tool_metadata}")

                # If no tools selected but we have previous data to synthesize, allow synthesis mode
                if not selected_tools:
                    if user_input_override:
                        log("loop", "⚠️ No tools selected, but previous data exists. Entering synthesis mode...")
                        tool_descriptions = "No tools available. You must synthesize the answer from the data already provided in the user input."
                    else:
                        log("loop", "⚠️ No tools selected — aborting step.")
                        break
                else:
                    tool_descriptions = summarize_tools(selected_tools)

                # === Planning ===
                prompt_path = select_decision_prompt_path(
                    planning_mode=self.context.agent_profile.strategy.planning_mode,
                    exploration_mode=self.context.agent_profile.strategy.exploration_mode,
                )

                plan = await generate_plan(
                    user_input=user_input_override or self.context.user_input,
                    perception=perception,
                    memory_items=self.context.memory.get_session_items(),
                    tool_descriptions=tool_descriptions,
                    prompt_path=prompt_path,
                    step_num=step + 1,
                    max_steps=max_steps,
                    context=self.context,  # Pass context for conversation history
                )
                print(f"[plan] {plan}")

                # === Execution ===
                if re.search(r"^\s*(async\s+)?def\s+solve\s*\(", plan, re.MULTILINE):
                    print("[loop] Detected solve() plan — running sandboxed...")

                    # Track tool execution for conversation history
                    tool_exec_start = time.time()
                    self.context.log_subtask(tool_name="solve_sandbox", status="pending")
                    sandbox_output = await run_python_sandbox(plan, dispatcher=self.mcp)

                    # Extract result and tool outputs from sandbox
                    if isinstance(sandbox_output, dict):
                        result = sandbox_output.get("result", str(sandbox_output))
                        tool_outputs = sandbox_output.get("tool_outputs", [])
                    else:
                        result = str(sandbox_output)
                        tool_outputs = []

                    # Apply result heuristics if available
                    if self.context.heuristics and isinstance(result, str):
                        result_context = {
                            "session_id": self.context.session_id,
                            "step": step,
                            "tool_outputs": tool_outputs  # Now properly tracked!
                        }
                        validated_result, result_metadata = await self.context.heuristics.process_result(
                            result, result_context
                        )
                        if result_metadata.get("failed", 0) > 0:
                            log("loop", f"⚠️ Result validation failed: {result_metadata}")
                        result = validated_result

                    success = False
                    if isinstance(result, str):
                        result = result.strip()
                        if result.startswith("FINAL_ANSWER:"):
                            success = True
                            self.context.final_answer = result
                            self.context.update_subtask_status("solve_sandbox", "success")

                            # Track tool execution
                            tool_execution = ToolExecution(
                                tool_name="solve_sandbox",
                                tool_args={"plan": plan[:200]},  # Store first 200 chars
                                tool_result=result[:200],  # Store first 200 chars
                                success=True,
                                timestamp=tool_exec_start
                            )
                            self.current_turn_tools.append(tool_execution)

                            self.context.memory.add_tool_output(
                                tool_name="solve_sandbox",
                                tool_args={"plan": plan},
                                tool_result={"result": result},
                                success=True,
                                tags=["sandbox"],
                            )

                            # 🆕 Capture full conversation turn for history
                            self._capture_conversation_turn(step, plan, result)

                            return {"status": "done", "result": self.context.final_answer}
                        elif result.startswith("FURTHER_PROCESSING_REQUIRED:"):
                            content = result.split("FURTHER_PROCESSING_REQUIRED:")[1].strip()
                            self.context.user_input_override  = (
                                f"Original user task: {self.context.user_input}\n\n"
                                f"Previous tool call result (already fetched - DO NOT call the same tool again):\n\n"
                                f"{content}\n\n"
                                f"IMPORTANT INSTRUCTIONS:\n"
                                f"1. If the above data fully answers the user's task, synthesize and return:\n"
                                f"   async def solve():\n"
                                f"       return \"FINAL_ANSWER: [your synthesized answer based on the data above]\"\n\n"
                                f"2. If you need to PROCESS this data further (calculate, transform, etc.), call a DIFFERENT tool.\n"
                                f"3. DO NOT call the same search/fetch tool again - you already have the data above.\n"
                            )
                            log("loop", f"📨 Forwarding intermediate result to next step:\n{self.context.user_input_override}\n\n")
                            log("loop", f"🔁 Continuing based on FURTHER_PROCESSING_REQUIRED — Step {step+1} continues...")
                            break  # Step will continue
                        elif result.startswith("[sandbox error:"):
                            success = False
                            self.context.final_answer = "FINAL_ANSWER: [Execution failed]"
                        else:
                            success = True
                            self.context.final_answer = f"FINAL_ANSWER: {result}"
                    else:
                        self.context.final_answer = f"FINAL_ANSWER: {result}"

                    if success:
                        self.context.update_subtask_status("solve_sandbox", "success")
                    else:
                        self.context.update_subtask_status("solve_sandbox", "failure")

                    self.context.memory.add_tool_output(
                        tool_name="solve_sandbox",
                        tool_args={"plan": plan},
                        tool_result={"result": result},
                        success=success,
                        tags=["sandbox"],
                    )

                    if success and "FURTHER_PROCESSING_REQUIRED:" not in result:
                        # Track tool execution
                        tool_execution = ToolExecution(
                            tool_name="solve_sandbox",
                            tool_args={"plan": plan[:200]},
                            tool_result=str(result)[:200] if result else "",
                            success=True,
                            timestamp=tool_exec_start
                        )
                        self.current_turn_tools.append(tool_execution)

                        # 🆕 Capture full conversation turn for history
                        self._capture_conversation_turn(step, plan, self.context.final_answer)

                        return {"status": "done", "result": self.context.final_answer}
                    else:
                        lifelines_left -= 1
                        log("loop", f"🛠 Retrying... Lifelines left: {lifelines_left}")
                        continue
                else:
                    log("loop", f"⚠️ Invalid plan detected — retrying... Lifelines left: {lifelines_left-1}")
                    lifelines_left -= 1
                    continue

        log("loop", "⚠️ Max steps reached without finding final answer.")
        self.context.final_answer = "FINAL_ANSWER: [Max steps reached]"
        return {"status": "done", "result": self.context.final_answer}
