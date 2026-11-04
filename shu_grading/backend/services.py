"""唯一组装入口；网页进程与工作进程使用相同的服务和数据库配置。"""
import os
from dataclasses import dataclass
from pathlib import Path

from ..config import load_settings
from .auth import AuthService
from .capture import CaptureService
from .database import Store
from .files import LocalFileStorage
from .grading import default_registry
from .grading.base import GraderRegistry
from .queue import JobQueue
from .reports import ReportService
from .resources import ResourceService
from .submissions import SubmissionService
from .tasks import TaskService


@dataclass
class Services:
    store: Store
    auth: AuthService
    resources: ResourceService
    tasks: TaskService
    submissions: SubmissionService
    capture: CaptureService
    reports: ReportService
    queue: JobQueue
    files: LocalFileStorage
    registry: GraderRegistry


def build_services(data_dir=None, provider_factory=None, registry=None):
    root = Path(data_dir) if data_dir is not None else Path(os.getenv('SHU_WEB_DATA_DIR', str(load_settings().data_dir/'web')))
    store = Store(root)
    files = LocalFileStorage(root/'uploads')
    registry = registry if registry is not None else default_registry(provider_factory)
    lease = float(os.getenv('SHU_JOB_LEASE_SECONDS','120'))
    attempts = int(os.getenv('SHU_JOB_MAX_ATTEMPTS','3'))
    if lease < 1 or not 1 <= attempts <= 10:
        raise ValueError('租约至少 1 秒，任务尝试次数为 1～10')
    queue = JobQueue(store, lease_seconds=lease)
    tasks = TaskService(store, registry)
    submissions = SubmissionService(store, tasks, files, queue, registry, max_attempts=attempts)
    return Services(store, AuthService(store), ResourceService(store), tasks, submissions,
                    CaptureService(store, tasks, submissions), ReportService(store, tasks), queue, files, registry)
