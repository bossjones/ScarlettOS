#!/usr/bin/env python3  # NOQA
# -*- coding: utf-8 -*-

"""Scarlett Listener Module."""

# NOTE: THIS IS THE CLASS THAT WILL BE REPLACING scarlett_speaker.py eventually.
# It is cleaner, more object oriented, and will allows us to run proper tests.
# Also threading.RLock() and threading.Semaphore() works correctly.

# There are a LOT of threads going on here, all of them managed by Gstreamer.
# If pyglet ever needs to run under a Python that doesn't have a GIL, some
# locks will need to be introduced to prevent concurrency catastrophes.
#
# At the moment, no locks are used because we assume only one thread is
# executing Python code at a time.  Some semaphores are used to block and wake
# up the main thread when needed, these are all instances of
# threading.Semaphore.  Note that these don't represent any kind of
# thread-safety.

# from __future__ import with_statement, division, absolute_import

import os
import sys
import time
import pprint
import signal
import threading
import logging
import random

from gettext import gettext as _

from scarlett_os.internal.gi import gi
from scarlett_os.internal.gi import GObject
from scarlett_os.internal.gi import GLib
from scarlett_os.internal.gi import Gst
from scarlett_os.internal.gi import Gio

# FIXME: Don't forget to comment this out
# import hunter
# hunter.trace(module='gi', action=hunter.CallPrinter)

from scarlett_os.exceptions import NoStreamError
from scarlett_os.exceptions import FileReadError

import queue
from urllib.parse import quote

from scarlett_os.utility.gnome import abort_on_exception
from scarlett_os.utility.gnome import _IdleObject
from scarlett_os.utility.thread import ThreadManager

import pydbus
from pydbus import SessionBus

from scarlett_os.utility.dbus_utils import DbusSignalHandler
from scarlett_os.utility.dbus_runner import DBusRunner
from scarlett_os.utility.threadmanager import SuspendableThread

from scarlett_os.common.configure.ruamel_config import ConfigManager

logger = logging.getLogger(__name__)

# global pretty print for debugging
pp = pprint.PrettyPrinter(indent=4)

_INSTANCE = None

# Constants
QUEUE_SIZE = -1
BUFFER_SIZE = 10
SENTINEL = "__GSTDEC_SENTINEL__"
SEMAPHORE_NUM = 0

gst = Gst
HERE = os.path.dirname(__file__)

# Pocketsphinx defaults
LANGUAGE_VERSION = 1473
HOMEDIR = "/home/pi"
LANGUAGE_FILE_HOME = "{}/dev/bossjones-github/scarlett_os/static/speech/lm".format(
    HOMEDIR
)
DICT_FILE_HOME = "{}/dev/bossjones-github/scarlett_os/static/speech/dict".format(
    HOMEDIR
)
LM_PATH = "{}/{}.lm".format(LANGUAGE_FILE_HOME, LANGUAGE_VERSION)
DICT_PATH = "{}/{}.dic".format(DICT_FILE_HOME, LANGUAGE_VERSION)
HMM_PATH = "{}/.virtualenvs/scarlett_os/share/pocketsphinx/model/en-us/en-us".format(
    HOMEDIR
)
bestpath = 0
PS_DEVICE = "plughw:CARD=Device,DEV=0"


# NOTE: GObject.object.connect(detailed_signal: str, handler: function, *args) -> handler_id: int[source]
SCARLETT_LISTENER_I_SIGNALS = {
    "completed": (GObject.SignalFlags.RUN_LAST, None, []),
    "progress": (GObject.SignalFlags.RUN_LAST, None, []),  # percent complete
    "eos": (GObject.SignalFlags.RUN_LAST, None, ()),
    "error": (
        GObject.SignalFlags.RUN_LAST,
        None,
        (GObject.TYPE_STRING, GObject.TYPE_STRING),
    ),
    "died": (GObject.SignalFlags.RUN_LAST, None, ()),
    "async-done": (GObject.SignalFlags.RUN_LAST, None, ()),
    "state-change": (
        GObject.SignalFlags.RUN_LAST,
        None,
        (GObject.TYPE_INT, GObject.TYPE_INT),
    ),
    "playing-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    "playback-status-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    # FIXME: AUDIT THE RETURN TYPES
    "bitrate-changed": (
        GObject.SignalFlags.RUN_LAST,
        None,
        (GObject.TYPE_INT, GObject.TYPE_INT),
    ),
    "keyword-recgonized": (
        GObject.SignalFlags.RUN_LAST,
        None,
        (GObject.TYPE_STRING, GObject.TYPE_STRING),
    ),
    "command-recgonized": (
        GObject.SignalFlags.RUN_LAST,
        None,
        (GObject.TYPE_STRING, GObject.TYPE_STRING),
    ),
    "stt-failed": (
        GObject.SignalFlags.RUN_LAST,
        None,
        (GObject.TYPE_STRING, GObject.TYPE_STRING),
    ),
    "listener-cancel": (
        GObject.SignalFlags.RUN_LAST,
        None,
        (GObject.TYPE_STRING, GObject.TYPE_STRING),
    ),
    "listener-ready": (
        GObject.SignalFlags.RUN_LAST,
        None,
        (GObject.TYPE_STRING, GObject.TYPE_STRING),
    ),
    "connected-to-server": (
        GObject.SignalFlags.RUN_LAST,
        None,
        (GObject.TYPE_STRING, GObject.TYPE_STRING),
    ),
    "listener-message": (
        GObject.SignalFlags.RUN_LAST,
        None,
        (GObject.TYPE_STRING, GObject.TYPE_STRING),
    ),
    "finished": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_PYOBJECT,)),
    "aborted": (GObject.SignalFlags.RUN_LAST, None, (GObject.TYPE_PYOBJECT,)),
}


