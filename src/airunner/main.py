#!/usr/bin/env python
"""
----------------------------------------------------------------
Import order is crucial for AI Runner to work as expected.
Do not change the order of the imports.
----------------------------------------------------------------
"""
################################################################
# Facehugger Shield is a privacy library that locks down
# file system, network and log operations.
# Keep this at the top of the main file.
################################################################
from airunner.settings import AIRUNNER_DISABLE_FACEHUGGERSHIELD
import os

"""
Temporary fix for windows - Facehuggershield is not working correctly
on windows at this time so we disable it.
"""
if not AIRUNNER_DISABLE_FACEHUGGERSHIELD:
    from airunner.facehuggershield.huggingface import activate
    import sys  # Import sys to access executable path

    airunner_path = os.path.join(os.path.expanduser("~"), ".local", "share", "airunner")
    # Determine site-packages path dynamically
    venv_path = os.path.dirname(os.path.dirname(sys.executable))
    site_packages_path = os.path.join(
        venv_path,
        "lib",
        f"python{sys.version_info.major}.{sys.version_info.minor}",
        "site-packages",
    )
    # Determine project root and src/airunner path
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    airunner_src_path = os.path.join(project_root, "src", "airunner")

    activate(
        activate_shadowlogger=False,
        darklock_os_whitelisted_operations=["makedirs", "mkdir", "open"],
        darklock_os_whitelisted_directories=[
            airunner_path,
            os.path.join(airunner_path, "data"),
            os.path.join(os.path.expanduser("~"), ".triton/cache/"),
            "/dev/",
            "/proc/",
            site_packages_path,  # Added site-packages path
            "/usr/share/zoneinfo/",  # Added /usr/share/zoneinfo/
            airunner_src_path,  # Added project src path
            "/tmp/",  # Added /tmp/ for temporary file operations
        ],
        nullscream_whitelist=[
            "huggingface_hub.file_download",
            "huggingface_hub.repocard_data",
            "transformers.utils.hub.PushToHubMixin",
            "transformers",
        ],
    )
#################################################################

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"

# Initialize the logger
import logging

logging.getLogger("torio._extension.utils").setLevel(logging.WARNING)
logging.getLogger("google.cloud.storage._opentelemetry_tracing").setLevel(
    logging.WARNING
)
logging.getLogger("numba").setLevel(logging.WARNING)
logging.getLogger("h5py._conv").setLevel(logging.WARNING)
logging.getLogger("matplotlib").setLevel(logging.WARNING)
logging.getLogger("datasets").setLevel(logging.WARNING)
logging.getLogger("bitsandbytes").setLevel(logging.WARNING)

import sys
from airunner.settings import AIRUNNER_LOG_FILE, AIRUNNER_SAVE_LOG_TO_FILE
import argparse
from airunner.utils.settings.get_qsettings import get_qsettings

base_path = os.path.join(os.path.expanduser("~"), ".local", "share", "airunner")

################################################################
# Ensure that the base directory exists.
################################################################
base_dir = os.path.join(base_path, "data")
try:
    os.makedirs(base_dir, exist_ok=True)
except FileExistsError:
    pass

DEV_ENV = os.environ.get("DEV_ENV", "1") == "1"
if AIRUNNER_SAVE_LOG_TO_FILE and not DEV_ENV:
    sys.stdout = open(AIRUNNER_LOG_FILE, "a")
    sys.stderr = open(AIRUNNER_LOG_FILE, "a")

################################################################
# Set the environment variable for PyTorch to use expandable
################################################################
import torch

torch.hub.set_dir(
    os.environ.get("TORCH_HOME", "/home/appuser/.local/share/airunner/torch/hub")
)

################################################################
# Import the main application class for AI Runner.
################################################################
from airunner.api import API

###############################################################
# Import Alembic modules to run migrations.
################################################################
from airunner.setup_database import setup_database


def main():
    parser = argparse.ArgumentParser(description="AI Runner")
    parser.add_argument(
        "--clear-window-settings",
        action="store_true",
        help="Clear window settings",
    )
    parser.add_argument(
        "--print-llm-system-prompt",
        action="store_true",
        help="Print LLM System prompt to console",
    )
    parser.add_argument(
        "--perform-llm-analysis",
        action="store_true",
        help="Perform LLM analysis",
    )
    parser.add_argument("--chatbot-only", action="store_true", help="Run LLM only")
    args = parser.parse_args()

    if args.clear_window_settings:
        # Clear all window settings from QSettings
        settings = get_qsettings()
        settings.beginGroup("splitters")
        settings.remove("")  # Removes all keys under the "splitters" group
        settings.endGroup()

    if args.print_llm_system_prompt:
        os.environ["AIRUNNER_LLM_PRINT_SYSTEM_PROMPT"] = "1"

    if args.perform_llm_analysis:
        os.environ["AIRUNNER_LLM_PERFORM_ANALYSIS"] = "1"

    if args.chatbot_only:
        os.environ["AIRUNNER_ART_ENABLED"] = "0"
    setup_database()

    # Start the main application
    API()


if __name__ == "__main__":
    main()
