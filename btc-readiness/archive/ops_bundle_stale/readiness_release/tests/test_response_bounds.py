import io,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'candidate'))
import kalshi_readonly as k
import btc_copilot as b
class ResponseBounds(unittest.TestCase):
 def test_response_limit_exact_and_overflow(self):
  self.assertEqual(len(k.bounded_response(io.BytesIO(b' '*k.MAX_RESPONSE_BYTES))),k.MAX_RESPONSE_BYTES)
  with self.assertRaises(k.ReadError):k.bounded_response(io.BytesIO(b' '*(k.MAX_RESPONSE_BYTES+1)))
 def test_invalid_utf8_fails_closed(self):
  with self.assertRaises(k.ReadError):k.bounded_response(io.BytesIO(b'\xff'))
 def test_both_transports_use_bounded_reads(self):
  class Response(io.BytesIO):
   status=200;headers={}
   def read(self,size=-1):
    self.requested_size=size
    if size<0:raise AssertionError('Unbounded response read')
    return super().read(size)
  for source in ('kalshi','coinbase'):
   with self.subTest(source=source):
    response=Response(b'{}');opener=unittest.mock.Mock();opener.open.return_value=response
    if source=='kalshi':
     c=k.KalshiReadOnly();c.opener=opener
     with patch('study_policy.launch_guard'),patch('request_pacing.before_read'):c.get('/exchange/status')
    else:
     with patch.object(b,'build_opener',return_value=opener):b.spot_get()
    self.assertEqual(response.requested_size,k.MAX_RESPONSE_BYTES+1)
if __name__=='__main__':unittest.main(verbosity=2)
