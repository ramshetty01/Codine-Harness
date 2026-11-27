"""Opt-in CI guard, loaded only when this directory is on PYTHONPATH."""
import socket


def deny_network(*args, **kwargs):
    raise AssertionError('Network access is forbidden during offline Codine checks')


socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
socket.create_connection = deny_network
