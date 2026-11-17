MNIST digit recognition
=======================

This application uses the MNIST runtime template shipped with Zephyr Utilities.
Its C++ runtime and configuration are maintained with Utilities. Creating an
application copies this template and the selected managed model assets; it does
not read another application as its source.

The generated CMake file uses this application's name, compiles its own ``src/``
files and references the separately managed TensorFlow Lite Micro dependency.
The model, reference images and metadata come from the managed MNIST asset pack.
Keep those dependency and asset identities with any build or inference results.

Choose an exact board target and SDK in Projects, then review, configure and build
the application. The common configuration enables the Zephyr serial shell and
C++17 runtime. The H753 configuration applies only to its named board. A board
listing or successful configuration does not prove that the application fits or
runs on that device.

On a running device, the shell commands are ``ai info``, ``ai list``, ``ai show 0``,
``ai infer 0`` and ``ai test``. TinyML's drawing viewer sends 28 by 28 grayscale
images using the ``mnist-gray8-v1`` UART protocol. Reported inference time measures
TensorFlow Lite Micro ``Invoke`` only; it excludes image preparation and transfer.

The runtime is licensed under Apache-2.0; see ``LICENSE`` and ``NOTICE.rst``.
The managed asset pack supplies its own model and dataset attribution.
