"""
The ReAct loop — multi-provider edition.

Sends the alert to the configured model with the read-only tool set, executes
tool calls, feeds results back, and repeats until the model produces a final
report or we hit a step limit.

Provider detection is by model name prefix:
  claude-*           -> Anthropic API  (ANTHROPIC_API_KEY)
  gpt-* / o1* / o3*  -> OpenAI API    (OPENAI_API_KEY)

Set MODEL in .env (or the environment) to switch providers.
"""
from __future__ import annotations

import json
import os

from agent.prompts import SYSTEM_PROMPT
from tools.registry import TOOL_SCHEMAS, dispatch
from tools.scrubber import safe_output

# Resolved at import time so preflight can surface it.
MODEL = os.environ.get("MODEL", "claude-opus-4-8")

MAX_STEPS = 25
MAX_TOKENS = 4096


def _provider(model: str) -> str:
    if model.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    return "anthropic"


def _openai_tools(schemas: list[dict]) -> list[dict]:
    """Convert Anthropic-format tool schemas to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s["description"],
                "parameters": s["input_schema"],
            },
        }
        for s in schemas
    ]


def _print_step(label: str, body: str = "") -> None:
    print(f"\n{'─' * 70}\n{label}\n{'─' * 70}")
    if body:
        print(body)


def _fmt_input(d: dict) -> str:
    return "\n".join(f"  {k}: {v}" for k, v in d.items())


def _indent(text: str, n: int = 4) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in text.splitlines()[:60])


def investigate(alert: str, cfg: dict, verbose: bool = True) -> str:
    """Run the investigation. Returns the model's final report text."""
    model = MODEL
    prov = _provider(model)

    if prov == "anthropic":
        import anthropic
        client = anthropic.Anthropic()
    else:
        import openai
        client = openai.OpenAI()

    messages: list[dict] = [{"role": "user", "content": f"ALERT: {alert}"}]

    for step in range(1, MAX_STEPS + 1):

        # ── call the model ──────────────────────────────────────────────────
        if prov == "anthropic":
            resp = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
            reasoning = "".join(b.text for b in resp.content if b.type == "text")
            done = resp.stop_reason != "tool_use"
            tool_calls = [
                (b.id, b.name, b.input)
                for b in resp.content
                if b.type == "tool_use"
            ]
            messages.append({"role": "assistant", "content": resp.content})

        else:  # openai
            oai_msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
            resp = client.chat.completions.create(
                model=model,
                max_completion_tokens=MAX_TOKENS,
                tools=_openai_tools(TOOL_SCHEMAS),
                messages=oai_msgs,
            )
            msg = resp.choices[0].message
            reasoning = msg.content or ""
            done = resp.choices[0].finish_reason != "tool_calls"
            tool_calls = [
                (tc.id, tc.function.name, json.loads(tc.function.arguments))
                for tc in (msg.tool_calls or [])
            ]
            # OpenAI requires tool_calls preserved in the assistant message
            messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [tc.model_dump() for tc in (msg.tool_calls or [])],
            })

        # ── surface reasoning ───────────────────────────────────────────────
        if verbose and reasoning.strip():
            _print_step(
                f"[step {step}] reasoning ({model})",
                safe_output(reasoning.strip()),
            )

        if done:
            return safe_output(reasoning)

        # ── execute tool calls ──────────────────────────────────────────────
        # Anthropic batches all results into one user message.
        # OpenAI uses one separate "tool" role message per result.
        anthropic_results: list[dict] = []

        for tc_id, tc_name, tc_input in tool_calls:
            if verbose:
                _print_step(f"[step {step}] tool: {tc_name}", _fmt_input(tc_input))

            observation = dispatch(tc_name, tc_input, cfg)
            observation = safe_output(observation)

            if verbose:
                print(f"\n  observation:\n{_indent(observation)}")

            if prov == "anthropic":
                anthropic_results.append(
                    {"type": "tool_result", "tool_use_id": tc_id, "content": observation}
                )
            else:
                messages.append(
                    {"role": "tool", "tool_call_id": tc_id, "content": observation}
                )

        if prov == "anthropic" and anthropic_results:
            messages.append({"role": "user", "content": anthropic_results})

    return (
        "[investigation hit MAX_STEPS without a conclusion — "
        "widen limits or refine the alert]"
    )
