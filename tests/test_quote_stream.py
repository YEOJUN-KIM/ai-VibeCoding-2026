import asyncio
import unittest
from unittest.mock import Mock
from auto_trader.quote_stream import QuoteStream

class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_subscriptions_latest_tick_and_cleanup(self):
        hub=QuoteStream(Mock(),"wss://example.invalid")
        async def idle():await asyncio.Event().wait()
        hub.run=idle
        first=hub.subscribe("005930")
        task=hub.task
        second=hub.subscribe("005930")
        self.assertIs(hub.task,task)
        other=hub.subscribe("069500")
        hub.publish("005930",{"price":1})
        hub.publish("005930",{"price":2})
        self.assertEqual(await first.get(),{"price":2})
        self.assertEqual(await second.get(),{"price":2})
        self.assertEqual((await other.get())["type"],"status")
        await hub.unsubscribe("005930",first)
        self.assertIn("005930",hub.listeners)
        await hub.unsubscribe("005930",second)
        self.assertNotIn("005930",hub.listeners)
        await hub.unsubscribe("069500",other)
        self.assertIsNone(hub.task)
