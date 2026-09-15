import importlib.util
import struct
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "send_batchable_burst.py"
SPEC = importlib.util.spec_from_file_location("send_batchable_burst", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WorkerLoadGeneratorTests(unittest.TestCase):
    def test_packet_matches_worker_protocol(self):
        packet = MODULE.build_packet(7, "hello", tag=3)
        magic, tag, flags, flow_id = struct.unpack("BBBB", packet[:4])

        self.assertEqual(0xAB, magic)
        self.assertEqual(3, tag)
        self.assertEqual(0x01, flags)
        self.assertEqual(7, flow_id)
        self.assertEqual(b"hello", packet[4:])

    def test_packet_rejects_values_outside_one_byte(self):
        with self.assertRaises(ValueError):
            MODULE.build_packet(256, "bad")
        with self.assertRaises(ValueError):
            MODULE.build_packet(1, "bad", tag=-1)

    def test_burst_size_cannot_trigger_full_batch_immediately(self):
        with self.assertRaises(ValueError):
            MODULE.send_bursts("127.0.0.1", 9000, 1, 16, 1, 0)


if __name__ == "__main__":
    unittest.main()
