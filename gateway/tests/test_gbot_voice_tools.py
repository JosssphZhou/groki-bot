# SPDX-License-Identifier: MIT
import asyncio
from unittest.mock import AsyncMock

import pytest

from stackchan_mcp.gbot_voice_bridge import GbotVoiceBridge


class Executor:
    def __init__(self):
        self.execute = AsyncMock(return_value={"ok": True})
        self.close = AsyncMock()
        self.observe_user_input = AsyncMock()

    def declarations(self):
        return [
            {
                "name": "self.tracking.start",
                "description": "Track",
                "parameters": {"type": "object"},
            },
            {
                "name": "ask_grokbot",
                "description": "Delegate",
                "parameters": {
                    "type": "object",
                    "properties": {"task": {"type": "string"}},
                    "required": ["task"],
                },
            },
        ]

    def instructions(self, **kwargs):
        assert kwargs == {"model_search": False}
        return "Shared local instructions"


def make(monkeypatch, enabled=True):
    monkeypatch.setenv("STACKCHAN_TOOL_BOT", "desktop" if enabled else "")
    monkeypatch.setenv("STACKCHAN_TOOL_BOT_ID", "agent")
    b = GbotVoiceBridge(
        on_audio=AsyncMock(),
        on_turn_complete=AsyncMock(),
        on_interrupt=AsyncMock(),
        on_session_dead=AsyncMock(),
        agent_id="agent",
        tool_executor=Executor(),
    )
    b._send = AsyncMock()
    b.running = True
    b._configure_tools(
        {
            "tools": [
                {
                    "type": "function",
                    "name": "send_task",
                    "parameters": {
                        "type": "object",
                        "properties": {"request": {"type": "string"}},
                        "required": ["request"],
                    },
                }
            ]
        }
    )
    return b


@pytest.mark.asyncio
async def test_advertised_alias_dispatches_original_local_name(monkeypatch):
    b = make(monkeypatch)
    name = next(
        name
        for name, original in b._local_tools.items()
        if original == "self.tracking.start"
    )
    assert "." not in name
    await b._tool({"name": name, "call_id": "one", "arguments": "{}"})
    b._executor.execute.assert_awaited_once_with("self.tracking.start", {})
    await b.stop()


@pytest.mark.asyncio
async def test_native_alias_once_per_real_user_and_no_result_recursion(monkeypatch):
    b = make(monkeypatch)
    b._local = AsyncMock(return_value={"receipt": "accepted"})
    await b._handle_event({"type": "input_audio_buffer.speech_started"})
    event = {"name": "ask_grokbot", "call_id": "one", "arguments": '{"task":"lookup"}'}
    await b._tool(event)
    await b._tool(event)
    assert b._local.await_count == 1
    assert b._local.call_args.args[1]["name"] == "send_task"
    assert b._local.call_args.args[1]["input"] == {"request": "lookup"}
    await b._native_update(
        {
            "type": "async_update.add",
            "async_update": {"topic": "work_landed", "payload": []},
        },
        False,
    )
    await b._tool({**event, "call_id": "two"})
    assert b._local.await_count == 1
    b._executor.execute.assert_not_awaited()
    await b.stop()


@pytest.mark.asyncio
async def test_slow_tool_does_not_block_interrupt_and_stop_drops_late_results(
    monkeypatch,
):
    b = make(monkeypatch)
    entered = asyncio.Event()

    async def slow(*args):
        entered.set()
        await asyncio.Event().wait()

    b._executor.execute = slow
    name = next(iter(b._local_tools))
    await b._handle_event(
        {
            "type": "response.function_call_arguments.done",
            "name": name,
            "call_id": "slow",
            "arguments": "{}",
        }
    )
    await entered.wait()
    await asyncio.wait_for(
        b._handle_event({"type": "input_audio_buffer.speech_started"}), 0.2
    )
    await b.stop()
    count = b._send.await_count
    await b._native_update({"type": "async_update.add"}, False)
    assert b._send.await_count == count


@pytest.mark.asyncio
async def test_function_result_waits_for_active_response_before_create(monkeypatch):
    b = make(monkeypatch)
    await b._handle_event({"type": "response.created", "response": {"id": "active"}})
    await b._tool(
        {"name": next(iter(b._local_tools)), "call_id": "tool", "arguments": "{}"}
    )
    assert not any(
        c.args[0]["type"] == "response.create" for c in b._send.call_args_list
    )
    await b._handle_event({"type": "response.done", "response": {"id": "active"}})
    assert any(c.args[0]["type"] == "response.create" for c in b._send.call_args_list)
    await b.stop()


