"""
The entrypoint the pipeline's manager function calls.

Source selection, one bad source not sinking the run, and the two things the
manager relies on: the list of tender folders that came back, and a source
that could not run saying so instead of raising from three layers down.
"""

from pathlib import Path

import pytest

from web_scrapers import common, run_scrapers, storage


def writes(*references):
    """
    A stand-in scraper that writes real tender folders.

    Real ones, not mocks: run() counts what landed on disk rather than
    trusting a return value, which is the behaviour worth testing.
    """

    def scrape(limit, out):
        made = []
        for reference in (references[:limit] if limit else references):
            folder = Path(out) / reference
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "tender.json").write_text('{"source_id": "fake"}')
            (folder / f"{reference}.txt").write_text("body")
            made.append(folder)
        return made

    return scrape


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
    @pytest.fixture(autouse=True)
    def chrome_is_installed(self, monkeypatch):
        """
        These tests are about orchestration, not the browser preflight, and
        they must pass on a machine with no Chrome on it.
        """
        monkeypatch.setattr(common, "chrome_available", lambda: True)

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

    def test_hands_back_every_folder_it_wrote(self, output_dir, monkeypatch):
        monkeypatch.setitem(run_scrapers.SCRAPERS, "austender", writes("A1", "A2"))
        monkeypatch.setitem(run_scrapers.SCRAPERS, "vic", writes("V1"))

        result = run_scrapers.run(["austender", "vic"], 0, output_dir)

        assert [folder.name for folder in result.tender_dirs] == ["A1", "A2", "V1"]
        assert result.counts == {"austender": 2, "vic": 1}
        assert result.total == 3
        assert result.ok

    def test_counts_folders_on_disk_not_records_returned(self, output_dir, monkeypatch):
        """
        A scraper that reports five tenders but writes two folders has lost
        three, and the next stage can only process folders. Believing the
        return value would hide that.
        """

        def optimistic(limit, out):
            writes("A1", "A2")(limit, out)
            return ["record"] * 5

        monkeypatch.setitem(run_scrapers.SCRAPERS, "austender", optimistic)

        assert run_scrapers.run(["austender"], 0, output_dir).counts == {"austender": 2}

    def test_a_directory_without_a_record_is_not_a_tender(self, output_dir, monkeypatch):
        def messy(limit, out):
            writes("A1")(limit, out)
            (Path(out) / "downloads").mkdir()          # scratch the portal left
            (Path(out) / "urls.txt").write_text("x")   # not a directory at all

        monkeypatch.setitem(run_scrapers.SCRAPERS, "austender", messy)

        result = run_scrapers.run(["austender"], 0, output_dir)

        assert [folder.name for folder in result.tender_dirs] == ["A1"]

    def test_a_failing_source_does_not_stop_the_others(self, output_dir, monkeypatch):
        def boom(limit, out):
            raise RuntimeError("portal down")

        monkeypatch.setitem(run_scrapers.SCRAPERS, "austender", boom)
        monkeypatch.setitem(run_scrapers.SCRAPERS, "vic", writes("V1", "V2"))

        result = run_scrapers.run(["austender", "vic"], 5, output_dir)

        assert result.total == 2
        assert result.counts == {"vic": 2}
        assert "RuntimeError: portal down" in result.failed["austender"]
        assert not result.ok

    def test_the_summary_names_what_went_wrong(self, output_dir, monkeypatch):
        def boom(limit, out):
            raise RuntimeError("portal down")

        monkeypatch.setitem(run_scrapers.SCRAPERS, "austender", boom)
        monkeypatch.setitem(run_scrapers.SCRAPERS, "vic", writes("V1"))

        summary = run_scrapers.run(["austender", "vic"], 5, output_dir).summary()

        assert "1 tender(s)" in summary
        assert "failed: austender" in summary


