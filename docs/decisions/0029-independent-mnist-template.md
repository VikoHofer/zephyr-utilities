# ADR 0029: Independent MNIST template and runtime assets

Status: accepted, 2026-11-15.

## Context

The initial MNIST integration copied runtime files from the workspace's original
demonstration whenever Projects prepared a new app. Its generated CMake retained
`project(mnist_shell)` and referenced a TFLM checkout beneath that demonstration.
Although generated apps owned their copied C++ files, the original app remained
both the template and the framework location. The user identified this dependency
while inspecting the generated `ai_demo` and required an independent integration.

The demonstration remains a development reference and attribution source. Neither
it nor a generated application should become a source of future templates. Trained
models, dataset images and captured device data must remain outside Git and the wheel.

## Decision

Projects owns a source template shipped with Utilities at
`features/projects/mnist_template/`. It includes the C++ runtime, common configuration,
the existing board-scoped H753 configuration, README, Apache-2.0 license and notice.
It contains no active CMake file, model, reference images or trained metadata.
The generated app receives its own source files and uses the requested application
name in CMake, for example `project(ai_demo)`.

Model/reference assets and TensorFlow Lite Micro are installed separately through
the existing launcher:

```sh
zephyr-utils --install-mnist-assets PACK --tflm-source FRAMEWORK
```

Both inputs are explicitly selected local directories. Installation completes before
the same application starts; it downloads nothing and runs no training, pip or west
installation. Ordinary startup without these arguments never installs or migrates
anything. There is no discovery or fallback to an application directory.

The seven asset-pack files are `model/mnist.tflite`, `model/metadata.json`,
`model/model_metadata.hpp`, `data/test_images.bin`, `data/test_samples.inc`,
`data/NOTICE.rst` and `LICENSE`. The framework must identify reviewed revision
`fcc760af130f3a595b5802cdebcc77461e54f382` with understood Zephyr module metadata.
Inputs are bounded, copied into a private staging directory and published only to
an absent destination after verification. Identical repeated installation is accepted;
different, damaged or changed inputs preserve existing files and report a reason.

`MNISTAssets` owns the feature-specific store:

```text
context.feature_data("projects") / "tinyml-runtime/mnist-v1"
```

This is `<data-dir>/.features/projects/tinyml-runtime/mnist-v1`. A schema-1
`manifest.json` binds runtime identity `mnist-v1`, model/reference hashes, framework
bytes and revision. The installed framework lives under `framework/tflite-micro`
and carries a verified `REVISION` marker; its source checkout's Git directory is
not needed. The installation is shared across workspace selections within this
data directory, while Projects retains captured workspace and build identities.
This fixed runtime installation is not a training-model registry or experiment store.

Creation reads only the bundled template and explicitly installed runtime. It converts
the model and images to lossless textual C initializers for the existing reviewed
publisher, preserving model/reference metadata and attribution. Generated CMake compiles
the created app's own runtime and references the managed framework through
`MNIST_TFLM_DIR` and `ZEPHYR_EXTRA_MODULES`. Creation and builds never copy from or
link to the demonstration or another generated application. The framework remains
an external dependency, like the chosen Zephyr checkout and SDK; moving an app to
another machine requires the same framework revision at a deliberately selected path.

## Provenance and existing data

The source adapter becomes `mnist-runtime-v2`, adapter version 2. The existing outer
sample provenance schemas 1/3 remain unchanged. New evidence binds bundled template
files, installed assets, application name, framework bytes, license references and
asset transformations. The catalogue/sample identity and public Projects handoff
remain stable. Classic C sample guards remain independent.

Version-one MNIST creation plans require a fresh preview. Existing project records,
application files and build histories remain readable and are not silently rewritten.
An explicit migration of an existing app must preserve a backup and migration receipt,
change the source/dependency identity deliberately, and obtain a fresh Configure result.
Earlier configuration and build evidence remains historical evidence for its captured
inputs; it must not be relabeled as validation of the migrated app.

The current installation may be seeded once from explicitly selected existing local
assets and the reviewed framework. That is an installation/migration action, not a
runtime source lookup. Subsequent creation and builds require no original-demo files.

## Consequences and validation

Installing Utilities supplies the source template and attribution; a separate explicit
offline installation supplies the trained assets and framework. Missing or inconsistent
inputs retain a visible setup reason. No application folder is created during setup,
and asset installation does not configure, compile or flash firmware.

The existing shared server, workspace, job manager, Projects records, reviewed publisher
and configure/build controls remain the process boundary. TinyML's drawing/UART viewer
continues to work independently of project, asset-pack, board and SDK selection.

Verification passed 1,665 Python tests, 609 frontend tests, quick checks and installed
package checks. Synthetic tests create projects without original application folders
and retain build validation after source-template/model-pack removal. The existing
`ai_demo` was backed up and migrated with an explicit receipt and retained build history.
A fresh MCXN947 CPU0 Configure and Build passed using the managed framework; file-access
tracing found no access to the original MNIST application. Browser checks verified the
bundled source identity, chooser handoff and generated CMake name/dependency path.
Physical-board operation remains a separate observation. See the
[MNIST integration guide](../tinyml-mnist-demo.md).
