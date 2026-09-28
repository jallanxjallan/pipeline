from __future__ import annotations

import json
import socket
from dataclasses import dataclass

FORENSIC_TTL = 30 * 24 * 60 * 60
EXECUTOR_QUEUE_KEY = "queue:executor"
WORKER_QUEUE_KEY = "queue:worker"


class RedisError(RuntimeError):
    pass


class RedisClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 6379, timeout: float = 2.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    @staticmethod
    def _encode(parts: tuple[object, ...]) -> bytes:
        encoded = [f"*{len(parts)}\r\n".encode()]
        for part in parts:
            data = str(part).encode("utf-8")
            encoded.append(f"${len(data)}\r\n".encode())
            encoded.append(data + b"\r\n")
        return b"".join(encoded)

    @staticmethod
    def _readline(f) -> bytes:
        line = f.readline()
        if not line.endswith(b"\r\n"):
            raise RedisError("short Redis reply")
        return line[:-2]

    @classmethod
    def _read_reply(cls, f):
        prefix = f.read(1)
        if prefix == b"+":
            return cls._readline(f).decode("utf-8")
        if prefix == b":":
            return int(cls._readline(f))
        if prefix == b"$":
            length = int(cls._readline(f))
            if length == -1:
                return None
            data = f.read(length)
            if f.read(2) != b"\r\n":
                raise RedisError("malformed bulk reply")
            return data.decode("utf-8")
        if prefix == b"*":
            length = int(cls._readline(f))
            if length == -1:
                return None
            return [cls._read_reply(f) for _ in range(length)]
        if prefix == b"-":
            raise RedisError(cls._readline(f).decode("utf-8"))
        raise RedisError(f"unsupported Redis reply prefix: {prefix!r}")

    def command(self, *parts: object):
        with socket.create_connection((self.host, self.port), self.timeout) as s:
            s.sendall(self._encode(parts))
            f = s.makefile("rb")
            return self._read_reply(f)

    def ping(self) -> None:
        if self.command("PING") != "PONG":
            raise RedisError("Redis PING did not return PONG")

    def hset(self, key: str, fields: dict[str, object]) -> None:
        parts: list[object] = ["HSET", key]
        for name, value in fields.items():
            parts.extend((name, value))
        self.command(*parts)

    def expire(self, key: str, seconds: int) -> None:
        self.command("EXPIRE", key, seconds)

    def hgetall(self, key: str) -> dict[str, str]:
        values = self.command("HGETALL", key)
        if values is None:
            return {}
        if not isinstance(values, list) or len(values) % 2:
            raise RedisError("malformed HGETALL reply")
        return {str(values[i]): str(values[i + 1]) for i in range(0, len(values), 2)}

    def zrange(self, key: str, start: int, stop: int) -> list[str]:
        values = self.command("ZRANGE", key, start, stop)
        if values is None:
            return []
        if not isinstance(values, list):
            raise RedisError("malformed ZRANGE reply")
        return [str(value) for value in values]

    def zadd(self, key: str, score: int | float, member: str) -> None:
        self.command("ZADD", key, score, member)

    def zrem(self, key: str, member: str) -> None:
        self.command("ZREM", key, member)


@dataclass(frozen=True)
class MaterializedCall:
    call_id: str
    content_key: str
    baggage_key: str


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def store_json(client: RedisClient, key: str, value: dict, ttl: int = FORENSIC_TTL) -> str:
    client.hset(key, {"json": _json(value)})
    client.expire(key, ttl)
    return key


def load_json(client: RedisClient, key: str) -> dict:
    record = client.hgetall(key)
    if not record:
        raise RedisError(f"missing Redis hash: {key}")
    raw = record.get("json")
    if raw is None:
        raise RedisError(f"Redis hash does not contain json: {key}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RedisError(f"invalid JSON in Redis hash: {key}") from exc
    if not isinstance(value, dict):
        raise RedisError(f"Redis JSON value must be an object: {key}")
    return value


def materialize_call(client: RedisClient, call_id: str, content: dict, baggage: dict) -> MaterializedCall:
    client.ping()
    content_key = f"call:{call_id}:content"
    baggage_key = f"call:{call_id}:baggage"
    store_json(client, content_key, content)
    store_json(client, baggage_key, baggage)
    return MaterializedCall(call_id=call_id, content_key=content_key, baggage_key=baggage_key)


def enqueue_call(client: RedisClient, call_id: str) -> None:
    # Queue membership is the execution fact; there is no mutable call state flag.
    client.zadd(EXECUTOR_QUEUE_KEY, 0, call_id)