@pytest.mark.asyncio
async def test_internal_speech_does_not_grant_desktop_permission(monkeypatch):
    b = make(monkeypatch)
    b._local = AsyncMock()
    await b.inject_user_text("read a task result")
    await b._tool(
        {"name": "ask_grokbot", "call_id": "one", "arguments": '{"task":"do more"}'}
    )
    b._local.assert_not_awaited()
    await b.stop()


def test_desktop_requires_explicit_matching_target(monkeypatch):
    b = make(monkeypatch, enabled=False)
    assert "ask_grokbot" not in b._local_tools
    assert "send_task" not in b._remote_tools


def test_alias_collision_fails_closed(monkeypatch):
    b = make(monkeypatch)
    b._executor.declarations = lambda: [
        {"name": "self.a", "parameters": {}},
        {"name": "self_a", "parameters": {}},
    ]
    with pytest.raises(ValueError, match="collision"):
        b._configure_tools({})


def test_diagnostics_disable_even_native_end_and_extra_tools(monkeypatch):
    b = make(monkeypatch)
    b._local_tools_enabled = False
    b._on_end_conversation = AsyncMock()
    monkeypatch.setenv("STACKCHAN_GBOT_VOICE_TOOLS", "1")
    assert (
        b._configure_tools(
            {"tools": [{"name": "end_the_call"}, {"name": "search_conversations"}]}
        )
        == []
    )


@pytest.mark.asyncio
async def test_native_update_waits_for_user_turn_and_never_creates_response(
    monkeypatch,
):
    b = make(monkeypatch)
    await b._handle_event({"type": "response.created", "response": {"id": "speech"}})
    update = {
        "type": "async_update.add",
        "async_update": {"topic": "work_landed", "payload": []},
    }
    await b._native_update(update, False)
    b._send.assert_not_awaited()
    await b._handle_event({"type": "response.done", "response": {"id": "speech"}})
    b._send.assert_awaited_once_with(update)
    await b.stop()


@pytest.mark.asyncio
async def test_native_task_survives_ordinary_interrupt(monkeypatch):
    b = make(monkeypatch)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def send(*args):
        entered.set()
        await finish.wait()
        return {"receipt": "accepted"}

    b._local = send
    await b._handle_event({"type": "input_audio_buffer.speech_started"})
    await b._handle_event(
        {
            "type": "response.function_call_arguments.done",
            "name": "ask_grokbot",
            "call_id": "task",
            "arguments": '{"task":"fixture"}',
        }
    )
    await entered.wait()
    await b.interrupt()
    assert any(not task.done() for task in b._tools)
    finish.set()
    await asyncio.gather(*b._tools)
    assert any(
        call.args[0]["type"] == "conversation.item.create"
        for call in b._send.call_args_list
    )
    await b.stop()


def test_executor_factory_gets_live_callback_options(monkeypatch):
    b = make(monkeypatch)
    b._executor = None
    observed = {}

    def factory(**options):
        observed.update(options)
        return Executor()

    b._executor_factory = factory
    b._configure_tools({})
    assert observed["announce_result"] == b._announce_result
    assert observed["cancel_idle"] == b._cancel_idle
    assert "wake_gate_state_getter" in observed


@pytest.mark.asyncio
async def test_idle_callback_uses_same_end_hook_and_keepalive_does_not_cancel(
    monkeypatch,
):
    b = make(monkeypatch)
    b._on_end_conversation = AsyncMock()
    b._conversation_idle_timeout_s = 0.01
    await b._finish_turn()
    await b.send_keepalive_audio(b"\0\0" * 160)
    await asyncio.sleep(0.03)
    b._on_end_conversation.assert_awaited_once()
    await b.stop()


@pytest.mark.asyncio
async def test_real_input_cancels_idle_and_is_forwarded_to_extensions(monkeypatch):
    b = make(monkeypatch)
    b._on_end_conversation = AsyncMock()
    b._conversation_idle_timeout_s = 0.01
    await b._finish_turn()
    await b.send_text("real request")
    await asyncio.sleep(0.03)
    b._on_end_conversation.assert_not_awaited()
    b._executor.observe_user_input.assert_awaited_once_with(
        "real request", finished=True
    )
    await b.stop()
