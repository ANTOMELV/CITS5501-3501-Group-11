"""Shared test setup: no test may open a real network connection."""

import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any attempt to connect a socket fail the test."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("tests must stay offline")

    monkeypatch.setattr(socket.socket, "connect", refuse)
