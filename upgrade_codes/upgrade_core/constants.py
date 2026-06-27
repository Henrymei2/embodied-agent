# upgrade/constants.py
# CURRENT_SCRIPT_VERSION = "0.2.0"
from ruamel.yaml import YAML
from src.open_llm_vtuber.config_manager.utils import load_text_file_with_guess_encoding
import os

USER_CONF = "conf.yaml"
BACKUP_CONF = "conf.yaml.backup"

ZH_DEFAULT_CONF = "config_templates/conf.ZH.default.yaml"
EN_DEFAULT_CONF = "config_templates/conf.default.yaml"

yaml = YAML()
# user_config = yaml.load(load_text_file_with_guess_encoding(USER_CONF))
# CURRENT_SCRIPT_VERSION = user_config.get("system_config", {}).get("conf_version")


def load_user_config():
    if not os.path.exists(USER_CONF):
        return None
    text = load_text_file_with_guess_encoding(USER_CONF)
    if text is None:
        return None
    return yaml.load(text)


def get_current_script_version():
    config = load_user_config()
    if config:
        return config.get("system_config", {}).get("conf_version", "UNKNOWN")
    return "UNKNOWN"


CURRENT_SCRIPT_VERSION = get_current_script_version()

TEXTS = {
    "en": {
        # "welcome_message": f"Auto-Upgrade Script {CURRENT_SCRIPT_VERSION}\nOpen-LLM-VTuber upgrade script - This script is highly experimental and may not work as expected.",
        "welcome_message": f"Starting auto upgrade from {CURRENT_SCRIPT_VERSION}...",
        "not_git_repo": "Error: Current directory is not a git repository. Please run this script inside the Open-LLM-VTuber directory.\nAlternatively, it is likely that the Open-LLM-VTuber you downloaded does not contain the .git folder (this can happen if you downloaded a zip archive instead of using git clone), in which case you cannot upgrade using this script.",
        "backup_user_config": "Backing up {user_conf} to {backup_conf}",
        "configs_up_to_date": "[DEBUG] User configuration is up-to-date.",
        "no_config": "Warning: conf.yaml not found",
        "copy_default_config": "Copying default configuration from template",
        "uncommitted": "Found uncommitted changes, stashing...",
        "stash_error": "Error: Unable to stash changes",
        "changes_stashed": "Changes stashed",
        "pulling": "Pulling updates from remote repository...",
        "pull_error": "Error: Unable to pull updates",
        "restoring": "Restoring stashed changes...",
        "conflict_warning": "Warning: Conflicts occurred while restoring stashed changes",
        "manual_resolve": "Please resolve conflicts manually",
        "stash_list": "Use 'git stash list' to view stashed changes",
        "stash_pop": "Use 'git stash pop' to restore changes",
        "upgrade_complete": "Upgrade complete!",
        "check_config": "1. Please check if conf.yaml needs updating",
        "resolve_conflicts": "2. Resolve any config file conflicts manually",
        "check_backup": "3. Check backup config to ensure no important settings are lost",
        "git_not_found": "Error: Git not found. Please install Git first:\nWindows: https://git-scm.com/download/win\nmacOS: brew install git\nLinux: sudo apt install git",
        "operation_preview": """
This script will perform the following operations:
1. Backup current conf.yaml configuration file
2. Stash all uncommitted changes (git stash)
3. Pull latest code from remote repository (git pull)
4. Attempt to restore previously stashed changes (git stash pop)

Continue? (y/N): """,
        "merged_config_success": "Merged new configuration items:",
        "merged_config_none": "No new configuration items found.",
        "merge_failed": "Configuration merge failed: {error}",
        "updating_submodules": "Updating submodules...",
        "submodules_updated": "Submodules updated successfully",
        "submodule_error": "Error updating submodules",
        "no_submodules": "No submodules detected, skipping update",
        "env_info": "Environment: {os_name} {os_version}, Python {python_version}",
        "git_version": "Git version: {git_version}",
        "current_branch": "Current branch: {branch}",
        "operation_time": "Operation '{operation}' completed in {time:.2f} seconds",
        "checking_stash": "Checking for uncommitted changes...",
        "detected_changes": "Detected changes in {count} files",
        "submodule_updating": "Updating submodule: {submodule}",
        "submodule_updated": "Submodule updated: {submodule}",
        "submodule_update_error": "❌ Submodule update failed.",
        "checking_remote": "Checking remote repository status...",
        "remote_ahead": "Local version is up to date",
        "remote_behind": "Found {count} new commits to pull",
        "config_backup_path": "Config backup path: {path}",
        "start_upgrade": "Starting upgrade process...",
        "version_upgrade_success": "Config version upgraded: {old} → {new}",
        "version_upgrade_none": "No upgrade needed. Current version is {version}",
        "version_upgrade_failed": "Failed to upgrade config version: {error}",
        "finish_upgrade": "Upgrade process completed, total time: {time:.2f} seconds",
        "backup_used_version": "✅ Loaded config version from backup: {backup_version}",
        "backup_read_error": "⚠️ Failed to read backup file. Falling back to default version {version}. Error: {error}",
        "version_too_old": "🔁 Detected old version {found} which is lower than the minimum supported version, forced to use {adjusted}",
        "checking_ahead_status": "🔍 Checking for unpushed local commits...",
        "local_ahead": "🚨 You have {count} local commit(s) on 'main' that are NOT pushed to remote.",
        "push_blocked": (
            "⛔ You do NOT have permission to push to the 'main' branch.\n"
            "Your commits are local only and will NOT be synced to GitHub.\n"
            "Continuing the upgrade may cause those commits to be lost or conflict with remote changes."
        ),
        "backup_suggestion": (
            "🛟 To keep your work safe, you can choose one of the following options:\n"
            "🔄 1. Undo the last commit:\n"
            "   • GitHub Desktop: Click the 'Undo' button at the bottom right.\n"
            "   • Terminal: Run: git reset --soft HEAD~1\n"
            "📦 2. Export your commit(s) as a patch file:\n"
            "   → Run: git format-patch origin/main --stdout > backup.patch\n"
            "🌿 3. Create a backup branch:\n"
            "   → Run: git checkout -b my-backup-before-upgrade\n"
            "💡 Recommendation: After undoing the commit, you can switch to a new branch or export changes as needed."
        ),
        "abort_upgrade": "🛑 Upgrade aborted to protect your local commits.",
        "no_config_fatal": (
            "❌ Config file conf.yaml not found.\n"
            "Please either:\n"
            "👉 Copy your old config file to the current directory\n"
            "👉 Or run run_server.py to generate a default template"
        ),
    },
}
# Chinese localization has been removed; "zh" falls back to English.
TEXTS["zh"] = TEXTS["en"]

