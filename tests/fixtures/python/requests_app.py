# Python HTTP client calls — test fixture for Feature 17.

import requests
import httpx


def fetch_users():
    resp = requests.get("http://example.com/api/users")
    return resp.json()


def create_user(data):
    resp = requests.post("http://example.com/api/users", json=data)
    return resp.json()


async def fetch_with_httpx():
    resp = httpx.get("http://example.com/api/health")
    return resp.json()


def fetch_with_fstring(user_id):
    resp = requests.get(f"/api/users/{user_id}")
    return resp.json()


async def fetch_with_session(session):
    resp = session.get("http://example.com/api/orders")
    return resp


def not_http():
    data = {"key": "value"}
    return data.get("key")
