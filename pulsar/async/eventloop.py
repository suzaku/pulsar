import os
import sys
import socket
import errno
from types import GeneratorType
from heapq import heappop, heappush
from threading import current_thread, Lock

from pulsar.utils.system import close_on_exec
from pulsar.utils.pep import range
from pulsar.utils.exceptions import ImproperlyConfigured

from .access import asyncio, BaseEventLoop, thread_data, LOGGER
from .futures import Future, maybe_async, async, Task


__all__ = ['EventLoop', 'call_repeatedly']


def file_descriptor(fd):
    if hasattr(fd, 'fileno'):
        return fd.fileno()
    else:
        return fd


def setid(self):
    ct = current_thread()
    self.tid = ct.ident
    self.pid = os.getpid()
    return ct


def set_as_loop(loop):
    if loop._iothreadloop:
        asyncio.set_event_loop(loop)


class EventLoopPolicy(asyncio.AbstractEventLoopPolicy):
    '''Pulsar event loop policy'''
    def get_event_loop(self):
        return thread_data('_event_loop')

    def get_request_loop(self):
        return thread_data('_request_loop') or self.get_event_loop()

    def new_event_loop(self):
        return EventLoop()

    def set_event_loop(self, event_loop):
        """Set the event loop."""
        assert event_loop is None or isinstance(event_loop,
                                                asyncio.AbstractEventLoop)
        if isinstance(event_loop, QueueEventLoop):
            thread_data('_request_loop', event_loop)
        else:
            thread_data('_event_loop', event_loop)


asyncio.set_event_loop_policy(EventLoopPolicy())


Handle = asyncio.Handle
TimerHandle = asyncio.TimerHandle


class LoopingCall(object):

    def __init__(self, loop, callback, args, interval=None):
        self._loop = loop
        self.callback = callback
        self.args = args
        self._cancelled = False
        interval = interval or 0
        if interval > 0:
            self.interval = interval
            self.handler = self._loop.call_later(interval, self)
        else:
            self.interval = None
            self.handler = self._loop.call_soon(self)

    @property
    def cancelled(self):
        return self._cancelled

    def cancel(self):
        '''Attempt to cancel the callback.'''
        self._cancelled = True

    def __call__(self):
        try:
            result = maybe_async(self.callback(*self.args), self._loop)
        except Exception:
            self._loop.logger.exception('Exception in looping callback')
            self.cancel()
            return
        if isinstance(result, Future):
            result.add_done_callback(self._might_continue)
        else:
            self._continue()

    def _continue(self):
        if not self._cancelled:
            handler = self.handler
            loop = self._loop
            if self.interval:
                handler._cancelled = False
                handler._when = loop.time() + self.interval
                loop._add_callback(handler)
            else:
                loop._ready.append(self.handler)

    def _might_continue(self, fut):
        try:
            fut.result()
        except Exception:
            self._loop.logger.exception('Exception in looping callback')
            self.cancel()
        else:
            self._continue()


class QueueTask(Task):

    def _wakeup(self, fut, inthread=False):
        if inthread or fut._loop is self._loop:
            super(QueueTask, self)._wakeup(fut)
        else:
            self._loop.call_soon(self._wakeup, fut, True)


class QueueEventLoop(BaseEventLoop):
    task_factory = QueueTask

    def __init__(self, ioqueue, iothreadloop=False, logger=None):
        super(QueueEventLoop, self).__init__()
        self._iothreadloop = iothreadloop
        self._ioqueue = ioqueue
        self.logger = logger or LOGGER
        self.call_soon(set_as_loop, self)


class EventLoop(asyncio.SelectorEventLoop):
    """A pluggable event loop which conforms with the pep-3156_ API.

    The event loop is the place where most asynchronous operations
    are carried out.

    .. attribute:: poll_timeout

        The timeout in seconds when polling with ``epolL``, ``kqueue``,
        ``select`` and so forth.

        Default: ``0.5``

    .. attribute:: tid

        The thread id where this event loop is running. If the
        event loop is not running this attribute is ``None``.

    """
    task_factory = Task

    def __init__(self, selector=None, iothreadloop=False, logger=None):
        super(EventLoop, self).__init__(selector)
        self._iothreadloop = iothreadloop
        self.logger = logger or LOGGER
        self.call_soon(set_as_loop, self)

    def __repr__(self):
        return self.name
    __str__ = __repr__

    @property
    def name(self):
        if self.is_running():
            return self.__class__.__name__
        else:
            return '%s <not running>' % self.__class__.__name__


def call_repeatedly(loop, interval, callback, *args):
    """Call a ``callback`` every ``interval`` seconds.

    It handles asynchronous results. If an error occur in the ``callback``,
    the chain is broken and the ``callback`` won't be called anymore.
    """
    return LoopingCall(loop, callback, args, interval)
