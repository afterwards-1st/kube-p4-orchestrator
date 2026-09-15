import unittest

from app.scoring import NodeSnapshot, ResourceRequest, choose_node, score_candidates


class ScoringTests(unittest.TestCase):
    def test_rejects_unhealthy_and_underprovisioned_nodes(self):
        result = score_candidates([
            NodeSnapshot("underpowered", 50, 512),
            NodeSnapshot("not-ready", 1000, 4096, ready=False),
        ], ResourceRequest(100, 1024))
        self.assertFalse(result[0].accepted)
        self.assertFalse(result[1].accepted)
        self.assertIsNone(choose_node(result))

    def test_prefers_healthy_node_with_matching_topology(self):
        result = score_candidates([
            NodeSnapshot("west", 2000, 4096, labels={"topology.kubernetes.io/zone": "west"}),
            NodeSnapshot("east", 2000, 4096, labels={"topology.kubernetes.io/zone": "east"}),
        ], ResourceRequest(500, 512), {"topology.kubernetes.io/zone": "east"})
        self.assertEqual("east", choose_node(result).node)
        self.assertEqual(1.0, choose_node(result).factors["topology"])


if __name__ == "__main__":
    unittest.main()