class TestChromePreflight:
    """
    The two browser sources need a real Chrome, and SeleniumBase cannot install
    one -- on a plain python:slim image (which is what the pipeline runs today)
    there is no browser at all. Checked before launching, because the failure
    otherwise surfaces as a driver exception that reads like a Cloudflare block
    and sends whoever is on call to look at the portal instead of the image.
    """

    @pytest.fixture
    def no_chrome(self, monkeypatch):
        monkeypatch.setattr(common, "chrome_available", lambda: False)

    @pytest.fixture
    def chrome(self, monkeypatch):
        monkeypatch.setattr(common, "chrome_available", lambda: True)

    def test_plain_http_sources_never_need_a_browser(self, no_chrome):
        assert run_scrapers.unrunnable(["austender"]) == {}

    def test_browser_sources_are_ruled_out_without_one(self, no_chrome):
        assert set(run_scrapers.unrunnable(["austender", "vic", "qld"])) == {"vic", "qld"}

    def test_nothing_is_ruled_out_when_chrome_is_installed(self, chrome):
        assert run_scrapers.unrunnable(["austender", "vic", "qld"]) == {}

    def test_the_reason_says_what_to_do_about_it(self, no_chrome):
        reason = run_scrapers.unrunnable(["vic"])["vic"]

        assert "Chrome" in reason
        assert "INTEGRATION.md" in reason

    def test_a_skipped_source_is_never_called(self, output_dir, monkeypatch, no_chrome):
        called = []
        monkeypatch.setitem(
            run_scrapers.SCRAPERS, "vic", lambda limit, out: called.append(True)
        )
        monkeypatch.setitem(run_scrapers.SCRAPERS, "austender", writes("A1"))

        result = run_scrapers.run(["austender", "vic"], 5, output_dir)

        assert called == []
        assert result.counts == {"austender": 1}

    def test_skipped_is_reported_apart_from_failed(self, output_dir, no_chrome):
        """
        Both leave no folders behind, and only one of them means the portal
        changed shape -- the other means our image is wrong.
        """
        result = run_scrapers.run(["vic"], 5, output_dir)

        assert result.failed == {}
        assert "vic" in result.skipped
        assert "skipped: vic" in result.summary()


class TestManagerEntrypoint:
    """`run_scraper` is what manager.py imports; its signature is the contract."""

    @pytest.fixture(autouse=True)
    def fake_sources(self, monkeypatch):
        monkeypatch.setattr(common, "chrome_available", lambda: True)
        monkeypatch.setitem(run_scrapers.SCRAPERS, "austender", writes("A1", "A2"))
        monkeypatch.setitem(run_scrapers.SCRAPERS, "vic", writes("V1"))

    def test_writes_into_the_directory_it_is_given(self, output_dir):
        result = run_scrapers.run_scraper(limit=10, output_dir=output_dir)

        assert result.output_dir == output_dir
        assert [folder.name for folder in result.tender_dirs] == ["A1", "A2"]
        assert (output_dir / "A1" / "tender.json").exists()

    def test_accepts_a_list_of_sources(self, output_dir):
        result = run_scrapers.run_scraper(
            limit=10, output_dir=output_dir, sources=["austender", "vic"]
        )

        assert result.counts == {"austender": 2, "vic": 1}

    def test_accepts_a_comma_separated_string(self, output_dir):
        result = run_scrapers.run_scraper(
            limit=10, output_dir=output_dir, sources="austender,vic"
        )

        assert result.counts == {"austender": 2, "vic": 1}

    def test_falls_back_to_the_environment(self, output_dir, monkeypatch):
        monkeypatch.setenv("SOURCES", "vic")

        assert run_scrapers.run_scraper(limit=10, output_dir=output_dir).counts == {"vic": 1}

    def test_limit_is_per_source(self, output_dir):
        result = run_scrapers.run_scraper(
            limit=1, output_dir=output_dir, sources="austender,vic"
        )

        assert result.counts == {"austender": 1, "vic": 1}

    def test_an_unknown_source_is_a_caller_error_and_raises(self, output_dir):
        """
        The one thing that does raise: everything else the manager needs to
        keep going through, but a typo in SOURCES should not scrape nothing
        and call it a quiet day.
        """
        with pytest.raises(ValueError, match="nsw"):
            run_scrapers.run_scraper(output_dir=output_dir, sources="nsw")

    def test_the_scrape_is_not_uploaded(self, output_dir, monkeypatch):
        """Publishing is the pipeline's decision, not a side effect of scraping."""
        published = []
        monkeypatch.setattr(storage, "publish", lambda *a, **k: published.append(True))
        monkeypatch.setenv("OUTPUT_BUCKET", "test-bucket")

        run_scrapers.run_scraper(limit=10, output_dir=output_dir)

        assert published == []


class TestTenderDirectories:
    def test_is_empty_for_a_directory_that_does_not_exist(self, tmp_path):
        assert run_scrapers.tender_directories(tmp_path / "nope") == []

    def test_finds_only_folders_holding_a_record(self, output_dir):
        writes("A1", "A2")(0, output_dir)
        (output_dir / "empty").mkdir()

        assert [f.name for f in run_scrapers.tender_directories(output_dir)] == ["A1", "A2"]


