import asyncio
import contextlib
import io
import logging
import os

import pytest
import aioredis

from arq import arq_mode

from .fixtures import Demo


@contextlib.contextmanager
def loop_context(existing_loop=None):
    if existing_loop:
        # loop already exists, pass it straight through
        yield existing_loop
    else:
        _loop = asyncio.new_event_loop()

        yield _loop

        _loop.stop()
        _loop.run_forever()
        _loop.close()


def pytest_pycollect_makeitem(collector, name, obj):
    """
    Fix pytest collecting for coroutines.
    """
    if collector.funcnamefilter(name) and asyncio.iscoroutinefunction(obj):
        return list(collector._genfunctions(name, obj))


def pytest_pyfunc_call(pyfuncitem):
    """
    Run coroutines in an event loop instead of a normal function call.
    """
    if asyncio.iscoroutinefunction(pyfuncitem.function):
        existing_loop = pyfuncitem.funcargs.get('loop', None)
        with loop_context(existing_loop) as _loop:
            testargs = {arg: pyfuncitem.funcargs[arg]
                        for arg in pyfuncitem._fixtureinfo.argnames}

            task = _loop.create_task(pyfuncitem.obj(**testargs))
            _loop.run_until_complete(task)

        return True


@pytest.yield_fixture
def loop():
    with loop_context() as _loop:
        yield _loop


@pytest.yield_fixture
def debug_logger():
    handler = logging.StreamHandler()
    logger = logging.getLogger('.')
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    yield

    logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)


@pytest.yield_fixture
def tmpworkdir(tmpdir):
    """
    Create a temporary working working directory.
    """
    cwd = os.getcwd()
    os.chdir(tmpdir.strpath)

    yield tmpdir

    os.chdir(cwd)


@pytest.yield_fixture
def redis_conn(loop):
    conn = None

    async def _get_conn():
        nonlocal conn
        conn = await aioredis.create_redis(('localhost', 6379), loop=loop)
        await conn.flushall()
        return conn

    yield _get_conn

    async def _flush():
        _conn = conn or await _get_conn()
        await _conn.flushall()
        _conn.close()
        await _conn.wait_closed()

    loop.run_until_complete(_flush())


class StreamLog:
    def __init__(self):
        self.logger = self.stream = self.handler = None
        self.set_logger()

    def set_logger(self, log_name='', level=logging.DEBUG):
        if self.logger is not None:
            self.finish()
        self.logger = logging.getLogger(log_name)
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(stream=self.stream)
        self.logger.addHandler(self.handler)
        self.set_level(level)

    def set_level(self, level):
        self.logger.setLevel(level)

    @property
    def log(self):
        self.stream.seek(0)
        return self.stream.read()

    def finish(self):
        self.logger.removeHandler(self.handler)


@pytest.yield_fixture
def logcap():
    stream_log = StreamLog()

    yield stream_log

    stream_log.finish()


@pytest.yield_fixture
def create_demo(loop):
    arq_mode.set_redis()
    demo = None

    async def _create():
        nonlocal demo
        demo = Demo(loop=loop)
        return demo

    yield _create

    async def _finish():
        if demo:
            await demo.close()
    loop.run_until_complete(_finish())
