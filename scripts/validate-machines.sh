#!/usr/bin/env bash
# Validate this repo's machine definitions against the CANONICAL machine schema.
#
# data/machines/*.json load into the Reality Engine alongside the corpus and
# write real positions in the universal vector, but they live here rather than
# in RealityEngine_Machines, so the corpus gates never saw them
# (jateeter/localAIStack#38). A machine writing the vector that no schema
# validated is a contributor the arbitration registry cannot account for.
#
# The schema and the validator both come from RealityEngine_Machines on purpose.
# "Validate against the canonical schema" must not turn into a second
# implementation of validation that can drift from the first: same
# schemas/machine.schema.json, same Ajv 2020 setup as scripts/validate-schemas.mjs.
#
# Usage:
#   ./scripts/validate-machines.sh                # sibling RealityEngine_Machines
#   MACHINES_DIR=/path/to/RealityEngine_Machines ./scripts/validate-machines.sh
#
# Exits non-zero on any violation, and on a missing schema, validator or Node —
# a gate that cannot run must say so rather than pass quietly.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MACHINES_DIR="${MACHINES_DIR:-$REPO_DIR/../RealityEngine_Machines}"
MACHINE_JSON_DIR="$REPO_DIR/data/machines"

fail() { echo "[fail] $*" >&2; exit 1; }

command -v node >/dev/null || fail "node is required to run the canonical Ajv validator"
[ -d "$MACHINE_JSON_DIR" ] || fail "no machine definitions at $MACHINE_JSON_DIR"
[ -f "$MACHINES_DIR/schemas/machine.schema.json" ] || \
  fail "canonical schema not found under $MACHINES_DIR/schemas — set MACHINES_DIR"
[ -d "$MACHINES_DIR/node_modules/ajv" ] || \
  fail "Ajv not installed in $MACHINES_DIR — run 'npm ci' there first"

count=$(find "$MACHINE_JSON_DIR" -name '*.json' | wc -l | tr -d ' ')
[ "$count" -gt 0 ] || fail "no *.json under $MACHINE_JSON_DIR"
echo "validate-machines: $count definition(s) against $MACHINES_DIR/schemas/machine.schema.json"

MACHINES_DIR="$MACHINES_DIR" MACHINE_JSON_DIR="$MACHINE_JSON_DIR" \
node --input-type=module -e '
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { createRequire } from "node:module";

const machinesDir = process.env.MACHINES_DIR;
const jsonDir     = process.env.MACHINE_JSON_DIR;
// Resolve Ajv out of the canonical repo so the version matches the corpus gate.
const require  = createRequire(join(machinesDir, "package.json"));
const Ajv2020  = require("ajv/dist/2020.js");
const addFormats = require("ajv-formats");

const ajv = new Ajv2020({ strict: false, allErrors: true, allowUnionTypes: true });
(addFormats.default || addFormats)(ajv);

const schemaDir = join(machinesDir, "schemas");
for (const f of readdirSync(schemaDir).filter((n) => n.endsWith(".schema.json"))) {
  ajv.addSchema(JSON.parse(readFileSync(join(schemaDir, f), "utf8")));
}
const validate = ajv.getSchema("https://realityengine.example.org/schemas/machine.schema.json");
if (!validate) { console.error("[fail] machine.schema.json not registered"); process.exit(1); }

let bad = 0;
for (const f of readdirSync(jsonDir).filter((n) => n.endsWith(".json")).sort()) {
  const doc = JSON.parse(readFileSync(join(jsonDir, f), "utf8"));
  if (validate(doc)) { console.log(`  ok   ${f}`); continue; }
  bad++;
  console.log(`  FAIL ${f}`);
  for (const e of validate.errors) console.log(`         ${e.instancePath || "/"} ${e.message}`);
}
if (bad) { console.error(`\n[fail] ${bad} machine definition(s) violate the canonical schema`); process.exit(1); }
console.log("\n[ok]   every localAI machine definition validates against the canonical schema");
'
