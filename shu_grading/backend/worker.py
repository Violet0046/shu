"""独立阅卷进程：python -m shu_grading.backend.worker。"""
import argparse
import asyncio
import logging
import os
import secrets
import signal
from contextlib import suppress

from .errors import GradingFailure, LeaseLost, ServiceError
from .grading.base import GradingInput

logger = logging.getLogger(__name__)


class GradingWorker:
    def __init__(self, services, worker_id=None):
        self.services = services
        self.queue = services.queue
        self.id = worker_id or f'{os.getpid()}-{secrets.token_hex(6)}'

    async def _renew(self, job):
        while True:
            await asyncio.sleep(self.queue.lease_seconds / 3)
            if not self.queue.renew(job):
                raise LeaseLost()
            self.queue.heartbeat(self.id)

    async def _grade(self, job):
        snapshot = job['snapshot']
        owner = self.services.store.one('''SELECT u.active FROM users u JOIN tasks t ON t.owner=u.id
            JOIN submissions s ON s.task_id=t.id WHERE s.id=?''', (job['submission_id'],))
        if not owner or not owner['active']:
            raise GradingFailure('账号已停用，无法处理此答卷')
        grader = self.services.registry.resolve(snapshot['task_type'], snapshot['grader_version'])
        request = GradingInput(snapshot['assignment'], (self.services.files.path(snapshot['image']),), snapshot['configuration'])
        result = await grader.grade(request)
        result.presentation_deduction = 0
        return grader.validate_review(snapshot['configuration'], result)

    async def run_once(self):
        self.queue.heartbeat(self.id)
        job = self.queue.claim(self.id)
        if not job:
            return False
        logger.info('领取阅卷作业 %s，题型 %s，第 %s/%s 次尝试，开始调用评分实现',
                    job['id'], job['snapshot']['task_type'], job['attempts'], job['max_attempts'])
        grade = asyncio.create_task(self._grade(job))
        heartbeat = asyncio.create_task(self._renew(job))
        try:
            finished, _ = await asyncio.wait([grade, heartbeat], return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in finished:
                await heartbeat
            result = await grade
            if self.queue.complete(job, result):
                logger.info('阅卷作业 %s 已完成，结果已保存', job['id'])
            else:
                logger.warning('阅卷作业 %s 的租约已过期，未提交本次结果', job['id'])
        except LeaseLost:
            logger.info('阅卷租约已失效，丢弃当前执行结果：%s', job['id'])
        except asyncio.CancelledError:
            self.queue.abandon(job)
            raise
        except (GradingFailure, ServiceError) as exc:
            message = str(exc) if isinstance(exc, GradingFailure) else exc.message
            self.queue.fail(job, message, retryable=getattr(exc, 'retryable', False))
            logger.warning('阅卷作业 %s 未完成：%s', job['id'], message)
        except Exception:
            logger.exception('阅卷任务处理异常：%s', job['id'])
            self.queue.fail(job, '评分服务异常，请管理员检查服务日志')
        finally:
            for task in (grade, heartbeat):
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        return True

    async def run(self, concurrency=1):
        async def slot():
            while True:
                if not await self.run_once():
                    await asyncio.sleep(.5)
        await asyncio.gather(*(slot() for _ in range(concurrency)))


async def serve(concurrency):
    from .services import build_services
    worker = GradingWorker(build_services())
    runner = asyncio.create_task(worker.run(concurrency))
    loop = asyncio.get_running_loop()
    for name in ('SIGTERM', 'SIGINT'):
        with suppress(NotImplementedError):
            loop.add_signal_handler(getattr(signal, name), runner.cancel)
    try:
        await runner
    except asyncio.CancelledError:
        pass


def main():
    from ..config import load_environment
    load_environment()
    parser = argparse.ArgumentParser(description='启动独立阅卷工作进程')
    parser.add_argument('--concurrency', type=int, default=int(os.getenv('SHU_WORKER_CONCURRENCY', '2')))
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 16:
        parser.error('concurrency 必须为 1～16')
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(args.concurrency))


if __name__ == '__main__':
    main()
