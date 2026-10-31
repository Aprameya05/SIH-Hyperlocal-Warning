// prints JSON records from the browser twin for the cells/hours/now given on argv: <now_iso> <cell:hour,...>
import fs from 'node:fs'; import { createRequire } from 'node:module';
const L = createRequire(import.meta.url)('../assets/drift_location.js');
const j = f => (fs.existsSync(`data/${f}`) ? JSON.parse(fs.readFileSync(`data/${f}`)) : null);
const S = L.create({ grid: j('india_grid_025.json'), places: j('india_places.json'), fields: j('india_fields_latest.json'), statics: j('india_static_025.json'), live: j('drift_live.json'), tsmodel: j('india_ts_model_latest.json') });
const now = Date.parse(process.argv[2]); const out = { fieldsOk: S.fieldsOk, records: {}, search: S.search('mumbai', 5), resolve: [S.resolve(12.97, 77.59), S.resolve(15, 65)] };
for (const spec of process.argv.slice(3)) { const [c, h] = spec.split(':'); out.records[spec] = S.record(c, +h, now); }
console.log(JSON.stringify(out));
