"""MNIST checks bind C++ application ownership, configuration and retained model inputs."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import uuid

import test_project_creator as creator_tests
from test_mnist_sample import mnist_fixture
from test_projects_api import wait_job
from zephyr_utilities.features.projects import sample_compatibility as checker
from zephyr_utilities.features.projects.build_workflow import _sample_source_files
from zephyr_utilities.features.projects.creation import path_identity
from zephyr_utilities.features.projects.mnist_sample import validate_mnist_dependency
from zephyr_utilities.features.projects.samples import preview_sample


SAMPLE_ID = "tinyml/mnist-shell"
ENTRYPOINTS = ("src/main.cpp", "src/inference.cpp", "src/assets.cpp")
CONFIG = ("CONFIG_CPP=y\nCONFIG_STD_CPP17=y\nCONFIG_TENSORFLOW_LITE_MICRO=y\nCONFIG_CRC=y\nCONFIG_CONSOLE=y\n"
          "CONFIG_SERIAL=y\nCONFIG_UART_CONSOLE=y\nCONFIG_SHELL=y\nCONFIG_SHELL_BACKEND_SERIAL=y\n"
          "CONFIG_CBPRINTF_FP_SUPPORT=y\n")


def runtime_files():
    contents = {
        "CMakeLists.txt": "target_sources(app PRIVATE src/main.cpp src/inference.cpp src/assets.cpp)\n",
        "prj.conf": CONFIG,
        "src/main.cpp": "SHELL_CMD_REGISTER(ai, &commands, \"MNIST\", NULL);\nint main() { mnist::Initialize(); }\n",
        "src/inference.cpp": "tflite::MicroInterpreter instance;\nresolver.AddFullyConnected();\nresolver.AddSoftmax();\n",
        "src/inference.hpp": "bool Initialize();\n",
        "src/assets.cpp": '#include "model.inc"\n#include "images.inc"\n#include "test_samples.inc"\n',
        "src/assets.hpp": "extern const unsigned char kModel[];\n",
        "model/model_metadata.hpp": 'constexpr char kModelSha256[] = "synthetic";\n',
        "model/metadata.json": '{"origin":"synthetic test fixture"}\n',
        "model/model.inc": "0x00,0x01,0x02,\n",
        "data/images.inc": "0x00,0x7f,0xff,\n",
        "data/test_samples.inc": "{0, 0, {1.0f}},\n",
        "boards/nucleo_h753zi.conf": "CONFIG_MAIN_STACK_SIZE=8192\n",
    }
    return [{"path": name, "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}
            for name, content in contents.items()]


def write_files(source, files):
    for item in files:
        path = source / item["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item["content"])


def configured_output(build, source, *, config=CONFIG, entries=None):
    (build / "zephyr").mkdir(parents=True, exist_ok=True)
    (build / "zephyr/.config").write_text(config)
    if entries is None:
        entries = [{"directory": str(build), "file": str(source / path),
                    "output": f"CMakeFiles/app.dir/{path}.obj"} for path in ENTRYPOINTS]
    (build / "compile_commands.json").write_text(json.dumps(entries))


class MnistCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "application"
        write_files(self.source, runtime_files())
        self.build = self.root / "output"
        dependency = patch("zephyr_utilities.features.projects.mnist_sample.validate_mnist_dependency", create=True)
        self.dependency = dependency.start()
        self.addCleanup(dependency.stop)

    def inspect(self, **options):
        configured_output(self.build, self.source, **options)
        with patch.object(checker, "run_probe") as probe:
            result = checker.inspect_sample_requirements({}, runtime_files(), SAMPLE_ID, self.build,
                                                         source_dir=self.source)
        probe.assert_not_called()
        return result

    def test_cpp_sources_and_enabled_console_framework_config_need_no_peripheral_alias(self):
        result = self.inspect()
        self.assertEqual(result["status"], "compatible")
        self.assertIn("No sensor", " ".join(result["details"]))
        self.assertIn("compilation and hardware operation were not tested", " ".join(result["details"]))
        self.assertFalse((self.build / "zephyr/zephyr.dts").exists())

    def test_missing_framework_cpp_shell_or_console_option_is_a_specific_incompatibility(self):
        for option in ("CONFIG_CPP", "CONFIG_STD_CPP17", "CONFIG_TENSORFLOW_LITE_MICRO", "CONFIG_CONSOLE", "CONFIG_SHELL",
                       "CONFIG_CRC", "CONFIG_SHELL_BACKEND_SERIAL", "CONFIG_CBPRINTF_FP_SUPPORT"):
            with self.subTest(option=option):
                result = self.inspect(config=CONFIG.replace(option + "=y\n", ""))
                self.assertEqual(result["status"], "incompatible")
                self.assertIn(option, result["message"])

    def test_every_cpp_entrypoint_must_belong_to_the_configured_app_target(self):
        for changed in ENTRYPOINTS:
            for replacement in ("missing", "other-source", "other-target", "other-output"):
                with self.subTest(changed=changed, replacement=replacement):
                    entries = [{"directory": str(self.build), "file": str(self.source / path),
                                "output": f"CMakeFiles/app.dir/{path}.obj"} for path in ENTRYPOINTS]
                    selected = next(entry for entry in entries if entry["file"] == str(self.source / changed))
                    if replacement == "missing":
                        entries.remove(selected)
                    elif replacement == "other-source":
                        selected["file"] = str(self.root / "different-app" / changed)
                    elif replacement == "other-target":
                        selected["output"] = f"CMakeFiles/unused.dir/{changed}.obj"
                    else:
                        selected["output"] = str(self.root / "different-output/CMakeFiles/app.dir/source.obj")
                    result = self.inspect(entries=entries)
                    self.assertEqual(result["status"], "unknown")
                    self.assertIn(changed, " ".join(result["details"]))

    def test_relative_cpp_commands_bind_to_main_and_other_runtime_entrypoints(self):
        entries = [{"directory": str(self.source), "file": path,
                    "arguments": ["c++", "-c", path, "-o", f"../output/CMakeFiles/app.dir/{path}.obj"]}
                   for path in ENTRYPOINTS]
        self.assertEqual(self.inspect(entries=entries)["status"], "compatible")

    def test_incomplete_model_inputs_and_unknown_source_version_stay_unknown(self):
        configured_output(self.build, self.source)
        for path in ("model/model.inc", "data/images.inc", "src/inference.hpp"):
            files = [item for item in runtime_files() if item["path"] != path]
            result = checker.inspect_sample_requirements({}, files, SAMPLE_ID, self.build, source_dir=self.source)
            self.assertEqual(result["status"], "unknown")
            self.assertIn(path, " ".join(result["details"]))
        files = runtime_files()
        next(item for item in files if item["path"] == "src/inference.cpp")["content"] = "// tflite::MicroInterpreter\n"
        result = checker.inspect_sample_requirements({}, files, SAMPLE_ID, self.build, source_dir=self.source)
        self.assertEqual(result["status"], "unknown")

    def test_explicit_check_only_configures_reviewed_runtime_in_a_disposable_directory(self):
        storage = self.root / "checks"
        captured = []

        def configure(profile, source, build, operation, **options):
            self.assertEqual(operation, "configure")
            self.assertEqual(profile["target"], "second-board/chip")
            self.assertEqual((source / "model/model.inc").read_text(), "0x00,0x01,0x02,\n")
            captured.append(source)
            configured_output(build, source)
            return {"status": "configured", "context": {}}

        with patch.object(checker, "execute_build", side_effect=configure), patch.object(checker, "run_probe") as probe:
            result = checker.check_sample({"target": "second-board/chip"}, runtime_files(), SAMPLE_ID, root=storage)
        self.assertEqual(result["status"], "compatible")
        self.assertEqual(list(storage.iterdir()), [])
        self.assertTrue(all(not path.exists() for path in captured))
        probe.assert_not_called()

    def test_retained_model_sources_configuration_and_new_runtime_files_invalidate_original_requirements(self):
        files = runtime_files()
        project = {"path": str(self.source), "sample": {"id": SAMPLE_ID},
                   "files": [{"path": item["path"], "sha256": item["sha256"]} for item in files]}
        self.assertEqual({item["path"] for item in _sample_source_files(project)}, {item["path"] for item in files})
        for path in ("src/inference.cpp", "src/assets.hpp", "model/model.inc", "model/model_metadata.hpp",
                     "data/images.inc", "data/test_samples.inc", "CMakeLists.txt", "prj.conf", "boards/nucleo_h753zi.conf"):
            with self.subTest(path=path):
                target = self.source / path
                original = target.read_bytes()
                target.write_bytes(original + b"\nchanged\n")
                self.assertIsNone(_sample_source_files(project))
                target.write_bytes(original)
        (self.source / "README.md").write_text("Application notes may change.\n")
        self.assertIsNotNone(_sample_source_files(project))
        (self.source / "src/extra.hpp").write_text("New runtime dependency.\n")
        self.assertIsNone(_sample_source_files(project))

    def test_removed_or_redirected_runtime_model_cannot_reuse_original_requirements(self):
        files = runtime_files()
        project = {"path": str(self.source), "sample": {"id": SAMPLE_ID},
                   "files": [{"path": item["path"], "sha256": item["sha256"]} for item in files]}
        target = self.source / "model/model.inc"
        original = target.read_bytes()
        target.unlink()
        self.assertIsNone(_sample_source_files(project))
        outside = self.root / "outside.inc"
        outside.write_bytes(original)
        target.symlink_to(outside)
        self.assertIsNone(_sample_source_files(project))

    def test_changed_external_firmware_dependency_withholds_original_requirements(self):
        files = runtime_files()
        provenance = {"id": SAMPLE_ID, "module": {"fingerprint": "saved-module"}}
        project = {"path": str(self.source), "sample": provenance,
                   "files": [{"path": item["path"], "sha256": item["sha256"]} for item in files]}
        cancel = Event()
        self.dependency.side_effect = ValueError("The reviewed firmware dependency changed.")
        self.assertIsNone(_sample_source_files(project, cancel=cancel))
        self.dependency.assert_called_once_with(provenance, cancel=cancel)

    def test_local_module_byte_changes_are_detected_beneath_the_same_pinned_revision(self):
        source, module, _, _ = mnist_fixture(self.root)
        with patch("zephyr_utilities.features.projects.mnist_sample.TEMPLATE_ROOT", source):
            preview = preview_sample({"zephyr": {"path": str(self.root / "zephyr")}}, SAMPLE_ID,
                                     {"cwd": "", "display_configure": "", "display_build": ""},
                                     mnist_assets_root=module.parent.parent)
        saved = self.root / "saved-application"
        write_files(saved, preview["files"])
        project = {"path": str(saved), "sample": preview["provenance"],
                   "files": [{"path": item["path"], "sha256": item["sha256"]} for item in preview["files"]]}
        self.dependency.side_effect = validate_mnist_dependency
        self.assertIsNotNone(_sample_source_files(project))
        source.rename(source.with_name("template-unavailable"))
        (module.parent.parent / "model").rename(module.parent.parent / "model-unavailable")
        self.assertIsNotNone(_sample_source_files(project))
        header = module / "tensorflow/lite/micro/micro_interpreter.h"
        header.write_text(header.read_text() + "\n// changed beneath the pinned HEAD\n")
        self.assertIsNone(_sample_source_files(project))


class MnistBuildPostcheckTests(unittest.TestCase):
    call = creator_tests.CreatorServiceTests.call
    body = creator_tests.CreatorServiceTests.body
    preview = creator_tests.CreatorServiceTests.preview

    def setUp(self):
        creator_tests.CreatorServiceTests.setUp(self)
        dependency = patch("zephyr_utilities.features.projects.mnist_sample.validate_mnist_dependency", create=True)
        self.dependency = dependency.start()
        self.addCleanup(dependency.stop)

    def test_saved_demo_passes_all_runtime_inputs_and_changed_model_withholds_original_requirements(self):
        profile = self.preview()["profile"]
        for changed in ("original", "model", "module"):
            with self.subTest(changed=changed):
                self.dependency.side_effect = ValueError("Changed dependency.") if changed == "module" else None
                source = self.workspace / "applications" / changed
                files = runtime_files()
                write_files(source, files)
                project = {"schema_version": 1, "id": uuid.uuid4().hex, "workspace": str(self.workspace),
                           "name": source.name, "path": str(source), "profile": deepcopy(profile),
                           "template": {"id": "local-sample", "version": 1}, "sample": {"id": SAMPLE_ID},
                           "files": [{"path": item["path"], "sha256": item["sha256"]} for item in files],
                           "directory_identity": path_identity(source), "created_at": "2026-11-15T00:00:00Z"}
                self.creator.store.save_project(project)
                if changed == "model":
                    (source / "model/model.inc").write_text("0xff,\n")
                plan = self.call("POST", "build-preview", self.body(project_id=project["id"], operation="build")).data["plan"]

                def execute(profile, source, build, operation, **options):
                    evidence = options["after_configure"](Path(build))
                    return {"status": "built", "sample_compatibility": evidence, "context": {}, "artifacts": [], "diagnostics": []}

                with patch("zephyr_utilities.features.projects.creator.execute_build", side_effect=execute), \
                        patch("zephyr_utilities.features.projects.build_workflow.inspect_sample_requirements",
                              return_value={"status": "compatible", "message": "Configured requirements checked.", "details": []}) as inspect:
                    job = self.call("POST", "build", self.body(build_plan_id=plan["id"])).data
                    result = wait_job(self.app.context.jobs, job["id"])
                self.assertEqual(result["status"], "completed", result.get("error"))
                evidence = result["result"]["attempt"]["sample_compatibility"]
                self.assertEqual(evidence["status"], "compatible" if changed == "original" else "unknown")
                if changed != "original":
                    inspect.assert_not_called()
                else:
                    self.assertEqual({item["path"] for item in inspect.call_args.args[1]}, {item["path"] for item in files})


if __name__ == "__main__":
    unittest.main()
