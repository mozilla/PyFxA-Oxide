import pickle
import threading
import time
from unittest import mock

import fxa.errors
from fxa._utils import APIClient
from fxa.tests.utils import unittest


class FakeResponse:
    def __init__(self, status_code, headers, body):
        self.status_code = status_code
        self.headers = headers
        self._body = body
        self.request = None

    def json(self):
        return self._body


class FakeSession:
    headers = {}
    auth = None
    hooks = {}
    verify = True

    def request(self, method, url, json=None, **kwds):
        return FakeResponse(
            429, {"content-type": "application/json", "retry-after": "1"}, {}
        )


class TestAPIClientBackoffConcurrency(unittest.TestCase):
    def test_concurrent_backoff_writes_are_serialized(self):
        client = APIClient("https://server/v1", session=FakeSession())

        real_dumps = pickle.dumps
        lock = threading.Lock()
        in_critical_section = []
        max_concurrent = [0]

        def slow_dumps(obj, *args, **kwds):
            with lock:
                in_critical_section.append(1)
                max_concurrent[0] = max(max_concurrent[0], len(in_critical_section))
            time.sleep(0.05)
            with lock:
                in_critical_section.pop()
            return real_dumps(obj, *args, **kwds)

        barrier = threading.Barrier(4)

        def worker():
            barrier.wait()
            try:
                client.request("GET", "/thing")
            except fxa.errors.ClientError:
                pass

        with mock.patch("fxa._utils.pickle.dumps", side_effect=slow_dumps):
            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(max_concurrent[0], 1)
