"""Safe, verified filesystem management package."""

from jarvis.filesystem.document_reader import DocumentReader
from jarvis.filesystem.operations import FileSystemManager, format_size
from jarvis.filesystem.organizer import CATEGORY_MAP, DirectoryOrganizer, DuplicateFinder

__all__ = [
    "CATEGORY_MAP",
    "DirectoryOrganizer",
    "DocumentReader",
    "DuplicateFinder",
    "FileSystemManager",
    "format_size",
]
