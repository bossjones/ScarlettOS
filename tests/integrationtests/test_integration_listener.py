#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
test_integration_listener
----------------------------------
"""

###########################################
# Borrowed from test_integration_player - START
###########################################
import os
import sys
import signal
import pytest
from _pytest.monkeypatch import MonkeyPatch
import builtins
import threading

import unittest
import unittest.mock as mock

import pydbus
import scarlett_os
import scarlett_os.exceptions

from tests.integrationtests.stubs import create_main_loop

from tests import PROJECT_ROOT
import time

from tests.integrationtests.baseclass import run_emitter_signal
from tests.integrationtests.baseclass import IntegrationTestbaseMainloop

done = 0

from scarlett_os.internal import gi  # noqa
from scarlett_os.internal.gi import Gio  # noqa
from scarlett_os.internal.gi import GObject  # noqa
from scarlett_os.internal.gi import GLib

from scarlett_os import listener
from scarlett_os.utility import threadmanager
from scarlett_os.common.configure.ruamel_config import ConfigManager


###########################################
# Borrowed from test_integration_player - END
###########################################

import imp

# source: https://github.com/YosaiProject/yosai/blob/master/test/isolated_tests/core/conf/conftest.py

# source: https://github.com/pytest-dev/pytest/issues/363
@pytest.fixture(scope="function")
def listener_monkeyfunc(request):
    mpatch = MonkeyPatch()
    yield mpatch
    mpatch.undo()


@pytest.fixture(scope="function")
def listener_mocker_stopall(mocker):
    "Stop previous mocks, yield mocker plugin obj, then stopall mocks again"
    print("Called [setup]: mocker.stopall()")
    mocker.stopall()
    print("Called [setup]: imp.reload(threadmanager)")
    imp.reload(listener)
    yield mocker
    print("Called [teardown]: mocker.stopall()")
    mocker.stopall()
    print("Called [setup]: imp.reload(threadmanager)")
    imp.reload(listener)


# source: test_signal.py in pygobject


class C(GObject.GObject):
    """Test class for verifying callbacks."""

    __gsignals__ = {
        "my_signal": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_INT,))
    }

    def do_my_signal(self, arg):
        self.arg = arg


@pytest.mark.threading
@pytest.mark.gobject
@pytest.mark.scarlettonly
@pytest.mark.scarlettonlyintgr
class TestSuspendableMainLoopThread(object):
    def test_SuspendableMainLoopThread(
        self, listener_mocker_stopall, listener_monkeyfunc
    ):
        def my_signal_handler_cb(*args):
            assert len(args) == 5
            assert isinstance(args[0], C)
            assert args[0] == inst

            assert isinstance(args[1], int)
            assert args[1] == 42

            assert args[2:] == (1, 2, 3)

        def quit(*args):
            print(
                "timeout reached, lets close out SuspendableMainLoopThread in [test_SuspendableMainLoopThread]"
            )
            with _loop_thread_lock:
                time.sleep(0.5)
                print(
                    "SuspendableMainLoopThread attempting to terminate in [test_SuspendableMainLoopThread]"
                )
                print("running: _shared_loop_thread.terminate()")
                _shared_loop_thread.terminate()
                # print('running: stop_event.set()')
                # stop_event.set()
                # FIXME: DISABLED 5/9/2017 # SINCE YOU CAN'T JOIN CURRENT THREAD # print('SuspendableMainLoopThread attempting to join in [test_SuspendableMainLoopThread]')
                # FIXME: DISABLED 5/9/2017 # SINCE YOU CAN'T JOIN CURRENT THREAD # _shared_loop_thread.join(2)

        _shared_loop_thread = None
        _loop_thread_lock = threading.RLock()
        # stop_event = threading.Event()

        with _loop_thread_lock:
            print(
                "SuspendableMainLoopThread _loop_thread_lock acquired in [test_SuspendableMainLoopThread]"
            )
            if not _shared_loop_thread:
                print(
                    "SuspendableMainLoopThread if not _shared_loop_thread in [test_SuspendableMainLoopThread]"
                )
                # Start a new thread.
                print("[start] new listener.SuspendableMainLoopThread()")
                # _shared_loop_thread = listener.SuspendableMainLoopThread(stop_event)
                _shared_loop_thread = listener.SuspendableMainLoopThread()
                # get MainLoop
                print("[start] _shared_loop_thread.get_loop()")
                _ = _shared_loop_thread.get_loop()
                # disable daemon thread for testing
                # that way thread dies after .terminate() is called
                # instead of during garbage collection during exit() of python interperter
                print(
                    "[start] listener_monkeyfunc.setattr(__name__ + '.listener.SuspendableMainLoopThread.daemon', False)"
                )
                listener_monkeyfunc.setattr(
                    __name__ + ".listener.SuspendableMainLoopThread.daemon", False
                )
                # validate monkeypatch worked
                assert _shared_loop_thread.daemon is False
                # start thread
                print("[start] _shared_loop_thread.start()")
                _shared_loop_thread.start()
                # this should simply return
                print("[start] _shared_loop_thread.do_run()")
                _shared_loop_thread.do_run()
                # FIXME: This is still returning a Mock
                # assert str(type(_shared_loop_thread.get_loop())) == "<class 'gi.overrides.GLib.MainLoop'>"

        inst = C()
        inst.connect("my_signal", my_signal_handler_cb, 1, 2, 3)

        inst.emit("my_signal", 42)
        assert inst.arg == 42

        # Create a timeout that checks how many
        # tasks have been completed. When 2 have finished,
        # kill threads and finish.
        # GLib.timeout_add_seconds(10, quit, _shared_loop_thread, _loop_thread_lock, stop_event)
        GLib.timeout_add_seconds(10, quit, _shared_loop_thread, _loop_thread_lock)

    # def test_terminate_SuspendableMainLoopThread(self, monkeypatch):

    #     def my_signal_handler_cb(*args):

    #         with pytest.raises(threadmanager.Terminated):
    #             _shared_loop_thread.terminate()

    #     def quit(*args):
    #         print('timeout reached, let close out SuspendableMainLoopThread')
    #         with _loop_thread_lock:
    #             print('attempting to terminate')
    #             _shared_loop_thread.terminate()
    #             print('attempting to join')
    #             _shared_loop_thread.join(2)

    #     _shared_loop_thread = None
    #     _loop_thread_lock = threading.RLock()

    #     with _loop_thread_lock:
    #         if not _shared_loop_thread:
    #             # Start a new thread.
    #             _shared_loop_thread = listener.SuspendableMainLoopThread()
    #             # get MainLoop
    #             _shared_loop_thread.get_loop()
    #             # start thread
    #             _shared_loop_thread.start()
    #             # this should simply return
    #             _shared_loop_thread.do_run()

    #     inst = C()
    #     inst.connect("my_signal", my_signal_handler_cb, 1, 2, 3)

    #     inst.emit("my_signal", 42)

    #     # Create a timeout that checks how many
    #     # tasks have been completed. When 2 have finished,
    #     # kill threads and finish.
    #     GLib.timeout_add_seconds(10, quit)


# fake_temp_data_pocketsphinx_dic
# fake_temp_data_pocketsphinx_lm


class TestScarlettListener(object):
    def test_ScarlettListenerI_init(
        self, listener_mocker_stopall, mocked_config_file_path
    ):
        fake_config_manager = ConfigManager(config_path=mocked_config_file_path)
        fake_config_manager.load()

        sl = listener.ScarlettListenerI("scarlett_listener", fake_config_manager)

        assert sl.running is False
        assert sl.finished is False
        assert sl.read_exc is None
        assert sl.dot_exc is None
        assert sl.running is False
        assert sl.cancelled is False
        assert sl.name == "scarlett_listener"
        assert sl._message == "This is the ScarlettListenerI"
        assert sl.config is None
        assert sl.override_parse == ""
        assert sl.failed == 0
        assert sl.kw_found == 0
        assert sl.debug is False
        assert sl.create_dot is True
        assert sl.terminate is False
        assert sl._status_ready == "  ScarlettListener is ready"
        assert sl._status_kw_match == "  ScarlettListener caught a keyword match"
        assert sl._status_cmd_match == "  ScarlettListener caught a command match"
        assert sl._status_stt_failed == "  ScarlettListener hit Max STT failures"
        assert sl._status_cmd_start == "  ScarlettListener emitting start command"
        assert sl._status_cmd_fin == "  ScarlettListener Emitting Command run finish"
        assert sl._status_cmd_cancel == "  ScarlettListener cancel speech Recognition"
        assert sl.bus is None
        assert sl.bus_message_element_handler_id is None
        assert sl.bus_message_error_handler_id is None
        assert sl.bus_message_eos_handler_id is None
        assert sl.bus_message_state_changed_handler_id is None
        assert sl.pipeline is None
        assert sl.start_time == 0
        assert sl.state == "stopped"
        assert sl.buffer_count == 0
        assert sl.byte_count == 0
        assert sl.kw_to_find == ["scarlett", "SCARLETT"]
