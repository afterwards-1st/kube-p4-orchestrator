"""P4 路由快照解析的本地测试，不访问真实 BMv2。"""

import unittest

from p4_route_snapshot import parse_routes, render_restore_cli


SAMPLE_DUMP = """
Dumping entry 0x0
Match key:
* task_hdr.task_tag             : EXACT     01
* scalars.userMetadata.batchable: EXACT     01
* task_hdr.flow_id              : EXACT     02
Action entry: MyIngress.set_backend - 08
Dumping entry 0x1
Match key:
* task_hdr.task_tag             : EXACT     01
* scalars.userMetadata.batchable: EXACT     01
* task_hdr.flow_id              : EXACT     01
Action entry: MyIngress.set_backend - 07
"""


class P4RouteSnapshotTests(unittest.TestCase):
    def test_dump_is_parsed_as_sorted_decimal_routes(self):
        self.assertEqual([(1, 7), (2, 8)], parse_routes(SAMPLE_DUMP))

    def test_restore_cli_is_deterministic(self):
        self.assertEqual(
            "table_clear udp_tag_table\n"
            "table_add udp_tag_table set_backend 1 1 1 => 7\n"
            "table_add udp_tag_table set_backend 1 1 2 => 8\n",
            render_restore_cli([(2, 8), (1, 7)]),
        )

    def test_empty_dump_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "没有可恢复"):
            parse_routes("TABLE ENTRIES\n")


if __name__ == "__main__":
    unittest.main()
