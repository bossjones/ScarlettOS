#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Contains the Base class for integration tests using the classic xunit-style setup.
----------------------------------
"""

import os
# import gc
import sys
import signal
import pytest
import builtins
import threading
# import traceback

import unittest
import unittest.mock as mock

import pydbus
import scarlett_os
import scarlett_os.exceptions

# from tests.integrationtests.stubs import create_main_loop

from tests import PROJECT_ROOT
import time

import logging

done = 0

from scarlett_os.internal import gi  # noqa
from scarlett_os.internal.gi import Gio  # noqa
from scarlett_os.internal.gi import GObject  # noqa
from scarlett_os.internal.gi import GLib
# from scarlett_os.internal.gi import Gst

from scarlett_os import tasker

# classic xunit-style setup
# source: http://doc.pytest.org/en/latest/xunit_setup.html

# from scarlett_os.utility.dbus_runner import DBusRunner

#########################################################################################
# NOTE: Are we ready to test for garbage collection leaks?
#########################################################################################
# def handle_uncaught_exception(exctype, value, trace):
#     traceback.print_tb(trace)
#     print(value, file=sys.stderr)
#     sys.exit(1)


# sys.excepthook = handle_uncaught_exception


# def handle_glog(domain, level, message, udata):
#     Gst.debug_print_stack_trace()
#     traceback.print_stack()
#     print("%s - %s" % (domain, message), file=sys.stderr)
#     sys.exit(-11)


# # GStreamer Not enabled because of an assertion on caps on the CI server.
# # See https://gitlab.gnome.org/thiblahute/pitivi/-/jobs/66570
# for category in ["Gtk", "Gdk", "GLib-GObject", "GES"]:
#     GLib.log_set_handler(category, GLib.LogLevelFlags.LEVEL_CRITICAL, handle_glog, None)

detect_leaks = os.environ.get("SCARLETT_TEST_DETECT_LEAKS", "0") not in ("0", "")
# os.environ["PITIVI_USER_CACHE_DIR"] = tempfile.mkdtemp(suffix="pitiviTestsuite")
#########################################################################################


def run_emitter_signal(request, get_environment, sig_name="ready"):
    print("Setting up emitter")
    print("[Emit]: {}".format(sig_name))

    # Return return code for running shell out command
    def cb(pid, status):
        """
        Set return code for emitter shell script.
        """
        print("status code: {}".format(status))

    # Send [ready] signal to dbus service
    # FIXME: THIS IS THE CULPRIT
    argv = [sys.executable, "-m", "scarlett_os.emitter", "-s", sig_name]

    # convert environment dict -> list of strings
    env_dict_to_str = ["{}={}".format(k, v) for k, v in get_environment.items()]

    # Async background call. Send a signal to running process.
    pid, stdin, stdout, stderr = GLib.spawn_async(
        argv,
        envp=env_dict_to_str,
        working_directory=PROJECT_ROOT,
        flags=GLib.SpawnFlags.DO_NOT_REAP_CHILD,
    )

    print("run_emitter_signal: stdout")
    print(stdout)
    print("run_emitter_signal: stderr")
    print(stderr)
    print("run_emitter_signal: stdin")
    print(stdin)

    # Close file descriptor when finished running scarlett emitter
    pid.close()

    # NOTE: We give this PRIORITY_HIGH to ensure callback [cb] runs before dbus signal callback
    id = GLib.child_watch_add(GLib.PRIORITY_HIGH, pid, cb)


# @pytest.mark.usefixtures("service_on_outside", "get_environment", "get_bus")
class IntegrationTestbase(object):
    # _tracked_types = (Gst.MiniObject, Gst.Element, Gst.Pad, Gst.Caps)

    """Base class for integration tests."""
    # _tracked_types = (Gst.MiniObject, Gst.Element, Gst.Pad, Gst.Caps)

    # Tests are not allowed to have an __init__ method
    # Python shells, the underscore (_) means the result of the last evaluated expression in the shell:
    # source: http://stackoverflow.com/questions/5787277/python-underscore-as-a-function-parameter
    # [Method and function level setup/teardown]
    def setup_method(self, _):
        """Set up called automatically before every test_XXXX method."""
        self.log = logging.getLogger()
        self.log.setLevel(logging.DEBUG)
        logging.basicConfig(
            format="%(filename)s:%(lineno)d (%(funcName)s): %(message)s"
        )

        # self.recieved_signals = []
        self.status = None
        self.tasker = None

        # if detect_leaks:
        #     self.gctrack()

    def setup_tasker(self, monkeypatch, get_bus):
        """Create ScarlettTasker object and call setup_controller."""
        monkeypatch.setattr(
            "scarlett_os.utility.dbus_runner.SessionBus", lambda: get_bus
        )
        self.log.info("setting up Controller")
        self.tasker = tasker.ScarlettTasker()

    def teardown_method(self, _):
        """Tear down called automatically after every test_XXXX method."""
        # self.recieved_signals = None
        self.status = None
        self.tasker.reset()
        self.tasker = None

    #     if detect_leaks:
    #         self.gccollect()
    #         self.gcverify()

    # def gctrack(self):
    #     self.gccollect()
    #     self._tracked = []
    #     for obj in gc.get_objects():
    #         if not isinstance(obj, self._tracked_types):
    #             continue

    #         self._tracked.append(obj)

    # def gccollect(self):
    #     ret = 0
    #     while True:
    #         c = gc.collect()
    #         ret += c
    #         if c == 0:
    #             break
    #     return ret

    # def gcverify(self):
    #     leaked = []
    #     for obj in gc.get_objects():
    #         if not isinstance(obj, self._tracked_types) or \
    #                 obj in self._tracked:
    #             continue

    #         leaked.append(obj)

    #     # we collect again here to get rid of temporary objects created in the
    #     # above loop
    #     self.gccollect()

    #     for elt in leaked:
    #         print(elt)
    #         for i in gc.get_referrers(elt):
    #             print("   ", i)

    #     # NOTE: I think these guys are the same, not sure
    #     # self.assertFalse(leaked, leaked)
    #     assert not leaked, leaked
    #     del self._tracked


# @pytest.mark.usefixtures("service_on_outside", "get_environment", "get_bus")
class IntegrationTestbaseMainloop(IntegrationTestbase):
    """Base class for integration tests that require a GLib-Mainloop.
    Used for Tests that register and wait for signals.
    """

    # Method and function level setup/teardown
    def setup_method(self, method):
        """Set up called automatically before every test_XXXX method."""
        super(IntegrationTestbaseMainloop, self).setup_method(method)
        self.mainloop = GLib.MainLoop()
        self.quit_count = 0

    def run_mainloop(self, timeout=5):
        """Start the MainLoop, set Quit-Counter to Zero"""
        self.quit_count = 0
        GLib.timeout_add_seconds(timeout, self.quit_mainloop)
        self.mainloop.run()

    def quit_mainloop(self, *_):
        """Quit the MainLoop, set Quit-Counter to Zero"""
        self.mainloop.quit()
        self.quit_count = 0

    def quit_mainloop_after(self, call_count):
        """Increment Quit-Counter, if it reaches call_count,
        Quit the MainLoop"""
        self.quit_count += 1
        if self.quit_count == call_count:
            self.quit_mainloop()
