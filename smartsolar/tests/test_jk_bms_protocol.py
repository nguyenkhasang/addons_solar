"""Offline tests run directly; fixture is real telemetry without device-info secrets."""
import importlib.util
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('jk_protocol', ROOT / 'services/jk_bms/protocol.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
RAW = bytes.fromhex((ROOT / 'tests/fixtures/jk_pb1a16s10p_19_10.hex').read_text())


class TestJkProtocol(unittest.TestCase):
    def test_real_fixture(self):
        s = p.decode_telemetry(RAW)
        self.assertEqual(len(s['cells']), 16)
        self.assertAlmostEqual(s['voltage'], 52.376)
        self.assertAlmostEqual(s['current'], -3.8)
        self.assertEqual(s['soc'], 34)
        self.assertAlmostEqual(s['power'], -199.0288)
        self.assertAlmostEqual(s['cell_delta_voltage'], .004)
        self.assertEqual(s['cell_min_index'], 5)
        self.assertEqual(s['cell_max_index'], 2)
        self.assertAlmostEqual(s['battery_temperature_1'], 34.7)
        self.assertEqual(s['nominal_capacity'], 100)

    def test_all_fragment_boundaries_and_concatenation(self):
        for boundary in range(1, 300):
            a = p.FrameAssembler()
            self.assertEqual(a.feed(RAW[:boundary]), [])
            self.assertEqual(a.feed(RAW[boundary:] + RAW), [RAW, RAW])
        a = p.FrameAssembler()
        out = []
        for b in b'noise' + RAW:
            out.extend(a.feed(bytes([b])))
        self.assertEqual(out, [RAW])

    def test_checksum_failure_and_recovery(self):
        corrupt = bytearray(RAW); corrupt[100] ^= 1
        a = p.FrameAssembler()
        self.assertEqual(a.feed(corrupt + RAW), [RAW])
        self.assertEqual(a.invalid_frames, 1)
        with self.assertRaises(ValueError): p.decode_telemetry(corrupt)
        self.assertEqual(p.FrameAssembler().feed(b'x' * 100000), [])

    def test_positive_current_signed_power(self):
        f = bytearray(RAW); struct.pack_into('<i', f, 158, 2000); f[299] = sum(f[:299]) & 255
        s = p.decode_telemetry(f)
        self.assertEqual(s['current'], 2)
        self.assertGreater(s['power'], 0)

    def test_only_two_zero_payload_read_requests(self):
        for command in range(256):
            if command in (0x96, 0x97):
                f = p.read_request(command)
                self.assertEqual(f[5:19], bytes(14))
                self.assertEqual(sum(f[:19]) & 255, f[19])
            else:
                with self.assertRaises(ValueError): p.read_request(command)

    def test_stale_offline_and_validation(self):
        self.assertEqual(p.connection_status(None), 'offline')
        self.assertEqual(p.connection_status(9), 'online')
        self.assertEqual(p.connection_status(10), 'stale')
        self.assertEqual(p.connection_status(61), 'offline')
        for key, value in [('soc', 101), ('current', float('nan')), ('voltage', -1), ('cells', [3.3])]:
            s = p.decode_telemetry(RAW); s[key] = value
            with self.assertRaises(ValueError): p.validate_state(s)


if __name__ == '__main__':
    unittest.main()
