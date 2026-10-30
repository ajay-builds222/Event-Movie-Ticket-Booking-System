
import time
import fnmatch
import threading

class MemoryRedis:
    def __init__(self):
        self.data = {}
        self.lock = threading.RLock()

    def _clean(self, key):
        item = self.data.get(key)
        if item and item["expires"] is not None:
            if item["expires"] <= time.time():
                del self.data[key]
                return None
        return item

    def get(self, key):
        with self.lock:
            item = self._clean(key)
            return item["value"] if item else None

    def set(self, key, value, nx=False, ex=None):
        with self.lock:
            if nx and self._clean(key) is not None:
                return None
            expires = time.time() + ex if ex is not None else None
            self.data[key] = {"value": str(value), "expires": expires}
            return True

    def setex(self, key, seconds, value):
        return self.set(key, value, ex=seconds)

    def incr(self, key):
        with self.lock:
            item = self._clean(key)
            value = int(item["value"]) + 1 if item else 1
            expires = item["expires"] if item else None
            self.data[key] = {"value": str(value), "expires": expires}
            return value

    def expire(self, key, seconds):
        with self.lock:
            item = self._clean(key)
            if not item:
                return False
            item["expires"] = time.time() + seconds
            return True

    def ttl(self, key):
        with self.lock:
            item = self._clean(key)
            if not item:
                return -2
            if item["expires"] is None:
                return -1
            return max(0, int(item["expires"] - time.time()))

    def delete(self, *keys):
        count = 0
        with self.lock:
            for key in keys:
                if self._clean(key) is not None:
                    del self.data[key]
                    count += 1
        return count

    def scan_iter(self, match="*"):
        with self.lock:
            keys = list(self.data.keys())
        for key in keys:
            with self.lock:
                if self._clean(key) is not None and fnmatch.fnmatch(key, match):
                    yield key

    def ping(self):
        return True


redis_client = MemoryRedis()

def ping_redis():
    return redis_client.ping()
