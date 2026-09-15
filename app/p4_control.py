"""114 单写者 P4 HTTP API 的小型客户端。"""

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def normalize_routes(routes) -> list:
    """把 API/状态中的路由规范化为按 flow_id 排序的整数列表。"""
    normalized = []
    for item in routes or []:
        if not isinstance(item, dict):
            raise ValueError("route must be an object")
        normalized.append({
            "flowId": int(item.get("flowId", item.get("flow_id"))),
            "backendId": int(item.get("backendId", item.get("backend_id"))),
        })
    normalized.sort(key=lambda item: item["flowId"])
    return normalized


class P4ControlClient:
    """通过 HTTP 调用 114；绝不直接连接 BMv2 Thrift。"""

    def __init__(
        self,
        base_url: str,
        token: str = "",
        timeout_seconds: float = 2.0,
        cache_seconds: float = 5.0,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.token = token
        self.timeout_seconds = float(timeout_seconds)
        self.cache_seconds = max(0.0, float(cache_seconds))
        self._cached_routes = None
        self._cache_deadline = 0.0

    def _request(self, method: str, path: str, payload=None) -> dict:
        if not self.base_url:
            return {"ok": False, "error": "P4 control URL is empty"}
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["X-P4-Control-Token"] = self.token
        request = Request(
            "{}{}".format(self.base_url, path),
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                result = json.loads(raw) if raw else {}
        except HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            try:
                result = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                result = {}
            result.setdefault("ok", False)
            result.setdefault("error", "P4 control HTTP {}".format(error.code))
            result["httpStatus"] = error.code
            return result
        except (URLError, TimeoutError, OSError) as error:
            return {"ok": False, "error": "P4 control unavailable: {}".format(error)}
        except (ValueError, json.JSONDecodeError) as error:
            return {"ok": False, "error": "invalid P4 control response: {}".format(error)}
        if not isinstance(result, dict):
            return {"ok": False, "error": "P4 control response must be an object"}
        return result

    def get_routes(self, force: bool = False) -> dict:
        now = time.monotonic()
        if not force and self._cached_routes is not None and now < self._cache_deadline:
            return dict(self._cached_routes)
        result = self._request("GET", "/v1/routes")
        if result.get("ok"):
            self._cached_routes = dict(result)
            self._cache_deadline = now + self.cache_seconds
        return result

    def replace_routes(self, routes: list, reason: str) -> dict:
        payload_routes = [
            {
                "flow_id": item["flowId"],
                "backend_id": item["backendId"],
            }
            for item in normalize_routes(routes)
        ]
        result = self._request(
            "POST",
            "/v1/routes/replace",
            {"routes": payload_routes, "reason": reason},
        )
        if result.get("ok"):
            # replace 响应不保证带 control_mode；下一轮重新读取权威状态。
            self._cached_routes = None
            self._cache_deadline = 0.0
        return result
