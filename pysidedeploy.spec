[app]
title = Auto Press
project_dir = .
input_file = main.py
project_file = pyproject.toml
exec_directory = dist
icon =

[python]
python_path =
# Add extra Python packages when they're imported lazily and tooling can't
# detect them statically.
packages = Nuitka==2.5

[qt]
qml_files =
excluded_qml_plugins =
# We don't use any QML, WebEngine, multimedia, bluetooth, serialport, etc.
# Trimming them shaves ~60 MB off the output.
modules = Core,Gui,Widgets
plugins = styles,platforms,iconengines,imageformats,generic

[nuitka]
# --standalone + --onefile = single executable, extracts to TEMP at first
# launch. --lto=yes + --python-flag=no_site keep the binary lean and fast.
macos.permissions =
mode = onefile
extra_args = --quiet --noinclude-qt-translations --lto=yes --python-flag=no_site --include-package=PySide6 --include-package=qfluentwidgets --include-package=cv2 --include-package=PIL --include-package=numpy --include-package=pyautogui

[buildozer]
mode =