# Multilingual texts for merge_configs log messages
TEXTS_MERGE = {
    "en": {
        "new_config_item": "[INFO] New config item: {key}",
    },
}
TEXTS_MERGE["zh"] = TEXTS_MERGE["en"]

# Multilingual texts for compare_configs log messages
TEXTS_COMPARE = {
    "en": {
        "missing_keys": "User config is missing the following keys, which may be out-of-date: {keys}",
        "extra_keys": "User config contains the following keys not present in default config: {keys}",
        "up_to_date": "User config is up-to-date with default config.",
        "compare_passed": "{name} comparison passed.",
        "compare_failed": "{name} comparison failed: configs differ.",
        "compare_diff_item": "- {item}",
        "compare_error": "{name} comparison error: {error}",
        "comments_up_to_date": "Comments are up to date, skipping comment sync.",
        "extra_keys_deleted_count": "Deleted {count} extra keys:",
        "extra_keys_deleted_item": "  - {key}",
        "comment_sync_success": "All comments synchronized successfully.",
        "comment_sync_error": "Failed to synchronize comments: {error}",
    },
}
TEXTS_COMPARE["zh"] = TEXTS_COMPARE["en"]

UPGRADE_TEXTS = {
    "en": {
        "model_dict_not_found": "⚠️ model_dict.json not found. Skipping upgrade.",
        "model_dict_read_error": "❌ Failed to read model_dict.json: {error}",
        "upgrade_success": "✅ model_dict.json upgraded to v1.2.1 format ({language} language)",
        "already_latest": "model_dict.json already in latest format.",
        "upgrade_error": "❌ Failed to upgrade model_dict.json: {error}",
        "no_upgrade_routine": "No upgrade routine for version {version}",
        "upgrading_path": "⬆️ Upgrading config: {from_version} → {to_version}",
    },
}
UPGRADE_TEXTS["zh"] = UPGRADE_TEXTS["en"]