class TestBlobPrefix:
    """
    Raw scrape output is namespaced under raw/<source_id>/ so it cannot land in
    whatever prefix the front end builds document URLs from -- the attachments
    bucket is shared.
    """

    def test_uses_the_source_id_from_the_tenders_own_record(self, output_dir):
        folder = output_dir / "ATM_2026_3494"
        folder.mkdir()
        (folder / "tender.json").write_text('{"source_id": "austender"}')

        assert storage.blob_prefix(folder) == "raw/austender/ATM_2026_3494"

    def test_keeps_two_sources_apart(self, output_dir):
        for reference, source in (("A1", "austender"), ("V1", "vic-buyingfor")):
            folder = output_dir / reference
            folder.mkdir()
            (folder / "tender.json").write_text('{"source_id": "%s"}' % source)

        assert storage.blob_prefix(output_dir / "A1") == "raw/austender/A1"
        assert storage.blob_prefix(output_dir / "V1") == "raw/vic-buyingfor/V1"

    def test_an_unreadable_record_still_gets_a_findable_prefix(self, output_dir):
        folder = output_dir / "ABC-1"
        folder.mkdir()

        assert storage.blob_prefix(folder) == "raw/unknown-source/ABC-1"

    def test_the_prefix_is_overridable(self, output_dir):
        folder = output_dir / "ABC-1"
        folder.mkdir()
        (folder / "tender.json").write_text('{"source_id": "qld-qtenders"}')

        assert storage.blob_prefix(folder, "scrapes") == "scrapes/qld-qtenders/ABC-1"


class TestUploadTenderFolder:
    """
    .txt is always a pipeline artifact (page text / extracted text), never a
    real attachment -- it must never reach the shared bucket the front end
    lists documents from.
    """

    def _fake_storage_client(self, monkeypatch, uploaded):
        class FakeBlob:
            def __init__(self, name):
                self.name = name

            def upload_from_filename(self, path, content_type=None):
                uploaded.append(self.name)

        class FakeBucket:
            def blob(self, name):
                return FakeBlob(name)

        class FakeClient:
            def bucket(self, name):
                return FakeBucket()

        import google.cloud.storage as gcs_storage

        monkeypatch.setattr(gcs_storage, "Client", FakeClient)

    def test_skips_txt_files_but_uploads_real_attachments(
        self, output_dir, monkeypatch
    ):
        folder = output_dir / "ABC-1"
        folder.mkdir()
        (folder / "tender.json").write_text('{"source_id": "fake"}')
        (folder / "ABC-1.txt").write_text("scraped page text")
        (folder / "document.PDF.txt").write_text("extracted text")
        (folder / "document.pdf").write_text("%PDF-1.4")
        (folder / "spreadsheet.xlsx").write_text("data")

        uploaded = []
        self._fake_storage_client(monkeypatch, uploaded)

        written = storage.upload_tender_folder("test-bucket", folder)

        assert written == 3
        assert not any(name.endswith(".txt") for name in uploaded)
        assert any(name.endswith("document.pdf") for name in uploaded)
        assert any(name.endswith("spreadsheet.xlsx") for name in uploaded)
        assert any(name.endswith("tender.json") for name in uploaded)


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
            (folder / "tender.json").write_text("{}")
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


class TestBrowserSetup:
    """
    The two browser-driven scrapers have to behave differently inside the
    image: Chrome cannot run as root without --no-sandbox, and Cloudflare
    challenges headless Chrome much harder than a headed one on a virtual
    display.
    """

    @pytest.fixture
    def captured(self, monkeypatch):
        """Capture the kwargs handed to SeleniumBase's Driver."""
        import seleniumbase

        recorded = {}
        monkeypatch.setattr(
            seleniumbase, "Driver", lambda **kwargs: recorded.update(kwargs) or "driver"
        )
        return recorded

    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("RUNNING_IN_CONTAINER", raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)

    def test_local_run_is_unchanged(self, captured, monkeypatch):
        from web_scrapers import common

        self._clean_env(monkeypatch)

        common.build_uc_driver(headless=True)

        assert captured == {"uc": True, "headless": True}

    def test_local_visible_run_is_unchanged(self, captured, monkeypatch):
        from web_scrapers import common

        self._clean_env(monkeypatch)

        common.build_uc_driver(headless=False)

        assert captured == {"uc": True, "headless": False}

    def test_in_the_container_chrome_gets_the_sandbox_and_shm_flags(
        self, captured, monkeypatch
    ):
        from web_scrapers import common

        self._clean_env(monkeypatch)
        monkeypatch.setenv("RUNNING_IN_CONTAINER", "1")

        common.build_uc_driver(headless=True)

        assert captured["no_sandbox"] is True
        assert "disable-dev-shm-usage" in captured["chromium_arg"]

    def test_the_reported_mode_matches_what_chrome_actually_does(self, monkeypatch):
        from web_scrapers import common

        self._clean_env(monkeypatch)

        assert common.describe_browser_mode(headless=True) == "headless"
        assert common.describe_browser_mode(headless=False) == "visible window"

        monkeypatch.setenv("DISPLAY", ":1001")

        # Chrome is headed here, so saying "headless" would send anyone
        # debugging a Cloudflare block down the wrong path.
        assert common.effective_headless(headless=True) is False
        assert common.describe_browser_mode(headless=True) == (
            "headed on virtual display :1001"
        )

    def test_a_virtual_display_overrides_headless(self, captured, monkeypatch):
        from web_scrapers import common

        self._clean_env(monkeypatch)
        monkeypatch.setenv("RUNNING_IN_CONTAINER", "1")
        monkeypatch.setenv("DISPLAY", ":99")

        common.build_uc_driver(headless=True)

        # Headed against Xvfb: much less likely to be challenged by Cloudflare.
        assert captured["headless"] is False

    def test_both_browser_scrapers_use_the_shared_factory(self, captured, monkeypatch):
        from web_scrapers.qld_qtenders import qld_qtenders
        from web_scrapers.vic_buyingfor import vic_buyingfor

        self._clean_env(monkeypatch)
        monkeypatch.setenv("RUNNING_IN_CONTAINER", "1")

        for module in (vic_buyingfor, qld_qtenders):
            captured.clear()
            module.build_driver(headless=True)
            assert captured["no_sandbox"] is True, module.__name__


