from voice_workbench_engines.directories import link_directory, unlink_directory


def test_training_reference_handles_spaces_and_unicode_without_deleting_target(tmp_path):
    target = tmp_path / "中文 工作目录"
    target.mkdir()
    (target / "keep.wav").write_bytes(b"keep")
    link = tmp_path / "训练 日志"
    link_directory(link, target)
    assert (link / "keep.wav").read_bytes() == b"keep"
    unlink_directory(link, tmp_path / "wrong target")
    assert link.exists()
    unlink_directory(link, target)
    assert not link.exists()
    assert (target / "keep.wav").read_bytes() == b"keep"
