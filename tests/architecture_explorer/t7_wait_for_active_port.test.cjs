"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { waitForActivePort } = require("./t7_wait_for_active_port.cjs");

function transientError(code) {
  return Object.assign(new Error(`${code}: resource busy or locked`), { code });
}

test("retries a transient EBUSY read and returns the published port", async () => {
  let reads = 0;
  const waits = [];
  const port = await waitForActivePort({
    activePortPath: "DevToolsActivePort",
    fs: {
      existsSync: () => true,
      readFileSync: () => {
        reads += 1;
        if (reads === 1) throw transientError("EBUSY");
        return "9222\n/devtools/browser/example";
      },
    },
    delay: async (milliseconds) => waits.push(milliseconds),
  });

  assert.equal(port, 9222);
  assert.equal(reads, 2);
  assert.deepEqual(waits, [50]);
});

test("retries a transient EPERM read and returns the published port", async () => {
  let reads = 0;
  const waits = [];
  const port = await waitForActivePort({
    activePortPath: "DevToolsActivePort",
    fs: {
      existsSync: () => true,
      readFileSync: () => {
        reads += 1;
        if (reads === 1) throw transientError("EPERM");
        return "9333\n/devtools/browser/example";
      },
    },
    delay: async (milliseconds) => waits.push(milliseconds),
  });

  assert.equal(port, 9333);
  assert.equal(reads, 2);
  assert.deepEqual(waits, [50]);
});

test("does not swallow an unexpected read error", async () => {
  const unexpected = Object.assign(new Error("disk read failed"), { code: "EIO" });
  let waits = 0;

  await assert.rejects(
    waitForActivePort({
      activePortPath: "DevToolsActivePort",
      fs: {
        existsSync: () => true,
        readFileSync: () => { throw unexpected; },
      },
      delay: async () => { waits += 1; },
    }),
    (error) => error === unexpected,
  );
  assert.equal(waits, 0);
});

test("fails after the existing wait limit when transient locks persist", async () => {
  let reads = 0;
  const waits = [];

  await assert.rejects(
    waitForActivePort({
      activePortPath: "DevToolsActivePort",
      fs: {
        existsSync: () => true,
        readFileSync: () => {
          reads += 1;
          throw transientError(reads % 2 === 0 ? "EPERM" : "EBUSY");
        },
      },
      delay: async (milliseconds) => waits.push(milliseconds),
    }),
    /Chrome did not publish DevToolsActivePort/,
  );
  assert.equal(reads, 100);
  assert.equal(waits.length, 100);
  assert.ok(waits.every((milliseconds) => milliseconds === 50));
});

test("fails after the existing wait limit when the port file stays unavailable", async () => {
  let checks = 0;
  let reads = 0;
  const waits = [];

  await assert.rejects(
    waitForActivePort({
      activePortPath: "DevToolsActivePort",
      fs: {
        existsSync: () => { checks += 1; return false; },
        readFileSync: () => { reads += 1; return "9444"; },
      },
      delay: async (milliseconds) => waits.push(milliseconds),
    }),
    /Chrome did not publish DevToolsActivePort/,
  );
  assert.equal(checks, 100);
  assert.equal(reads, 0);
  assert.equal(waits.length, 100);
  assert.ok(waits.every((milliseconds) => milliseconds === 50));
});
