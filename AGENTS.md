# AGENTS.md

## Project

ScolioScan School is a hackathon prototype for mobile scoliosis risk screening in school settings.

The working architecture is:

- Next.js app in `app/`
- Flask analysis server in `api_server.py`
- SQLite persistence and auth in `storage.py`
- pose and silhouette logic in `pose_analyzer.py`
- MediaPipe lite model in `models/pose_landmarker_lite.task`
- public demo images in `public/test_images/`

The phone must interact only with the Next.js site on port `3000`. Browser uploads go to the same-origin Next.js route `/api/analyze`; Next.js then proxies the request to the local Python server at `127.0.0.1:5000`.

The default screening flow is Basic analysis with one front photo. Advanced analysis is a paid five-view capture protocol: front, back, left side, right side, and Adams forward bend test. Do not regress the app to only one of these modes; both Basic and Advanced must remain available.

Reports, overlay images, users, password hashes, session tokens, and Advanced subscription entitlements are stored in SQLite at `data/scolioscan.db` by default. Do not reintroduce filesystem report persistence as the primary storage path. The root `reports/` folder is legacy runtime output only and may be used as a migration source.

Advanced pricing is currently represented as local demo billing: individual one-time `$10`, individual monthly `$25`, individual annual `$199`, corporate school monthly `$99`, corporate school annual `$999`, and corporate network monthly `$249`. The backend must enforce Advanced access; do not rely only on hiding buttons in the UI.

The default local account is `admin` with password `12345678`. Passwords must be stored as hashes, not plaintext.

## User Preferences

- Reply to the user in Russian unless they explicitly ask otherwise.
- Keep the project presentation clean and hackathon-ready.
- Do not add generic "not medical software" disclaimers unless the user explicitly asks for them.
- Avoid irrelevant comments, placeholder text, debug UI, and internal-looking labels in the user interface.
- Prefer practical implementation over long explanations.

## Commands

Use `uv` for Python and `npm` for frontend work.

```bash
uv --cache-dir /tmp/uv-cache sync
npm install
npm run api
npm run dev
npm test
npm run build
```

Before committing, run:

```bash
uv --cache-dir /tmp/uv-cache run --no-sync python -m py_compile api_server.py pose_analyzer.py
npm test
npm run build
```

When checking the analyzer directly, use an image from `public/test_images/`.

## Testing Policy

- Add or update tests for new behavior, bug fixes, API response changes, metric logic, and upload/proxy changes.
- Keep tests deterministic and avoid requiring a live Next.js or Flask server when a unit test can cover the behavior.
- Prefer the standard Python `unittest` suite unless a new test framework is intentionally introduced.

## Local Network

For phone testing, the user usually opens:

```text
http://192.168.18.144:3000
```

Only port `3000` needs to be reachable from the phone. Do not reintroduce a visible Python API URL field in the UI.

## Git Hygiene

- Git author should be `GodzillaG <kabi052009@gmail.com>`.
- Do not commit `.next/`, `.venv/`, `node_modules/`, `reports/`, `archive/`, or root `/test_images/`.
- Do not commit SQLite runtime databases from `data/`.
- Keep `public/test_images/` tracked because the site uses those images.
- Commit only intentional project files.
- Push finished work to `origin/main` unless the user asks for another branch.
- For stable working milestones, create and push git tags so the user can quickly return to known-good versions. The first stable tag is `v0.1.0`.

## Notes

- If `npm run build` is needed, stop any running `next dev` first to avoid stale `.next` cache issues.
- `reports/` is runtime output and should stay local.
- `archive/` is only for unused local files and should stay ignored.
