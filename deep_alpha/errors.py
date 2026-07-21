"""User-facing error types and CLI exit codes."""


class DeepAlphaError(Exception):
    exit_code = 1


class ArgumentError(DeepAlphaError):
    exit_code = 2


class ProviderError(DeepAlphaError):
    exit_code = 3


class DownloadError(DeepAlphaError):
    exit_code = 4


class QueryError(DeepAlphaError):
    exit_code = 5
