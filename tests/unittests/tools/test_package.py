#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
test_package
----------------------------------

Tests for `scarlett_os` module.
"""

# import ipdb

# import mock
import builtins
import imp
import os
import signal
import sys
import unittest
import unittest.mock as mock

# import threading
import pytest

import scarlett_os
from scarlett_os.tools import package  # Module with our thing to test

# source: https://github.com/YosaiProject/yosai/blob/master/test/isolated_tests/core/conf/conftest.py
# FIXME: Since we currently have an issue with mocks leaking into other tests,
# this fixture ensures that we isolate the patched object, stop mocks,
# and literally re-import modules to set environment back to normal.
# It's possible this will all get fixed when we upgrade to a later version of python past 3.5.2
@pytest.fixture(scope='function')
def package_unit_mocker_stopall(mocker):
    "Stop previous mocks, yield mocker plugin obj, then stopall mocks again"
    print('Called [setup]: mocker.stopall()')
    mocker.stopall()
    print('Called [setup]: imp.reload(package)')
    imp.reload(package)
    yield mocker
    print('Called [teardown]: mocker.stopall()')
    mocker.stopall()
    print('Called [setup]: imp.reload(package)')
    imp.reload(package)

# SOURCE: https://github.com/ansible/ansible/blob/370a7ace4b3c8ffb6187900f37499990f1b976a2/test/units/module_utils/basic/test_atomic_move.py
@pytest.fixture
def sys_and_site_mocks(package_unit_mocker_stopall):
    environ = dict()
    mocks = {
        'environ': package_unit_mocker_stopall.patch('scarlett_os.user.os.environ', environ),
        'getlogin': package_unit_mocker_stopall.patch('scarlett_os.user.os.getlogin'),
        'getuid': package_unit_mocker_stopall.patch('scarlett_os.user.os.getuid'),
        'getpass': package_unit_mocker_stopall.patch('scarlett_os.user.getpass')
    }

    mocks['getlogin'].return_value = 'root'
    mocks['getuid'].return_value = 0
    mocks['getpass'].getuser.return_value = 'root'

    mocks['environ']['LOGNAME'] = 'root'
    mocks['environ']['USERNAME'] = 'root'
    mocks['environ']['USER'] = 'root'
    mocks['environ']['LNAME'] = 'root'

    yield mocks


@pytest.fixture
def fake_stat(package_unit_mocker_stopall):
    stat1 = package_unit_mocker_stopall.MagicMock()
    stat1.st_mode = 0o0644
    stat1.st_uid = 0
    stat1.st_gid = 0
    yield stat1

@pytest.mark.unittest
@pytest.mark.scarlettonly
@pytest.mark.scarlettonlyunittest
class TestPackage(object):

    # pytest -s -p no:timeout -k test_get_uniq_list --pdb
    def test_get_uniq_list(self, sys_and_site_mocks):
        seq = ['/usr/local/share/jhbuild/sitecustomize', '/usr/lib/python3.5/dist-packages', '/usr/lib/python3.5/site-packages']

        assert scarlett_os.tools.package.get_uniq_list(seq) == ['/usr/local/share/jhbuild/sitecustomize', '/usr/lib/python3.5/dist-packages', '/usr/lib/python3.5/site-packages']

    def test_get_uniq_list_with_dups(self, sys_and_site_mocks):
        seq = ['/usr/local/share/jhbuild/sitecustomize', '/usr/lib/python3.5/dist-packages', '/usr/lib/python3.5/site-packages', '/usr/local/share/jhbuild/sitecustomize', '/usr/lib/python3.5/dist-packages', '/usr/lib/python3.5/site-packages', '/usr/local/share/jhbuild/sitecustomize', '/usr/lib/python3.5/dist-packages', '/usr/lib/python3.5/site-packages', '/usr/local/share/jhbuild/sitecustomize', '/usr/lib/python3.5/dist-packages', '/usr/lib/python3.5/site-packages']

        assert scarlett_os.tools.package.get_uniq_list(seq) == ['/usr/local/share/jhbuild/sitecustomize', '/usr/lib/python3.5/dist-packages', '/usr/lib/python3.5/site-packages']
