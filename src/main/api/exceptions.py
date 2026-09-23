class ProviderError(Exception):
    pass


class ProviderConnectionError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


class ProviderInterrupted(ProviderError):
    pass
