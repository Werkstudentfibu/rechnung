"""Belegter Port -> eigener freier Port statt Verbindung zum alten Prozess."""

import socket

from rechnung.desktop import freier_port


def test_freier_port_bleibt_wenn_frei():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]
    assert freier_port(p) == p


def test_belegter_port_wird_ausgewichen():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); s.listen(1)
        belegt = s.getsockname()[1]
        neu = freier_port(belegt)
        assert neu != belegt and neu > 0
