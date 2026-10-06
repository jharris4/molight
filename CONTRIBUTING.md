# Contributing to MoLight

Prerequisites on the host: Node.js 20 or newer and Docker. `npm install` brings the Dev Containers CLI and Playwright, and `npm run release` also needs Python 3.9 or newer.

Local development uses **two independent containers**, each with a distinct job. They are unrelated (no shared network or startup dependency), but both bind port `8123`, so only one can run at a time.

| Container | Image | What it's for |
| --- | --- | --- |
| **Dev container** (`dev:*`) | generic Debian + Python 3.14 | Your toolchain: editing, `pytest`, `ruff`. VS Code attaches here. HA is pip-installed into a venv (`/opt/molight-venv`) as a library. |
| **HA runtime** (`hass:*`) | official `home-assistant:stable` | The real Home Assistant app, for manual/UI testing. Your integration is mounted read-only. |

The npm scripts prefixed `dev:` and `hass:` act on these two containers. `test`, `lint` and `format` run inside the dev container, while `release`, `lint:sh` and the `test:e2e` scripts run on the host, the last two in throwaway containers of their own.

## Running the tests

```bash
npm install         # install tooling
npm run hass:down   # free port 8123 (devcontainer and HA runtime can't coexist)
npm run dev:up      # start the devcontainer (fast after first build)
npm test
```

`npm test` runs the suite on the Home Assistant version pinned in `requirements_test.txt`. CI also fails when coverage of lines and branches together drops below 98 %; `npm test -- --cov` checks that locally. Besides the pinned version, CI runs the suite on the minimum Home Assistant version in `hacs.json` (with Python 3.13), so a test that passes locally can still fail there, and, as an advisory leg, on the newest version `pytest-homeassistant-custom-component` targets, which is often a beta.

## Releasing

Development is done on the `develop` branch. Start a release by fast-forwarding
`main` to the tested `develop` commit, then prepare, commit and tag the release
on `main`:

```bash
git switch main
git merge --ff-only develop
git push
```

Once `main` is up to date, the rest is scripted. `npm run release <version>`
writes nothing on its own; it lists the changes a release would make (stamping
the changelog, bumping the manifest version, updating the compare links) and
prints the command that applies them. Applying then prints the git commands to
commit, tag and push. Before either previewing or applying, the script also
checks that the worktree is clean, `main` contains `develop`, both branches
match their live `origin` refs, the version moves forward, and the tag does not
already exist locally or on GitHub.

```bash
git commit -am 'release 1.x.0'
git push
git tag v1.x.0
git push origin v1.x.0
```

Pushing the tag starts the **Release** GitHub Actions workflow. The workflow
validates that the tag is on `main` and that the tag, manifest and changelog
versions agree, runs the lint, unit test and validation jobs and every live
E2E suite, and once all of them pass puts the release notes extracted from
the changelog in the summary of its **Release notes** job. It does **not**
create the GitHub Release. After the workflow succeeds:

1. Open the **Release notes** job summary and copy the generated notes.
2. In the repository's **Releases** page, choose **Draft a new release** and
   select the tag you just pushed.
3. Give the release a descriptive title, paste the generated notes into its
   body, and publish it as a normal release (not a draft or prerelease). The
   published body is what HACS shows users in its update dialog.

Creating only the tag is not a complete release: the corresponding published
GitHub Release is required for users and HACS to see the version and its notes.

Finally, fast-forward `develop` to include the release commit too, so
development resumes with the stamped changelog and new manifest version:

```bash
git switch develop
git merge --ff-only main
git push
```

Do not start new work on `develop` between the first merge and this final sync;
keeping the release window short ensures both updates remain fast-forwards.

## Working in the dev container

`dev:up`, `dev:stop`, `dev:down` and `dev:rebuild` manage the dev container from the host, and `lint:sh` runs on the host too. The rest run *inside* the dev container via `devcontainer exec`:

```bash
npm run dev:up        # create/start the dev container (fast after first build)
npm test              # pytest
npm run lint          # ruff check
npm run lint:fix      # ruff check --fix
npm run format        # ruff format
npm run format:check  # ruff format --check (what CI runs)
npm run lint:sh       # shellcheck at the version CI pins (runs on the host, in Docker)
npm run dev:shell     # open a bash shell inside the container
npm run dev:stop      # stop the container, keeping it for a fast dev:up next time
npm run dev:down      # stop and remove the container (dev:up recreates it)
npm run dev:rebuild   # tear down and rebuild from scratch (e.g. after changing devcontainer.json)
```

