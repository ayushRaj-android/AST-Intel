import express from "express";

const router = express.Router();

router.get("/health", healthHandler);

router.get("/api/users", listUsers);

router.post("/api/users", createUser);

router.delete("/api/users/:id", deleteUser);

function healthHandler(req, res) {
    res.send("OK");
}

function listUsers(req, res) {
    res.json([]);
}

function createUser(req, res) {
    res.json({ id: 1 });
}

function deleteUser(req, res) {
    res.json({ deleted: true });
}

function internalHelper() {
    return "no route";
}
