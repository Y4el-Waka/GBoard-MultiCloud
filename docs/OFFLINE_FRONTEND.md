# Offline Frontend Assets

Phase 0.5C removes every runtime CDN dependency from CoreView Cloud Lab. The
browser now receives application, CSS, JavaScript, fonts, and icons only from
the same CoreView origin. This is a laboratory reproducibility property, not a
claim that the Docker build itself can run without first obtaining its pinned
base images and npm packages.

## Audited dependency inventory

The audit covered both templates, every tracked runtime CSS/JavaScript file,
CSS `url()` and `@import`, fonts, icon styles, and the previous CSP allowlist.
The eight external requests represented seven packages; Font Awesome loaded
CSS plus font files and Google Fonts loaded CSS plus Inter files indirectly.

| Dependency | Previous runtime source | Fixed local version | Purpose | Declared license | Distributed files |
| --- | --- | --- | --- | --- | --- |
| Tailwind CSS | `cdn.tailwindcss.com` browser runtime | 3.4.17 | utility CSS/layout | MIT | generated `tailwind/tailwind.min.css` |
| jQuery | cdnjs | 3.7.1 | existing dashboard/Toastr compatibility | MIT | `jquery/jquery.min.js` |
| Toastr | cdnjs `latest` | 2.1.4 | dashboard notifications | MIT | minified JS and CSS |
| Chart.js | jsDelivr without an exact version | 4.5.1 | existing charts | MIT | UMD bundle and source map |
| chartjs-adapter-date-fns | jsDelivr | 3.0.0 | time scale adapter | MIT | bundled adapter |
| Font Awesome Free | cdnjs | 6.4.0 | existing icons | CC-BY-4.0 AND OFL-1.1 AND MIT | minified CSS and supplied webfonts |
| Inter | Google Fonts | 5.3.0 (`@fontsource/inter`) | existing typography | OFL-1.1 | Latin WOFF2 weights 400–800 and local CSS |

The source packages come from the npm registry according to the exact
`package-lock.json` resolution and integrity fields. Their license texts are
copied into `static/vendor/licenses/`; the Toastr npm archive lacks a license
file, so its upstream MIT text is kept in `frontend/licenses/toastr-MIT.txt`
and copied by the build. Required copyright/license notices are not stripped.

Playwright 1.63.0 (Apache-2.0) is a development-only browser validation tool.
It is not copied to `static/vendor` or the runtime image. The browser runner is
also fixed as `mcr.microsoft.com/playwright:v1.63.0-noble` at digest
`sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27`.

## Reproducible build

Node 20 or newer is required locally. The validated build uses Node 22.23.3;
the Docker builder is `node:22-alpine` pinned at digest
`sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402`.

```bash
npm ci --ignore-scripts --no-audit --no-fund
npm run build:frontend
npm run test:frontend
```

`scripts/build_frontend.mjs` deletes and recreates only `static/vendor`, runs
the Tailwind CLI, copies the reviewed runtime subsets, copies license texts,
and writes `static/vendor/manifest.json`. The manifest records package
versions/licenses and a SHA-256 digest for every distributed file. Tests
recalculate every digest.

`tailwind.config.cjs` scans both templates plus the two runtime JavaScript
files. Dynamic status/modal classes currently occur as complete literal
strings and therefore remain discoverable, including the existing PRTG color
classes. No broad safelist is used. Tests assert representative runtime-only
classes such as pause/error states and the modal overlay.

The Dockerfile uses a pinned Node build stage and copies only
`/build/static/vendor` to the pinned Python runtime stage. `node`, npm,
`node_modules`, Playwright, and Tailwind tooling are absent from the runtime
image. `.dockerignore` deliberately excludes the host-generated vendor tree,
so a successful image build proves the assets were regenerated from the lock.
The generated directory contains 30 files and measures 2.6 MiB on disk. The
validated OCI image measured 50,161,513 bytes versus 48,990,418 bytes for
Phase 0.5B, an increase of 1,171,095 bytes after layer compression.

## CSP

The response CSP now allows runtime scripts, styles, fonts, images, and API
connections only from the application origin (plus `data:` images):

```text
default-src 'self';
script-src 'self' <reviewed inline script hashes>;
style-src 'self' <reviewed inline style hashes>;
img-src 'self' data:;
font-src 'self';
connect-src 'self';
object-src 'none';
base-uri 'self';
form-action 'self';
frame-ancestors 'none'
```

