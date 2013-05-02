# Copyright (C) 2013 Canonical Ltd.
# Author: Colin Watson <cjwatson@ubuntu.com>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Unit tests for clickpackage.install."""

from __future__ import print_function

__metaclass__ = type
__all__ = [
    'TestClickInstaller',
    ]


import os
import subprocess

from contextlib import closing

try:
    from unittest import mock
except ImportError:
    import mock
try:
    from unittest import skipUnless
except ImportError:
    from unittest2 import skipUnless


from debian.deb822 import Deb822
# BAW 2013-04-16: Get the DebFile class from here because of compatibility
# issues.  See the comments in that module for details.
from clickpackage.install import DebFile

from clickpackage.install import ClickInstaller
from clickpackage.preinst import static_preinst
from clickpackage.tests.helpers import TestCase, mkfile, touch


def mock_quiet_subprocess_call():
    original_call = subprocess.call

    def side_effect(*args, **kwargs):
        if "TEST_VERBOSE" in os.environ:
            return original_call(*args, **kwargs)
        else:
            with open("/dev/null", "w") as devnull:
                return original_call(
                    *args, stdout=devnull, stderr=devnull, **kwargs)

    return mock.patch("subprocess.call", side_effect=side_effect)


class TestClickInstaller(TestCase):
    def setUp(self):
        super(TestClickInstaller, self).setUp()
        self.use_temp_dir()

    def make_fake_package(self, control_fields=None, control_scripts=None,
                          data_files=None):
        """Build a fake package with given contents.

        We can afford to use dpkg-deb here since it's easy, just for testing.
        """
        control_fields = {} if control_fields is None else control_fields
        control_scripts = {} if control_scripts is None else control_scripts
        data_files = [] if data_files is None else data_files

        package_dir = os.path.join(self.temp_dir, "fake-package")
        control_dir = os.path.join(package_dir, "DEBIAN")
        with mkfile(os.path.join(control_dir, "control")) as control:
            for key, value in control_fields.items():
                print('%s: %s' % (key.title(), value), file=control)
            print(file=control)
        for name, contents in control_scripts.items():
            with mkfile(os.path.join(control_dir, name)) as script:
                script.write(contents)
        for name in data_files:
            touch(os.path.join(package_dir, name))
        package_path = '%s.click' % package_dir
        with open("/dev/null", "w") as devnull:
            subprocess.check_call(
                ["dpkg-deb", "--nocheck", "-b", package_dir, package_path],
                stdout=devnull, stderr=devnull)
        return package_path

    def test_audit_control_no_package(self):
        path = self.make_fake_package()
        with closing(DebFile(filename=path)) as package:
            self.assertRaisesRegex(
                ValueError, "No Package field",
                ClickInstaller(self.temp_dir).audit_control, package.control)

    def test_audit_control_package_bad_character(self):
        path = self.make_fake_package(control_fields={"Package": "../evil"})
        with closing(DebFile(filename=path)) as package:
            self.assertRaisesRegex(
                ValueError, "Invalid character '/' in Package: ../evil",
                ClickInstaller(self.temp_dir).audit_control, package.control)

    def test_audit_control_no_click_version(self):
        path = self.make_fake_package(
            control_fields={"Package": "test-package"})
        with closing(DebFile(filename=path)) as package:
            self.assertRaisesRegex(
                ValueError, "No Click-Version field",
                ClickInstaller(self.temp_dir).audit_control, package.control)

    def test_audit_control_bad_click_version(self):
        path = self.make_fake_package(
            control_fields={"Package": "test-package", "Click-Version": "|"})
        with closing(DebFile(filename=path)) as package:
            self.assertRaises(
                ValueError,
                ClickInstaller(self.temp_dir).audit_control, package.control)

    def test_audit_control_new_click_version(self):
        path = self.make_fake_package(
            control_fields={"Package": "test-package", "Click-Version": "999"})
        with closing(DebFile(filename=path)) as package:
            self.assertRaisesRegex(
                ValueError,
                "Click-Version: 999 newer than maximum supported version .*",
                ClickInstaller(self.temp_dir).audit_control, package.control)

    def test_audit_control_no_click_base_system(self):
        path = self.make_fake_package(
            control_fields={"Package": "test-package", "Click-Version": "0.1"})
        with closing(DebFile(filename=path)) as package:
            self.assertRaisesRegex(
                ValueError, "No Click-Base-System field",
                ClickInstaller(self.temp_dir).audit_control, package.control)

    def test_audit_control_bad_click_base_system(self):
        path = self.make_fake_package(
            control_fields={
                "Package": "test-package",
                "Click-Version": "0.1",
                "Click-Base-System": "`",
            })
        with closing(DebFile(filename=path)) as package:
            self.assertRaises(
                ValueError,
                ClickInstaller(self.temp_dir).audit_control, package.control)

    def test_audit_control_new_click_base_system(self):
        path = self.make_fake_package(
            control_fields={
                "Package": "test-package",
                "Click-Version": "0.1",
                "Click-Base-System": "999",
            })
        with closing(DebFile(filename=path)) as package:
            self.assertRaisesRegex(
                ValueError,
                "Click-Base-System: 999 newer than current version .*",
                ClickInstaller(self.temp_dir).audit_control, package.control)

    def test_audit_control_forbids_depends(self):
        path = self.make_fake_package(
            control_fields={
                "Package": "test-package",
                "Click-Version": "0.1",
                "Click-Base-System": "13.04",
                "Depends": "libc6",
            })
        with closing(DebFile(filename=path)) as package:
            self.assertRaisesRegex(
                ValueError, "Depends field is forbidden in Click packages",
                ClickInstaller(self.temp_dir).audit_control, package.control)

    def test_audit_control_forbids_maintscript(self):
        path = self.make_fake_package(
            control_fields={
                "Package": "test-package",
                "Click-Version": "0.1",
                "Click-Base-System": "13.04",
            },
            control_scripts={
                "preinst": "#! /bin/sh\n",
                "postinst": "#! /bin/sh\n",
            })
        with closing(DebFile(filename=path)) as package:
            self.assertRaisesRegex(
                ValueError,
                r"Maintainer scripts are forbidden in Click packages "
                r"\(found: postinst preinst\)",
                ClickInstaller(self.temp_dir).audit_control, package.control)

    def test_audit_passes_correct_package(self):
        path = self.make_fake_package(
            control_fields={
                "Package": "test-package",
                "Click-Version": "0.1",
                "Click-Base-System": "13.04",
            },
            control_scripts={"preinst": static_preinst})
        self.assertEqual(
            "test-package", ClickInstaller(self.temp_dir).audit(path))

    @skipUnless(
        os.path.exists(ClickInstaller(None)._preload_path()),
        "preload bits not built; installing packages will fail")
    def test_install(self, *args):
        path = self.make_fake_package(
            control_fields={
                "Package": "test-package",
                "Version": "1.0",
                "Architecture": "all",
                "Maintainer": "Foo Bar <foo@example.org>",
                "Description": "test",
                "Click-Version": "0.1",
                "Click-Base-System": "13.04",
            },
            control_scripts={"preinst": static_preinst},
            data_files=["foo"])
        root = os.path.join(self.temp_dir, "root")
        with mock_quiet_subprocess_call():
            ClickInstaller(root).install(path)
        self.assertCountEqual([".click.log", "test-package"], os.listdir(root))
        inst_dir = os.path.join(root, "test-package")
        self.assertCountEqual([".click", "foo"], os.listdir(inst_dir))
        status_path = os.path.join(inst_dir, ".click", "status")
        with open(status_path) as status_file:
            status = list(Deb822.iter_paragraphs(status_file))
        self.assertEqual(1, len(status))
        self.assertEqual({
            "Package": "test-package",
            "Status": "install ok installed",
            "Version": "1.0",
            "Architecture": "all",
            "Maintainer": "Foo Bar <foo@example.org>",
            "Description": "test",
            "Click-Version": "0.1",
            "Click-Base-System": "13.04",
        }, status[0])
