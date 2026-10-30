"""Smoke-check FastAPI composition and worker lifetime without external I/O."""

import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from auto_trader import lifecycle, main, runtime
from auto_trader.api import pages


class ApplicationCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, path, method="GET", extra_headers=()):
        messages = []
        async def receive():
            await asyncio.Event().wait()
        async def send(message):
            messages.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": method, "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "headers": [(b"host", b"localhost"), *extra_headers],
                 "server": ("localhost", 80), "client": ("127.0.0.1", 1234)}
        await main.app(scope, receive, send)
        return next(m for m in messages if m["type"] == "http.response.start")

    async def test_pages_authentication_readiness_and_security_middleware(self):
        for path in ("/account", "/live/orders/real/readiness", "/openapi.json", "/docs"):
            with self.subTest(path=path):
                self.assertEqual((await self.request(path))["status"], 401)
        self.assertEqual((await self.request("/stocks"))["status"], 303)
        with patch.object(pages, "connect"):
            response = await self.request("/startup/readiness")
        self.assertEqual(response["status"], 200)
        self.assertIn((b"x-frame-options", b"DENY"), response["headers"])
        response = await self.request("/live/orders/real", "POST", [(b"origin", b"https://elsewhere.invalid")])
        self.assertEqual(response["status"], 403)

    async def test_lifespan_initializes_and_closes_workers_without_starting_trading(self):
        worker = lambda: NS(start=AsyncMock(), stop=AsyncMock())
        recorder = worker()
        candidates = worker()
        collection, quality, macro = worker(), worker(), worker()
        broker = Mock(snapshot_metadata={})
        engine = Mock()
        stream = NS(close=AsyncMock())
        workspace = NS(user_id=None, broker=broker, engine=engine, quote_stream=stream)
        stop_engines = AsyncMock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(lifecycle, "initialize"))
            stack.enter_context(patch.object(lifecycle, "initialize_ownership", return_value=None))
            for name, value in {"legacy_workspace": workspace, "broker": broker, "engine": engine,
                                "toss_client": NS(configured=False), "workspaces": NS(items={}),
                                "paper_state": Mock(return_value=workspace), "ml_decision_recorder": recorder,
                                "ml_candidate_recorder": candidates,
                                "ml_collection_worker": collection, "ml_quality_worker": quality,
                                "ml_macro_worker": macro, "_stop_all_paper_engines": stop_engines,
                                "_restore_paper_strategy": Mock(return_value=None)}.items():
                stack.enter_context(patch.object(runtime, name, value))
            async with main.app.router.lifespan_context(main.app):
                broker.initialize.assert_called_once()
                recorder.start.assert_awaited_once()
                candidates.start.assert_awaited_once()
                engine.start.assert_not_called()
            recorder.stop.assert_awaited_once()
            candidates.stop.assert_awaited_once()
            stop_engines.assert_awaited_once()
            stream.close.assert_awaited_once()
            for task in (collection, quality, macro):
                task.start.assert_not_awaited()
                task.stop.assert_awaited_once()
