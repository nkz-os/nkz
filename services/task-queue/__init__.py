# Task Queue Module

try:
    from .task_queue import (
        Task,
        TaskQueue,
        get_task_queue,
        enqueue_task,
        TaskType
    )
except ImportError:
    from task_queue import (
        Task,
        TaskQueue,
        get_task_queue,
        enqueue_task,
        TaskType
    )

__all__ = [
    'Task',
    'TaskQueue',
    'get_task_queue',
    'enqueue_task',
    'TaskType',
]