The existing inline login styles/script and dashboard theme/style blocks are
authorized with exact SHA-256 hashes. `unsafe-inline`, `unsafe-eval`, and CDN
origins are absent. A test recomputes every inline block hash, so an edit must
be reviewed together with its CSP hash.

## Offline browser validation

Start the normal Compose lab, then run:

```bash
scripts/run_offline_frontend_smoke.sh
```

The runner creates a temporary Docker network with `--internal`, connects only
the laboratory network namespace, and launches the digest-pinned Chromium
image with a read-only root filesystem, no capabilities, and
`no-new-privileges`. The browser script independently aborts any request whose
host is not the laboratory alias. It fails on external attempts, failed local
assets, CSP violations, JavaScript errors, or contract/visual-control errors.

The 2026-09-27 run produced `artifacts/offline-frontend/report.json` and eleven
synthetic-only screenshots. It verified:

- login and authenticated dashboard with 1,000 cards;
- local Chart.js 4.5.1, jQuery 3.7.1, Toastr, Inter, and Font Awesome;
- logs and trends views;
- Ping, CPU, memory, Traffic IN/OUT, complete SFP, and partial SFP charts;
- repeated modal close/reopen, search, controlled legacy fallback, and
  pause/resume restoration;
- zero external requests, zero local asset failures, zero CSP errors, and zero
  unexpected console errors.

After review, the frozen contract hashes changed only for the intentional
asset references/import removal and the new generated manifest:

| File | SHA-256 |
| --- | --- |
| `templates/login.html` | `9ee39fe858beb3d2ceb7799d4bdfed076613ee1423aa70c2efecdd334775e696` |
| `templates/dashboard_prtg.html` | `eb81b590f2054b1c3201bf26ce92dfca0362020cd6fd0ef1ac924637960023c5` |
| `static/dashboard.css` | `0b5b9ba1dd5a74bca9e3b2d235e427d844fefaa6c0c937e54344ed3e9f22f774` |
| `static/vendor/manifest.json` | `b71c7893dbacfe592c4c66813f61d2ab57d508c4186bf13fa4c6e5e7529f69a0` |

`static/dashboard.js`, `static/metrics-history.js`, and the login background
retain their previous hashes; behavior code and visual source image did not
change.

`npm audit --audit-level=high --json` was executed on 2026-09-27 against this
lock and reported 84 resolved dependencies with zero current npm advisory
findings at every severity. This is a time-bound registry result, not proof
that the packages or generated browser code are vulnerability-free. The
runtime image remains subject to the separate Trivy SBOM/scan policy.

The UI has a fixed dark dashboard/login presentation and a card grid. There is
currently no user-selectable light theme, alternate sensor list mode, or
persistent sidebar state to validate. Phase 0.5C does not invent these controls
because that would be a redesign. Responsive Tailwind output is generated and
tested, but the recorded browser matrix uses a 1440×1000 viewport.

There was no pre-Phase-0.5C pixel-baseline image set, so no numerical pixel
diff is claimed. The visual review used the new synthetic screenshots plus the
source contract: `dashboard.js`, `metrics-history.js`, and the background image
are byte-identical; dashboard CSS changed only by removing the remote font
import; template changes are asset references and replacement of the browser
Tailwind config with the equivalent build config. Representative login,
dashboard, Ping, Traffic, SFP, and fallback screenshots were directly reviewed
without finding clipped layouts, missing glyphs, wrong fonts, or duplicate
charts.

## Update procedure

1. Review one upstream release, changelog, license, browser compatibility, and
   security advisories.
2. Change the exact version in `package.json`; run `npm install --save-dev
   --save-exact <package>@<version>` to update the lock.
3. Review lock integrity/origin changes and the package license. Update the
   build copy list only for intentional package-layout changes.
4. Run the frontend build twice and confirm the generated vendor diff is
   stable.
5. Run Python and JavaScript suites, compile, diff checks, clean Docker build,
   functional smoke, offline Chromium smoke, SBOM, and Trivy scan.
6. Review screenshots and every new vulnerability. Update this inventory,
   relevant hashes, and evidence; never replace license notices silently.

No runtime package auto-update, CDN fallback, or `latest` reference is used.
