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
