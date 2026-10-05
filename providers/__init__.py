import config
from providers.base import BaseProvider, Match, TeamForm


def get_provider() -> BaseProvider:
    """Factory driven by the PROVIDER env var.

    hybrid (default) -> free CSVs, falling back to API-Football when the free
                        card is too thin. Costs 0 API requests on a normal day.
    free             -> football-data.co.uk only. No key, no limits, ever.
    apifootball      -> live API only (needs APIFOOTBALL_KEY, 100 req/day free)
    mock             -> offline generated data, for tests with no network
    """
    name = config.PROVIDER
    if name in ("hybrid", "auto", "both"):
        from providers.hybrid import HybridProvider
        return HybridProvider()
    if name in ("free", "footballdata", "footballdata_uk", "csv"):
        from providers.footballdata_uk import FootballDataUKProvider
        return FootballDataUKProvider()
    if name in ("apifootball", "api", "api-football"):
        from providers.apifootball import APIFootballProvider
        return APIFootballProvider()
    from providers.mock import MockProvider
    return MockProvider()


__all__ = ["BaseProvider", "Match", "TeamForm", "get_provider"]