# from pocketsphinx.pocketsphinx import *
# from sphinxbase.sphinxbase import *
# config = Decoder.default_config()
# config.set_float("-vad_threshold", 3.0)

#################################################################
# Managing the Gobject main loop thread.
#################################################################

_shared_loop_thread = None
_loop_thread_lock = threading.RLock()


def get_loop_thread():
    """Get the shared main-loop thread.
    """
    global _shared_loop_thread
    with _loop_thread_lock:
        if not _shared_loop_thread:
            # Start a new thread.
            _shared_loop_thread = MainLoopThread()
            _shared_loop_thread.start()
        return _shared_loop_thread


# NOTE: doc updated via
# https://github.com/Faham/emophiz/blob/15612aaf13401201100d67a57dbe3ed9ace5589a/emotion_engine/dependencies/src/sensor_lib/SensorLib.Tobii/Software/tobiisdk-3.0.2-Win32/Win32/Python26/Modules/tobii/sdk/mainloop.py


class MainLoopThread(threading.Thread):
    """
    A daemon thread encapsulating a Gobject main loop.
    A mainloop is used by all asynchronous objects to defer
    handlers and callbacks to. The function run() blocks until
    the function quit() has been called
    (and all queued handlers have been executed).
    The run() function will then execute all the handlers in order.
    """

    def __init__(self):
        super(MainLoopThread, self).__init__()
        self.__loop = GObject.MainLoop()
        self.daemon = True

    def run(self):
        try:
            self.__loop.run()
        except KeyboardInterrupt:
            print("MainLoopThread recieved a Ctrl-C. Exiting gracefully...")
            self.__loop.quit()
            # self.join(timeout=1)
            print("MainLoopThread finished ...")
            return

    def get_loop(self):
        return self.__loop

    def set_loop(self, loop):
        self.__loop = loop


class SuspendableMainLoopThread(SuspendableThread):
    """A daemon thread encapsulating a Gobject main loop.

    A mainloop is used by all asynchronous objects to defer handlers and callbacks to. The function run() blocks until the function quit() has been called (and all queued handlers have been executed). The run() function will then execute all the handlers in order.
    """

    # DISABLED 5/15/2017 # def __init__(self, stop_event):
    def __init__(self):
        super(SuspendableMainLoopThread, self).__init__(
            name="SuspendableMainLoopThread"
        )
        #####################
        # NOTE: A thread can be flagged as a "daemon thread".
        # The significance of this flag is that the entire
        # Python program exits when only daemon threads are left.
        # The initial value is inherited from the creating thread.
        # The flag can be set through the daemon property or the daemon constructor argument.
        #####################
        # NOTE: Daemon threads are abruptly stopped at shutdown.
        # Their resources (such as open files, database transactions, etc.)
        # may not be released properly.
        # If you want your threads to stop gracefully,
        # make them non-daemonic and use a suitable signalling mechanism such as an Event.
        #####################
        self.daemon = True
        self.__loop = GObject.MainLoop()
        self.__active = False
        # NOTE: How to stop a daemon thread
        # source: https://stackoverflow.com/questions/41131117/how-to-stop-daemon-thread
        # self.__stop_event = stop_event

    def do_run(self):
        """
        Start the :func:`GObject.MainLoop` to establish event loop.
        """
        if self.__active:
            return

        self.__active = True

        try:
            self.__loop.run()
            # Definition: GLib.MainLoop.get_context

            # The GLib.MainContext with which the source is associated,
            # or None if the context has not yet been added to a source.
            # Return type: GLib.MainContext or None

            # Gets the GLib.MainContext with which the source is associated.
            # You can call this on a source that has been destroyed,
            # provided that the GLib.MainContext it was attached to still
            # exists (in which case it will return that GLib.MainContext).
            # In particular, you can always call this function on the
            # source returned from GLib.main_current_source().
            # But calling this function on a source whose
            # GLib.MainContext has been destroyed is an error.
            context = self.__loop.get_context()
            # DISABLED: 5/15/2017 # while not self.stop_event.is_set():
            while self.__active:
                context.iteration(False)
                # Checks if any sources have pending events for the given context.
                # Return True if events are pending.
                if not context.pending():
                    time.sleep(.1)
                    self.emit(
                        "progress", -1, "SuspendableMainLoopThread working interminably"
                    )
                    self.check_for_sleep()
        except (KeyboardInterrupt, SystemExit):
            print("MainLoopThread recieved a Ctrl-C. Exiting gracefully...")
            self.__active = False
            # 5/15/2017: do we need this below???
            # self.emit('stopped')
            self.__loop.quit()
            print("MainLoopThread finished ...")
            ##############################################################################################
            # https://stackoverflow.com/questions/15300550/python-return-return-none-and-no-return-at-all#
            # Using return.
            # This is used for the same reason as break in loops.
            # The return value doesn't matter and you only want to exit the whole function.
            # It's extremely useful in some places, even tho you don't need it that often.
            # We got 15 prisoners and we know one of them has a knife.
            # We loop through each prisoner one by one to check if they have a knife.
            # If we hit the person with a knife,
            # we can just exit the function cause we know there's only one knife and no reason the check rest of the prisoners.
            # If we don't find the prisoner with a knife, we raise an the alert.
            # This could be done in many different ways and using return is probably not even the best way,
            # but it's just an example to show how to use return for exiting a function.
            ##############################################################################################
            return  # no need to check rest of the prisoners nor raise an alert

    def get_loop(self):
        return self.__loop

    def set_loop(self, loop):
        self.__loop = loop


