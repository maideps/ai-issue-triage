#!/usr/bin/env node
// Runs Python tooling for the backend with the right interpreter, from any OS.
//
//   node tools/py.mjs setup                create backend/.venv and install the backend with dev tools
//   node tools/py.mjs -m pytest -q         run a Python module inside backend/
//
// Interpreter order: $ISSUE_TRIAGE_PYTHON, backend/.venv, then python3 / python on PATH.
// The harness gates (npm run lint / typecheck / test / build) call this launcher so they
// behave the same on Windows, macOS and Linux.

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const backend = path.join(root, "backend");

function works(cmd) {
  return spawnSync(cmd, ["--version"], { stdio: "ignore" }).status === 0;
}

function systemPython() {
  return ["python3", "python"].find(works) ?? "";
}

function venvPython() {
  const candidates = [
    path.join(backend, ".venv", "bin", "python"),
    path.join(backend, ".venv", "Scripts", "python.exe"),
  ];
  return candidates.find((candidate) => existsSync(candidate)) ?? "";
}

function run(cmd, args, options = {}) {
  const result = spawnSync(cmd, args, { cwd: backend, stdio: "inherit", ...options });
  return result.status ?? 1;
}

const args = process.argv.slice(2);

if (args[0] === "setup") {
  const python = process.env.ISSUE_TRIAGE_PYTHON || systemPython();
  if (!python) {
    console.error("No Python interpreter found (need Python >= 3.11 on PATH).");
    process.exit(1);
  }
  if (!venvPython() && run(python, ["-m", "venv", ".venv"]) !== 0) process.exit(1);
  const venv = venvPython();
  process.exit(run(venv, ["-m", "pip", "install", "--quiet", "-e", ".[dev]"]));
}

const python = process.env.ISSUE_TRIAGE_PYTHON || venvPython() || systemPython();
if (!python) {
  console.error("No Python interpreter found (need Python >= 3.11 on PATH).");
  process.exit(1);
}
const ready = spawnSync(python, ["-c", "import issue_triage, fastapi, pytest, mypy, ruff"], {
  cwd: backend,
  stdio: "ignore",
}).status === 0;
if (!ready) {
  console.error("Backend dependencies are not installed. Run: npm run setup:backend");
  process.exit(1);
}
process.exit(run(python, args));
