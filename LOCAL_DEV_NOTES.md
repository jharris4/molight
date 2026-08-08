# Local editor setup

The development scripts run Python inside the dev container, where the project
dependencies are installed in `/opt/molight-venv`. When VS Code is opened
locally instead of in the container, Pylance cannot access that environment and
reports those imports as unresolved.

To give Pylance access to the dependencies without reopening the repository in
the dev container, create an editor-only virtual environment on the host:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements_test.txt
```

The repository's `.vscode/settings.json` configures VS Code to use
`.venv/bin/python` by default. If VS Code has already remembered a different
interpreter for this workspace, run **Python: Select Interpreter** from the
Command Palette and select `.venv/bin/python`.

This local environment is only for import resolution, type checking, and editor
features. Continue to use the `npm` scripts for testing, linting, formatting,
and running Home Assistant in the dev container. Re-run the `pip install`
command after the Python dependencies change.
