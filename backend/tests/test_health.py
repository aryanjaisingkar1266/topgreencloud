import unittest

from app.main import app, health


class HealthTests(unittest.TestCase):
    def test_health_route(self) -> None:
        self.assertEqual(health(), {"status": "healthy"})
        self.assertTrue(any(route.path == "/health" for route in app.routes))


if __name__ == "__main__":
    unittest.main()
