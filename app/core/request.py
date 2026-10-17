from flask import Request


class LimitedRequest(Request):
    """Allow routes to narrow the body limit without changing shared app config.

    Flask 3.0 has a read-only max_content_length property. Keeping the override on
    the request also bounds multipart parsing, including chunked request bodies.
    A stricter operator-configured limit always takes precedence.
    """

    _route_content_limit = None

    @property
    def max_content_length(self):
        configured = super().max_content_length
        if self._route_content_limit is None:
            return configured
        if configured is None:
            return self._route_content_limit
        return min(configured, self._route_content_limit)

    @max_content_length.setter
    def max_content_length(self, value):
        self._route_content_limit = value
