"""Smoke-test stand-in for the Edge window: ping like the page does, then 'close'."""
import json, sys, time, urllib.request
url = next(a for a in sys.argv if a.startswith('--app=')).split('=', 1)[1].split('?')[0].rstrip('/')
state = json.load(urllib.request.urlopen(url + '/api/state', timeout=10))
for _ in range(3):
    req = urllib.request.Request(url + '/api/shell/ping', data=b'{}', method='POST',
                                 headers={'x-session-token': state['token'], 'content-type': 'application/json'})
    urllib.request.urlopen(req, timeout=10)
    time.sleep(3)
print('fake window closing', flush=True)
