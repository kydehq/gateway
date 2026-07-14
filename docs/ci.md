# CI: running the sandbox and enterprise pipelines

The edition boundary **is** the repo boundary, so CI is two pipelines, each
proving one thing:

- **Public `gateway` CI** proves the OSS core stands alone. It cannot see the
  enterprise code, so a green run *is* the guarantee that the sandbox edition works
  with zero enterprise dependencies.
- **Private `gateway-enterprise` CI** proves the enterprise composition. It pulls the
  core in and tests both packages merged into the one `kyde` namespace package.

The enterprise wheel and enterprise image are assembled **only** in the private pipeline —
they never touch public CI or a public registry.

See also: `building-images.md` (image build details) and
`../../gateway-enterprise/docs/signing-install.md` (key management).

---

## Public `gateway` pipeline — sandbox edition

Triggers: pull request, push to `main`, and version tags.

1. **Lint** (ruff / formatting).
2. **Core test suite in sandbox mode** against a Postgres service container,
   with coverage. Nothing enterprise is installed, so `HAS_SIGNING` /
   `HAS_ENFORCEMENT` are `False`, the edition-aware tests take their sandbox
   path, and the moved enterprise tests are simply absent. On pushes to
   `main`, backend + frontend coverage percentages are force-pushed as
   shields.io JSON to the orphan `badges` branch, which feeds the README's
   coverage badges.
3. **Build + smoke the sandbox image**: `docker build --build-arg EDITION=sandbox`,
   then assert `edition() == 'sandbox'` inside the image.
4. **On tag `vX`**: push the sandbox image to the public registry (e.g. GHCR) and
   publish the `kyde-gateway` wheel as a release artifact so the private
   pipeline can consume a pinned version.

Because the public repo has no enterprise code, this pipeline *only* ever exercises the
sandbox edition — that is intentional, not a gap.

---

## Private `gateway-enterprise` pipeline — enterprise edition

Triggers: pull request, push to `main`, version tags, plus a nightly canary
(below).

1. **Pull core in** at the pinned version — check out `kydehq/gateway@vX`, or
   install the published `kyde-gateway==X` wheel.
2. **Install core + enterprise together**, then run **two** test passes in enterprise
   mode:
   - the **enterprise suite** (`gateway-enterprise/tests` — the signing +
     enforcement tests), and
   - the **core suite again, in enterprise mode** (catches the edition-aware paths and
     any core-plus-enterprise integration regressions).
3. **Build the enterprise wheel**, then **assemble the enterprise image** here: check
   out the public core build context, drop the wheel into `wheels/`,
   `docker build --build-arg EDITION=enterprise`, and smoke it — `edition()=='enterprise'`,
   `signing`/`enforce` both `True`, and the wheel-did-not-leak check.
4. **On tag**: push the enterprise image to the **private** registry.

---

## Cross-repo version coupling

`kyde-enterprise` pins a matching `kyde-gateway` (both `0.1.0` today), so:

- **Release gate = wheel + wheel.** Install the pinned published core wheel and
  the enterprise wheel — the exact shape a customer receives.
- **Nightly canary.** A scheduled enterprise job that builds against core `main`
  (editable + editable) surfaces drift *before* a core release breaks the enterprise
  edition, instead of at release time.
- **Tag-driven releases.** Keep a single version source of truth; a core `vX`
  tag can `repository_dispatch` the enterprise pipeline to rebuild `vX`.

---

## Gotchas to bake in

- **Postgres per job.** Each job gets its own `postgres` service with
  `kyde:kyde-dev-only`. Set `TEST_POSTGRES_URL` explicitly — the conftest default
  (`witness:witness-dev-only`) is wrong. The suite auto-creates the `witness_test`
  database on first connect.
- **No shared-DB parallelism.** The suites `TRUNCATE` between tests, so do **not**
  run `pytest-xdist` against a single database (it produces spurious
  duplicate-key / FK-violation failures). One DB per job is isolated; keep pytest
  serial within a job.
- **Test the interpreter you ship.** Dev/test and the image runtime are both on
  Python 3.14 (`python:3.14-slim`), so CI runs a single 3.14 job — no version
  matrix. If the base image and the test interpreter ever diverge again, restore
  a matrix covering both so you never ship an untested interpreter.
- **Least-privilege secrets.** Private-registry credentials and the core-repo
  read token live only in the enterprise pipeline.

---

## Skeletons (GitHub Actions)

### `gateway/.github/workflows/ci.yml`
```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env: { POSTGRES_USER: kyde, POSTGRES_PASSWORD: kyde-dev-only }
        ports: ['5432:5432']
        options: >-
          --health-cmd "pg_isready -U kyde" --health-interval 5s --health-retries 10
    env:
      TEST_POSTGRES_URL: postgresql://kyde:kyde-dev-only@localhost:5432
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv run --python 3.14 --extra test pytest -q
  sandbox-image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build --build-arg EDITION=sandbox -t kyde-gateway:sandbox .
      - run: >-
          docker run --rm kyde-gateway:sandbox
          python -c "from kyde._features import edition; assert edition()=='sandbox'"
```

### `gateway-enterprise/.github/workflows/ci.yml`
```yaml
name: ci
on: [push, pull_request]
jobs:
  test-enterprise:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env: { POSTGRES_USER: kyde, POSTGRES_PASSWORD: kyde-dev-only }
        ports: ['5432:5432']
        options: >-
          --health-cmd "pg_isready -U kyde" --health-interval 5s --health-retries 10
    env:
      TEST_POSTGRES_URL: postgresql://kyde:kyde-dev-only@localhost:5432
    steps:
      - uses: actions/checkout@v4                       # enterprise
      - uses: actions/checkout@v4                       # pinned core
        with: { repository: kydehq/gateway, ref: v0.1.0, path: gateway }
      - uses: astral-sh/setup-uv@v6
      - run: uv venv --python 3.14
      - run: uv pip install -e ./gateway -e '.[test]'
      - run: .venv/bin/python -m pytest -q              # enterprise suite (enterprise)
      - run: cd gateway && ../.venv/bin/python -m pytest -q   # core suite in enterprise mode
  enterprise-image:
    needs: test-enterprise
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4                       # public core build context
        with: { repository: kydehq/gateway, ref: v0.1.0, path: gateway }
      - uses: actions/checkout@v4                       # enterprise (wheel build)
        with: { path: enterprise }
      - uses: astral-sh/setup-uv@v6
      - run: cd enterprise && uv build --wheel
      - run: cp enterprise/dist/kyde_enterprise-*.whl gateway/wheels/
      - run: cd gateway && docker build --build-arg EDITION=enterprise -t kyde-gateway:enterprise .
      - run: >-
          docker run --rm kyde-gateway:enterprise
          python -c "from kyde._features import edition,HAS_SIGNING,HAS_ENFORCEMENT; \
                     assert (edition(),HAS_SIGNING,HAS_ENFORCEMENT)==('enterprise',True,True)"
```

> The nightly canary is the `test-enterprise` job with `ref: main` for the core
> checkout and an `on: schedule:` trigger; the tag-triggered publish steps
> (`docker push` to the respective registries) are omitted here since they depend
> on your registry/secret setup.
