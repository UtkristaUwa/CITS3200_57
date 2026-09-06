"""The job entrypoint: source selection, and one bad source not sinking the run."""

import pytest

from web_scrapers import run_scrapers, storage


class TestSourceSelection:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("austender", ["austender"]),
            ("austender,vic,qld", ["austender", "vic", "qld"]),
            ("  VIC , QLD ", ["vic", "qld"]),
            ("", ["austender"]),
            (None, ["austender"]),
        ],
    )
    def test_parses_the_source_list(self, value, expected):
        assert run_scrapers.parse_sources(value) == expected

    def test_rejects_an_unknown_source_rather_than_silently_skipping_it(self):
        with pytest.raises(ValueError, match="nsw"):
            run_scrapers.parse_sources("austender,nsw")


class TestRun:
    def test_runs_each_requested_source(self, output_dir, monkeypatch):
        called = []
        monkeypatch.setitem(
            run_scrapers.SCRAPERS, "austender", lambda limit, out: called.append(("a", limit)) or []
        )
        monkeypatch.setitem(
            run_scrapers.SCRAPERS, "vic", lambda limit, out: called.append(("v", limit)) or []
        )

        run_scrapers.run(["austender", "vic"], 5, output_dir)

        assert called == [("a", 5), ("v", 5)]

    def test_a_failing_source_does_not_stop_the_others(self, output_dir, monkeypatch):
        def boom(limit, out):
            raise RuntimeError("portal down")

        monkeypatch.setitem(run_scrapers.SCRAPERS, "austender", boom)
        monkeypatch.setitem(
            run_scrapers.SCRAPERS, "vic", lambda limit, out: ["one", "two"]
        )

        total, failed = run_scrapers.run(["austender", "vic"], 5, output_dir)

        assert total == 2
        assert failed == ["austender"]


class TestBlobPrefix:
    """
    Raw scrape output is namespaced under raw/<source_id>/ so it cannot land in
    whatever prefix the front end builds document URLs from -- the attachments
    bucket is shared.
    """

    def test_uses_the_source_id_from_the_tenders_own_manifest(self, output_dir):
        folder = output_dir / "ATM_2026_3494"
        folder.mkdir()
        (folder / "documents.json").write_text('{"source_id": "austender"}')

        assert storage.blob_prefix(folder) == "raw/austender/ATM_2026_3494"

    def test_keeps_two_sources_apart(self, output_dir):
        for reference, source in (("A1", "austender"), ("V1", "vic-buyingfor")):
            folder = output_dir / reference
            folder.mkdir()
            (folder / "documents.json").write_text('{"source_id": "%s"}' % source)

        assert storage.blob_prefix(output_dir / "A1") == "raw/austender/A1"
        assert storage.blob_prefix(output_dir / "V1") == "raw/vic-buyingfor/V1"

    def test_an_unreadable_manifest_still_gets_a_findable_prefix(self, output_dir):
        folder = output_dir / "ABC-1"
        folder.mkdir()

        assert storage.blob_prefix(folder) == "raw/unknown-source/ABC-1"

    def test_the_prefix_is_overridable(self, output_dir):
        folder = output_dir / "ABC-1"
        folder.mkdir()
        (folder / "documents.json").write_text('{"source_id": "qld-qtenders"}')

        assert storage.blob_prefix(folder, "scrapes") == "scrapes/qld-qtenders/ABC-1"


class TestPublishing:
    def test_is_skipped_when_no_bucket_is_configured(self, output_dir, monkeypatch):
        monkeypatch.delenv("OUTPUT_BUCKET", raising=False)

        assert storage.publish(output_dir) == (0, 0)

    def test_uploads_every_tender_folder_when_a_bucket_is_set(
        self, output_dir, monkeypatch
    ):
        for reference in ("ABC-1", "ABC-2"):
            folder = output_dir / reference
            folder.mkdir()
            (folder / f"{reference}.txt").write_text("body")
            (folder / "documents.json").write_text("{}")
        (output_dir / "stray.txt").write_text("not a tender folder")

        uploaded = []
        monkeypatch.setenv("OUTPUT_BUCKET", "test-bucket")
        monkeypatch.setattr(
            storage,
            "upload_tender_folder",
            lambda bucket, folder, prefix: uploaded.append((bucket, folder.name)) or 2,
        )

        folders, objects = storage.publish(output_dir)

        assert folders == 2
        assert objects == 4
        assert uploaded == [("test-bucket", "ABC-1"), ("test-bucket", "ABC-2")]

    def test_one_failed_upload_does_not_lose_the_rest(self, output_dir, monkeypatch):
        for reference in ("ABC-1", "ABC-2"):
            (output_dir / reference).mkdir()

        def flaky(bucket, folder, prefix):
            if folder.name == "ABC-1":
                raise RuntimeError("permission denied")
            return 3

        monkeypatch.setenv("OUTPUT_BUCKET", "test-bucket")
        monkeypatch.setattr(storage, "upload_tender_folder", flaky)

        assert storage.publish(output_dir) == (1, 3)
