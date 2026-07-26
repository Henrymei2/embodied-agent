import os
import requests
import tarfile
from pathlib import Path
from tqdm import tqdm
from loguru import logger


def get_github_asset_url(owner, repo, release_tag, filename_without_ext):
    """
    Fetch the URL of a GitHub release asset by its filename (without extension).

    Args:
        owner (str): The owner of the repository.
        repo (str): The name of the repository.
        release_tag (str): The tag of the release.
        filename_without_ext (str): The filename to search for (without extension).

    Returns:
        str: The download URL of the matched asset, or None if no match is found.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{release_tag}"
    headers = {}  # Add authentication headers if needed

    try:
        # Make a GET request to fetch release data
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        # Parse the JSON response
        release_data = response.json()
        assets = release_data.get("assets", [])

        # Look for a matching file
        for asset in assets:
            if asset["name"].startswith(filename_without_ext):
                logger.info(f"Match found: {asset['name']}")
                return asset["browser_download_url"]

        # If no match found, log the error
        logger.error(
            f"No match found for filename: {filename_without_ext} in release {release_tag}."
        )
        return None

    except requests.exceptions.RequestException as e:
        logger.error(f"An error occurred while fetching release data: {e}")
        return None


def download_and_extract(url: str, output_dir: str) -> Path:
    """
    Download a file from a URL and extract it if it is a tar.bz2 archive.

    Args:
        url (str): The URL to download the file from.
        output_dir (str): The directory to save the downloaded file.

    Returns:
        Path: Path to the extracted directory if it's a tar.bz2 file,
             otherwise Path to the downloaded file.
    """
    # Create the output directory if it doesn't exist
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Get the file name from the URL
    file_name = url.split("/")[-1]
    file_path = os.path.join(output_dir, file_name)

    # Extract the root directory name from the filename (removing .tar.bz2)
    root_dir = file_name.replace(".tar.bz2", "")
    extracted_dir_path = Path(output_dir) / root_dir

    # Check if the extracted directory already exists
    if extracted_dir_path.exists():
        if not is_model_dir_complete(extracted_dir_path, Path(file_path)):
            # Re-downloading over a half-extracted directory would silently
            # produce the same broken model, so stop with the fix instead.
            raise RuntimeError(
                f"The model at {extracted_dir_path} is a partial extraction. "
                f"Delete that directory and {file_path}, then start again to "
                "download a fresh copy."
            )
        logger.info(
            f"✅ The directory {extracted_dir_path} already exists. I would assume that the model is already downloaded and we are ready to go. Skipping download and extraction."
        )
        return extracted_dir_path

    # Download the file
    logger.info(f"🏃‍♂️Downloading {url} to {file_path}...")
    response = requests.get(url, stream=True)
    response.raise_for_status()  # Raise an error for bad status codes
    total_size = int(response.headers.get("content-length", 0))
    logger.debug(f"Total file size: {total_size / 1024 / 1024:.2f} MB")

    with (
        open(file_path, "wb") as f,
        tqdm(
            desc=file_name,
            total=total_size,
            unit="iB",
            unit_scale=True,
            unit_divisor=1024,
        ) as pbar,
    ):
        for chunk in response.iter_content(chunk_size=8192):
            size = f.write(chunk)
            pbar.update(size)

    logger.info(f"Downloaded {file_name} successfully.")

    # Extract the tar.bz2 file
    if file_name.endswith(".tar.bz2"):
        logger.info(f"Extracting {file_name}...")
        with tarfile.open(file_path, "r:bz2") as tar:
            tar.extractall(path=output_dir)
        logger.info("Extraction completed.")

        # Delete the compressed file
        os.remove(file_path)
        logger.debug(f"Deleted the compressed file: {file_name}")

        return extracted_dir_path
    else:
        logger.warning("The downloaded file is not a tar.bz2 archive.")
        return Path(file_path)


def is_model_dir_complete(model_dir: Path, archive_path: Path | None = None) -> bool:
    """True when a model directory can be trusted as a finished extraction.

    An interrupted download or a truncated archive leaves the directory in
    place holding a partial `model.onnx`, which every later check happily
    accepts until the engine fails deep inside ONNX with "No graph was found in
    the protobuf" — a message that says nothing about which file to delete.

    Two things give it away. The weights may be missing entirely; and because a
    successful extraction deletes its archive, an archive still sitting beside
    the directory means the extraction never finished. A truncated `.onnx` of
    plausible size cannot be detected here — that is what `scripts/check_voice.py`
    is for — but these two cases cover an interrupted download.
    """
    if archive_path is not None and archive_path.exists():
        return False
    try:
        weights = [
            path
            for path in model_dir.rglob("*.onnx")
            if path.is_file() and path.stat().st_size > 0
        ]
    except OSError:
        return False
    return bool(weights)


def check_and_extract_local_file(url: str, output_dir: str) -> Path | None:
    """
    Check if a local file exists and extract it if it is a tar.bz2 archive.

    Args:
        url (str): The URL of the file.
        output_dir (str): The directory to save the extracted files.

    Returns:
        Path | None: Path to the extracted directory if it's a tar.bz2 file,
            otherwise None.
    """
    # Get the file name from the URL
    file_name = url.split("/")[-1]
    compressed_path = Path(output_dir) / file_name

    # Check if the compressed file exists and is a tar.bz2 archive
    extracted_dir = Path(output_dir) / file_name.replace(".tar.bz2", "")

    if extracted_dir.exists():
        if is_model_dir_complete(extracted_dir, compressed_path):
            logger.info(
                f"✅ Extracted directory exists: {extracted_dir}, no operation needed."
            )
            return extracted_dir
        # A half-extracted directory looks finished, and the engine only
        # discovers otherwise as an opaque protobuf error much later.
        logger.error(
            f"❌ Model directory {extracted_dir} is a partial extraction. "
            f"Delete it and {compressed_path}, then start the server again to "
            "download a fresh copy."
        )
        return None

    if compressed_path.exists() and file_name.endswith(".tar.bz2"):
        logger.info(f"🔍 Found local archive file: {compressed_path}")

        try:
            logger.info("⏳ Extracting archive file...")
            with tarfile.open(compressed_path, "r:bz2") as tar:
                tar.extractall(path=output_dir)
            logger.success(f"Extracted archive to the path: {extracted_dir}")
            os.remove(compressed_path)  # Remove the compressed file
            return extracted_dir
        except Exception as e:
            logger.error(
                f"❌ Failed to extract {compressed_path}: {e}. The archive is "
                "truncated or corrupt; delete it (and any partially extracted "
                f"{extracted_dir}) so it can be downloaded again."
            )
            return None

    logger.warning(f"Local file not found or not a tar.bz2 archive: {compressed_path}")
    return None


if __name__ == "__main__":
    url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2"
    output_dir = "./models"

    # Try local extraction first.
    local_result = check_and_extract_local_file(url, output_dir)

    # Download if not available locally.
    if local_result is None:
        logger.info("Local archive not found. Starting download...")
        download_and_extract(url, output_dir)
    else:
        logger.info("Extraction completed using local file.")
