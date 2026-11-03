# Portable controller deployment

`docker compose up --build -d` starts a CPU controller and read-only dashboard
sharing one persistent state volume. The dashboard is published on localhost:8787.
The image uses the portable example config; no local credentials, runtime storage,
native CLI login state, or installed Windows environment is copied into it.

Configure actual providers separately (mount a reviewed workflow_config.json and
inject credentials through the host secret mechanism). Native agent CLIs are not
bundled. The default daemon is idle until work is queued; this is not a GPU image.
Use `docker compose run --rm controller python main.py --no-env doctor` to inspect
the container. CLI/API availability still depends on the configured host/account.

Personal Colab's browser login/CDP belongs to the host. A Linux container does not
inherit that browser or its localhost bridge; use an explicitly configured supported
host bridge/network layout rather than pretending container localhost is the host.
Set CHROME_PATH or COLAB_BROWSER for the browser launcher. A headless browser does
not supply a Google login. Rented compute adapters remain disabled.

Venv Python and site-packages discovery now supports Windows Scripts/Lib and POSIX
bin/lib/pythonX.Y layouts. Root hook config is a generated machine binding: rerun
`python scripts/setup_hooks.py --apply --check` after relocating a checkout/env.
An installed venv snapshot is host-local: copying it is not a portable installation.
Recreate environments from dependencies on another host; never copy the frozen
Colab runtime directory into an image or claim source-host verification proves
it works elsewhere. Browser automation requires an explicitly supplied browser/
authenticated external bridge; the CPU image does not contain one.

Build/start results must be recorded separately from source/fixture checks; adding
these files alone is not evidence that Docker ran successfully on a Linux host.
