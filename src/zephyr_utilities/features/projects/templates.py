"""Trusted, versioned application templates shipped with Project Creator."""

from .default_overlay import add_default_overlay, default_overlay


_TEMPLATES = {
    "minimal-c": {
        "id": "minimal-c", "version": 2, "title": "Minimal C", "language": "C",
        "description": "A small C application that prints a greeting.",
    },
    "minimal-cpp": {
        "id": "minimal-cpp", "version": 2, "title": "Minimal C++", "language": "C++",
        "description": "A small C++ application that prints a greeting without standard-library extras.",
    },
    "local-sample": {
        "id": "local-sample", "version": 1, "title": "Local Zephyr sample", "language": "C",
        "description": "Copy a supported sample from the selected Zephyr checkout.",
    },
}


def template_catalog():
    """Return public descriptors without sharing the trusted registry's objects."""
    return [dict(template) for template in _TEMPLATES.values()]


def select_template(template_id):
    """Choose a built-in template; only omission at the API boundary implies C."""
    if not isinstance(template_id, str) or template_id not in _TEMPLATES:
        raise ValueError("Choose an available application template.")
    template = _TEMPLATES[template_id]
    return {"id": template["id"], "version": template["version"]}


def validate_template(value):
    """Validate a retained plan's complete template identity before publication."""
    if (not isinstance(value, dict) or set(value) != {"id", "version"} or
            type(value.get("version")) is not int or value["version"] not in {1, 2} or
            not isinstance(value.get("id"), str) or value["id"] not in _TEMPLATES or
            value["id"] == "local-sample" and value["version"] != 1):
        raise ValueError("Unsupported application template identity or version. Preview again.")
    return dict(value)


def template_paths(template, profile=None):
    """Only these generated paths may be published for the selected template."""
    identity = validate_template(template)
    if identity["id"] == "local-sample":
        raise ValueError("Local sample files require a validated source preview.")
    source = "src/main.cpp" if identity["id"] == "minimal-cpp" else "src/main.c"
    paths = {"CMakeLists.txt", "prj.conf", source, ".gitignore", "README.md"}
    if identity["version"] == 2:
        paths.add(default_overlay((profile or {}).get("target"))["path"])
    return paths


def render_template(template, name, profile, recipe):
    """Render retained version-one or current sources and standalone build instructions."""
    identity = validate_template(template)
    if identity["id"] == "local-sample":
        raise ValueError("Local samples require a validated source preview.")
    descriptor = _TEMPLATES[identity["id"]]
    cpp = identity["id"] == "minimal-cpp"
    source = "src/main.cpp" if cpp else "src/main.c"
    languages = "C CXX" if cpp else "C"
    readme = (f"# {name}\n\nA minimal Zephyr {descriptor['language']} application generated from the "
              f"{identity['id']} template, version {identity['version']}.\n\n"
              "The application sources do not depend on Zephyr Utilities. The selected board and SDK\n"
              "are arguments to the following commands, not hardcoded into the source.\n\n"
              f"Requested target: `{profile['target']}`.\n\n"
              "Run the commands from the workspace shown below. They configure and compile only;\n"
              "they do not flash hardware. Local CMake and toolchain code will run.\n\n"
              f"Working directory: `{recipe['cwd']}`\n\n"
              f"```sh\n{recipe['display_configure']}\n{recipe['display_build']}\n```\n\n"
              "The commands use build/manual. Choose a different build directory after changing\n"
              "the target, SDK or configuration; no automatic pristine deletion is performed.\n"
              "Zephyr Utilities uses a new build/<attempt-id> directory for every UI attempt.\n\n"
              "A successful build validates compilation only, not operation on hardware.\n")
    source_content = ("#include <zephyr/kernel.h>\n\nint main(void)\n{\n"
                      f'\tprintk("Hello from {name}!\\n");\n\treturn 0;\n}}\n')
    if cpp:
        source_content = ("#include <zephyr/kernel.h>\n\n"
                          f'constexpr char greeting[] = "Hello from {name}!";\n\n'
                          'int main(void)\n{\n\tprintk("%s\\n", greeting);\n\treturn 0;\n}\n')
    contents = {
        "CMakeLists.txt": ("cmake_minimum_required(VERSION 3.20.0)\n\n"
                           "find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\n"
                           f"project({name} LANGUAGES {languages})\n\n"
                           f"target_sources(app PRIVATE {source})\n"),
        "prj.conf": ("# Application configuration, independent of board selection.\n" +
                     ("CONFIG_CPP=y\n" if cpp else "") + "CONFIG_PRINTK=y\n"),
        source: source_content,
        ".gitignore": "/build/\n",
        "README.md": readme,
    }
    if identity["version"] == 2:
        contents, overlay = add_default_overlay(contents, profile["target"])
        contents["README.md"] += (
            f"\n## Default application overlay\n\nEdit `{overlay['path']}` for `{overlay['target']}`. "
            "It starts empty and adds no guessed hardware mapping. Automatic overlay discovery is preserved; "
            "this overlay follows discovered upstream overlays and precedes appended overlays.\n\n"
            "Use a fresh build directory when changing overlay selection. Replace selection omits this default "
            "unless explicitly selected. For an existing standalone build, "
            "`-DZEPHYR_UTILS_DEFAULT_OVERLAY=OFF` disables its automatic inclusion.\n")
    return contents
