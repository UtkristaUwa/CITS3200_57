"""
The tender scrapers.

The pipeline's manager function imports this package and calls it in-process:

    from web_scrapers import run_scraper

    result = run_scraper(limit=10, output_dir=temp_dir)
    for folder in result.tender_dirs:
        ...

See INTEGRATION.md for the full contract.
"""

__all__ = ["run_scraper", "ScrapeResult"]


def __getattr__(name):
    """
    Expose the entrypoint lazily.

    Importing run_scrapers here at module level would make `web_scrapers` a
    partially initialised module while run_scrapers imports `common` and
    `storage` back out of it. Deferring the import keeps `from web_scrapers
    import common` -- which every scraper does -- free of that cycle.
    """
    if name in __all__:
        from web_scrapers import run_scrapers

        return getattr(run_scrapers, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
