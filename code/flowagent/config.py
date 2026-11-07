"""Machine-specific settings, read from the environment or the git-ignored .env.

Each default is the value the study ran with: macOS, Android Studio's default SDK
location, and the ports AndroidWorld documents. Set the variable to override it.
"""
import os
import pathlib

from flowagent.llm import load_env

load_env()

ANDROID_HOME = pathlib.Path(os.environ.get('ANDROID_HOME', '~/Library/Android/sdk')).expanduser()
ADB = str(ANDROID_HOME / 'platform-tools' / 'adb')
CONSOLE_PORT = int(os.environ.get('ANDROID_CONSOLE_PORT', '5554'))
GRPC_PORT = int(os.environ.get('ANDROID_GRPC_PORT', '8554'))
