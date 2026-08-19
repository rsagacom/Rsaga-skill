from pathlib import Path
import unittest


WEB_SOURCE = (Path(__file__).parents[1] / "web" / "app" / "page.tsx").read_text(encoding="utf-8")


class WebReadinessContractTests(unittest.TestCase):
    def test_workbench_loads_and_renders_server_side_project_readiness(self):
        self.assertIn('api<ProjectReadiness>(`/api/projects/${project.id}/readiness`)', WEB_SOURCE)
        self.assertIn('data-testid="project-readiness"', WEB_SOURCE)
        self.assertIn('data-readiness-stage={item.key}', WEB_SOURCE)
        self.assertIn('ready_for_delivery', WEB_SOURCE)

    def test_readiness_exposes_human_review_boundary(self):
        self.assertIn('readiness.needs_human_review', WEB_SOURCE)
        self.assertIn('待人工复核', WEB_SOURCE)

    def test_unknown_visual_review_exposes_manual_decision_actions(self):
        self.assertIn('/review/decision', WEB_SOURCE)
        self.assertIn('"Idempotency-Key": `manual-review:${asset.id}:${status}:${requestId()}`', WEB_SOURCE)
        self.assertIn('人工通过视觉审核', WEB_SOURCE)
        self.assertIn('人工驳回视觉审核', WEB_SOURCE)
        self.assertIn('asset.reviews?.[0]?.status === "UNKNOWN"', WEB_SOURCE)

    def test_first_run_onboarding_has_templates_and_next_action_bridge(self):
        for marker in (
            "PROJECT_TEMPLATES",
            "QUICK START",
            "applyProjectTemplate",
            "newProjectStyle",
            'data-testid="next-action"',
            "nextActionForStage",
            'id=\"source-panel\"',
            '"shots-panel"',
        ):
            self.assertIn(marker, WEB_SOURCE)
        self.assertIn("style: newProjectStyle", WEB_SOURCE)


if __name__ == "__main__":
    unittest.main()
