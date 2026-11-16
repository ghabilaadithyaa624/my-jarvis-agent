"""Phase 4 Tests: Application management, file ops, .docx/.blend readers, organizer & duplicates."""

from __future__ import annotations

import gzip
from pathlib import Path

import docx

from jarvis.automation.app_controller import AppController
from jarvis.automation.app_registry import AppRegistry
from jarvis.filesystem.document_reader import DocumentReader
from jarvis.filesystem.operations import FileSystemManager
from jarvis.filesystem.organizer import DirectoryOrganizer, DuplicateFinder


def test_file_crud_search_and_overwrite_protection(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    fs = FileSystemManager(lambda: [root])

    # Create folder & file with verification
    fres = fs.create_folder(str(root / "notes"))
    assert fres["success"] and fres["verified"]

    cres = fs.create_file(str(root / "notes" / "plan.txt"), "Phase 4 verified!")
    assert cres["success"] and cres["verified"]
    assert cres["size_bytes"] == len("Phase 4 verified!")

    # Refuse silent overwrite of existing user file!
    overwrite_attempt = fs.create_file(str(root / "notes" / "plan.txt"), "clobber", overwrite=False)
    assert overwrite_attempt["success"] is False
    assert "Refused to overwrite" in overwrite_attempt["error"]
    assert (root / "notes" / "plan.txt").read_text() == "Phase 4 verified!"

    # Copy, rename, move — all verified
    cp = fs.copy_path(str(root / "notes" / "plan.txt"), str(root / "plan_copy.txt"))
    assert cp["success"] and cp["verified"]

    rn = fs.rename_path(str(root / "plan_copy.txt"), "plan_renamed.txt")
    assert rn["success"] and rn["verified"]
    assert (root / "plan_renamed.txt").is_file()

    fs.create_folder(str(root / "archive"))
    mv = fs.move_path(str(root / "plan_renamed.txt"), str(root / "archive"))
    assert mv["success"] and mv["verified"]
    assert (root / "archive" / "plan_renamed.txt").is_file()

    # Search by extension & tokenized name
    srch = fs.search_files("plan", extension=".txt")
    assert srch["count"] == 2


def test_document_reader_text_docx_and_blender_inspection(tmp_path: Path):
    reader = DocumentReader()

    # 1. Word .docx document
    docx_path = tmp_path / "spec.docx"
    d = docx.Document()
    d.add_paragraph("JARVIS Local AI Architecture")
    d.add_paragraph("Optimized for RTX 3050 6GB VRAM")
    d.save(str(docx_path))

    r_docx = reader.read(str(docx_path))
    assert r_docx["success"] and r_docx["verified"]
    assert "RTX 3050 6GB" in r_docx["content"]

    # 2. Valid uncompressed Blender 4.2 project + backup + textures folder
    proj_dir = tmp_path / "SolarEducation"
    (proj_dir / "textures").mkdir(parents=True)
    (proj_dir / "textures" / "earth_albedo.png").write_bytes(b"pngdata")
    blend_ok = proj_dir / "SolarSystem_Edu.blend"
    blend_ok.write_bytes(b"BLENDER-v402" + b"\x00" * 128)
    (proj_dir / "SolarSystem_Edu.blend1").write_bytes(b"BLENDER-v402" + b"\x00" * 64)

    info = reader.inspect_blend_file(str(blend_ok))
    assert info["success"] and info["verified"]
    assert info["healthy"] is True
    assert info["blender_version"] == "4.2"
    assert info["pointer_bits"] == 64
    assert "SolarSystem_Edu.blend1" in info["backup_files"]
    assert "textures" in info["companion_folders"]
    assert info["issues"] == []

    # 3. Gzip-compressed Blender 3.6 file
    blend_gz = tmp_path / "Compressed.blend"
    with gzip.open(blend_gz, "wb") as gzf:
        gzf.write(b"BLENDER-v306" + b"\x00" * 64)
    info_gz = reader.inspect_blend_file(str(blend_gz))
    assert info_gz["healthy"] is True
    assert info_gz["compression"] == "gzip"
    assert info_gz["blender_version"] == "3.6"

    # 4. Corrupted .blend file must be flagged honestly
    blend_bad = tmp_path / "Broken.blend"
    blend_bad.write_bytes(b"NOT_A_BLEND_FILE_AT_ALL")
    info_bad = reader.inspect_blend_file(str(blend_bad))
    assert info_bad["healthy"] is False
    assert len(info_bad["issues"]) >= 1


def test_downloads_organizer_collision_safety_and_duplicates(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "invoice.pdf").write_text("pdf1")
    (downloads / "photo.png").write_bytes(b"\x89PNG_12345")
    (downloads / "photo_copy.png").write_bytes(b"\x89PNG_12345")  # exact duplicate!
    (downloads / "robot.blend").write_bytes(b"BLENDER-v401" + b"\x00" * 32)
    (downloads / "helper.py").write_text("print('hi')")
    (downloads / "incomplete.crdownload").write_text("partial")  # must be skipped!

    # Pre-existing file inside Documents/invoice.pdf must NOT be overwritten!
    (downloads / "Documents").mkdir()
    (downloads / "Documents" / "invoice.pdf").write_text("ORIGINAL_DO_NOT_CLOBBER")

    # Duplicate finder first
    dups = DuplicateFinder().find_duplicates(str(downloads))
    assert dups["success"] and dups["duplicate_groups"] == 1
    assert len(dups["groups"][0]["files"]) == 2

    # Dry-run preview moves 0 files
    org = DirectoryOrganizer()
    preview = org.organize(str(downloads), dry_run=True)
    assert preview["planned_count"] == 5
    assert preview["moved_count"] == 0
    assert (downloads / "invoice.pdf").is_file()

    # Real organize moves all 5 safely, preserves original invoice.pdf, skips .crdownload
    done = org.organize(str(downloads), dry_run=False)
    assert done["success"] and done["verified"]
    assert done["moved_count"] == 5
    assert (downloads / "Documents" / "invoice.pdf").read_text() == "ORIGINAL_DO_NOT_CLOBBER"
    assert (downloads / "Documents" / "invoice (1).pdf").read_text() == "pdf1"
    assert (downloads / "3D_Blender" / "robot.blend").is_file()
    assert (downloads / "Code" / "helper.py").is_file()
    assert (downloads / "incomplete.crdownload").is_file()
    assert Path(done["manifest_path"]).is_file()


def test_app_registry_and_running_process_detection():
    reg = AppRegistry()
    py_entry = reg.resolve("python")
    assert py_entry is not None

    ctrl = AppController(reg)
    status = ctrl.is_running("python")
    assert status["success"] and status["verified"]
    assert status["running"] is True
    assert status["instance_count"] >= 1
