import redis
import json
from typing import Optional
from dotenv import load_dotenv
import os 

load_dotenv('src/config/.env')

class SessionCache:
    def __init__(
        self,
        host: str = os.getenv("REDIS_HOST", "redis-server"),
        port: int = int(os.getenv("REDIS_PORT", "6379")),
        db: int = 1,
        ttl: int = 600,
    ):
        self._ttl = ttl
        try:
            self._redis = redis.Redis(
                host=host,
                port=port,
                db=db,
                decode_responses=True
            )
            self._redis.ping()
            self._fallback = None
            print(f"Redis session cache connected to {host}:{port} ✅ (db={db})")
        except Exception as e:
            print(f"⚠️ Redis unavailable, falling back to in-memory: {e}")
            self._redis = None
            self._fallback = {}

    def get(self, env_id: str) -> dict:
        if self._redis:
            try:
                val = self._redis.get(f"session:{env_id}")
                return json.loads(val) if val else {}
            except Exception as e:
                print(f"Redis GET error: {e}")
                return {}
        return self._fallback.get(env_id, {})

    def set(self, env_id: str, data: dict) -> None:
        if self._redis:
            try:
                self._redis.setex(
                    f"session:{env_id}",
                    self._ttl,
                    json.dumps(data, default=str)
                )
            except Exception as e:
                print(f"Redis SET error: {e}")
        else:
            self._fallback[env_id] = data

    def delete(self, env_id: str) -> None:
        if self._redis:
            try:
                self._redis.delete(f"session:{env_id}")
            except Exception as e:
                print(f"Redis DELETE error: {e}")
        else:
            self._fallback.pop(env_id, None)

    def exists(self, env_id: str) -> bool:
        if self._redis:
            try:
                return bool(self._redis.exists(f"session:{env_id}"))
            except Exception as e:
                print(f"Redis EXISTS error: {e}")
                return False
        return env_id in self._fallback