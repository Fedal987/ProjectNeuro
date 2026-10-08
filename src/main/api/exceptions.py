"""
    ProjectNeuro
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/ProjectNeuro
"""

class ProviderError(Exception):
    pass


class ProviderConnectionError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


class ProviderInterrupted(ProviderError):
    pass
