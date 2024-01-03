import unittest
from unittest import mock
import random

import numpy as np

from ibllib.tests import TEST_DB
from ibllib.qc.base import QC
from one.api import ONE
from one.registration import RegistrationClient

one = ONE(**TEST_DB)


class TestQC(unittest.TestCase):
    """Test base QC class."""

    eid = None
    """str: An experiment UUID to use for updating QC fields."""

    @classmethod
    def setUpClass(cls):
        date = f'20{random.randint(0, 30):02}-{random.randint(1, 12):02}-{random.randint(1, 28):02}'
        _, eid = RegistrationClient(one).create_new_session('ZM_1150', date=date)
        cls.eid = str(eid)

    def setUp(self) -> None:
        ses = one.alyx.rest('sessions', 'partial_update', id=self.eid, data={'qc': 'NOT_SET'})
        assert ses['qc'] == 'NOT_SET', 'failed to reset qc field for test'
        extended = one.alyx.json_field_write('sessions', field_name='extended_qc',
                                             uuid=self.eid, data={})
        assert not extended, 'failed to reset extended_qc field for test'
        self.qc = QC(self.eid, one=one)

    def test__set_eid_or_path(self) -> None:
        """Test setting both the eid and session path when providing one or the other"""
        # Check that eid was set by constructor
        self.assertEqual(self.qc.eid, self.eid, 'failed to set eid in constructor')
        expected_path = one.eid2path(self.eid)
        self.assertEqual(self.qc.session_path, expected_path, 'failed to set path in constructor')
        self.qc.eid = self.qc.session_path = None  # Reset both properties

        # Test handling of valid eid
        self.qc._set_eid_or_path(self.eid)  # Provide eid
        self.assertEqual(self.qc.eid, self.eid, 'failed to set valid eid')
        self.assertEqual(self.qc.session_path, expected_path, 'failed to set path from eid')

        # Test handling of session path
        self.qc.eid = self.qc.session_path = None  # Reset both properties
        self.qc._set_eid_or_path(expected_path)  # Provide eid
        self.assertEqual(self.qc.eid, self.eid, 'failed to set eid from path')
        self.assertEqual(self.qc.session_path, expected_path, 'failed to set valid session path')

        # Test handling of unknown input
        self.qc.eid = self.qc.session_path = None  # Reset both properties
        with self.assertRaises(ValueError):
            self.qc._set_eid_or_path('invalid')  # Provide eid
        self.assertIsNone(self.qc.eid)
        self.assertIsNone(self.qc.session_path)

    def test_update(self) -> None:
        """Test setting the QC field in Alyx"""
        # Fist check the default outcome
        self.assertEqual(self.qc.outcome, 'NOT_SET', 'unexpected default QC outcome')

        # Test setting outcome
        outcome = 'PASS'
        current = self.qc.update(outcome)
        self.assertEqual(outcome, current, 'Failed to update QC field')
        # Check that extended QC field was updated
        extended = one.alyx.get('/sessions/' + self.eid, clobber=True)['extended_qc']
        updated = 'experimenter' in extended and extended['experimenter'] == outcome
        self.assertTrue(updated, 'failed to update extended_qc field')
        # Check that outcome property is set
        self.assertEqual(outcome, self.qc.outcome, 'Failed to update outcome attribute')

        # Test setting namespace
        outcome = 'fail'  # Check handling of lower case
        namespace = 'task'
        current = self.qc.update(outcome, namespace=namespace)
        self.assertEqual(outcome.upper(), current, 'Failed to update QC field')
        extended = one.alyx.get('/sessions/' + self.eid, clobber=True)['extended_qc']
        updated = namespace in extended and extended[namespace] == outcome.upper()
        self.assertTrue(updated, 'failed to update extended_qc field')

        # Test setting lower outcome: update should not occur if overall outcome is more severe
        outcome = 'PASS'  # Check handling of lower case
        namespace = 'task'
        current = self.qc.update(outcome)
        self.assertNotEqual(outcome, current, 'QC field updated with less severe outcome')
        extended = one.alyx.get('/sessions/' + self.eid, clobber=True)['extended_qc']
        updated = namespace in extended and extended[namespace] != outcome
        self.assertTrue(updated, 'failed to update extended_qc field')

        # Test setting lower outcome with override: update should still occur
        outcome = 'NOT_SET'  # Check handling of lower case
        namespace = 'task'
        current = self.qc.update(outcome, override=True, namespace=namespace)
        self.assertEqual(outcome, current, 'QC field updated with less severe outcome')
        extended = one.alyx.get('/sessions/' + self.eid, clobber=True)['extended_qc']
        updated = namespace in extended and extended[namespace] == outcome
        self.assertTrue(updated, 'failed to update extended_qc field')

        # Test setting invalid outcome
        with self.assertRaises(ValueError):
            self.qc.update('%INVALID%')

    def test_extended_qc(self) -> None:
        """Test that the extended_qc JSON field is correctly updated."""
        current = one.alyx.rest('sessions', 'read', id=self.eid)['extended_qc']
        data = {'_qc_test_foo': np.random.rand(), '_qc_test_bar': np.random.rand()}
        updated = self.qc.update_extended_qc(data)
        self.assertEqual(updated, {**current, **data}, 'failed to update the extended qc')

    def test_outcome_setter(self):
        """Test for QC.outcome property setter."""
        qc = self.qc
        qc.outcome = 'Fail'
        self.assertEqual(qc.outcome, 'FAIL')
        # Test setting invalid outcome
        with self.assertRaises(ValueError):
            qc.outcome = '%INVALID%'
        qc.outcome = 'PASS'
        self.assertEqual(qc.outcome, 'FAIL')

        # Set remote session to 'PASS' and check object reflects this on init
        ses = one.alyx.rest('sessions', 'partial_update', id=self.eid, data={'qc': 'PASS'})
        assert ses['qc'] == 'PASS', 'failed to reset qc field for test'
        qc = QC(self.eid, one=one)
        self.assertEqual(qc.outcome, 'PASS')

    def test_code_to_outcome(self):
        """Test for QC.code_to_outcome method."""
        self.assertEqual(QC.code_to_outcome(3), 'FAIL')

    def test_overall_outcome(self):
        """Test for QC.overall_outcome method."""
        self.assertEqual(QC.overall_outcome(['PASS', 'NOT_SET', None, 'FAIL']), 'FAIL')

    def test_compute_outcome_from_extended_qc(self):
        """Test for QC.compute_outcome_from_extended_qc method."""
        detail = {'extended_qc': {'foo': 'FAIL', 'bar': 'WARNING', '_baz_': 'CRITICAL'},
                  'json': {'extended_qc': {'foo': 'PASS', 'bar': 'WARNING', '_baz_': 'CRITICAL'}}}
        with mock.patch.object(self.qc.one.alyx, 'get', return_value=detail):
            self.qc.json = False
            self.assertEqual(self.qc.compute_outcome_from_extended_qc(), 'FAIL')
            self.qc.json = True
            self.assertEqual(self.qc.compute_outcome_from_extended_qc(), 'WARNING')

    @classmethod
    def tearDownClass(cls):
        one.alyx.rest('sessions', 'delete', id=cls.eid)


if __name__ == '__main__':
    unittest.main(exit=False, verbosity=2)
