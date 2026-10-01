import asyncio
import unittest
from unittest.mock import Mock, patch
from fastapi import HTTPException
from auto_trader import main
from auto_trader.quote_stream import QuoteStream

class Request:
    async def is_disconnected(self): return False

class MarketStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_twenty_symbols_share_batch_and_cleanup(self):
        hub=QuoteStream(Mock(),"wss://example.invalid")
        async def idle(): await asyncio.Event().wait()
        hub.run=idle
        codes=[f"{i:06}" for i in range(20)]
        with patch.object(main,'quote_stream',hub), patch.object(main,'require_user',return_value=Mock()):
            response=await main.live_market_stream(Request(),','.join(codes),None)
            iterator=response.body_iterator
            await anext(iterator)
            self.assertEqual(len(hub.listeners),20)
            for code in codes: hub.publish(code,{'type':'message','topic':'trade:kr:'+code,'data':{'price':100}})
            batch=await anext(iterator)
            self.assertEqual(batch.count('"type": "message"'),20)
            await iterator.aclose()
            self.assertEqual(hub.listeners,{})
            self.assertIsNone(hub.task)

    async def test_invalid_codes_rejected(self):
        for codes in ('','../bad',','.join(f"{i:06}" for i in range(21))):
            with self.assertRaises(HTTPException):
                await main.live_market_stream(Request(),codes,None)
