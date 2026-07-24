/**
 * Fixture: TypeScript imports — tests import extraction and import map building.
 */

import { Router, Request, Response } from "express";
import axios from "axios";
import * as fs from "fs";
import type { Config } from "./config";
import { readFile as readAsync } from "fs/promises";

// Scoped method calls (imported_package_methods)
const r = Router();
Router.use("/api");
axios.get("/url");
fs.readFile("test");

// Deep property access
fs.promises.readFile("test");
