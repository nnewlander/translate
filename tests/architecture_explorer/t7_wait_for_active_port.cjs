"use strict";

async function waitForActivePort({ activePortPath, fs, delay }) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (fs.existsSync(activePortPath)) {
      try {
        const [port] = fs.readFileSync(activePortPath, "utf8").trim().split(/\r?\n/);
        if (port) return Number(port);
      } catch (error) {
        if (error.code !== "EBUSY" && error.code !== "EPERM") throw error;
      }
    }
    await delay(50);
  }
  throw new Error("Chrome did not publish DevToolsActivePort");
}

module.exports = { waitForActivePort };
