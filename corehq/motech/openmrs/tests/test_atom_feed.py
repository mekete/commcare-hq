from __future__ import absolute_import
from __future__ import unicode_literals

import doctest

from django.test import SimpleTestCase

import corehq.motech.openmrs.atom_feed


class GetTimestampTests(SimpleTestCase):

    def test_no_node(self):
        pass

    def test_bad_date(self):
        pass

    def test_timezone(self):
        pass


class GetPatientUuidTests(SimpleTestCase):

    def test_no_content_node(self):
        pass

    def test_bad_cdata(self):
        pass

    def test_success(self):
        pass


class DocTests(SimpleTestCase):
    def test_doctests(self):
        results = doctest.testmod(corehq.motech.openmrs.atom_feed)
        self.assertEqual(results.failed, 0)
