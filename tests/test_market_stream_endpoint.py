import auto_trader.api.market as _api_market
import auto_trader.runtime as _runtime
import asyncio
import unittest
from unittest.mock import Mock, patch
from fastapi import HTTPException
from auto_trader.quote_stream import QuoteStream

class Request:
    async def is_disconnected(self): return False

class MarketStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_twenty_symbols_share_batch_and_cleanup(self):
        await self.check_batch_size(20)

    async def test_hundred_symbols_share_batch_and_cleanup(self):
        await self.check_batch_size(100)

    async def check_batch_size(self, count):
        hub=QuoteStream(Mock(),"wss://example.invalid")
        async def idle(): await asyncio.Event().wait()
        hub.run=idle
        codes=[f"{i:06}" for i in range(count)]
        with patch.object(_runtime,'quote_stream',hub), patch.object(_api_market,'require_user',return_value=Mock()):
            response=await _api_market.live_market_stream(Request(),','.join(codes),None)
            iterator=response.body_iterator
            await anext(iterator)
            self.assertEqual(len(hub.listeners),count)
            for code in codes: hub.publish(code,{'type':'message','topic':'trade:kr:'+code,'data':{'price':100}})
            batch=await anext(iterator)
            self.assertEqual(batch.count('"type": "message"'),count)
            await iterator.aclose()
            self.assertEqual(hub.listeners,{})
            self.assertIsNone(hub.task)

    async def test_invalid_codes_rejected(self):
        for codes in ('','../bad',','.join(f"{i:06}" for i in range(101))):
            with self.assertRaises(HTTPException):
                await _api_market.live_market_stream(Request(),codes,None)
