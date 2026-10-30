def test_project_files_exist():
    from pathlib import Path
    assert Path("app/main.py").exists()
    assert Path("app/models.py").exists()
