// Validate every .glb under a directory with the Khronos glTF validator.
import fs from "node:fs";
import path from "node:path";
import validator from "gltf-validator";

const dir = process.argv[2];
let errors = 0, warnings = 0, files = 0;
for (const f of fs.readdirSync(dir).filter((f) => f.endsWith(".glb"))) {
  const report = await validator.validateBytes(new Uint8Array(fs.readFileSync(path.join(dir, f))));
  files++;
  errors += report.issues.numErrors;
  warnings += report.issues.numWarnings;
  if (report.issues.numErrors || report.issues.numWarnings)
    console.log(f, JSON.stringify(report.issues.messages.slice(0, 5)));
}
console.log(JSON.stringify({ files, errors, warnings }));
process.exit(errors ? 1 : 0);
