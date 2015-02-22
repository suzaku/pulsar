import asyncio
from unittest import SkipTest

from pulsar import async, is_async, HaltServer

from .utils import (TestFailure, skip_test, skip_reason,
                    expecting_failure, AsyncAssert)


class Runner(object):

    def __init__(self, monitor, runner, tests):
        self._loop = monitor._loop
        self._time_start = self._loop.time()
        self.logger = monitor.logger
        self.monitor = monitor
        self.runner = runner
        self.concurrent = set()
        self.tests = list(reversed(tests))
        async(self._run_all_tests(tests), loop=self._loop)
        self._loop.call_soon(self._check_done)

    def _check_done(self):
        if self.tests or self.concurrent:
            return self._loop.call_soon(self._check_done)
        #
        time_taken = self._loop.time() - self._time_start
        runner = self.runner
        runner.on_end()
        runner.printSummary(time_taken)
        if runner.result.errors or runner.result.failures:
            exit_code = 2
        else:
            exit_code = 0
        self._loop.call_soon(self._exit, exit_code)

    def _exit(self, exit_code):
        raise HaltServer(exit_code=exit_code)

    def _run_all_tests(self, tests):
        runner = self.runner
        cfg = self.monitor.cfg

        while self.tests:
            tag, testcls = self.tests.pop()
            testcls.tag = tag
            testcls.cfg = cfg
            testcls.async = AsyncAssert(testcls)
            try:
                all_tests = runner.loadTestsFromTestCase(testcls)
            except Exception:
                self.logger.exception('Could not load tests', exc_info=True)
                continue
            if not all_tests.countTestCases():
                continue

            self.logger.info('Running Tests from %s', testcls)
            runner.startTestClass(testcls)
            self.concurrent.add(testcls)
            yield from self._run_testcls(testcls, all_tests)
            self.logger.info('Finished Tests from %s', testcls)

    def _run_testcls(self, testcls, all_tests):
        cfg = testcls.cfg
        seq = getattr(testcls, '_sequential_execution', cfg.sequential)
        try:
            if skip_test(testcls):
                raise SkipTest(skip_reason(testcls))
            yield from self._run(testcls.setUpClass)
            yield None  # release the loop
        except SkipTest as exc:
            reason = str(exc)
            for test in all_tests:
                self.runner.addSkip(test, reason)
        except Exception as exc:
            self.logger.exception('Failure in setUpClass', exc_info=True)
            exc = TestFailure(exc)
            # setUpClass failed, fails all tests
            for test in all_tests:
                self.add_failure(test, exc)
        else:
            if seq:
                for test in all_tests:
                    yield from self._run_test(test)
            else:
                yield from asyncio.wait([self._run_test(test)
                                         for test in all_tests],
                                        loop=self._loop)

        try:
            yield from self._run(testcls.tearDownClass)
        except Exception as exc:
            self.logger.exception('Failure in tearDownClass',
                                  exc_info=True)

        self.concurrent.remove(testcls)

    def _run(self, method):
        coro = method()
        # a coroutine
        if coro:
            timeout = self.monitor.cfg.test_timeout
            yield from asyncio.wait_for(coro, timeout, loop=self._loop)

    def _run_test(self, test):
        '''Run a ``test`` function using the following algorithm

        * Run :meth:`setUp` method in :attr:`testcls`
        * Run the test function
        * Run :meth:`tearDown` method in :attr:`testcls`
        '''
        error = None
        runner = self.runner
        runner.startTest(test)
        test_name = test._testMethodName
        method = getattr(test, test_name)
        if skip_test(method):
            reason = skip_reason(method)
            runner.addSkip(test, reason)
        else:
            error = yield from self._run_safe(test, 'setUp')
            if not error:
                test = runner.before_test_function_run(test)
                error = yield from self._run_safe(test, test_name)
            error = yield from self._run_safe(test, 'tearDown', error)
            if not error:
                runner.addSuccess(test)
        runner.stopTest(test)
        yield None  # release the loop

    def _run_safe(self, test, method_name, error=None):
        try:
            method = getattr(test, method_name)
            coro = method()
            # a coroutine
            if is_async(coro):
                timeout = getattr(method, 'timeout',
                                  self.monitor.cfg.test_timeout)
                yield from asyncio.wait_for(coro, timeout, loop=self._loop)
        except Exception as exc:
            if not error:
                error = TestFailure(exc)
                self.add_failure(test, error, expecting_failure(method))
            return error

    def add_failure(self, test, failure, expecting_failure=False):
        '''Add ``error`` to the list of errors.

        :param test: the test function object where the error occurs
        :param runner: the test runner
        :param error: the python exception for the error
        :param add_err: if ``True`` the error is added to the list of errors
        :return: a tuple containing the ``error`` and the ``exc_info``
        '''
        runner = self.runner
        if expecting_failure:
            runner.addExpectedFailure(test, failure)
        elif isinstance(failure.exc, test.failureException):
            runner.addFailure(test, failure)
        else:
            runner.addError(test, failure)