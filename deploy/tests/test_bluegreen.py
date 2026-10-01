"""Unit tests for deploy/bluegreen.py.

Run with:  python3 -m unittest discover -s deploy/tests -v

A fake kubectl records every call, so the controller's decisions can be
tested without a real cluster. These run in CI before anything is deployed.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bluegreen as bg  # noqa: E402


def sample(verdict_inputs, limits=None):
    rps, error_ratio, p95, restarts, not_ready = verdict_inputs
    return bg.judge_sample(
        bg.Sample(0, rps, error_ratio, p95, restarts, not_ready),
        limits or bg.Thresholds(),
    )


class ColourTests(unittest.TestCase):
    def test_other_colour_flips(self):
        self.assertEqual(bg.other_colour("blue"), "green")
        self.assertEqual(bg.other_colour("green"), "blue")

    def test_unknown_colour_is_rejected(self):
        with self.assertRaises(bg.ControllerError):
            bg.other_colour("red")


class JudgeSampleTests(unittest.TestCase):
    def test_healthy_traffic(self):
        self.assertEqual(sample((10, 0.0, 0.05, 0, 0)).verdict, "healthy")

    def test_error_rate_above_threshold_is_unhealthy(self):
        result = sample((10, 0.30, 0.05, 0, 0))
        self.assertEqual(result.verdict, "unhealthy")
        self.assertIn("error rate", result.reasons[0])

    def test_error_rate_at_threshold_is_still_healthy(self):
        self.assertEqual(sample((10, 0.05, 0.05, 0, 0)).verdict, "healthy")

    def test_slow_responses_are_unhealthy(self):
        self.assertEqual(sample((10, 0.0, 2.5, 0, 0)).verdict, "unhealthy")

    def test_low_traffic_gives_no_data_not_a_false_pass(self):
        self.assertEqual(sample((0.1, 0.0, 0.05, 0, 0)).verdict, "no-data")
        self.assertEqual(sample((None, None, None, 0, 0)).verdict, "no-data")

    def test_crashing_pods_are_unhealthy_even_without_traffic(self):
        self.assertEqual(sample((None, None, None, 2, 0)).verdict, "unhealthy")
        self.assertEqual(sample((None, None, None, 0, 1)).verdict, "unhealthy")


class GateDecisionTests(unittest.TestCase):
    limits = bg.Thresholds(consecutive_failures=2, min_healthy_samples=3)

    def run_gate(self, verdicts, finished):
        samples = [bg.Sample(i, 1, 0, 0, 0, 0, verdict=v) for i, v in enumerate(verdicts)]
        return bg.gate_decision(samples, self.limits, finished)

    def test_fails_fast_on_consecutive_unhealthy_samples(self):
        self.assertEqual(self.run_gate(["healthy", "unhealthy", "unhealthy"], False), "fail")

    def test_single_blip_does_not_trigger_rollback(self):
        verdicts = ["healthy", "unhealthy", "healthy", "healthy"]
        self.assertEqual(self.run_gate(verdicts, False), "continue")
        self.assertEqual(self.run_gate(verdicts, True), "pass")

    def test_passes_with_enough_healthy_samples(self):
        self.assertEqual(self.run_gate(["healthy"] * 3, True), "pass")

    def test_unverifiable_release_fails_safe(self):
        self.assertEqual(self.run_gate(["no-data", "healthy", "no-data"], True), "fail")


class RenderTests(unittest.TestCase):
    def render(self, **overrides):
        options = dict(colour="green", version="abc1234def", registry="ghcr.io/AtishayDeakin",
                       replicas=2, fault_rate=0.0, fault_delay=0.0)
        options.update(overrides)
        return bg.render_release(**options)

    def test_renders_every_service_for_the_colour(self):
        manifest = self.render()
        for name in bg.deployment_names("green"):
            self.assertIn(f"name: {name}\n", manifest)
        self.assertNotIn("-blue", manifest)
        self.assertNotIn("${", manifest, "a template variable was left unrendered")

    def test_registry_is_lower_cased_for_ghcr(self):
        self.assertIn("image: ghcr.io/atishaydeakin/koalatech-user-service:abc1234def",
                      self.render())

    def test_frontend_only_talks_to_its_own_colour(self):
        self.assertIn("value: -green", self.render())

    def test_fault_injection_values_are_passed_through(self):
        manifest = self.render(fault_rate=0.5, fault_delay=90)
        self.assertIn('value: "0.5"', manifest)
        self.assertIn('value: "90"', manifest)

    def test_invalid_version_is_rejected(self):
        with self.assertRaises(bg.ControllerError):
            self.render(version="abc; rm -rf /")


class FakeKubectl:
    """Pretends to be kubectl for a namespace with blue live and green staged."""

    def __init__(self, live="blue", ready_colours=("blue", "green")):
        self.calls = []
        self.selector = {"koalatech-prod": live, "koalatech-preview": live}
        self.annotations = {}
        self.ready_colours = set(ready_colours)
        self.scaled = {}

    def deployment(self, name, colour):
        ready = colour in self.ready_colours and self.scaled.get(colour, 2) > 0
        return {
            "metadata": {"name": name, "labels": {"version": "abc1234"}},
            "spec": {"replicas": self.scaled.get(colour, 2)},
            "status": {"availableReplicas": 2 if ready else 0},
        }

    def __call__(self, args, stdin=None):
        self.calls.append((args, stdin))
        if args[:3] == ["-n", "koalatech", "get"]:
            kind, rest = args[3], args[4:]
            if kind == "service":
                name = rest[0]
                return json.dumps({
                    "spec": {"selector": {"app": "frontend", "color": self.selector[name]}},
                    "metadata": {"annotations": dict(self.annotations)},
                })
            if kind == "deployments":
                colour = rest[1].split("=")[1]
                return json.dumps({"items": [
                    self.deployment(n, colour) for n in bg.deployment_names(colour)]})
            if kind == "pods":
                return "pods" if "-o" not in rest or "wide" in rest else json.dumps({"items": []})
        if args[:3] == ["-n", "koalatech", "patch"]:
            patch = json.loads(args[-1])
            self.selector[args[4]] = patch["spec"]["selector"]["color"]
        if args[:3] == ["-n", "koalatech", "annotate"]:
            for pair in args[6:]:
                key, value = pair.split("=", 1)
                self.annotations[key] = value
        if args[:3] == ["-n", "koalatech", "scale"]:
            colour = args[4].rsplit("-", 1)[1]
            self.scaled[colour] = int(args[5].split("=")[1])
        return ""


class Args:
    def __init__(self, **values):
        self.__dict__.update(values)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.outputs = tempfile.NamedTemporaryFile("w+", delete=False)
        os.environ["GITHUB_OUTPUT"] = self.outputs.name
        os.environ.pop("GITHUB_STEP_SUMMARY", None)

    def tearDown(self):
        os.environ.pop("GITHUB_OUTPUT", None)
        os.unlink(self.outputs.name)

    def read_outputs(self):
        with open(self.outputs.name) as handle:
            return dict(line.strip().split("=", 1) for line in handle if "=" in line)

    def test_plan_targets_the_idle_colour(self):
        bg.cmd_plan(bg.Kube(FakeKubectl(live="blue")), Args())
        outputs = self.read_outputs()
        self.assertEqual(outputs["live_color"], "blue")
        self.assertEqual(outputs["target_color"], "green")
        self.assertEqual(outputs["has_previous"], "true")

    def test_first_deployment_has_nothing_to_roll_back_to(self):
        bg.cmd_plan(bg.Kube(FakeKubectl(live="blue", ready_colours=())), Args())
        self.assertEqual(self.read_outputs()["has_previous"], "false")

    def test_swap_moves_production_and_records_history(self):
        fake = FakeKubectl(live="blue")
        fake.annotations[bg.ANNOTATION + "live-version"] = "old1234"
        bg.cmd_swap(bg.Kube(fake), Args(to="green", version="new5678"))
        self.assertEqual(fake.selector["koalatech-prod"], "green")
        self.assertEqual(fake.annotations[bg.ANNOTATION + "previous-version"], "old1234")
        self.assertEqual(fake.annotations[bg.ANNOTATION + "live-version"], "new5678")

    def test_swap_refuses_a_colour_that_is_not_ready(self):
        fake = FakeKubectl(live="blue", ready_colours=("blue",))
        with self.assertRaises(bg.ControllerError):
            bg.cmd_swap(bg.Kube(fake), Args(to="green", version="new5678"))
        self.assertEqual(fake.selector["koalatech-prod"], "blue")

    def test_rollback_restores_previous_colour_and_scales_down_faulty_one(self):
        fake = FakeKubectl(live="green")
        fake.annotations[bg.ANNOTATION + "previous-version"] = "old1234"
        bg.cmd_rollback(bg.Kube(fake), Args(from_colour="green", to="blue", reason="test"))
        self.assertEqual(fake.selector["koalatech-prod"], "blue")
        self.assertEqual(fake.annotations[bg.ANNOTATION + "last-result"], "rolled-back")
        self.assertEqual(fake.annotations[bg.ANNOTATION + "live-version"], "old1234")
        self.assertEqual(fake.scaled["green"], 0)

    def test_rollback_without_a_healthy_previous_release_fails_loudly(self):
        fake = FakeKubectl(live="green", ready_colours=("green",))
        with self.assertRaises(bg.ControllerError):
            bg.cmd_rollback(bg.Kube(fake), Args(from_colour="green", to="blue", reason=""))
        self.assertEqual(fake.selector["koalatech-prod"], "green")

    def test_finalize_retires_the_old_colour(self):
        fake = FakeKubectl(live="green")
        bg.cmd_finalize(bg.Kube(fake), Args(live="green", retire="blue"))
        self.assertEqual(fake.scaled["blue"], 0)
        self.assertNotIn("green", fake.scaled)

    def test_every_kubectl_call_is_pinned_to_the_namespace_or_cluster_scope(self):
        fake = FakeKubectl()
        bg.cmd_plan(bg.Kube(fake), Args())
        for args, _ in fake.calls:
            self.assertEqual(args[:2], ["-n", "koalatech"])


class PromQLTests(unittest.TestCase):
    def test_queries_are_scoped_to_one_colour(self):
        for query in bg.promql_for("green").values():
            self.assertIn('color="green"', query)
            self.assertIn('namespace="koalatech"', query)


if __name__ == "__main__":
    unittest.main()