The container's virtual environment is rebuilt when `requirements_test.txt` or the setup scripts change: checked at every container start and before `npm test`, `lint` and `format`, so a dependency bump needs no `dev:rebuild`.

If you open the repository locally rather than in the container, Pylance can't see the container's Python environment and flags every Home Assistant import as unresolved; see [LOCAL_DEV_NOTES.md](LOCAL_DEV_NOTES.md) for the editor-only fix.

## Running Home Assistant

There are two ways to get a live HA instance, depending on what you're testing:

```bash
npm run hass:up      # run stock HA (stable image) with the integration mounted read-only
npm run hass:down    # stop it and free port 8123
npm run hass:pull    # update the HA stable image

npm run dev:hass     # run the pip-installed HA inside the dev container against your working tree
```

Use `hass:up` to confirm behavior against a real, released HA build; use `dev:hass` for active development, where HA loads the integration from your working tree and can be restarted in place. Remember to `hass:down` before starting the dev container (or vice versa) so port 8123 is free.

## Running the live acceptance tests

The end-to-end suite starts an isolated official Home Assistant container and
a short-lived API runner on a private Docker network. It does not use the
stateful manual-development `config/` directory or publish port 8123, so it can
run alongside the dev container. A test-only `molight_testbed` integration
provides persistent simulated lights, sensors, a door, a schedule and selects
(lights can be switched into slow, piecewise, stepwise, quantized or XY-reporting
behavior to emulate real bulbs);
it is mounted only into the disposable acceptance environment and is never
part of a MoLight release.

```bash
npm run test:e2e              # core, restarts, and scenario shards a/b/c, concurrently
npm run test:e2e:core         # the sequential restart chain
npm run test:e2e:restarts     # independent restart scenarios on their own fixtures
npm run test:e2e:scenarios-a  # behavior scenario shard a (b and c likewise; one fresh HA each)
npm run test:e2e:browser      # browser smoke tests (not part of test:e2e)
```

The suite onboards Home Assistant automatically, creates and edits MoLight
entries through the backend config-flow API, and drives them through restarts
of Home Assistant core and of the whole container on the same temporary
`/config`, checking the resulting logs. The scenarios themselves are in
`tests/e2e/`.

The default image is pinned, in `tests/e2e/env.sh`, to the Home Assistant
release used by the current test dependencies; CI fails if the two drift
apart. Override it to exercise another release:

```bash
MOLIGHT_E2E_HA_IMAGE=ghcr.io/home-assistant/home-assistant:stable npm run test:e2e
MOLIGHT_E2E_HA_IMAGE=floor npm run test:e2e   # the minimum release hacs.json declares
```

Some races only show on slow hardware: a busy CI runner caught two that a fast
laptop always won. `MOLIGHT_E2E_HA_CPUS=0.5` caps the Home Assistant
container's CPU to approximate that locally (the suites take about twice as
long); `npm run test:e2e` also loads the machine by running all lanes at once.

A successful run removes its temporary configuration. On failure the runner
prints a retained run directory under the system temporary directory
containing the isolated HA configuration and Compose logs. That directory
contains the disposable test account, so remove it after debugging. The
browser lane also keeps its failure artifacts (Playwright output, a copy of
the HA configuration, and Compose logs) in `tests/e2e/artifacts/browser` in
the repository, and clears that directory at the start of each run.

CI runs the live suite against the pinned current Home Assistant image on
pushes and pull requests. Nightly and manually dispatched E2E workflows run
every suite (API lanes and the browser smoke tests) on a
minimum-supported/current matrix plus an advisory floating `stable` canary;
the nightly run covers both `main` and `develop`. Release tags run every
suite on the current and minimum-supported images, and write their release
notes only after all of them pass. Every CI job uploads what its lane
retained (the run directory with the HA config and Compose logs, plus
`tests/e2e/artifacts/browser` for the browser suite) as a workflow artifact
when it fails or is cancelled; locally, `MOLIGHT_E2E_RUN_ROOT` sets the
directory that each lane run creates its own run directory in.
