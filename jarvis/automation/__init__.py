"""Desktop application and developer workflow automation package."""

from jarvis.automation.app_controller import AppController
from jarvis.automation.app_registry import AppEntry, AppRegistry
from jarvis.automation.developer import DeveloperAutomation

__all__ = ["AppController", "AppEntry", "AppRegistry", "DeveloperAutomation"]
