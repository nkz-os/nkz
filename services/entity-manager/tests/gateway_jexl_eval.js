// Evaluates IoT Agent attribute mappings over a measure with the agent's own JEXL code.
// Usage: node gateway_jexl_eval.js < {"mappings": [...], "measures": [{...}, ...]}  (run in the
// fiware/iotagent-json image, no network). It repeats, per mapping and in order, what
// sendUpdateValueNgsiLD (iotagent-node-lib entities-NGSI-LD.js) does with the JEXL context:
//   ctx[name] = measure[object_id]; value = expression(ctx) (null/NaN -> null); ctx[name] = value.
// Output per measure: {entity_name: {attribute: {"value": v, "metadata": m?}}} for the
// attributes written (non-null value). Instants are replaced by "<NOW>".
const lib = '/opt/iotagent-json/node_modules/iotagent-node-lib/lib/plugins/jexlParser';
const jexlParser = require(lib);
jexlParser.setTransforms({});
const TYPE_INFO = { service: 'svc', subservice: '/' };
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

function run(mappings, measure) {
  const ctx = JSON.parse(JSON.stringify(measure));
  ctx.entity_name = 'urn:ngsi-ld:Device:acme:gw-aaaaaaaaaaaa';
  const written = {};
  for (const raw of mappings) {
    const attr = JSON.parse(JSON.stringify(raw));
    const objectId = attr.object_id || attr.name;
    ctx[attr.name] = measure[objectId];
    let value = measure[objectId] === undefined ? undefined : measure[objectId];
    if (typeof attr.expression === 'string' && attr.expression !== '') {
      try {
        value = jexlParser.applyExpression(attr.expression, ctx, TYPE_INFO);
        if (value === null || value === undefined || Number.isNaN(value)) value = null;
      } catch (e) {
        value = null;
      }
    }
    ctx[attr.name] = value;
    if (value === null || value === undefined) continue;
    const ent = attr.entity_name || '<primary>';
    written[ent] = written[ent] || {};
    const out = { value: typeof value === 'string' && ISO.test(value) ? '<NOW>' : value };
    if (attr.metadata) out.metadata = attr.metadata;
    out.type = attr.type;
    written[ent][attr.name] = out;
  }
  return written;
}

let input = '';
process.stdin.on('data', (c) => (input += c));
process.stdin.on('end', () => {
  const { mappings, measures } = JSON.parse(input);
  process.stdout.write('\nRESULT:' + JSON.stringify(measures.map((m) => run(mappings, m))) + '\n');
});
