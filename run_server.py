import os
import sys
import atexit
import asyncio
import argparse
import subprocess
import shutil
from pathlib import Path
import tomli
import uvicorn
from src.open_llm_vtuber.workspace import ensure_workspace
from loguru import logger
from upgrade_codes.upgrade_manager import UpgradeManager

from src.open_llm_vtuber.server import WebSocketServer
from src.open_llm_vtuber.config_manager import Config, read_yaml, validate_config

os.environ["HF_HOME"] = str(Path(__file__).parent / "models")
os.environ["MODELSCOPE_CACHE"] = str(Path(__file__).parent / "models")

upgrade_manager = UpgradeManager()
PROJECT_ROOT = Path(__file__).parent
FRONTEND_SOURCE_DIR = PROJECT_ROOT / "frontend" / "src" / "Open-LLM-VTuber-Web"
FRONTEND_DIST_DIR = FRONTEND_SOURCE_DIR / "dist" / "web"
FRONTEND_SERVE_DIR = PROJECT_ROOT / "frontend"


def get_version() -> str:
    with open("pyproject.toml", "rb") as f:
        pyproject = tomli.load(f)
    return pyproject["project"]["version"]


def init_logger(console_log_level: str = "INFO") -> None:
    logger.remove()
    # Console output
    logger.add(
        sys.stderr,
        level=console_log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | {message}",
        colorize=True,
    )

    # File output
    logger.add(
        "logs/debug_{time:YYYY-MM-DD}.log",
        rotation="10 MB",
        retention="30 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message} | {extra}",
        backtrace=True,
        diagnose=True,
    )


def check_frontend_submodule(lang=None):
    """
    Check if the frontend submodule is initialized. If not, attempt to initialize it.
    If initialization fails, log an error message.
    """
    frontend_path = Path(__file__).parent / "frontend" / "index.html"
    if not frontend_path.exists():
        logger.warning(
            "Frontend submodule not found, attempting to initialize submodules..."
        )

        # try:
        #     subprocess.run(
        #         ["git", "submodule", "update", "--init", "--recursive"], check=True
        #     )
        #     if frontend_path.exists():
        #         logger.info(
        #             "👍 Frontend submodule (and other submodules) initialized successfully."
        #         )
        #     else:
        #         logger.critical(
        #             'Failed to initialize submodules. \nYou might see {{"detail":"Not Found"}} in your browser. Please check our quick start guide and common issues page from our documentation.'
        #         )
        #         logger.error(
        #             "Frontend files are still missing after submodule initialization.\n"
        #             + "Did you manually change or delete the `frontend` folder?  \n"
        #             + "It's a Git submodule — you shouldn't modify it directly.  \n"
        #             + "If you did, discard your changes with `git restore frontend`, then try again.\n"
        #         )
        # except Exception as e:
        #     logger.critical(
        #         f'Failed to initialize submodules: {e}. \nYou might see {{"detail":"Not Found"}} in your browser. Please check our quick start guide and common issues page from our documentation.\n'
        #     )


def build_frontend() -> None:
    """
    Build the React frontend source and copy generated files into the served
    frontend directory without deleting docs or source files.
    """
    if not FRONTEND_SOURCE_DIR.exists():
        logger.critical(f"Frontend source directory not found: {FRONTEND_SOURCE_DIR}")
        sys.exit(1)

    node_modules_path = FRONTEND_SOURCE_DIR / "node_modules"
    if not node_modules_path.exists():
        logger.critical(
            "Frontend dependencies are missing. Run `npm install` in "
            f"`{FRONTEND_SOURCE_DIR}` before starting the server."
        )
        sys.exit(1)

    logger.info("Building frontend from frontend/src...")
    # Resolve npm ourselves: on Windows it is `npm.CMD`, and CreateProcess does
    # not apply PATHEXT, so passing the bare name here never finds it.
    npm_executable = shutil.which("npm")
    if npm_executable is None:
        logger.critical(
            "`npm` was not found. Install Node.js/npm before starting the server."
        )
        sys.exit(1)
    try:
        subprocess.run(
            [npm_executable, "run", "build:web"],
            cwd=FRONTEND_SOURCE_DIR,
            check=True,
        )
    except FileNotFoundError:
        logger.critical(
            "`npm` was not found. Install Node.js/npm before starting the server."
        )
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        logger.critical(f"Frontend build failed with exit code {e.returncode}.")
        sys.exit(e.returncode)

    if not (FRONTEND_DIST_DIR / "index.html").exists():
        logger.critical(
            f"Frontend build did not produce {FRONTEND_DIST_DIR / 'index.html'}."
        )
        sys.exit(1)

    FRONTEND_SERVE_DIR.mkdir(parents=True, exist_ok=True)
    for item in FRONTEND_DIST_DIR.iterdir():
        if item.name in {"src", "frontend_api_doc.md"}:
            logger.warning(
                f"Skipping preserved frontend path from build output: {item.name}"
            )
            continue

        destination = FRONTEND_SERVE_DIR / item.name
        if destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()

        if item.is_dir():
            shutil.copytree(item, destination)
        else:
            shutil.copy2(item, destination)

    logger.info("Frontend build copied to served frontend directory.")


def parse_args():
    parser = argparse.ArgumentParser(description="Open-LLM-VTuber Server")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--hf_mirror", action="store_true", help="Use Hugging Face mirror"
    )
    parser.add_argument(
        "--skip-frontend-build",
        action="store_true",
        help="Skip rebuilding frontend/src before starting the server",
    )
    return parser.parse_args()


@logger.catch
def run(console_log_level: str, skip_frontend_build: bool = False):
    init_logger(console_log_level)
    logger.info(f"Open-LLM-VTuber, version v{get_version()}")
    workspace = ensure_workspace()
    logger.info(f"Agent workspace ready: {workspace.root}")

    # Get selected language
    lang = upgrade_manager.lang

    if skip_frontend_build:
        logger.info(
            "Skipping frontend build because --skip-frontend-build was provided."
        )
    else:
        build_frontend()

    # Check if the frontend submodule is initialized
    check_frontend_submodule(lang)

    # Sync user config with default config
    try:
        upgrade_manager.sync_user_config()
    except Exception as e:
        logger.error(f"Error syncing user config: {e}")

    atexit.register(WebSocketServer.clean_cache)

    # Load configurations from yaml file
    config: Config = validate_config(read_yaml("conf.yaml"))
    server_config = config.system_config

    if server_config.enable_proxy:
        logger.info("Proxy mode enabled - /proxy-ws endpoint will be available")

    # Initialize the WebSocket server (synchronous part)
    server = WebSocketServer(config=config)

    # Perform asynchronous initialization (loading context, etc.)
    logger.info("Initializing server context...")
    try:
        asyncio.run(server.initialize())
        logger.info("Server context initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize server context: {e}")
        sys.exit(1)  # Exit if initialization fails

    # Run the Uvicorn server
    logger.info(f"Starting server on {server_config.host}:{server_config.port}")
    uvicorn.run(
        app=server.app,
        host=server_config.host,
        port=server_config.port,
        log_level=console_log_level.lower(),
    )


if __name__ == "__main__":
    args = parse_args()
    console_log_level = "DEBUG" if args.verbose else "INFO"
    if args.verbose:
        logger.info("Running in verbose mode")
    else:
        logger.info(
            "Running in standard mode. For detailed debug logs, use: uv run run_server.py --verbose"
        )
    if args.hf_mirror:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    run(
        console_log_level=console_log_level,
        skip_frontend_build=args.skip_frontend_build,
    )
