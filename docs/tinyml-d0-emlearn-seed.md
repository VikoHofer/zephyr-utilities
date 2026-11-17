# TinyML D0: emlearn seed and creation handoff qualification

Observation date: 2026-11-10. This is source qualification for the
[demo creator plan](tinyml-demo-creator-plan.md), not an implemented adapter or a build
result. No model was trained or generated, dependency installed, repository downloaded,
manifest changed, firmware built, or device run during this investigation.

## Seed decision

Use the small, static XOR decision-tree fixture from upstream
[`test/test_trees.c` at 0.23.2](https://github.com/emlearn/emlearn/blob/3a4053a60e53d79d42b9d359eba72cd5c07e4e6b/test/test_trees.c)
as the D4 source candidate. It already contains a constructed synthetic model and expected
predictions. Adapt only its model initialization and vectors into one reviewed Zephyr C
translation unit; replace the Unity test harness with explicit prediction checks and
console output. Preserve upstream attribution and the MIT license. This source does not
need scikit-learn, NumPy, emlearn Python conversion, or host-side training during creation.

The inspected fixture has three decision nodes, one root, two class leaves, two signed
16-bit features, and majority voting. Its four expected cases are:

| First feature | Second feature | Expected class |
| --- | --- | --- |
| -1 | -1 | 0 |
| 1 | 1 | 0 |
| -1 | 1 | 1 |
| 1 | -1 | 1 |

Record the model origin as **upstream hand-constructed synthetic test fixture**, with
generation set to **not applicable**. Calling it a generated or trained model would be
incorrect. Use these signed vectors rather than substituting the separate tutorial's
0/255 input convention. The fixture is inference plumbing evidence, not an accuracy or
sensor-recognition benchmark.

Upstream's [Zephyr walkthrough](https://emlearn.readthedocs.io/en/latest/getting_started_zephyr.html)
trains a random forest and creates a header before building. Likewise,
[`examples/xor.py`](https://raw.githubusercontent.com/emlearn/emlearn/master/examples/xor.py)
trains and converts a model, and
[`examples/xor.c`](https://raw.githubusercontent.com/emlearn/emlearn/master/examples/xor.c)
depends on the generated header. They are useful documentation, but are not the selected
fixed seed. A `docs/helloworld_xor` directory is listed upstream; its complete contents
were not retrievable during this review and are not qualified as a ready-made seed.

## Revision, license and unresolved admission work

The upstream [0.23.2 tag](https://github.com/emlearn/emlearn/tags) identifies commit
[`3a4053a60e53d79d42b9d359eba72cd5c07e4e6b`](https://github.com/emlearn/emlearn/commit/3a4053a60e53d79d42b9d359eba72cd5c07e4e6b).
This is the selected source/runtime qualification baseline, matching the installed Python
distribution's version. After the browsing service returned a cache miss, a separate
read-only retrieval verified the pinned fixture and runtime bytes. The inspected pinned
fixture contains the model and expected cases described above.

| Upstream path at the pinned commit | Bytes | SHA-256 |
| --- | --- | --- |
| `test/test_trees.c` | 2058 | `920286ed2b0410fffbbf6c9bf2209b2c15e75d75f54d978044121723510e1fca` |
| `emlearn/eml_trees.h` | 8473 | `f177ce640251a531781f6dd242ca417235f0cef9cabd56b5be2362b869b1eb15` |
| `zephyr/module.yml` | 94 | `212d56883c99f73a29c41398c7535ee5da9365c115c33ffe6c4fbb8d39cfe0b8` |
| `LICENSE.md` | 1075 | `4bde60380e061bb6b097ff748fb5cccd4686e3b423cc9f3cdd5c0f9c862d8a28` |

The [0.23.2 MIT license](https://raw.githubusercontent.com/emlearn/emlearn/0.23.2/LICENSE.md)
names Jon Nordby, copyright 2018–2023. Retain its full notice with the adapted seed and
refer to it in project provenance. Before D4 can publish an available emlearn recipe:

1. Retain the verified pinned `LICENSE.md` bytes/hash above. They match the inspected
   package's MIT notice. The earlier `LICENSE` filename returned 404; the correct file
   was subsequently retrieved and hashed at the pinned commit.
2. Review the small adaptation and its own hash. Record copied portions, changed harness,
   expected output, feature/class contract, and that no generation step exists.
3. Admit the adapted synthetic fixture explicitly. A maintainer-reviewed fixture is still
   required; no real model header or captured device data should enter this repository.
4. Resolve an installed firmware module and hash its actual headers/build metadata.
   Configure and Compile the reviewed application for each claimed exact target.

This leaves D4 with a concrete source choice and explicit admission work, rather than a
dependency on user training or a promise that a bundled seed already exists.

## Installed Python package is not an installed firmware module

The selected Zephyr environment contains `emlearn` **0.23.2** at
`<Zephyr-env>/lib64/python3.14/site-packages/emlearn`. Distribution metadata contains no
`direct_url.json`, so an installed-package Git revision cannot be inferred. The package
has runtime headers and Python code, but no examples, XOR seed, `zephyr/module.yml`, or
`zephyr/module.yaml`. These local SHA-256 observations identify inspected bytes:

| Installed path relative to `site-packages` | SHA-256 |
| --- | --- |
| `emlearn/VERSION.txt` | `228e6f3bd9ed9041edbee63beef08708f3a7dea54b92583f6579c522e371bf05` |
| `emlearn/eml_trees.h` | `f177ce640251a531781f6dd242ca417235f0cef9cabd56b5be2362b869b1eb15` |
| `emlearn/eml_common.h` | `ecbdc19bca901282db0a80c2b56f284a3c191476a3dd98a79d898b6ed57a8770` |
| `emlearn/eml_log.h` | `cc7c429f921332e47b1f1ea2f9cfa98bcb51f1ebd53c950e8fe33a54f3d81998` |
| `emlearn-0.23.2.dist-info/licenses/LICENSE.md` | `4bde60380e061bb6b097ff748fb5cccd4686e3b423cc9f3cdd5c0f9c862d8a28` |

The conventional `<west-workspace>/modules/lib/emlearn` directory is absent, and
`west list emlearn` reports an unknown project. These observations establish that this
workspace does not currently supply emlearn through that manifest entry. They do not
rule out a separately installed custom module root; a future adapter must inspect the
selected Projects module evidence rather than infer availability from a conventional path.

[Zephyr's external-module documentation](https://docs.zephyrproject.org/latest/develop/manifest/external/emlearn.html)
describes registering the emlearn repository as a module. Its presence must be checked
separately from the Python distribution. Module setup and `CONFIG_EMLEARN=y` need actual
metadata verification against the selected revision. Do not silently copy headers out of
the Python environment as a substitute for the chosen installed-module policy.

The inspected [`eml_trees.h` API](https://raw.githubusercontent.com/emlearn/emlearn/0.23.2/emlearn/eml_trees.h)
supports a single-tree forest and `int16_t` inputs through `eml_trees_predict`. Its header
also contains function definitions, so the initial recipe should include the runtime
and static fixture in one C translation unit. Compilation and linkage still require a
real check; neither header inspection nor C99 portability establishes a Zephyr build.

## Existing form and route handoff facts

These are code observations and proposed D1 requirements, not new implemented endpoints.

| Current code | Consequence for D1 |
| --- | --- |
| `projects/web/index.js:createProjectsNavigation` recognizes the exact view `create`; slash subviews are preserved only for `applications/...` | Preserve and forward `create/<handoff-id>` to the creator; otherwise it is normalized to Applications. |
| `projects/web/creator-view.js:navigate` maps only the exact `create` string to the creation form | Parse a bounded creation route before choosing the form. Guard asynchronous resolution by lifetime, navigation revision, workspace and form revision. |
| `creator-state.js` defaults include `name`, `destination`, `board_id`, `target`, `sdk_id`, `template_id`, `sample_id` | Preselect a coherent board/target pair, optional SDK, demo/variant and supported template. Keep name and destination editable. |
| `creatorTemplates` accepts three known template IDs and currently labels `local-sample` as C | A demo recipe/C++ entrypoint cannot be introduced through a frontend-only selection change. Extend supported descriptors deliberately. |
| `creatorBody`, `CreatorService.preview`, and `creation.preview_project` accept explicit fields | Add validated demo/variant/source identity fields together. Unknown recipe objects, source paths or commands must remain rejected. |
| `CreatorService._remember` retains at most 32 review plans for 30 minutes, while `_consume` consumes a creation review | Reuse those bounds for separate preselection tokens, but keep token reads idempotent and distinct from a fresh publication review. |
| Existing application handoffs resolve saved project/configuration/attempt IDs | Preserve that workflow; creation preselection is a separate route and does not submit Configure or Build. |

The proposed public contract remains under Projects: workspace-bound demo catalogue,
creation-handoff submission and token resolution. A token should capture schema version,
workspace, demo/variant/adapter identity, source fingerprint, environment observation,
module identity and coherent target/SDK preselection. Resolve through the shared API,
then apply through the creator session's ordinary field-update/invalidation path. Do not
write another feature's browser state.

If a stored form has unsaved edits, show the proposed selection with **Keep current
draft** and **Use demo selection** actions before replacement. Retain the current form
when a token is expired, stale, from another workspace, or resolves after navigation or
another edit. Reload/re-entry must revalidate the token. Catalogue and token reads do not
create files, start discovery, submit a job, or authorize publication. Normal Preview,
Create, Configure and Build remain separate explicit actions.

## Acceptance investigation matrix

Local Zephyr checkout observed at `fbab2f598d545ad043ce50a75fefa4550b053f0b`.
Board YAML establishes these exact identities, not demo compatibility. Local SDK 1.0.1
contains Arm and x86 GCC executables plus Arm/x86 QEMU executables; their presence is
not a compiler execution or build result.

| Exact target | Role and source evidence | D0 observed state | Required later evidence |
| --- | --- | --- | --- |
| `qemu_x86/atom` | Exact target from Projects discovery; upstream TFLM declares the board-level `qemu_x86` alias | emlearn seed unadmitted; firmware module unresolved/missing through current manifest; Configure/Compile/run not performed | Revalidate source/module/toolchain identity, then separate Configure, Compile and console-output evidence for each recipe. |
| `qemu_cortex_m3/ti_lm3s6965` | Exact target from Projects discovery; emlearn walkthrough uses the board-level `qemu_cortex_m3` alias | Same local blockers; upstream walkthrough is documentation, not a result for this checkout | First emlearn emulator investigation after seed admission and module setup; verify all four fixed predictions. |
| `nrf52840dk/nrf52840` | Local board metadata distinguishes the nRF52840 target from the nRF52811 emulation target | Physical-board candidate only; no TinyML build or hardware run observed | Exact-target Compile, retained errors/size evidence, and separately recorded physical execution if performed. |
| `nucleo_f446re/stm32f446xx` | Exact STM32F446 target from Projects discovery | Physical-board candidate only; no TinyML build or hardware run observed | Same separate compile and physical-run gates; no claim of successful inference from board-family recognition. |

RAM/ROM footprint, inference latency, runtime memory peak, emulator output and hardware
behavior remain unknown. No support badge or successful-build claim follows from this
research note.
