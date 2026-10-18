# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: MIT
"""Ordered output callbacks, independent of realtime control-message reception."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class VoiceOutputQueue:
    """Preserve audio/end ordering; cancel the old generation on interruption.

    A response completing normally must drain, rather than cancel, its audio.
    The receiver never awaits this drain. Only interruption or shutdown discards
    pending callbacks, before the sink is reset for the next response.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()
        self._tail: asyncio.Task[None] | None = None
        self._generation = 0

    def submit(self, callback: Callable[[], Awaitable[None]]) -> asyncio.Task[None]:
        previous = self._tail
        generation = self._generation

        async def run() -> None:
            if previous is not None:
                # Shield so cancelling one queued callback cannot recursively
                # cancel an earlier callback from another owner.
                await asyncio.shield(previous)
            if generation == self._generation:
                try:
                    await callback()
                except Exception:
                    logger.warning("Voice output callback failed (details redacted)")

        task = asyncio.create_task(run(), name="voice-output")
        self._tasks.add(task)
        self._tail = task
        task.add_done_callback(self._tasks.discard)
        return task

    @property
    def busy(self) -> bool:
        return self._tail is not None and not self._tail.done()

    async def cancel(self) -> None:
        self._generation += 1
        current = asyncio.current_task()
        tasks = tuple(task for task in self._tasks if task is not current)
        self._tail = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.difference_update(tasks)
