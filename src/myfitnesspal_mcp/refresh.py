from pathlib import Path

from . import auth, config, mfp_client

MFP_URL = "https://www.myfitnesspal.com/"
SETTLE_MS = 4000


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def profile_dir() -> Path:
    return config.data_dir() / "browser-profile"


def profile_seeded() -> bool:
    return profile_dir().is_dir()


def _visit_and_harvest(seed_cookies: dict[str, str] | None) -> dict[str, str]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(profile_dir()), headless=True
        )
        try:
            if seed_cookies:
                context.add_cookies(
                    [
                        {
                            "name": name,
                            "value": value,
                            "domain": ".myfitnesspal.com",
                            "path": "/",
                            "secure": True,
                        }
                        for name, value in seed_cookies.items()
                    ]
                )
            page = context.pages[0]
            page.goto(MFP_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(SETTLE_MS)
            harvested = {}
            for cookie in context.cookies(MFP_URL):
                harvested[cookie["name"]] = cookie["value"]
            return harvested
        finally:
            context.close()


def seed_profile(cookies: dict[str, str]) -> None:
    harvested = _visit_and_harvest(cookies)
    if auth.SESSION_COOKIE not in harvested:
        raise RuntimeError(
            "the browser visit did not produce a MyFitnessPal session cookie"
        )


def refresh_session() -> None:
    """Rotate the session by revisiting MFP in the seeded headless browser
    profile, then persist the fresh cookies and drop the cached client."""
    if available() and profile_seeded():
        harvested = _visit_and_harvest(None)
        if auth.SESSION_COOKIE in harvested:
            auth.save_cookies(harvested)
    mfp_client.reset()