class ScarlettListenerI(threading.Thread, _IdleObject):
    """
    Attempt to take out all Gstreamer logic and put it in a
    class ouside the dbus server.
    Cancellable thread which uses gobject signals to return information
    to the GUI.
    """

    __gsignals__ = SCARLETT_LISTENER_I_SIGNALS

    device = PS_DEVICE
    hmm = HMM_PATH
    lm = LM_PATH
    dic = DICT_PATH

    # __dr = None
    __instance = None

    MAX_FAILURES = 10

    def __init__(self, name, config_manager, *args):
        threading.Thread.__init__(self)
        _IdleObject.__init__(self)

        self.running = False
        self.finished = False
        self.ready_sem = threading.Semaphore(SEMAPHORE_NUM)
        self.queue = queue.Queue(QUEUE_SIZE)

        # Load in config object, and set default device information
        self._config_manager = config_manager
        self._graphviz_debug_dir = self._config_manager.cfg["graphviz_debug_dir"]

        self._device = self._config_manager.cfg["pocketsphinx"]["device"]
        self._hmm = self._config_manager.cfg["pocketsphinx"]["hmm"]
        self._lm = self._config_manager.cfg["pocketsphinx"]["lm"]
        self._dic = self._config_manager.cfg["pocketsphinx"]["dict"]
        self._fwdflat = bool(self._config_manager.cfg["pocketsphinx"]["fwdflat"])
        self._bestpath = bool(self._config_manager.cfg["pocketsphinx"]["bestpath"])
        self._dsratio = int(self._config_manager.cfg["pocketsphinx"]["dsratio"])
        self._maxhmmpf = int(self._config_manager.cfg["pocketsphinx"]["maxhmmpf"])
        self._bestpath = bool(self._config_manager.cfg["pocketsphinx"]["bestpath"])
        self._silprob = float(self._config_manager.cfg["pocketsphinx"]["silprob"])
        self._wip = float(self._config_manager.cfg["pocketsphinx"]["wip"])

        # dotfile setup
        self._dotfile_listener = os.path.join(
            self._graphviz_debug_dir, "generator-listener.dot"
        )
        self._pngfile_listener = os.path.join(
            self._graphviz_debug_dir, "generator-listener-pipeline.png"
        )

        # self._handler = DbusSignalHandler()

        # Get a dbus proxy and check if theres a service registered called 'org.scarlett.Listener'
        # if not, then we can skip all further processing. (The scarlett-os-mpris-dbus seems not to be running)
        # self.__dr = DBusRunner.get_instance()

        logger.info("Initializing ScarlettListenerI")

        # This wil get filled with an exception if opening fails.
        self.read_exc = None
        self.dot_exc = None

        self.cancelled = False
        self.name = name
        self.setName("{}".format(self.name))

        self.pipelines_stack = []
        self.elements_stack = []
        self.gst_bus_stack = []

        self._message = "This is the ScarlettListenerI"
        # TODO: When we're ready to unit test, config this back in!!!!!
        # self.config = scarlett_config.Config()
        self.config = None
        self.override_parse = ""
        self.failed = 0
        self.kw_found = 0
        self.debug = False
        self.create_dot = True
        self.terminate = False

        self.capsfilter_queue_overrun_handler_id = None

        self._cancel_signal_callback = None

        # source: https://github.com/ljmljz/xpra/blob/b32f748e0c29cdbfab836b3901c1e318ea142b33/src/xpra/sound/sound_pipeline.py  # NOQA
        self.bus = None
        self.bus_message_element_handler_id = None
        self.bus_message_error_handler_id = None
        self.bus_message_eos_handler_id = None
        self.bus_message_state_changed_handler_id = None
        self.pipeline = None
        self.start_time = 0
        self.state = "stopped"
        self.buffer_count = 0
        self.byte_count = 0

        self._status_ready = "  ScarlettListener is ready"
        self._status_kw_match = "  ScarlettListener caught a keyword match"
        self._status_cmd_match = "  ScarlettListener caught a command match"
        self._status_stt_failed = "  ScarlettListener hit Max STT failures"
        self._status_cmd_start = "  ScarlettListener emitting start command"
        self._status_cmd_fin = "  ScarlettListener Emitting Command run finish"
        self._status_cmd_cancel = "  ScarlettListener cancel speech Recognition"

        if self.debug:
            # NOTE: For testing puposes, mainly when in public
            # so you dont have to keep yelling scarlett in front of strangers
            self.kw_to_find = ["yo", "hello", "man", "children"]
        else:
            # NOTE: Before we start worrying about the config class, lets hardcode what we care about
            # ADD ME BACK IN WHEN WE REALLY START UNIT TESTING # self.kw_to_find = self.config.get('scarlett', 'keywords')
            self.kw_to_find = ["scarlett", "SCARLETT"]

        if self.read_exc:
            # An error occurred before the stream became ready.
            self.close(True)
            raise self.read_exc  # pylint: disable=raising-bad-type

    def scarlett_reset_listen(self):
        self.failed = 0
        self.kw_found = 0

    def on_cancel_listening(self, *args, **kwargs):
        logger.debug("Inside cancel_listening function")
        self.scarlett_reset_listen()
        logger.debug("self.failed = {}".format(self.failed))
        logger.debug("self.keyword_identified = {}".format(self.kw_found))

    def play(self):
        p = self.pipelines_stack[0]
        self.state = "active"
        self.running = True
        # GST_STATE_PAUSED is the state in which an element is ready to accept and handle data.
        # For most elements this state is the same as PLAYING. The only exception to this rule are sink elements.
        # Sink elements only accept one single buffer of data and then block.
        # At this point the pipeline is 'prerolled' and ready to render data immediately.
        p.set_state(Gst.State.PAUSED)
        # GST_STATE_PLAYING is the highest state that an element can be in.
        # For most elements this state is exactly the same as PAUSED,
        # they accept and process events and buffers with data.
        # Only sink elements need to differentiate between PAUSED and PLAYING state.
        # In PLAYING state, sink elements actually render incoming data,
        # e.g. output audio to a sound card or render video pictures to an image sink.
        ret = p.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            logger.error("ERROR: Unable to set the pipeline to the playing state")

        # 8/8/2018 (Only enable this if we turn on debug mode)
        if os.environ.get("SCARLETT_DEBUG_MODE"):
            self.on_debug_activate()

        logger.debug("BEFORE: self.ready_sem.acquire()")
        self.ready_sem.acquire()
        logger.debug("AFTER: self.ready_sem.acquire()")
        logger.info("Press Ctrl+C to quit ...")

    def stop(self):
        p = self.pipelines_stack[0]
        self.state = "stopped"
        self.running = False
        # GST_STATE_NULL is the default state of an element.
        # In this state, it has not allocated any runtime resources,
        # it has not loaded any runtime libraries and it can obviously not handle data.
        p.set_state(Gst.State.NULL)

    def get_pocketsphinx_definition(self, override=False):
        r"""
        GST_DEBUG=2,pocketsphinx*:5 gst-launch-1.0 alsasrc device=plughw:CARD=Device,DEV=0 ! \
                                                    queue name=capsfilter_queue \
                                                          leaky=2 \
                                                          max-size-buffers=0 \
                                                          max-size-time=0 \
                                                          max-size-bytes=0 ! \
                                                    capsfilter caps='audio/x-raw,format=(string)S16LE,rate=(int)16000,channels=(int)1,layout=(string)interleaved' ! \
                                                    audioconvert ! \
                                                    audioresample ! \
                                                    pocketsphinx \
                                                    name=asr \
                                                    lm=~/dev/bossjones-github/scarlett_os/static/speech/lm/1473.lm \
                                                    dict=~/dev/bossjones-github/scarlett_os/static/speech/dict/1473.dic \
                                                    hmm=~/.virtualenvs/scarlett_os/share/pocketsphinx/model/en-us/en-us
                                                    bestpath=true ! \
                                                    tee name=tee ! \
                                                    queue name=appsink_queue \
                                                          leaky=2 \
                                                          max-size-buffers=0 \
                                                          max-size-time=0 \
                                                          max-size-bytes=0 ! \
                                                    appsink caps='audio/x-raw,format=(string)S16LE,rate=(int)16000,channels=(int)1,layout=(string)interleaved' \
                                                    drop=false max-buffers=10 sync=false \
                                                    emit-signals=true tee.
                                                    queue name=fakesink_queue \
                                                          leaky=2 \
                                                          max-size-buffers=0 \
                                                          max-size-time=0 \
                                                          max-size-bytes=0 ! \
                                                    fakesink sync=false
        """
        logger.debug("Inside get_pocketsphinx_definition")

        if override:
            _gst_launch = override
        else:
            # TODO: Add audio levels, see the following
            # SOURCE: http://stackoverflow.com/questions/5686424/detecting-blowing-on-a-microphone-with-gstreamer-or-another-library
            _gst_launch = [
                "alsasrc device=" + self.device,
                # source: https://github.com/walterbender/story/blob/master/grecord.py
                # without a buffer here, gstreamer struggles at the start of the
                # recording and then the A/V sync is bad for the whole video
                # (possibly a gstreamer/ALSA bug -- even if it gets caught up, it
                # should be able to resync without problem)
                # 'progressreport name=progressreport update-freq=1',  # NOTE: comment this in when you want performance information
                "queue name=capsfilter_queue silent=false leaky=2 max-size-buffers=0 max-size-time=0 max-size-bytes=0",
                "capsfilter name=capsfilter caps=audio/x-raw,format=S16LE,channels=1,layout=interleaved",
                "audioconvert name=audioconvert",
                "audioresample name=audioresample",
                "identity name=ident",
                "pocketsphinx name=asr",
                "tee name=tee",
                "queue name=appsink_queue silent=false leaky=2 max-size-buffers=0 max-size-time=0 max-size-bytes=0",
                #  caps=audio/x-raw,format=(string)S16LE,rate=(int)16000,channels=(int)1,layout=(string)interleaved   # NOQA
                "appsink name=appsink drop=false max-buffers=10 sync=false emit-signals=true tee.",
                "queue leaky=2 name=fakesink_queue",
                "fakesink",
            ]

        return _gst_launch

    def cancel(self):
        """
        Threads in python are not cancellable, so we implement our own
        cancellation logic
        """
        self.cancelled = True

    @abort_on_exception
    def run(self, event=None):
        # TODO: WE NEED TO USE A THREADING EVENT OR A RLOCK HERE TO WAIT TILL DBUS SERVICE IS RUNNING TO CONNECT
        # TODO: SIGNALS TO THE DBUS PROXY METHODS WE WANT TO USE
        # TODO: lock.acquire() / event / condition
        # TODO: self.connect_to_dbus()
        # TODO: self.setup_dbus_callbacks_handlers()
        self._connect_to_dbus()
        self.init_gst()
        print("Running {}".format(str(self)))
        self.play()
        self.emit("playback-status-changed")
        self.emit("playing-changed")
        # FIXME: is this needed? # self.mainloop.run()

    def _connect_to_dbus(self):
        self.bus = SessionBus()
        self.dbus_proxy = self.bus.get(
            "org.scarlett", object_path="/org/scarlett/Listener"
        )  # NOQA
        self.dbus_proxy.emitConnectedToListener("ScarlettListener")
        time.sleep(2)
        logger.info("_connect_to_dbus")
        # TODO: Add a ss_cancel_signal.disconnect() function later
        ss_cancel_signal = self.bus.subscribe(
            sender=None,
            iface="org.scarlett.Listener",
            signal="ListenerCancelSignal",
            object="/org/scarlett/Listener",
            arg0=None,
            flags=0,
            signal_fired=self.on_cancel_listening,
        )

    # NOTE: This function generates the dot file, checks that graphviz in installed and
    # then finally generates a png file, which it then displays
    def on_debug_activate(self):
        # FIXME: This needs to use dynamic paths, it's possible that we're having issues because of order of operations
        # FIXME: STATIC PATH 7/3/2018
        # dotfile = (
        #     "/home/pi/dev/bossjones-github/scarlett_os/_debug/generator-listener.dot"
        # )
        # pngfile = "/home/pi/dev/bossjones-github/scarlett_os/_debug/generator-listener-pipeline.png"  # NOQA
        dotfile = self._dotfile_listener
        pngfile = self._pngfile_listener

        if os.access(dotfile, os.F_OK):
            os.remove(dotfile)
        if os.access(pngfile, os.F_OK):
            os.remove(pngfile)

        Gst.debug_bin_to_dot_file(
            self.pipelines_stack[0], Gst.DebugGraphDetails.ALL, "generator-listener"
        )

        cmd = "/usr/bin/dot -Tpng -o {pngfile} {dotfile}".format(
            pngfile=pngfile, dotfile=dotfile
        )
        os.system(cmd)

    def result(self, final_hyp):
        """Forward result signals on the bus to the main thread."""
        logger.debug("Inside result function")
        logger.debug("final_hyp: {}".format(final_hyp))
        pp.pprint(final_hyp)
        logger.debug("kw_to_find: {}".format(self.kw_to_find))
        if final_hyp in self.kw_to_find and final_hyp != "":
            logger.debug("HYP-IS-SOMETHING: {}\n\n\n".format(final_hyp))
            self.failed = 0
            self.kw_found = 1
            self.dbus_proxy.emitKeywordRecognizedSignal()  # CHANGEME
        else:
            failed_temp = self.failed + 1
            self.failed = failed_temp
            logger.debug("self.failed = {}".format(int(self.failed)))
            # failed > 10
            if self.failed > 4:
                # reset pipline
                self.dbus_proxy.emitSttFailedSignal()  # CHANGEME
                self.scarlett_reset_listen()

    def run_cmd(self, final_hyp):
        logger.debug("Inside run_cmd function")
        logger.debug("KEYWORD IDENTIFIED BABY")
        logger.debug("self.kw_found = {}".format(int(self.kw_found)))
        if final_hyp == "CANCEL":
            self.dbus_proxy.emitListenerCancelSignal()  # CHANGEME
            self.on_cancel_listening()
        else:
            current_kw_identified = self.kw_found
            self.kw_found = current_kw_identified
            self.dbus_proxy.emitCommandRecognizedSignal(final_hyp)  # CHANGEME
            logger.info("Command = {}".format(final_hyp))
            logger.debug("AFTER run_cmd, self.kw_found = {}".format(int(self.kw_found)))

    def init_gst(self):
        logger.debug("Inside init_gst")
        self.start_time = time.time()
        pipeline = Gst.parse_launch(" ! ".join(self.get_pocketsphinx_definition()))
        logger.debug("After get_pocketsphinx_definition")
        # Add pipeline obj to stack we can pull from later
        self.pipelines_stack.append(pipeline)

        gst_bus = pipeline.get_bus()
        # gst_bus = pipeline.get_gst_bus()
        gst_bus.add_signal_watch()
        self.bus_message_element_handler_id = gst_bus.connect(
            "message::element", self._on_message
        )
        self.bus_message_eos_handler_id = gst_bus.connect(
            "message::eos", self._on_message
        )
        self.bus_message_error_handler_id = gst_bus.connect(
            "message::error", self._on_message
        )
        self.bus_message_state_changed_handler_id = gst_bus.connect(
            "message::state-changed", self._on_state_changed
        )

        # Add bus obj to stack we can pull from later
        self.gst_bus_stack.append(gst_bus)

        appsink = pipeline.get_by_name("appsink")
        appsink.set_property(
            "caps",
            Gst.Caps.from_string(
                "audio/x-raw,format=(string)S16LE,rate=(int)16000,channels=(int)1,layout=(string)interleaved"
            ),
        )

        appsink.set_property("drop", False)
        appsink.set_property("max-buffers", BUFFER_SIZE)
        appsink.set_property("sync", False)

        # The callback to receive decoded data.
        appsink.set_property("emit-signals", True)
        appsink.connect("new-sample", self._new_sample)

        self.caps_handler = appsink.get_static_pad("sink").connect(
            "notify::caps", self._notify_caps
        )

        self.elements_stack.append(appsink)

        # ************************************************************
        # get gst pipeline element pocketsphinx and set properties - BEGIN
        # ************************************************************
        pocketsphinx = pipeline.get_by_name("asr")
        # from scarlett_os.internal.debugger import dump
        # print("debug-2018-pocketsphinx - BEGIN")
        # dump(pocketsphinx.get_property('decoder'))
        # print("debug-2018-pocketsphinx - END")
        # print(pocketsphinx.list_properties())
        if self._hmm:
            pocketsphinx.set_property("hmm", self._hmm)
        if self._lm:
            pocketsphinx.set_property("lm", self._lm)
        if self._dic:
            pocketsphinx.set_property("dict", self._dic)

        if self._fwdflat:
            pocketsphinx.set_property("fwdflat", self._fwdflat)

        if self._bestpath:
            pocketsphinx.set_property("bestpath", self._bestpath)

        if self._dsratio:
            pocketsphinx.set_property("dsratio", self._dsratio)

        if self._maxhmmpf:
            pocketsphinx.set_property("maxhmmpf", self._maxhmmpf)

        if self._bestpath:
            pocketsphinx.set_property("bestpath", self._bestpath)

        # if self._silprob:
        #     pocketsphinx.set_property("silprob", self._silprob)

        # if self._wip:
        #     pocketsphinx.set_property("wip", self._wip)
        # ************************************************************
        # get gst pipeline element pocketsphinx and set properties - END
        # ************************************************************

        # NOTE: Old way of setting pocketsphinx properties. 8/5/2018
        # pocketsphinx.set_property(
        #     "fwdflat", True
        # )  # Enable Flat Lexicon Search | Default: true
        # pocketsphinx.set_property(
        #     "bestpath", True
        # )  # Enable Graph Search | Boolean. Default: true
        # pocketsphinx.set_property(
        #     "dsratio", 1
        # )  # Evaluate acoustic model every N frames |  Integer. Range: 1 - 10 Default: 1
        # pocketsphinx.set_property(
        #     "maxhmmpf", 3000
        # )  # Maximum number of HMMs searched per frame | Integer. Range: 1 - 100000 Default: 30000
        # pocketsphinx.set_property(
        #     "bestpath", True
        # )  # Enable Graph Search | Boolean. Default: true
        # pocketsphinx.set_property('maxwpf', -1)  #
        # pocketsphinx.set_property('maxwpf', 20)  # Maximum number of words
        # searched per frame | Range: 1 - 100000 Default: -1

        self.elements_stack.append(pocketsphinx)

        capsfilter_queue = pipeline.get_by_name("capsfilter_queue")
        capsfilter_queue.set_property("leaky", True)  # prefer fresh data
        capsfilter_queue.set_property("silent", False)
        capsfilter_queue.set_property("max-size-time", 0)  # 0 seconds
        capsfilter_queue.set_property("max-size-buffers", 0)
        capsfilter_queue.set_property("max-size-bytes", 0)
        self.capsfilter_queue_overrun_handler_id = capsfilter_queue.connect(
            "overrun", self._log_queue_overrun
        )

        # capsfilter_queue.connect('overrun', self._on_overrun)
        # capsfilter_queue.connect('underrun', self._on_underrun)
        # capsfilter_queue.connect('pushing', self._on_pushing)
        # capsfilter_queue.connect('running', self._on_running)

        self.elements_stack.append(capsfilter_queue)

        ident = pipeline.get_by_name("ident")
        # ident.connect('handoff', self._on_handoff)

        self.elements_stack.append(ident)

        logger.debug("After all self.elements_stack.append() calls")
        # Set up the queue for data and run the main thread.
        self.queue = queue.Queue(QUEUE_SIZE)
        self.thread = get_loop_thread()

    # NOTE: Disabled since we aren't connecting to handoff
    # def _on_handoff(self, element, buf):
    #     logger.debug('buf:')
    #     pp.pprint(buf)
    #     pp.pprint(dir(buf))
    #     logger.debug("on_handoff - %d bytes".format(len(buf))

    #     if self.signed is None:
    #         # only ever one caps struct on our buffers
    #         struct = buf.get_caps().get_structure(0)

    #         # I think these are always set too, but catch just in case
    #         try:
    #             self.signed = struct["signed"]
    #             self.depth = struct["depth"]
    #             self.rate = struct["rate"]
    #             self.channels = struct["channels"]
    #         except Exception:
    #             logger.debug('on_handoff: missing caps')
    #             pass

    # raw = str(buf)
    #
    # # print 'len(raw) =', len(raw)
    #
    # sm = 0
    # for i in range(0, len(raw)):
    #     sm += ord(raw[i])
    # # print sm
    # FIXEME: Add somthing like analyse.py
    # SOURCE: https://github.com/jcupitt/huebert/blob/master/huebert/audio.py

    def _on_state_changed(self, bus, msg):
        states = msg.parse_state_changed()
        # To state is PLAYING
        if msg.src.get_name() == "pipeline0" and states[1] == 4:
            logger.info("Inside pipeline0 on _on_state_changed")
            logger.info("State: {}".format(states[1]))
            self.ready_sem.release()
            return False
        else:
            # logger.error('NOTHING RETURNED in _on_state_changed')
            logger.info("State: {}".format(states[1]))

    def _on_overrun(self, element):
        logging.debug("on_overrun")

    def _on_underrun(self, element):
        logging.debug("on_underrun")

    def _on_running(self, element):
        logging.debug("on_running")

    def _on_pushing(self, element):
        logging.debug("on_pushing")

    def _notify_caps(self, pad, args):
        """The callback for the sinkpad's "notify::caps" signal.
        """
        # The sink has started to receive data, so the stream is ready.
        # This also is our opportunity to read information about the
        # stream.
        self.got_caps = True

        # Allow constructor to complete.
        self.ready_sem.release()

    _got_a_pad = False

    def _log_queue_overrun(self, queue):
        cbuffers = queue.get_property("current-level-buffers")
        cbytes = queue.get_property("current-level-bytes")
        ctime = queue.get_property("current-level-time")

    def _new_sample(self, sink):
        """The callback for appsink's "new-sample" signal.
        """
        if self.running:
            # New data is available from the pipeline! Dump it into our
            # queue (or possibly block if we're full).
            buf = sink.emit("pull-sample").get_buffer()
            # IMPORTANT!!!!!
            # NOTE: I think this is causing a deadlock
            self.queue.put(buf.extract_dup(0, buf.get_size()))
        # "OK = 0. Data passing was ok.""
        return Gst.FlowReturn.OK

    def _on_message(self, bus, message):
        """The callback for GstBus's "message" signal (for two kinds of
        messages).
        """
        # logger.debug("[_on_message](%s, %s)", bus, message)
        if not self.finished:
            struct = message.get_structure()

            if message.type == Gst.MessageType.EOS:
                # The file is done. Tell the consumer thread.
                self.queue.put(SENTINEL)
                if not self.got_caps:
                    logger.error(
                        "If the stream ends before _notify_caps was called, this is an invalid stream."
                    )
                    # If the stream ends before _notify_caps was called, this
                    # is an invalid file.
                    self.read_exc = NoStreamError()
                    self.ready_sem.release()
            elif struct and struct.get_name() == "pocketsphinx":
                # "final", G_TYPE_BOOLEAN, final,
                # SOURCE: https://github.com/cmusphinx/pocketsphinx/blob/1fdc9ccb637836d45d40956e745477a2bd3b470a/src/gst-plugin/gstpocketsphinx.c
                if struct["final"]:
                    logger.info("confidence: {}".format(struct["confidence"]))
                    logger.info("hypothesis: {}".format(struct["hypothesis"]))
                    if self.kw_found == 1:
                        # If keyword is set AND qualifier
                        # then perform action
                        self.run_cmd(struct["hypothesis"])
                    else:
                        # If it's the main keyword,
                        # set values wait for qualifier
                        self.result(struct["hypothesis"])
            elif message.type == Gst.MessageType.ERROR:
                gerror, debug = message.parse_error()
                pp.pprint(("gerror,debug:", gerror, debug))
                if "not-linked" in debug:
                    logger.error("not-linked")
                    self.read_exc = NoStreamError()
                elif "No such device" in debug:
                    logger.error("No such device")
                    self.read_exc = NoStreamError()
                else:
                    logger.info("FileReadError")
                    pp.pprint(
                        ("SOME FileReadError", bus, message, struct, struct.get_name())
                    )
                    self.read_exc = FileReadError(debug)
                self.ready_sem.release()

    # Cleanup.
    def close(self, force=False):
        """Close the file and clean up associated resources.

        Calling `close()` a second time has no effect.
        """
        if self.running or force:
            self.running = False
            self.finished = True

            try:
                gst_bus = self.gst_bus_stack[0]
            except Exception:
                logger.error("Failed to get gst_bus from gst_bus_stack[0]")

            if gst_bus:
                gst_bus.remove_signal_watch()
                if self.bus_message_element_handler_id:
                    gst_bus.disconnect(self.bus_message_element_handler_id)
                if self.bus_message_error_handler_id:
                    gst_bus.disconnect(self.bus_message_error_handler_id)
                if self.bus_message_eos_handler_id:
                    gst_bus.disconnect(self.bus_message_eos_handler_id)
                if self.bus_message_state_changed_handler_id:
                    gst_bus.disconnect(self.bus_message_state_changed_handler_id)

            self.bus = None
            self.pipeline = None
            self.codec = None
            self.bitrate = -1
            self.state = None

            # Unregister for signals, which we registered for above with
            # `add_signal_watch`. (Without this, GStreamer leaks file
            # descriptors.)
            logger.debug("BEFORE p = self.pipelines_stack[0]")
            p = self.pipelines_stack[0]
            p.get_bus().remove_signal_watch()
            logger.debug("AFTER p.get_bus().remove_signal_watch()")

            # Block spurious signals.
            appsink = self.elements_stack[0]
            appsink.get_static_pad("sink").disconnect(self.caps_handler)

            # Make space in the output queue to let the decoder thread
            # finish. (Otherwise, the thread blocks on its enqueue and
            # the interpreter hangs.)
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass

            # Halt the pipeline (closing file).
            self.stop()

            # Delete the pipeline object. This seems to be necessary on Python
            # 2, but not Python 3 for some reason: on 3.5, at least, the
            # pipeline gets dereferenced automatically.
            del p

    def __del__(self):
        self.close()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class ListenerDemo:
    """ListenerDemo Class strictly for testing out ScarlettListenerI."""

    @abort_on_exception
    def __init__(self):
        self.manager = ThreadManager(1)
        self.add_thread()

    def quit(self, timeout):
        # NOTE: when we connect this as a callback to a signal being emitted, we'll need to chage quit to look
        # like this quit(self, sender, event):
        self.manager.stop_all_threads(block=True, timeout=timeout)

    def stop_threads(self, *args):
        self.manager.stop_all_threads()

    def add_thread(self):
        # NOTE: if we do this via a gobject connect we need def add_thread(self, sender):
        # make a thread and start it

        # Load in config object, and set default device information
        config_manager = ConfigManager()
        config_manager.load()
        name = "Thread #{}".format(random.randint(0, 1000))
        self.manager.make_thread(
            self.thread_finished,  # completedCb
            self.thread_progress,  # progressCb
            ScarlettListenerI,  # threadclass
            name,
            config_manager,
        )  # args[1] <- verify that this is value is correct

    def thread_finished(self, thread):
        logger.debug("thread_finished.")

    def thread_progress(self, thread):
        logger.debug("thread_progress.")


