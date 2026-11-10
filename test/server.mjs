// Local stand-in for production: an in-memory DynamoDB (dynalite), the real Lambda handler behind a tiny HTTP
// wrapper that adds CORS headers the way a Function URL does, and a static server for web/ on a DIFFERENT port,
// so the browser talks to the API cross-origin exactly as it will between Amplify and Lambda.
//   npm run dev  ->  site http://127.0.0.1:8787/   api http://127.0.0.1:8788/
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import dynalite from 'dynalite';
import { CreateTableCommand, DynamoDBClient } from '@aws-sdk/client-dynamodb';

const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.png': 'image/png', '.css': 'text/css; charset=utf-8' };
const listen = (server, port) => new Promise((r) => server.listen(port, '127.0.0.1', r));

export async function start({ sitePort = 0, apiPort = 0 } = {}) {
  const db = dynalite({ createTableMs: 0 });
  await listen(db, 0);
  const endpoint = 'http://127.0.0.1:' + db.address().port;
  Object.assign(process.env, { DDB_ENDPOINT: endpoint, TABLE_NAME: 'santa-local' });
  const raw = new DynamoDBClient({ endpoint, region: 'local', credentials: { accessKeyId: 'local', secretAccessKey: 'local' } });
  await raw.send(new CreateTableCommand({
    TableName: 'santa-local', BillingMode: 'PAY_PER_REQUEST',
    AttributeDefinitions: [{ AttributeName: 'pk', AttributeType: 'S' }], KeySchema: [{ AttributeName: 'pk', KeyType: 'HASH' }],
  }));
  const { handler } = await import('../api/index.mjs');

  let apiUrl = '';
  const site = http.createServer(async (req, res) => {
    const path = new URL(req.url, 'http://local').pathname;
    if (path === '/config.js') { res.writeHead(200, { 'content-type': TYPES['.js'] }); return res.end(`window.SANTA_API = ${JSON.stringify(apiUrl)};\n`); }
    const file = path === '/' ? '/index.html' : path;
    try {
      const body = await readFile(new URL('../web' + file, import.meta.url));
      res.writeHead(200, { 'content-type': TYPES[file.slice(file.lastIndexOf('.'))] || 'application/octet-stream' });
      res.end(body);
    } catch { res.writeHead(404); res.end('not found'); }
  });
  await listen(site, sitePort);
  const siteUrl = 'http://127.0.0.1:' + site.address().port;

  const api = http.createServer(async (req, res) => {
    const cors = { 'access-control-allow-origin': siteUrl, 'access-control-allow-methods': 'POST', 'access-control-allow-headers': 'content-type', 'access-control-max-age': '86400', vary: 'origin' };
    if (req.method === 'OPTIONS') { res.writeHead(204, cors); return res.end(); }
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const out = await handler({
      rawPath: new URL(req.url, 'http://local').pathname,
      headers: Object.fromEntries(Object.entries(req.headers).map(([k, v]) => [k.toLowerCase(), String(v)])),
      requestContext: { http: { method: req.method, sourceIp: req.headers['x-test-ip'] || req.socket.remoteAddress }, domainName: req.headers.host },
      body: chunks.length ? Buffer.concat(chunks).toString('utf8') : undefined,
      isBase64Encoded: false,
    });
    res.writeHead(out.statusCode, { ...out.headers, ...cors });
    res.end(out.body);
  });
  await listen(api, apiPort);
  apiUrl = 'http://127.0.0.1:' + api.address().port;
  return { siteUrl, apiUrl, raw, table: 'santa-local', stop: () => { site.close(); api.close(); db.close(); } };
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split('/').pop())) {
  const s = await start({ sitePort: Number(process.env.PORT) || 8787, apiPort: Number(process.env.API_PORT) || 8788 });
  console.log('site: ' + s.siteUrl + '/');
  console.log('api:  ' + s.apiUrl + '/');
}
