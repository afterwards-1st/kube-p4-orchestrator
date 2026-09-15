"""读取预计完成时间算法使用的运行指标。"""

import json
from copy import deepcopy
from pathlib import Path
from typing import Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MAX_RESPONSE_BYTES = 1024 * 1024


class WorkerMetricsFetchError(RuntimeError):
    """动态 Worker 指标不可用或不可信。"""


def load_runtime_metrics(path: str) -> dict:
    """读取 JSON 指标；未配置路径时返回空字典以使用算法默认值。"""
    if not path:
        return {}
    metric_path = Path(path)
    if not metric_path.exists():
        return {}
    data = json.loads(metric_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("运行指标文件的根节点必须是 JSON 对象")
    return data


def metrics_from_worker_reports(
    payload: dict,
    allowed_backend_ids: Optional[Iterable[str]] = None,
) -> dict:
    """把后端上报快照转换为调度算法使用的动态指标覆盖层。"""
    if not isinstance(payload, dict):
        raise ValueError("Worker 指标响应必须是 JSON 对象")
    if payload.get("ok") is not True:
        raise ValueError("Worker 指标响应未标记为成功")
    reports = payload.get("backends", [])
    if not isinstance(reports, list):
        raise ValueError("Worker 指标响应的 backends 必须是数组")

    allowed = (
        {str(value) for value in allowed_backend_ids}
        if allowed_backend_ids is not None
        else None
    )
    backends = {}
    for report in reports:
        if not isinstance(report, dict) or report.get("fresh") is not True:
            continue
        backend_id = str(report.get("backend_id", "")).strip()
        if not backend_id or (allowed is not None and backend_id not in allowed):
            continue
        try:
            queue_depth = max(0.0, float(report.get("queue_depth", 0)))
        except (TypeError, ValueError):
            continue
        backends[backend_id] = {"queue_depth": queue_depth}
    return {"backends": backends}


def merge_runtime_metrics(baseline: dict, dynamic: dict) -> dict:
    """用动态指标覆盖静态基线；输入对象保持不变。"""
    if not isinstance(baseline, dict) or not isinstance(dynamic, dict):
        raise ValueError("运行指标基线和动态覆盖层都必须是 JSON 对象")
    merged = deepcopy(baseline)
    merged_backends = merged.setdefault("backends", {})
    if not isinstance(merged_backends, dict):
        raise ValueError("运行指标基线的 backends 必须是 JSON 对象")
    dynamic_backends = dynamic.get("backends", {})
    if not isinstance(dynamic_backends, dict):
        raise ValueError("动态运行指标的 backends 必须是 JSON 对象")
    for backend_id, values in dynamic_backends.items():
        if not isinstance(values, dict):
            continue
        current = merged_backends.setdefault(str(backend_id), {})
        if not isinstance(current, dict):
            current = {}
            merged_backends[str(backend_id)] = current
        current.update(deepcopy(values))
    return merged


def fetch_worker_metrics(
    url: str,
    token: str,
    allowed_backend_ids: Optional[Iterable[str]] = None,
    timeout_seconds: float = 2.0,
    opener=urlopen,
) -> dict:
    """从接收器读取并校验动态指标，返回调度算法的覆盖层。"""
    if not url:
        raise WorkerMetricsFetchError("未配置动态指标 URL")
    if not token:
        raise WorkerMetricsFetchError("未配置动态指标令牌")
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "X-Metrics-Token": token,
        },
    )
    try:
        with opener(
            request,
            timeout=max(0.1, float(timeout_seconds)),
        ) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise WorkerMetricsFetchError(
            "动态指标请求失败：{}".format(error)
        ) from error
    if len(body) > MAX_RESPONSE_BYTES:
        raise WorkerMetricsFetchError("动态指标响应超过 1 MiB 限制")
    try:
        payload = json.loads(body.decode("utf-8"))
        return metrics_from_worker_reports(payload, allowed_backend_ids)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise WorkerMetricsFetchError(
            "动态指标响应无效：{}".format(error)
        ) from error


def load_combined_runtime_metrics(
    baseline_path: str,
    metrics_url: str = "",
    token: str = "",
    allowed_backend_ids: Optional[Iterable[str]] = None,
    timeout_seconds: float = 2.0,
    fetcher=fetch_worker_metrics,
):
    """加载静态基线并尝试叠加动态指标，同时返回数据源状态。"""
    baseline = load_runtime_metrics(baseline_path)
    if not metrics_url:
        return baseline, {
            "source": "static",
            "dynamic_backend_count": 0,
            "error": "",
        }
    try:
        dynamic = fetcher(
            metrics_url,
            token,
            allowed_backend_ids,
            timeout_seconds,
        )
    except WorkerMetricsFetchError as error:
        return baseline, {
            "source": "static-fallback",
            "dynamic_backend_count": 0,
            "error": str(error),
        }
    count = len(dynamic.get("backends", {}))
    return merge_runtime_metrics(baseline, dynamic), {
        "source": "dynamic" if count else "static-no-fresh-data",
        "dynamic_backend_count": count,
        "error": "",
    }