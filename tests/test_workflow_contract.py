import unittest

from studio_core.workflow_contract import validate_comfyui_api_workflow


class ComfyUIWorkflowContractTests(unittest.TestCase):
    def test_accepts_api_format_nodes_and_references(self):
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
            "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        }
        self.assertEqual(validate_comfyui_api_workflow(workflow), [])

    def test_rejects_ui_workflow_or_missing_node_contract(self):
        self.assertEqual(validate_comfyui_api_workflow([]), ["workflow-must-be-object"])
        self.assertEqual(validate_comfyui_api_workflow({}), ["workflow-must-be-non-empty-object"])
        self.assertIn(
            "workflow-node-class-type-required",
            validate_comfyui_api_workflow({"1": {"inputs": {}}}),
        )

    def test_rejects_missing_node_reference_without_leaking_payload(self):
        errors = validate_comfyui_api_workflow(
            {"save": {"class_type": "SaveImage", "inputs": {"images": ["missing", 0]}}}
        )
        self.assertEqual(errors, ["workflow-node-reference-missing"])

    def test_role_contract_requires_runtime_business_inputs(self):
        image_errors = validate_comfyui_api_workflow(
            {"save": {"class_type": "SaveImage", "inputs": {}}}, role="image"
        )
        self.assertEqual(image_errors, ["workflow-image-prompt-placeholder-required"])
        video_errors = validate_comfyui_api_workflow(
            {"save": {"class_type": "SaveVideo", "inputs": {}}}, role="video"
        )
        self.assertEqual(video_errors, ["workflow-video-identity-placeholder-required"])

    def test_rejects_unsupported_placeholder_without_echoing_it(self):
        errors = validate_comfyui_api_workflow(
            {"save": {"class_type": "SaveImage", "inputs": {"prompt": "{{SECRET_NODE_INPUT}}"}}}
        )
        self.assertEqual(errors, ["workflow-placeholder-unsupported"])


if __name__ == "__main__":
    unittest.main()
