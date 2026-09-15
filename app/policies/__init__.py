"""多 Worker 调度策略入口。"""

from app.policies.completion_time_scheduler import choose_by_completion_time

__all__ = ["choose_by_completion_time"]