if __name__ == "__main__":
    if os.environ.get("SCARLETT_DEBUG_MODE"):
        import faulthandler

        faulthandler.register(signal.SIGUSR2, all_threads=True)
        faulthandler.enable(file=sys.stderr, all_threads=True)
        print(
            "Installed SIGUSR1 handler to print stack traces: pkill -USR1 -f scarlett_os.listener"
        )

        from scarlett_os.internal.debugger import init_debugger
        from scarlett_os.internal.debugger import set_gst_grapviz_tracing
        from scarlett_os.internal.debugger import enable_remote_debugging

        enable_remote_debugging()

        init_debugger()
        set_gst_grapviz_tracing()
        # Example of how to use it

    from scarlett_os.logger import setup_logger

    setup_logger()

    loop = GObject.MainLoop()

    ###################################################################################################################
    # source: http://stackoverflow.com/questions/28465611/python-dbusgmainloop-inside-thread-and-try-and-catch-block
    # Catch KeyboardInterrupt and stop pipeline from running!
    # run_event = threading.Event()
    # run_event.set()
    ###################################################################################################################

    _INSTANCE = demo = ListenerDemo()

    if os.environ.get("TRAVIS_CI"):
        try:
            loop.run()
        except KeyboardInterrupt:
            logger.warning("***********************************************")
            logger.warning('Note: Added an exception "pass" for KeyboardInterrupt')
            logger.warning(
                "It is very possible that this might mask other errors happening with the application."
            )
            logger.warning("Remove this while testing manually")
            logger.warning("***********************************************")
            # NOTE: fixme, we still have a leak in the gst pipeline, it doesn't actually stop.
            demo.quit(1)
            loop.quit()
            # pass
        except:
            raise
    else:
        # Close into a ipython debug shell
        loop.run()

    def sigint_handler_to_pdb(*args):
        """Exit on Ctrl+C to PDB"""

        # Unregister handler, next Ctrl-C will kill app
        # TODO: figure out if this is really needed or not

        signal.signal(signal.SIGINT, signal.SIG_DFL)

        demo.quit(1)

        loop.quit()

    def sigint_handler_exit(*args):
        """Exit on Ctrl+C from python"""

        # signal.signal(signal.SIGINT, signal.SIG_DFL)

        demo.quit(1)

        loop.quit()

    # quit cleanly if we're in CI
    if os.environ.get("TRAVIS_CI"):
        print("registering sigint_handler = sigint_handler_exit")
        sigint_handler = sigint_handler_exit
    else:
        print("registering sigint_handler = sigint_handler_to_pdb")
        sigint_handler = sigint_handler_to_pdb

    signal.signal(signal.SIGINT, sigint_handler)

    # BEFORE REFACTOR
    # from scarlett_os.logger import setup_logger
    # setup_logger()
    #
    # demo = ListenerDemo()
    # loop.run()
    #
    # def sigint_handler(*args):
    #     """Exit on Ctrl+C"""
    #
    #     # Unregister handler, next Ctrl-C will kill app
    #     # TODO: figure out if this is really needed or not
    #     signal.signal(signal.SIGINT, signal.SIG_DFL)
    #
    #     demo.quit()
    #
    #     loop.quit()
    #
    # signal.signal(signal.SIGINT, sigint_handler)
