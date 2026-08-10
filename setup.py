from setuptools import setup, Extension
from Cython.Build import cythonize
import os

extensions = []

for root, dirs, files in os.walk("src"):

    if "__pycache__" in root:
        continue

    for file in files:

        if not file.endswith(".py") or file == "__init__.py":
            continue

        filepath = os.path.join(root, file)

        relative = filepath.replace(os.sep, ".")[:-3]

        keep_python = [
            "src.main",
            "src.comms",
            "src.configs",
            "src.utils.logger",
            "src.comms.server.ws_stream",
            "src.YOLOX",
        ]

        if any(relative.startswith(k) for k in keep_python):
            continue

        extensions.append(
            Extension(
                relative,
                [filepath],
            )
        )

setup(
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            "language_level": "3"
        }
    )
)