class TestVirtualDisplay:
    """
    The container starts Xvfb from Python rather than wrapping the entrypoint
    in xvfb-run: the wrapper produces no output when it fails to bring the X
    server up, so a failure is indistinguishable from a slow scrape.
    """

    def test_is_a_no_op_outside_a_container(self, monkeypatch):
        monkeypatch.delenv("RUNNING_IN_CONTAINER", raising=False)
        started = []
        monkeypatch.setattr(
            run_scrapers, "_start_display", lambda: started.append(True) or None
        )

        with run_scrapers.virtual_display(["vic", "qld"]):
            pass

        assert started == []

    def test_is_a_no_op_when_no_browser_source_is_requested(self, monkeypatch):
        monkeypatch.setenv("RUNNING_IN_CONTAINER", "1")
        started = []
        monkeypatch.setattr(
            run_scrapers, "_start_display", lambda: started.append(True) or None
        )

        with run_scrapers.virtual_display(["austender"]):
            pass

        assert started == []

    @pytest.mark.parametrize("sources", [["vic"], ["qld"], ["austender", "vic"]])
    def test_starts_for_a_containerised_browser_run(self, monkeypatch, sources):
        monkeypatch.setenv("RUNNING_IN_CONTAINER", "1")
        started = []
        monkeypatch.setattr(
            run_scrapers, "_start_display", lambda: started.append(True) or None
        )

        with run_scrapers.virtual_display(sources):
            pass

        assert started == [True]

    def test_is_stopped_afterwards(self, monkeypatch):
        monkeypatch.setenv("RUNNING_IN_CONTAINER", "1")
        stopped = []

        class FakeDisplay:
            def stop(self):
                stopped.append(True)

        monkeypatch.setattr(run_scrapers, "_start_display", FakeDisplay)

        with run_scrapers.virtual_display(["vic"]):
            pass

        assert stopped == [True]

    def test_a_failed_display_degrades_to_headless_rather_than_raising(
        self, monkeypatch
    ):
        monkeypatch.setenv("RUNNING_IN_CONTAINER", "1")
        monkeypatch.setattr(run_scrapers, "_start_display", lambda: None)

        # The scrape must still run; Chrome just falls back to headless.
        with run_scrapers.virtual_display(["vic"]):
            ran = True

        assert ran

    def test_a_display_that_will_not_stop_does_not_fail_the_run(self, monkeypatch):
        monkeypatch.setenv("RUNNING_IN_CONTAINER", "1")

        class BrokenDisplay:
            def stop(self):
                raise RuntimeError("Xvfb already gone")

        monkeypatch.setattr(run_scrapers, "_start_display", BrokenDisplay)

        with run_scrapers.virtual_display(["vic"]):
            pass  # must not raise on exit


class TestPerSourceOptions:
    def test_max_pages_reaches_the_qld_scraper(self, output_dir, monkeypatch):
        """
        LIMIT only trims QTenders' tender list *after* every search page has
        been walked, so MAX_PAGES is the knob that actually shortens the run.
        It has to survive the trip through this entrypoint.
        """
        from web_scrapers.qld_qtenders import qld_qtenders

        captured = {}
        monkeypatch.setenv("MAX_PAGES", "2")
        monkeypatch.setattr(
            qld_qtenders, "run_scraper", lambda **kwargs: captured.update(kwargs) or []
        )

        run_scrapers.scrape_qld(5, output_dir)

        assert captured["max_pages"] == 2
        assert captured["limit"] == 5

    def test_defaults_to_walking_every_page(self, output_dir, monkeypatch):
        from web_scrapers.qld_qtenders import qld_qtenders

        captured = {}
        monkeypatch.delenv("MAX_PAGES", raising=False)
        monkeypatch.setattr(
            qld_qtenders, "run_scraper", lambda **kwargs: captured.update(kwargs) or []
        )

        run_scrapers.scrape_qld(5, output_dir)

        assert captured["max_pages"] == 0
