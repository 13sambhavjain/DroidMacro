# Setup on macOS

The study ran on a MacBook Air (M2, 8 GB RAM). It has not been tried on another machine. These steps are
for macOS; Linux or Windows would need their own notes.

## 1. Android emulator

1. Install Android Studio. It puts the SDK at `~/Library/Android/sdk`, which is the default this code uses.
2. In Android Studio, create a virtual device: hardware **Pixel 6**, system image **Tiramisu, API 33**. Name it
   `AndroidWorldAvd`.
3. Start it from the command line. The `-grpc` port is what AndroidWorld talks to.

   ```bash
   export ANDROID_HOME="$HOME/Library/Android/sdk"
   "$ANDROID_HOME/emulator/emulator" -avd AndroidWorldAvd -no-snapshot -grpc 8554 &
   ```

   Wait for `adb shell getprop sys.boot_completed` to print `1`, then about a minute more. The accessibility
   service starts late, and the first run after a cold boot can fail without that wait.

## 2. Python and AndroidWorld

Python 3.11, with AndroidWorld at the commit used and the one patch applied. From the repo root:

```bash
brew install ffmpeg                     # AndroidWorld's own requirement
git clone https://github.com/google-research/android_world.git
git -C android_world checkout 3e50888527
git -C android_world apply ../patches/android_world_slow_host.patch
cd code
python3.11 -m venv .venv
.venv/bin/pip install -r ../requirements.txt
.venv/bin/pip install --no-deps -e ../android_world
```

The patch raises AndroidWorld's app-launch timeout from 5 s to 45 s, because a warm app launch took about
5.6 s on this machine, and its app-install timeout from 30 s to 120 s. On a faster machine, set `ANDROID_WORLD_LAUNCH_TIMEOUT=5` to restore the original.

Once, before the first run, install AndroidWorld's apps on the emulator: run AndroidWorld's `run.py` with
`--perform_emulator_setup`, as its README describes. It takes several minutes.

## 3. Settings

Every machine-specific value is read from the environment, with the value the study used as its default.
Set a variable only if your machine differs. Put it in your shell, or in `.env` at the repo root (copy
`.env.example`). A shell export wins over `.env`. `scripts/run_overnight.sh` reads only the shell.

| Variable | Default | What it is |
|---|---|---|
| `ANDROID_HOME` | `~/Library/Android/sdk` | Android SDK; `adb` and the emulator are found under it |
| `ANDROID_CONSOLE_PORT` | `5554` | Emulator console port |
| `ANDROID_GRPC_PORT` | `8554` | Must match the emulator's `-grpc` flag |
| `ANDROID_AVD` | `AndroidWorldAvd` | Virtual device name, used by `scripts/run_overnight.sh` |
| `ANDROID_WORLD_LAUNCH_TIMEOUT` | `45` | App-launch timeout from the patch, in seconds |
| `GROQ_API_KEY` | none | Needed only to run the baseline agent |

## 4. Check

```bash
cd code
.venv/bin/python -m pytest -q                 # no emulator or key needed
.venv/bin/python -m flowagent.analyze         # regenerates runs/results.md, byte-identical
.venv/bin/python scripts/smoke_env.py         # needs the emulator; no key
```

`smoke_env.py` resets the device, runs a few steps of one task and prints observe and act latency. If it
works, replay will run. Commands for the experiments are in `README.md`.

The emulator uses about 1.3 GB of RAM. Stop it when idle: `adb emu kill`.
