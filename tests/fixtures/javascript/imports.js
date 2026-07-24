/**
 * Fixture: JavaScript imports — tests both CommonJS require and ES import.
 */

// CommonJS
const express = require("express");
const { Router, Request } = require("express");
const axios = require("axios");

// ES module imports
import { readFile } from "fs/promises";
import path from "path";
import * as os from "os";

// Usage (scoped method calls)
Router.use("/api");
axios.get("/url");
express.json();
path.join("/a", "/b");
os.platform();
