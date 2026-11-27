from app.storage.markdown_file import MarkdownFile


class TestMarkdownFile:
    def test_roundtrip(self, tmp_path):
        file = MarkdownFile(tmp_path / "test.md")
        file.write({"title": "Test", "status": "done", "duration": 120}, "Hello transcript")
        assert file.read() == ({"title": "Test", "status": "done", "duration": "120"}, "Hello transcript")

    def test_no_body(self, tmp_path):
        file = MarkdownFile(tmp_path / "test.md")
        file.write({"title": "Test"})
        assert file.read() == ({"title": "Test"}, "")

    def test_keeps_key_order(self, tmp_path):
        path = tmp_path / "test.md"
        MarkdownFile(path).write({"zebra": 1, "apple": 2})
        assert path.read_text().index("zebra") < path.read_text().index("apple")

    def test_missing_file(self, tmp_path):
        assert MarkdownFile(tmp_path / "missing.md").read() is None

    def test_no_frontmatter(self, tmp_path):
        path = tmp_path / "plain.md"
        path.write_text("just text")
        assert MarkdownFile(path).read() == ({}, "")

    def test_invalid_yaml_reads_as_empty_meta(self, tmp_path):
        path = tmp_path / "broken.md"
        path.write_text("---\n: : :\n  - [\n---\nbody\n")
        assert MarkdownFile(path).read() == ({}, "body")

    def test_empty_values_become_empty_strings(self, tmp_path):
        path = tmp_path / "test.md"
        path.write_text("---\nerror:\n---\n")
        assert MarkdownFile(path).read() == ({"error": ""}, "")
