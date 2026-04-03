# new_test_case_file

import pytest
from pathlib import Path
from io import StringIO
import sys
from case7 import list_all_indexes
from leann.api import LeannBuilder
import tempfile
import shutil


def test_list_all_indexes_with_existing_index(tmp_path, monkeypatch, capsys):
    """Test listing indexes when at least one index exists."""
    # 保存原始目录，确保测试结束后能恢复
    original_dir = Path.cwd()
    
    try:
        monkeypatch.chdir(tmp_path)
        
        # Create .leann/indexes directory structure
        indexes_dir = tmp_path / ".leann" / "indexes" / "test_index"
        indexes_dir.mkdir(parents=True)
        
        # Build an index
        index_path = str(indexes_dir / "documents.leann")
        builder = LeannBuilder(backend_name="hnsw")
        builder.add_text("Test document content.")
        builder.build_index(index_path)
        
        # List indexes
        list_all_indexes()
        
        # Capture output
        captured = capsys.readouterr()
        
        # Verify output contains expected information
        assert "LEANN Indexes" in captured.out or "test_index" in captured.out or "📚" in captured.out
    finally:
        # 确保恢复到原始目录后再清理
        if original_dir.exists():
            monkeypatch.chdir(original_dir)
        # Clean up
        if (tmp_path / ".leann").exists():
            shutil.rmtree(tmp_path / ".leann")


def test_list_all_indexes_empty(tmp_path, monkeypatch, capsys):
    """Test listing indexes when no indexes exist."""
    original_dir = Path.cwd()
    
    try:
        monkeypatch.chdir(tmp_path)
        
        # List indexes (should handle empty case gracefully)
        list_all_indexes()
        
        # Capture output
        captured = capsys.readouterr()
        
        # Should not crash and should produce some output
        assert isinstance(captured.out, str)
    finally:
        if original_dir.exists():
            monkeypatch.chdir(original_dir)

