import unittest
from tests.test_orchestrator_service_integration import OrchestratorServiceIntegrationTests
import sys

if __name__ == "__main__":
    suite = unittest.TestSuite()
    suite.addTest(OrchestratorServiceIntegrationTests("test_worker_handoff_service_matches_orchestrator_handoff_contract"))
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
