// TypeScript fetch + axios HTTP client calls — test fixture for Feature 17.

import axios from "axios";

async function fetchUsers() {
    const resp = await fetch("/api/users");
    return resp.json();
}

async function createUser(data: object) {
    const resp = await fetch("/api/users", {
        method: "POST",
        body: JSON.stringify(data),
    });
    return resp.json();
}

async function fetchWithAxios() {
    const resp = await axios.get("/api/health");
    return resp.data;
}

async function updateWithAxios(id: string) {
    const resp = await axios.put(`/api/users/${id}`);
    return resp.data;
}

function notHttp() {
    const map = new Map();
    return map.get("key");
}
