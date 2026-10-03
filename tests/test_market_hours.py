import unittest
from datetime import datetime
from unittest.mock import patch
from auto_trader.market_hours import regular_status, MarketHours

def at(value): return datetime.fromisoformat(value)
def session(start, end): return {'startTime':start,'endTime':end}

class MarketHoursTests(unittest.TestCase):
    def test_holiday_next_open_and_close_time(self):
        calendar={'today':{'date':'2026-10-03','integrated':None},'nextBusinessDay':{'integrated':{'regularMarket':session('2026-10-05T09:00:00+09:00','2026-10-05T15:30:00+09:00')}}}
        status=regular_status(calendar,'KR',at('2026-10-03T12:00:00+09:00'))
        self.assertTrue(status['holiday'])
        self.assertEqual(status['next_open_at'],'2026-10-05T09:00:00+09:00')
        self.assertIsNone(status['closes_at'])
        status=regular_status(calendar,'KR',at('2026-10-05T09:00:00+09:00'))
        self.assertEqual(status['state'],'open')
        self.assertEqual(status['closes_at'],'2026-10-05T15:30:00+09:00')

    def test_current_client_cache_and_us_local_date(self):
        class Client:
            base_url='https://example.test'
            def __init__(self): self.paths=[]
            def _authorized_json_request(self,path):
                self.paths.append(path)
                return {'result':{'today':{'integrated':None,'regularMarket':None}}}
        original,current,replacement=Client(),Client(),Client()
        service=MarketHours(original)
        with patch('auto_trader.market_hours.datetime') as clock:
            clock.now.return_value=at('2026-10-03T01:00:00+09:00')
            service.status('US',current)
            service.status('US',current)
            service.status('US',replacement)
            service.status('KR',current)
        self.assertEqual(original.paths,[])
        self.assertEqual(len(current.paths),2)
        self.assertIn('US?date=2026-10-02',current.paths[0])
        self.assertIn('KR?date=2026-10-03',current.paths[1])
        self.assertEqual(len(replacement.paths),1)

    def test_regular_boundaries_and_holiday(self):
        calendar={'today':{'integrated':{'regularMarket':session('2026-03-25T09:00:00+09:00','2026-03-25T15:30:00+09:00')}}}
        self.assertEqual(regular_status(calendar,'KR',at('2026-03-25T09:00:00+09:00'))['state'],'open')
        self.assertEqual(regular_status(calendar,'KR',at('2026-03-25T15:30:00+09:00'))['state'],'closed')
        self.assertEqual(regular_status({'today':{'integrated':None}},'KR',at('2026-03-25T10:00:00+09:00'))['state'],'closed')

    def test_us_previous_session_after_midnight_and_early_close(self):
        calendar={'today':{'regularMarket':None},'previousBusinessDay':{'regularMarket':session('2026-07-02T22:30:00+09:00','2026-07-03T02:00:00+09:00')}}
        self.assertEqual(regular_status(calendar,'US',at('2026-07-03T01:00:00+09:00'))['state'],'open')
        self.assertEqual(regular_status(calendar,'US',at('2026-07-03T02:00:00+09:00'))['state'],'closed')

    def test_offset_time_and_no_guess_for_failure(self):
        calendar={'today':{'regularMarket':session('2026-12-01T23:30:00+09:00','2026-12-02T06:00:00+09:00')}}
        self.assertEqual(regular_status(calendar,'US',at('2026-12-01T15:00:00+00:00'))['state'],'open')
        class BrokenClient:
            base_url='example'
            def _authorized_json_request(self,path): raise RuntimeError('unavailable')
        self.assertEqual(MarketHours(BrokenClient()).status('KR')['state'],'unknown')
        with self.assertRaises(ValueError): regular_status({'today':{'integrated':{}}},'KR',at('2026-03-25T09:00:00+09:00'))

if __name__=='__main__': unittest.main()
